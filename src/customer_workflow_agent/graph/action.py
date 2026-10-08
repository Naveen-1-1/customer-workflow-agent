"""The shared flow of every subgraph that changes the store.

    start → interpret (LLM) → <each policy check, one named node per check> → prepare_summary
      any check → ask → wait (pause) → interpret → first check again …
      any check → deny → finish
      prepare_summary → confirm (popup pause)
        yes → execute (store write) → finish
              (returns only: approval_gate → supervisor (pause) → rejected → block + offer human)
        no  → back to wait

Each request type lists its checks in order (`steps`), so the graph shows what it checks.
`prepare_summary` builds the popup and the store call from the same data, so the popup shows
exactly what `execute` writes. Nothing writes before the popup's yes.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from customer_workflow_agent.contract import ApprovalReply
from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.common import (
    TRANSFER_SUMMARY,
    build_requests,
    confirm_pause,
    enqueue,
    keyword_intent,
    phrase,
    says,
    transfer_request,
    wait_for_customer,
)
from customer_workflow_agent.graph.state import (
    ChatState,
    Outcome,
    Request,
    agent_msg,
    event_msg,
    last_agent_text,
    last_id,
    undelivered,
)
from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.schemas import Turn
from customer_workflow_agent.llm.structured import LLMUnavailable
from customer_workflow_agent.policy.rules import Denial, needs_approval
from customer_workflow_agent.store import RetailStore, StoreError
from customer_workflow_agent.store.models import User, UserAddress
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates import messages as M
from customer_workflow_agent.templates import suggestions as S
from customer_workflow_agent.templates.denials import denial_text


@dataclass
class Ask:
    question: str  # phrased by the LLM (no facts) or used as-is
    details: str = ""  # factual lines shown after the question (choices, options)
    preface: str = ""  # factual notice shown before the question
    slot: str = ""
    choices: dict | None = None  # {"kind": ..., "values": [...], "index": int | None}
    options: list[str] = field(default_factory=list)  # a numbered list, shown as `details`
    suggestions: list[dict] | None = None  # reply buttons; default: one per option

    def __post_init__(self):
        # The message and the buttons come from the same labels, so they always match.
        if self.options and not self.details:
            self.details = F.numbered(self.options)


@dataclass
class Ready:
    summary: dict  # ConfirmSummary
    call: dict  # {"fn": store method, "args": {...}}
    result: dict = field(default_factory=dict)  # facts for the result message
    refund_total: float | None = None
    approval: dict | None = None  # ApprovalRequest, for returns


@dataclass
class Found:
    """A check passed. `values` are facts later checks need (order id, item lines, method)."""

    values: dict = field(default_factory=dict)


Check = Found | Ask | Denial


@dataclass
class Ctx:
    deps: Deps
    store: RetailStore
    user: User
    request: Request


Step = Callable[[dict, dict, Ctx], Check]  # (slots, found so far, ctx) -> result


class ActionSpec(Protocol):
    name: str
    turn_schema: type[Turn]
    reminder: str | None
    needs_approval_step: bool  # only returns can need a supervisor
    steps: Sequence[tuple[str, Step]]  # the policy checks, in order; each becomes a named node

    def merge(self, slots: dict, turn: Turn, text: str, ctx: Ctx) -> dict: ...

    def prepare(self, slots: dict, found: dict, ctx: Ctx) -> Ready: ...

    def catalog(self, slots: dict, ctx: Ctx) -> list[dict] | None: ...

    def result_text(self, returned: object, ready: Ready, ctx: Ctx) -> str: ...


def apply_choice(slots: dict, turn: Turn, choices: dict | None) -> dict:
    """Map 'the second one' onto the list the agent just offered."""
    n = turn.choice_number
    if not choices or not n or not 1 <= n <= len(choices["values"]):
        return slots
    value = choices["values"][n - 1]
    kind, index = choices["kind"], choices.get("index")
    slots = dict(slots)
    if kind in ("order", "payment", "refund"):
        slots[
            {"order": "order_id", "payment": "payment_method_id", "refund": "refund_pm_id"}[kind]
        ] = value
    elif kind in ("line", "variant") and index is not None:
        changes = [dict(c) for c in slots.get("changes", [])]
        if index < len(changes):
            changes[index]["line" if kind == "line" else "new_item_id"] = value
        slots["changes"] = changes
    elif kind == "return_line" and index is not None:
        items = [dict(i) for i in slots.get("items", [])]
        if index < len(items):
            items[index]["lines"] = [value]
        slots["items"] = items
    return slots


def run_call(store: RetailStore, call: dict, op_id: str) -> object:
    args = dict(call["args"])
    if "address" in args:
        args["address"] = UserAddress(**args["address"])
    return getattr(store, call["fn"])(**args, op_id=op_id)


def build_action_subgraph(spec: ActionSpec, deps: Deps):
    settings = deps.settings
    step_names = [name for name, _ in spec.steps]
    first_step = step_names[0]

    def ctx(state: ChatState) -> Ctx:
        return Ctx(deps, deps.store, deps.store.get_user(state["user_id"]), state["current"])

    def finish_update(state: ChatState, status: str, code: str | None = None, **extra) -> dict:
        req = state["current"]
        work = state.get("work") or {}
        outcome: Outcome = {
            "request_id": req["id"],
            "type": req["type"],
            "order_id": (work.get("slots") or {}).get("order_id") or req.get("order_id"),
            "status": status,
            "code": code,
        }
        update = {"outcomes": [*(state.get("outcomes") or []), outcome], "route": "finish", **extra}
        group = req.get("group_id")
        if group:
            shared = {
                k: v
                for k, v in (work.get("slots") or {}).items()
                if k in ("reason", "payment", "payment_method_id", "refund_to")
            }
            update["group_slots"] = {**(state.get("group_slots") or {}), group: shared}
        return update

    async def start(state: ChatState) -> dict:
        req = state["current"]
        slots = {"order_id": req.get("order_id")}
        if req.get("group_id"):
            slots.update((state.get("group_slots") or {}).get(req["group_id"], {}))
        msgs = [agent_msg(spec.reminder, "notice")] if spec.reminder else []
        return {
            "work": {"slots": slots, "asks": {}, "declines": 0, "first": True},
            "messages": msgs,
            "route": "interpret",
        }

    async def interpret(state: ChatState) -> dict:
        work = dict(state["work"])
        req = state["current"]
        first = work.pop("first", False)
        # `text` is only what the customer typed: ids and values are checked against it.
        text = req["source_text"] if first else state.get("last_text", "")
        prompt_text = text
        if first and req.get("details") and req["details"] not in text:
            prompt_text = f"{text}\n(Request: {req['details']})"
        c = ctx(state)
        slots = work["slots"]
        try:
            turn = await deps.llm.extract(
                spec.turn_schema,
                prompts.turn(
                    spec.name,
                    prompt_text,
                    last_agent=None if first else last_agent_text(state),
                    known=_known(slots),
                    catalog=spec.catalog(slots, c),
                ),
            )
        except LLMUnavailable:
            if first:
                return {"work": work, "route": first_step}
            intent = keyword_intent(text)
            if intent == "skip":
                return {"work": work, "messages": [agent_msg(M.SKIPPED)], "route": "dropped"}
            if intent == "goodbye":
                return {"work": work, "queue": [], "end_requested": True, "route": "dropped"}
            if intent == "human":
                return {
                    "work": work,
                    "queue": enqueue(state.get("queue") or [], [transfer_request(text)]),
                    "messages": [agent_msg(M.QUEUED)],
                    "route": first_step,
                }
            return {"work": work, "messages": [agent_msg(M.LLM_TROUBLE)], "route": "wait"}

        update: dict = {}
        msgs = []
        # The current request is only dropped on an explicit "skip" (or goodbye): the label
        # must match the customer's own words. Otherwise the turn is treated as an answer.
        if turn.relation == "skip" and not first and says("skip", text):
            return {"work": work, "messages": [agent_msg(M.SKIPPED)], "route": "dropped"}
        if turn.relation == "goodbye" and not first and says("goodbye", text):
            return {"work": work, "queue": [], "end_requested": True, "route": "dropped"}
        queue = state.get("queue") or []
        new = []
        if turn.relation == "wants_human" and not first and says("human", text):
            new.append(transfer_request(text))
        if turn.new_requests and not first:
            current_order = slots.get("order_id")
            others = [
                r
                for r in turn.new_requests
                if not (r.type == spec.name and r.order_id in (None, current_order))
            ]  # an "extra" request identical to the current one is just this request
            new.extend(build_requests(others, text, deps, state["user_id"]))
        if new:
            update["queue"] = enqueue(queue, new)
            msgs.append(agent_msg(M.QUEUED))
        slots = apply_choice(slots, turn, work.get("choices"))
        slots = spec.merge(slots, turn, text, c)
        work["slots"] = slots
        work["choices"] = None
        return {**update, "work": work, "messages": msgs, "route": first_step}

    def check_node(index: int, step: Step):
        """One policy check as a node: pass → next check, else ask / deny / drop."""
        nxt = step_names[index + 1] if index + 1 < len(step_names) else "prepare_summary"

        async def node(state: ChatState) -> dict:
            work = dict(state["work"])
            found = {} if index == 0 else dict(work.get("found") or {})
            result = step(work["slots"], found, ctx(state))
            if isinstance(result, Denial):
                work["denial"] = {"code": result.code, "params": result.params}
                return {"work": work, "route": "deny"}
            if isinstance(result, Ask):
                asks = dict(work.get("asks") or {})
                asks[result.slot] = asks.get(result.slot, 0) + 1
                if asks[result.slot] > settings.max_asks_per_slot:
                    return {
                        "work": work,
                        "messages": [agent_msg(M.TOO_MANY_ASKS)],
                        "route": "dropped",
                    }
                work.update(asks=asks, ask=result.__dict__, choices=result.choices)
                return {"work": work, "route": "ask"}
            work["found"] = {**found, **result.values}
            return {"work": work, "route": nxt}

        return node

    async def prepare_summary(state: ChatState) -> dict:
        work = dict(state["work"])
        ready = spec.prepare(work["slots"], work.get("found") or {}, ctx(state))
        work["ready"] = ready.__dict__
        return {"work": work, "route": "confirm"}

    async def ask(state: ChatState) -> dict:
        a = state["work"]["ask"]
        # Questions introducing a list ("These are in stock:") keep their exact wording.
        question = a["question"]
        if not a["details"]:
            question = await phrase(deps, question, state.get("last_text", ""))
        text = "\n".join(p for p in (a["preface"], question, a["details"]) if p)
        buttons = a["suggestions"]
        if buttons is None:
            buttons = [S.suggestion(o) for o in a["options"]]
        return {"messages": [agent_msg(text, "question")], "suggestions": buttons, "route": "wait"}

    async def deny(state: ChatState) -> dict:
        d = state["work"]["denial"]
        return {
            "messages": [agent_msg(denial_text(d["code"], **d["params"]), "denial")],
            **finish_update(state, "denied", d["code"]),
        }

    async def confirm(state: ChatState) -> dict:
        ready = state["work"]["ready"]
        ok, update = confirm_pause(state, ready["summary"], spec.name)
        return {**update, "work": {**state["work"], "confirmed": ok}, "route": "after_confirm"}

    async def after_confirm(state: ChatState) -> dict:
        work = dict(state["work"])
        if work.get("confirmed"):
            return {"route": "approval_gate" if spec.needs_approval_step else "execute"}
        work["declines"] = work.get("declines", 0) + 1
        work["ready"] = None
        if work["declines"] >= settings.max_declines:
            return {
                "work": work,
                "messages": [agent_msg(M.TOO_MANY_DECLINES)],
                **finish_update(state, "declined"),
            }
        return {
            "work": work,
            "messages": [agent_msg(M.DECLINED)],
            "suggestions": S.SKIP_REQUEST,
            "route": "wait",
        }

    async def approval_gate(state: ChatState) -> dict:
        ready = state["work"]["ready"]
        if ready.get("approval") and needs_approval(settings, ready["refund_total"] or 0):
            return {"messages": [agent_msg(M.APPROVAL_WAIT, "notice")], "route": "approval"}
        return {"route": "execute"}

    async def approval(state: ChatState) -> dict:
        ready = state["work"]["ready"]
        reply = interrupt(
            {
                "type": "supervisor_approval",
                "messages": undelivered(state),
                "request": ready["approval"],
            }
        )
        decision = ApprovalReply.model_validate(reply)
        return {
            "work": {**state["work"], "approved": decision.approved, "note": decision.note},
            "messages": [
                event_msg("Supervisor approved" if decision.approved else "Supervisor rejected")
            ],
            "last_delivered": last_id(state),
            "route": "after_approval",
        }

    async def after_approval(state: ChatState) -> dict:
        work = state["work"]
        if work.get("approved"):
            return {"messages": [agent_msg(M.APPROVAL_GRANTED)], "route": "execute"}
        order_id = work["ready"]["approval"]["order_id"]
        deps.store.block_returns(order_id, op_id=f"{state['current']['id']}:block")
        return {
            "messages": [
                agent_msg(M.approval_rejected(work.get("note"))),
                agent_msg(M.TRANSFER_OFFER),
            ],
            "route": "offer_transfer",
        }

    async def offer_transfer(state: ChatState) -> dict:
        ok, update = confirm_pause(state, TRANSFER_SUMMARY, "transfer")
        return {**update, "work": {**state["work"], "transfer_ok": ok}, "route": "after_offer"}

    async def after_offer(state: ChatState) -> dict:
        if state["work"].get("transfer_ok"):
            queue = enqueue(
                state.get("queue") or [],
                [
                    transfer_request(
                        state.get("last_text", ""),
                        "Return rejected by supervisor; customer accepted transfer",
                    )
                ],
            )
            return {"queue": queue, **finish_update(state, "rejected", "approval_rejected")}
        return {
            "messages": [agent_msg(M.NO_TRANSFER)],
            **finish_update(state, "rejected", "approval_rejected"),
        }

    async def execute(state: ChatState) -> dict:
        ready_d = state["work"]["ready"]
        ready = Ready(**ready_d)
        c = ctx(state)
        try:
            returned = run_call(
                deps.store, ready.call, op_id=f"{state['current']['id']}:{spec.name}"
            )
        except StoreError as e:
            return {
                "messages": [agent_msg(denial_text("store_rejected", reason=e.message), "denial")],
                **finish_update(state, "failed", e.code),
            }
        return {
            "messages": [agent_msg(spec.result_text(returned, ready, c), "result")],
            **finish_update(state, "done"),
        }

    async def dropped(state: ChatState) -> dict:
        return finish_update(state, "dropped")

    async def finish(state: ChatState) -> dict:
        return {"current": None, "work": {}}

    g = StateGraph(ChatState)
    nodes = [("start", start), ("interpret", interpret)]
    nodes += [(name, check_node(i, step)) for i, (name, step) in enumerate(spec.steps)]
    nodes += [
        ("prepare_summary", prepare_summary),
        ("ask", ask),
        ("wait", wait_for_customer),
        ("deny", deny),
        ("confirm", confirm),
        ("after_confirm", after_confirm),
        ("execute", execute),
        ("dropped", dropped),
        ("finish", finish),
    ]
    if spec.needs_approval_step:
        nodes += [
            ("approval_gate", approval_gate),
            ("approval", approval),
            ("after_approval", after_approval),
            ("offer_transfer", offer_transfer),
            ("after_offer", after_offer),
        ]
    for name, fn in nodes:
        g.add_node(name, fn)

    def route(state: ChatState) -> str:
        return state["route"]

    g.add_edge(START, "start")
    g.add_conditional_edges("start", route, ["interpret"])
    g.add_conditional_edges("interpret", route, [first_step, "wait", "dropped"])
    for i, name in enumerate(step_names):
        nxt = step_names[i + 1] if i + 1 < len(step_names) else "prepare_summary"
        g.add_conditional_edges(name, route, [nxt, "ask", "deny", "dropped"])
    g.add_conditional_edges("prepare_summary", route, ["confirm"])
    g.add_conditional_edges("ask", route, ["wait"])
    g.add_edge("wait", "interpret")
    g.add_conditional_edges("deny", route, ["finish"])
    g.add_conditional_edges("confirm", route, ["after_confirm"])
    after_yes = "approval_gate" if spec.needs_approval_step else "execute"
    g.add_conditional_edges("after_confirm", route, [after_yes, "wait", "finish"])
    if spec.needs_approval_step:
        g.add_conditional_edges("approval_gate", route, ["approval", "execute"])
        g.add_conditional_edges("approval", route, ["after_approval"])
        g.add_conditional_edges("after_approval", route, ["execute", "offer_transfer"])
        g.add_conditional_edges("offer_transfer", route, ["after_offer"])
        g.add_conditional_edges("after_offer", route, ["finish"])
    g.add_conditional_edges("execute", route, ["finish"])
    g.add_conditional_edges("dropped", route, ["finish"])
    g.add_edge("finish", END)
    return g.compile()


def _known(slots: dict) -> dict:
    """Details collected so far, for the prompt (the customer's own data only)."""
    return {k: v for k, v in slots.items() if v not in (None, [], {}, "")}
