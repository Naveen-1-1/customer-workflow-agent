"""Shared pieces for the parent graph and subgraphs."""

import re
import uuid

from langgraph.types import interrupt

from customer_workflow_agent.contract import ConfirmReply, CustomerReply
from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.state import (
    ChatState,
    Request,
    customer_msg,
    event_msg,
    last_id,
    new_request,
    undelivered,
)
from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.guard import safe_reply
from customer_workflow_agent.llm.schemas import RequestItem
from customer_workflow_agent.llm.structured import LLMUnavailable
from customer_workflow_agent.policy.rules import REQUIRED_STATUS, eligible_orders
from customer_workflow_agent.resolve.ids import normalize_order_id, order_ids_in

ITEM_LEVEL = {"modify_order_items", "exchange_items", "return_items"}


# ---- pauses (each is the only thing its node does) -------------------------------------


MAX_SUGGESTIONS = 3


async def wait_for_customer(state: ChatState) -> dict:
    suggestions = (state.get("suggestions") or [])[:MAX_SUGGESTIONS]
    reply = interrupt(
        {"type": "await_customer", "messages": undelivered(state), "suggestions": suggestions}
    )
    text = CustomerReply.model_validate(reply).text.strip()
    return {
        "messages": [customer_msg(text)],
        "last_text": text,
        "last_delivered": last_id(state),
        "suggestions": [],  # each pause offers only what the question before it set
    }


def confirm_pause(state: ChatState, summary: dict, action: str) -> tuple[bool, dict]:
    reply = interrupt(
        {"type": "confirm", "messages": undelivered(state), "summary": summary, "action": action}
    )
    ok = ConfirmReply.model_validate(reply).confirmed
    return ok, {
        "messages": [event_msg("Confirmed" if ok else "Declined")],
        "last_delivered": last_id(state),
    }


TRANSFER_SUMMARY = {
    "title": "Transfer to a human agent?",
    "lines": ["A human agent will take over this chat."],
    "amount": None,
    "amount_label": None,
}


# ---- wording ---------------------------------------------------------------------------


async def phrase(deps: Deps, question: str, customer_text: str) -> str:
    """Let the LLM phrase a question (no facts allowed); fall back to the template."""
    if not deps.settings.llm_reply_writer_enabled:
        return question
    try:
        text = await deps.llm.write(prompts.ask(question, customer_text))
    except LLMUnavailable:
        return question
    return safe_reply(text) or question


# ---- keywords: the fallback when the LLM is down, and the check on what it labels ------

_HUMAN = re.compile(r"\b(human|agent|representative|person|someone real)\b", re.I)
_BYE = re.compile(
    r"\b(bye|goodbye|that'?s (all|it|everything)|no thanks|nothing else|i'?m done)\b", re.I
)
_SKIP = re.compile(r"\b(skip|never ?mind|forget (it|about it|that)|drop (it|this|that))\b", re.I)


def says(intent: str, text: str) -> bool:
    """Did the customer actually say this? (LLM labels for skip/goodbye/human must agree.)"""
    pattern = {"human": _HUMAN, "skip": _SKIP, "goodbye": _BYE}[intent]
    return bool(pattern.search(text))


def keyword_intent(text: str) -> str | None:
    if _HUMAN.search(text):
        return "human"
    if _SKIP.search(text):
        return "skip"
    if _BYE.search(text):
        return "goodbye"
    return None


# ---- turning classified requests into the queue ----------------------------------------


def transfer_request(
    source_text: str, details: str = "Customer asked for a human agent"
) -> Request:
    return new_request("transfer", details, source_text)


def build_requests(
    items: list[RequestItem], text: str, deps: Deps, user_id: str | None
) -> list[Request]:
    """Code post-processing of the classifier's output."""
    text_ids = order_ids_in(text)
    out: list[Request] = []
    for it in items:
        oid = normalize_order_id(it.order_id, text)
        if it.all_eligible_orders and it.type in REQUIRED_STATUS and user_id:
            group = uuid.uuid4().hex
            orders = eligible_orders(it.type, deps.store.get_user_orders(user_id))
            if orders:
                out.extend(
                    new_request(it.type, it.details, text, o.order_id, group) for o in orders
                )
                continue
        out.append(new_request(it.type, it.details, text, oid))

    # One order id in the text and one order-level request without an id: they go together.
    order_level = [r for r in out if r["type"] in REQUIRED_STATUS]
    if len(text_ids) == 1 and len(order_level) == 1 and order_level[0]["order_id"] is None:
        order_level[0]["order_id"] = text_ids[0]

    # Item-level changes to the same order must happen in one call: merge them.
    merged: list[Request] = []
    for r in out:
        twin = next(
            (
                m
                for m in merged
                if r["type"] in ITEM_LEVEL
                and m["type"] == r["type"]
                and r["order_id"]
                and m["order_id"] == r["order_id"]
            ),
            None,
        )
        if twin:
            twin["details"] += "; " + r["details"]
        else:
            merged.append(r)

    # Transfers go last (they end the chat), at most one.
    transfers = [r for r in merged if r["type"] == "transfer"]
    rest = [r for r in merged if r["type"] != "transfer"]
    final = rest[: deps.settings.max_queue] + transfers[:1]
    return final


def enqueue(queue: list[Request], new: list[Request]) -> list[Request]:
    """Add requests, keeping a single transfer at the very end."""
    transfers = [r for r in [*queue, *new] if r["type"] == "transfer"]
    others = [r for r in [*queue, *new] if r["type"] != "transfer"]
    return others + transfers[:1]
