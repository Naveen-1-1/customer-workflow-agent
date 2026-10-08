"""Cancel a pending order (policy: status pending; reason 'no longer needed' or 'ordered by
mistake'; refund to the original payment methods, gift cards immediately)."""

import re
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
from customer_workflow_agent.resolve.ids import literal_in
from customer_workflow_agent.store.models import Order
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates.messages import refund_line
from customer_workflow_agent.templates.suggestions import CANCEL_REASONS


def _refunds(order: Order) -> dict[str, float]:
    net: dict[str, float] = defaultdict(float)
    for p in order.payment_history:
        net[p.payment_method_id] += p.amount if p.transaction_type == "payment" else -p.amount
    return {pm: round(a, 2) for pm, a in net.items() if round(a, 2) > 0}


# A reason counts only if the customer's own words for it are in their message and actually say
# it: models sometimes fill one in, or "quote" the whole request as the reason (ISSUES.md #1).
_SAYS_REASON = {
    "no longer needed": re.compile(
        r"no longer|need(?!s? to\b)|any ?more|changed? (?:my|our) mind|(?:don'?t|do not|not|never) "
        r"(?:want|use)|no use for",
        re.I,
    ),
    "ordered by mistake": re.compile(
        r"mistake|accident|wrong|error|didn'?t mean|unintentional|by chance", re.I
    ),
}
_REQUEST_ECHO = re.compile(r"cancel|#?W\d{7}", re.I)


def gave_reason(reason: str | None, quote: str | None, text: str) -> bool:
    """Did the customer give this reason in `text`?"""
    quote = literal_in(quote, text)
    if not reason or quote is None:
        return False
    if reason in _SAYS_REASON:
        return bool(_SAYS_REASON[reason].search(quote))
    return not _REQUEST_ECHO.search(quote)  # "other": anything but the request itself


def check_reason(slots: dict, found: dict, ctx: Ctx) -> Check:
    """Only 'no longer needed' or 'ordered by mistake' (policy)."""
    reason = slots.get("reason")
    if not reason:
        return Ask(
            question="Could you tell me why you'd like to cancel — is it no longer needed, "
            "or was it ordered by mistake?",
            slot="reason",
            suggestions=CANCEL_REASONS,
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
        if gave_reason(turn.reason, turn.reason_quote, text):  # else check_reason asks
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
