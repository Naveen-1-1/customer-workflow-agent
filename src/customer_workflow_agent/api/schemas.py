"""JSON shapes of the web API (mirrored in frontend/src/api/types.ts)."""

from typing import Literal

from pydantic import BaseModel, Field

from customer_workflow_agent.contract import Suggestion


class MessageOut(BaseModel):
    id: str
    role: Literal["agent", "customer", "event"]
    text: str
    kind: str


class Pending(BaseModel):
    type: Literal["await_customer", "confirm", "supervisor_approval"]
    interrupt_id: str
    action: str | None = None
    summary: dict | None = None  # confirm: {title, lines, amount, amount_label}
    refund_total: float | None = None  # supervisor_approval
    suggestions: list[Suggestion] = []  # await_customer: at most 3 reply buttons


class ChatView(BaseModel):
    chat_id: str
    created_at: str
    messages: list[MessageOut]
    pending: Pending | None
    running: bool
    ended: bool
    end_reason: Literal["goodbye", "transferred"] | None
    error: str | None  # the last run failed; POST /retry continues it


class ApprovalView(BaseModel):
    chat_id: str
    interrupt_id: str
    status: Literal["pending", "processing"]
    requested_at: str
    request: dict  # ApprovalRequest: order_id, user_id, items, refund_total, payment_method…


class ApprovalsView(BaseModel):
    approvals: list[ApprovalView]


class SendMessage(BaseModel):
    interrupt_id: str
    text: str = Field(min_length=1, max_length=2000)


class ConfirmBody(BaseModel):
    interrupt_id: str
    confirmed: bool


class DecisionBody(BaseModel):
    interrupt_id: str
    approved: bool
    note: str | None = Field(default=None, max_length=500)


class Meta(BaseModel):
    approval_enabled: bool
    approval_threshold: float
    llm_configured: bool
    primary_model: str
    fallback_model: str


class ResetResult(BaseModel):
    chats_deleted: int


class ErrorBody(BaseModel):
    code: str
    message: str
