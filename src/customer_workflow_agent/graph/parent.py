"""The main conversation loop.

greet → wait_auth → auth_interpret → auth_check ─ verified → post_auth
            ↑__________ not yet / failed ________|          (3 failures → offer a human)
post_auth → wait_main → classify → dispatch → <request subgraph> → after_request → dispatch …
                ↑                    └ queue empty → anything_else ──────────────┘
                └── small talk / unclear
goodbye → END        transfer subgraph → END
"""

from langgraph.graph import END, START, StateGraph

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.action import build_action_subgraph
from customer_workflow_agent.graph.common import (
    TRANSFER_SUMMARY,
    build_requests,
    confirm_pause,
    enqueue,
    keyword_intent,
    transfer_request,
    wait_for_customer,
)
from customer_workflow_agent.graph.state import ChatState, agent_msg, last_agent_text
from customer_workflow_agent.graph.subgraphs.addresses import DEFAULT_SPEC, ORDER_SPEC
from customer_workflow_agent.graph.subgraphs.cancel_order import SPEC as CANCEL_SPEC
from customer_workflow_agent.graph.subgraphs.info import build_info_subgraph
from customer_workflow_agent.graph.subgraphs.modify_order_payment import SPEC as PAYMENT_SPEC
from customer_workflow_agent.graph.subgraphs.return_items import SPEC as RETURN_SPEC
from customer_workflow_agent.graph.subgraphs.swap_items import EXCHANGE_SPEC, MODIFY_SPEC
from customer_workflow_agent.graph.subgraphs.transfer import build_transfer_subgraph
from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.guard import safe_reply
from customer_workflow_agent.llm.schemas import AuthExtraction, Classification
from customer_workflow_agent.llm.structured import LLMUnavailable
from customer_workflow_agent.resolve.ids import email_in, literal_in, zip_in
from customer_workflow_agent.store import StoreError
from customer_workflow_agent.templates import messages as M

WRITE_SPECS = [
    CANCEL_SPEC,
    ORDER_SPEC,
    PAYMENT_SPEC,
    MODIFY_SPEC,
    RETURN_SPEC,
    EXCHANGE_SPEC,
    DEFAULT_SPEC,
]
REQUEST_NODES = [s.name for s in WRITE_SPECS] + ["info", "transfer"]


