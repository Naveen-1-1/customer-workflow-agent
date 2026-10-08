"""Change a pending order's payment method (policy: one payment on record; a single different
method; a gift card must cover the total; the original is refunded)."""

from customer_workflow_agent.graph.action import Ctx, Plan, Ready
from customer_workflow_agent.graph.subgraphs.base import (
    merge_order_id,
    pick_payment,
    resolve_order,
    summary,
)
from customer_workflow_agent.llm.schemas import PaymentTurn
from customer_workflow_agent.policy.rules import check_single_payment, gift_card_covers
from customer_workflow_agent.resolve.payments import has_reference, payment_candidates
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates.messages import refund_line


class ModifyOrderPayment:
    name = "modify_order_payment"
    turn_schema = PaymentTurn
    reminder = None

    def merge(self, slots, turn, text, ctx):
        slots = merge_order_id(slots, turn, text)
        if turn.payment:
            slots = {**slots, "payment": turn.payment.model_dump(), "payment_method_id": None}
        return slots

    def catalog(self, slots, ctx):
        return None

    def plan(self, slots, ctx: Ctx) -> Plan:
        order, other = resolve_order(self.name, slots, ctx)
        if other:
            return other
        assert order is not None
        if denial := check_single_payment(order):
            return denial
        original = order.payment_history[0]
        pms = ctx.user.payment_methods
        others = [p for p in pms if p != original.payment_method_id]
        question = "Which payment method would you like to use instead?"
        ref = slots.get("payment")
        if slots.get("payment_method_id") == original.payment_method_id or (
            has_reference(ref) and payment_candidates(ref, ctx.user) == [original.payment_method_id]
        ):
            _, ask = pick_payment(
                {},
                ctx.user,
                allowed=others,
                question=question,
                preface=f"Order {order.order_id} is already paid with your "
                f"{F.pm_text(pms[original.payment_method_id])}.",
            )
            assert ask
            return ask
        pm_id, ask = pick_payment(slots, ctx.user, allowed=others, question=question)
        if ask:
            return ask
        assert pm_id is not None
        if not gift_card_covers(pms[pm_id], original.amount):
            _, ask = pick_payment(
                {},
                ctx.user,
                allowed=others,
                preface=f"That gift card's balance doesn't cover the order total of "
                f"{F.usd(original.amount)}.",
                question="Which payment method would you like to use instead?",
            )
            assert ask
            return ask
        old = pms[original.payment_method_id]
        return Ready(
            summary=summary(
                f"Change payment method for order {order.order_id}",
                [
                    f"Order total: {F.usd(original.amount)}",
                    f"New payment method: {F.pm_text(pms[pm_id])}",
                    refund_line(original.amount, F.pm_text(old), F.refund_timing(old)),
                ],
                original.amount,
                "Order total",
            ),
            call={
                "fn": "modify_pending_order_payment",
                "args": {"order_id": order.order_id, "payment_method_id": pm_id},
            },
            result={"old": original.payment_method_id, "new": pm_id, "amount": original.amount},
        )

    def result_text(self, returned, ready, ctx):
        pms = ctx.store.get_user(ctx.user.user_id).payment_methods
        r = ready.result
        old = pms[r["old"]]
        return (
            f"Done — order {returned.order_id} is now paid with your {F.pm_text(pms[r['new']])}. "
            f"The original payment of {F.usd(r['amount'])} will be refunded to your "
            f"{F.pm_text(old)}, {F.refund_timing(old)}."
        )


SPEC = ModifyOrderPayment()
