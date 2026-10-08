"""Cancel a pending order (policy: status pending; reason 'no longer needed' or 'ordered by
mistake'; refund to the original payment methods, gift cards immediately)."""

from collections import defaultdict

from customer_workflow_agent.graph.action import Ask, Check, Ctx, Found, Ready
from customer_workflow_agent.graph.subgraphs.base import (
    merge_order_id,
    order_steps,
    summary,
    the_order,
)
from customer_workflow_agent.llm.schemas import CancelTurn
from customer_workflow_agent.policy.rules import check_cancel_reason
from customer_workflow_agent.store.models import Order
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates.messages import refund_line


def _refunds(order: Order) -> dict[str, float]:
    net: dict[str, float] = defaultdict(float)
    for p in order.payment_history:
        net[p.payment_method_id] += p.amount if p.transaction_type == "payment" else -p.amount
    return {pm: round(a, 2) for pm, a in net.items() if round(a, 2) > 0}


def check_reason(slots: dict, found: dict, ctx: Ctx) -> Check:
    """Only 'no longer needed' or 'ordered by mistake' (policy)."""
    reason = slots.get("reason")
    if not reason:
        return Ask(
            question="Could you tell me why you'd like to cancel — is it no longer needed, "
            "or was it ordered by mistake?",
            slot="reason",
        )
    return check_cancel_reason(reason) or Found()


class CancelOrder:
    name = "cancel_order"
    turn_schema = CancelTurn
    reminder = None
    needs_approval_step = False
    steps = (
        *order_steps("cancel_order"),
        ("check_reason", check_reason),
    )

    def merge(self, slots, turn, text, ctx):
        slots = merge_order_id(slots, turn, text)
        if turn.reason:
            slots = {**slots, "reason": turn.reason}
        return slots

    def catalog(self, slots, ctx):
        return None

    def prepare(self, slots, found, ctx: Ctx) -> Ready:
        order = the_order(found, ctx)
        reason = slots["reason"]
        refunds = _refunds(order)
        pms = ctx.user.payment_methods
        lines = [F.item_text(i) for i in order.items]
        lines.append(f"Reason: {reason}")
        lines += [
            refund_line(a, F.pm_text(pms[pm]), F.refund_timing(pms[pm]))
            for pm, a in refunds.items()
        ]
        return Ready(
            summary=summary(
                f"Cancel order {order.order_id}", lines, round(sum(refunds.values()), 2), "Refund"
            ),
            call={
                "fn": "cancel_pending_order",
                "args": {"order_id": order.order_id, "reason": reason},
            },
            result={"refunds": refunds},
        )

    def result_text(self, returned, ready, ctx):
        pms = ctx.store.get_user(ctx.user.user_id).payment_methods
        refunds = ready.result["refunds"]
        text = f"Done — order {returned.order_id} is cancelled."
        for pm, amount in refunds.items():
            text += (
                f" {F.usd(amount)} will be refunded to your {F.pm_text(pms[pm])}, "
                f"{F.refund_timing(pms[pm])}."
            )
        return text


SPEC = CancelOrder()
