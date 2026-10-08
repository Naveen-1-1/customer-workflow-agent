"""The contract between the graph and the API: what the graph pauses for, and how to resume.

The graph only waits on the outside world through `interrupt(payload)`, one per node:
- {"type": "await_customer", "messages": [...], "suggestions": [...]}
                                                          resume: {"text": str}
- {"type": "confirm", "messages": [...], "summary": {...}, "action": str}
                                                          resume: {"confirmed": bool}
- {"type": "supervisor_approval", "messages": [...], "request": {...}}
                                              resume: {"approved": bool, "note": str | None}
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PendingType = Literal["await_customer", "confirm", "supervisor_approval"]


class CustomerReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)


class Suggestion(BaseModel):
    """A reply the customer can click instead of typing; `text` is sent as their message."""

    label: str
    text: str


class ConfirmReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool


class ApprovalReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    note: str | None = Field(default=None, max_length=500)


RESUME_MODELS: dict[str, type[BaseModel]] = {
    "await_customer": CustomerReply,
    "confirm": ConfirmReply,
    "supervisor_approval": ApprovalReply,
}


class ConfirmSummary(BaseModel):
    title: str
    lines: list[str]
    amount: float | None = None
    amount_label: str | None = None  # "Refund", "Charge", "Order total"


class ApprovalItem(BaseModel):
    name: str
    options: dict[str, str]
    price: float


class ApprovalRequest(BaseModel):
    order_id: str
    user_id: str
    items: list[ApprovalItem]
    refund_total: float
    payment_method_id: str
    payment_method: str
