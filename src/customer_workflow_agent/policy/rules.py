"""The retail policy as plain checks. Each returns a Denial, or None when allowed.

Source: data/policy.md (τ²-bench retail). These run before every confirmation popup; the
store re-checks status and balances again at write time.
"""

from dataclasses import dataclass, field

from customer_workflow_agent.settings import Settings
from customer_workflow_agent.store.models import GiftCard, Order, PaymentMethod, User

ActionName = str

# Status each action needs (policy: pending → cancel/modify; delivered → return/exchange).
REQUIRED_STATUS: dict[ActionName, str] = {
    "cancel_order": "pending",
    "modify_order_address": "pending",
    "modify_order_payment": "pending",
    "modify_order_items": "pending",
    "return_items": "delivered",
    "exchange_items": "delivered",
}

ACTION_VERB: dict[ActionName, str] = {
    "cancel_order": "cancelled",
    "modify_order_address": "changed",
    "modify_order_payment": "changed",
    "modify_order_items": "changed",
    "return_items": "returned",
    "exchange_items": "exchanged",
}


@dataclass(frozen=True)
class Denial:
    code: str
    params: dict = field(default_factory=dict)


def check_owner(order: Order | None, user_id: str, order_id: str) -> Denial | None:
    """One customer per chat: other users' orders look exactly like missing ones."""
    if order is None or order.user_id != user_id:
        return Denial("order_not_found", {"order_id": order_id})
    return None


def check_status(action: ActionName, order: Order) -> Denial | None:
    required = REQUIRED_STATUS[action]
    if order.status == required:
        return None
    by_status = {
        "processed": "status_processed",
        "pending (item modified)": "status_items_modified",
        "cancelled": "status_cancelled",
        "return requested": "status_return_requested",
        "exchange requested": "status_exchange_requested",
        "delivered": "status_delivered_not_pending",
        "pending": "status_pending_not_delivered",
    }
    return Denial(by_status[order.status], {"order_id": order.order_id})


def eligible_orders(action: ActionName, orders: list[Order]) -> list[Order]:
    return [o for o in orders if o.status == REQUIRED_STATUS[action]]


def check_cancel_reason(reason: str) -> Denial | None:
    if reason in ("no longer needed", "ordered by mistake"):
        return None
    return Denial("cancel_reason_not_allowed")


def check_single_payment(order: Order) -> Denial | None:
    history = order.payment_history
    if len(history) == 1 and history[0].transaction_type == "payment":
        return None
    return Denial("payment_history_not_single", {"order_id": order.order_id})


def gift_card_covers(pm: PaymentMethod, amount: float) -> bool:
    return not isinstance(pm, GiftCard) or pm.balance >= amount


def refund_destinations(order: Order, user: User) -> list[str]:
    """Return refunds go to the original payment method or any of the user's gift cards."""
    original = order.payment_history[0].payment_method_id
    gift_cards = [pid for pid, pm in user.payment_methods.items() if isinstance(pm, GiftCard)]
    return [original, *[g for g in gift_cards if g != original]]


def needs_approval(settings: Settings, refund_total: float) -> bool:
    """Our addition (not in τ²-bench): large returns need a supervisor when enabled."""
    return settings.approval_enabled and refund_total > settings.approval_threshold
