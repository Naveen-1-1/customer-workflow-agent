"""Turn graph state into what the browser shows. Pure functions (easy to test)."""

from customer_workflow_agent.api.schemas import ApprovalView, ChatView, MessageOut, Pending


def pending_from(interrupts: list) -> Pending | None:
    if not interrupts:
        return None
    intr = interrupts[0]
    value = intr.value
    kind = value["type"]
    if kind == "confirm":
        return Pending(
            type=kind,
            interrupt_id=intr.id,
            action=value.get("action"),
            summary=value.get("summary"),
        )
    if kind == "supervisor_approval":
        return Pending(
            type=kind, interrupt_id=intr.id, refund_total=value["request"]["refund_total"]
        )
    return Pending(type=kind, interrupt_id=intr.id)


def chat_view(
    chat_id: str,
    created_at: str,
    values: dict,
    interrupts: list,
    *,
    running: bool,
    error: str | None,
) -> ChatView:
    pending = None if running else pending_from(interrupts)
    ended = values.get("phase") == "ended" and not interrupts
    return ChatView(
        chat_id=chat_id,
        created_at=created_at,
        messages=[MessageOut(**m) for m in values.get("messages") or []],
        pending=pending,
        running=running,
        ended=ended,
        end_reason=values.get("end_reason") if ended else None,
        error=None if running else error,
    )


def approval_view(chat_id: str, interrupts: list, requested_at: str) -> ApprovalView | None:
    for intr in interrupts:
        if intr.value.get("type") == "supervisor_approval":
            return ApprovalView(
                chat_id=chat_id,
                interrupt_id=intr.id,
                status="pending",
                requested_at=requested_at,
                request=intr.value["request"],
            )
    return None
