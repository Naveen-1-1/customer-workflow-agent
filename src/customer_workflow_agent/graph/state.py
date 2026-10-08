"""Graph state. Everything is plain JSON (dicts, lists, strings) so checkpoints stay portable."""

import uuid
from typing import Annotated, Literal, TypedDict


class Msg(TypedDict):
    id: str
    role: Literal["agent", "customer", "event"]
    text: str
    kind: str


def merge_messages(left: list[Msg] | None, right: list[Msg] | None) -> list[Msg]:
    """Append, skipping ids already present (subgraph output re-sends parent messages)."""
    left, right = left or [], right or []
    seen = {m["id"] for m in left}
    return left + [m for m in right if m["id"] not in seen]


def _msg(role: str, text: str, kind: str) -> Msg:
    return {"id": uuid.uuid4().hex, "role": role, "text": text, "kind": kind}  # type: ignore[typeddict-item]


def agent_msg(text: str, kind: str = "text") -> Msg:
    return _msg("agent", text, kind)


def customer_msg(text: str) -> Msg:
    return _msg("customer", text, "text")


def event_msg(text: str) -> Msg:
    return _msg("event", text, "event")


class Request(TypedDict):
    id: str
    type: str
    order_id: str | None
    details: str
    source_text: str
    group_id: str | None


class Outcome(TypedDict):
    request_id: str
    type: str
    order_id: str | None
    status: str  # done | denied | declined | dropped | rejected | failed | answered | transferred
    code: str | None


class ChatState(TypedDict, total=False):
    messages: Annotated[list[Msg], merge_messages]
    last_delivered: str | None  # id of the last message shown at the previous pause
    last_text: str  # the customer's latest message
    phase: Literal["auth", "main", "ended"]
    end_reason: Literal["goodbye", "transferred"] | None
    user_id: str | None
    first_name: str | None
    auth: dict  # partial credentials and attempt count
    held_texts: list[str]  # requests made before verification
    queue: list[Request]
    current: Request | None
    work: dict  # scratch space for the current request
    outcomes: list[Outcome]
    group_slots: dict[str, dict]  # details shared by "all my orders" sibling requests
    end_requested: bool
    suggestions: list[dict]  # Suggestion dicts for the next customer pause
    route: str


def new_request(
    type: str,
    details: str,
    source_text: str,
    order_id: str | None = None,
    group_id: str | None = None,
) -> Request:
    return {
        "id": uuid.uuid4().hex,
        "type": type,
        "order_id": order_id,
        "details": details,
        "source_text": source_text,
        "group_id": group_id,
    }


def undelivered(state: ChatState) -> list[Msg]:
    """Agent/event messages added since the last pause."""
    msgs = state.get("messages") or []
    last = state.get("last_delivered")
    start = 0
    if last:
        for i, m in enumerate(msgs):
            if m["id"] == last:
                start = i + 1
    return [m for m in msgs[start:] if m["role"] != "customer"]


def last_id(state: ChatState) -> str | None:
    msgs = state.get("messages") or []
    return msgs[-1]["id"] if msgs else None


def last_agent_text(state: ChatState) -> str | None:
    for m in reversed(state.get("messages") or []):
        if m["role"] == "agent":
            return m["text"]
    return None
