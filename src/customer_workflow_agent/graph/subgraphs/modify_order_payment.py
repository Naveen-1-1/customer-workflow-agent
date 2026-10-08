"""Change a pending order's payment method (policy: one payment on record; a single different
method; a gift card must cover the total; the original is refunded)."""

from customer_workflow_agent.graph.action import Check, Ctx, Found, Ready
from customer_workflow_agent.graph.subgraphs.base import (
    merge_order_id,
    order_steps,
    pick_payment,
    summary,
    the_order,
)
from customer_workflow_agent.llm.schemas import PaymentTurn
from customer_workflow_agent.policy.rules import check_single_payment, gift_card_covers
from customer_workflow_agent.resolve.payments import has_reference, payment_candidates
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates.messages import refund_line

QUESTION = "Which payment method would you like to use instead?"


def _others(found: dict, ctx: Ctx) -> list[str]:
    original = the_order(found, ctx).payment_history[0].payment_method_id
    return [p for p in ctx.user.payment_methods if p != original]


def check_one_payment(slots: dict, found: dict, ctx: Ctx) -> Check:
    """The order must have exactly one payment on record (policy)."""
    return check_single_payment(the_order(found, ctx)) or Found()


def choose_payment_method(slots: dict, found: dict, ctx: Ctx) -> Check:
    """A single payment method, different from the one the order was paid with."""
    order = the_order(found, ctx)
    original = order.payment_history[0].payment_method_id
    ref = slots.get("payment")
    if slots.get("payment_method_id") == original or (
        has_reference(ref) and payment_candidates(ref, ctx.user) == [original]
    ):
        _, ask = pick_payment(
            {},
            ctx.user,
            allowed=_others(found, ctx),
            question=QUESTION,
            preface=f"Order {order.order_id} is already paid with your "
            f"{F.pm_text(ctx.user.payment_methods[original])}.",
        )
        assert ask
        return ask
    pm_id, ask = pick_payment(slots, ctx.user, allowed=_others(found, ctx), question=QUESTION)
    return ask or Found({"pm_id": pm_id})


def check_gift_card_balance(slots: dict, found: dict, ctx: Ctx) -> Check:
    """A gift card must cover the whole order total (policy)."""
    amount = the_order(found, ctx).payment_history[0].amount
    if gift_card_covers(ctx.user.payment_methods[found["pm_id"]], amount):
        return Found()
    _, ask = pick_payment(
        {},
        ctx.user,
        allowed=_others(found, ctx),
        preface=f"That gift card's balance doesn't cover the order total of {F.usd(amount)}.",
        question=QUESTION,
    )
    assert ask
    return ask


class ModifyOrderPayment:
    name = "modify_order_payment"
    turn_schema = PaymentTurn
    reminder = None
    needs_approval_step = False
    steps = (
        *order_steps("modify_order_payment"),
        ("check_single_payment", check_one_payment),
        ("choose_payment_method", choose_payment_method),
        ("check_gift_card_balance", check_gift_card_balance),
    )

    def merge(self, slots, turn, text, ctx):
        slots = merge_order_id(slots, turn, text)
        if turn.payment:
            slots = {**slots, "payment": turn.payment.model_dump(), "payment_method_id": None}
        return slots

    def catalog(self, slots, ctx):
        return None

    def prepare(self, slots, found, ctx: Ctx) -> Ready:
        order = the_order(found, ctx)
        original = order.payment_history[0]
        pms = ctx.user.payment_methods
        pm_id = found["pm_id"]
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