def build_parent_graph(deps: Deps) -> StateGraph:
    settings = deps.settings

    async def greet(state: ChatState) -> dict:
        return {
            "messages": [agent_msg(M.GREETING)],
            "phase": "auth",
            "auth": {"attempts": 0},
            "queue": [],
            "outcomes": [],
            "held_texts": [],
            "group_slots": {},
            "end_requested": False,
            "user_id": None,
        }

    async def auth_interpret(state: ChatState) -> dict:
        text = state.get("last_text", "")
        auth = dict(state.get("auth") or {"attempts": 0})
        held = list(state.get("held_texts") or [])
        email, zip_code = email_in(text), zip_in(text)
        wants_human = goodbye = has_request = False
        first = last = None
        try:
            ex = await deps.llm.extract(AuthExtraction, prompts.auth(text))
            email = email or literal_in(ex.email, text)
            zip_code = zip_code or literal_in(ex.zip, text)
            first, last = literal_in(ex.first_name, text), literal_in(ex.last_name, text)
            wants_human, goodbye, has_request = ex.wants_human, ex.goodbye, ex.has_request
        except LLMUnavailable:
            intent = keyword_intent(text)
            wants_human, goodbye = intent == "human", intent == "goodbye"
        if wants_human:
            return {"queue": [transfer_request(text)], "route": "dispatch"}
        if goodbye and not (email or first or zip_code):
            return {"route": "goodbye"}
        if has_request:
            held.append(text)
        for key, value in (
            ("email", email),
            ("first_name", first),
            ("last_name", last),
            ("zip", zip_code),
        ):
            if value:
                auth[key] = value
        update = {"auth": auth, "held_texts": held}
        if auth.get("email") or all(auth.get(k) for k in ("first_name", "last_name", "zip")):
            return {**update, "route": "auth_check"}
        partial = [
            label
            for key, label in (
                ("first_name", "first name"),
                ("last_name", "last name"),
                ("zip", "zip code"),
            )
            if not auth.get(key)
        ]
        if len(partial) < 3:
            return {
                **update,
                "messages": [agent_msg(M.auth_missing(partial))],
                "route": "wait_auth",
            }
        return {**update, "messages": [agent_msg(M.ASK_AUTH)], "route": "wait_auth"}

    async def auth_check(state: ChatState) -> dict:
        auth = dict(state["auth"])
        user_id = None
        for lookup in (
            lambda: deps.store.find_user_id_by_email(auth["email"]) if auth.get("email") else None,
            lambda: (
                deps.store.find_user_id_by_name_zip(
                    auth["first_name"], auth["last_name"], auth["zip"]
                )
                if all(auth.get(k) for k in ("first_name", "last_name", "zip"))
                else None
            ),
        ):
            try:
                user_id = lookup()
            except StoreError:
                user_id = None
            if user_id:
                break
        if user_id:
            user = deps.store.get_user(user_id)
            return {
                "user_id": user_id,
                "first_name": user.name.first_name,
                "auth": {"attempts": auth.get("attempts", 0)},
                "route": "post_auth",
            }
        attempts = auth.get("attempts", 0) + 1
        if attempts >= settings.auth_max_attempts:
            return {
                "auth": {"attempts": attempts},
                "messages": [agent_msg(M.AUTH_OFFER)],
                "route": "offer_human",
            }
        return {
            "auth": {"attempts": attempts},
            "messages": [agent_msg(M.auth_failed(attempts, settings.auth_max_attempts))],
            "route": "wait_auth",
        }

    async def offer_human(state: ChatState) -> dict:
        ok, update = confirm_pause(state, TRANSFER_SUMMARY, "transfer")
        if ok:
            return {
                **update,
                "queue": [
                    transfer_request(state.get("last_text", ""), "Customer could not be verified")
                ],
                "route": "dispatch",
            }
        return {
            **update,
            "auth": {"attempts": 0},
            "messages": [agent_msg(M.AUTH_RETRY)],
            "route": "wait_auth",
        }

    async def post_auth(state: ChatState) -> dict:
        msgs = [agent_msg(M.verified(state["first_name"]))]
        held = state.get("held_texts") or []
        if held:
            return {
                "messages": msgs,
                "phase": "main",
                "last_text": " ".join(held),
                "held_texts": [],
                "route": "classify",
            }
        return {
            "messages": [*msgs, agent_msg(M.HOW_CAN_I_HELP)],
            "phase": "main",
            "route": "wait_main",
        }

    async def classify(state: ChatState) -> dict:
        text = state.get("last_text", "")
        try:
            c = await deps.llm.extract(
                Classification, prompts.classification(text, last_agent_text(state))
            )
        except LLMUnavailable:
            intent = keyword_intent(text)
            if intent == "human":
                return {
                    "queue": enqueue(state.get("queue") or [], [transfer_request(text)]),
                    "route": "dispatch",
                }
            if intent == "goodbye":
                return {"route": "goodbye"}
            return {"messages": [agent_msg(M.LLM_TROUBLE)], "route": "wait_main"}
        requests = build_requests(c.requests, text, deps, state["user_id"])
        msgs = [agent_msg(M.OTHER_PERSON, "denial")] if c.about_other_person else []
        if requests:
            return {
                "messages": msgs,
                "queue": enqueue(state.get("queue") or [], requests),
                "end_requested": c.goodbye,
                "route": "dispatch",
            }
        if c.goodbye:
            return {"messages": msgs, "route": "goodbye"}
        if msgs:
            return {"messages": msgs, "route": "wait_main"}
        return {"route": "small_talk"}

    async def small_talk(state: ChatState) -> dict:
        text = None
        if settings.llm_reply_writer_enabled:
            try:
                text = safe_reply(
                    await deps.llm.write(prompts.small_talk(state.get("last_text", "")))
                )
            except LLMUnavailable:
                text = None
        return {"messages": [agent_msg(text or M.NOT_UNDERSTOOD)], "route": "wait_main"}

    async def dispatch(state: ChatState) -> dict:
        queue = list(state.get("queue") or [])
        if queue:
            current = queue.pop(0)
            return {"queue": queue, "current": current, "work": {}, "route": current["type"]}
        if state.get("end_requested"):
            return {"route": "goodbye"}
        return {"route": "anything_else"}

    async def after_request(state: ChatState) -> dict:
        if state.get("phase") == "ended":
            return {"route": "end"}
        return {"route": "dispatch"}

    async def anything_else(state: ChatState) -> dict:
        return {"messages": [agent_msg(M.ANYTHING_ELSE)], "route": "wait_main"}

    async def goodbye(state: ChatState) -> dict:
        return {
            "messages": [agent_msg(M.GOODBYE)],
            "phase": "ended",
            "end_reason": "goodbye",
            "queue": [],
            "current": None,
        }

    g = StateGraph(ChatState)
    for name, fn in [
        ("greet", greet),
        ("wait_auth", wait_for_customer),
        ("auth_interpret", auth_interpret),
        ("auth_check", auth_check),
        ("offer_human", offer_human),
        ("post_auth", post_auth),
        ("wait_main", wait_for_customer),
        ("classify", classify),
        ("small_talk", small_talk),
        ("dispatch", dispatch),
        ("after_request", after_request),
        ("anything_else", anything_else),
        ("goodbye", goodbye),
    ]:
        g.add_node(name, fn)
    for spec in WRITE_SPECS:
        g.add_node(spec.name, build_action_subgraph(spec, deps))
    g.add_node("info", build_info_subgraph(deps))
    g.add_node("transfer", build_transfer_subgraph(deps))

    def route(state: ChatState) -> str:
        return state["route"]

    g.add_edge(START, "greet")
    g.add_edge("greet", "wait_auth")
    g.add_edge("wait_auth", "auth_interpret")
    g.add_conditional_edges(
        "auth_interpret", route, ["auth_check", "wait_auth", "dispatch", "goodbye"]
    )
    g.add_conditional_edges("auth_check", route, ["post_auth", "wait_auth", "offer_human"])
    g.add_conditional_edges("offer_human", route, ["dispatch", "wait_auth"])
    g.add_conditional_edges("post_auth", route, ["classify", "wait_main"])
    g.add_edge("wait_main", "classify")
    g.add_conditional_edges("classify", route, ["dispatch", "goodbye", "wait_main", "small_talk"])
    g.add_edge("small_talk", "wait_main")
    g.add_conditional_edges("dispatch", route, [*REQUEST_NODES, "goodbye", "anything_else"])
    for name in REQUEST_NODES:
        g.add_edge(name, "after_request")
    g.add_conditional_edges("after_request", route, {"dispatch": "dispatch", "end": END})
    g.add_edge("anything_else", "wait_main")
    g.add_edge("goodbye", END)
    return g
