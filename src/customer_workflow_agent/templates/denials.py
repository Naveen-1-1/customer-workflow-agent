"""Why a request can't be done, in customer wording. Codes come from policy rules."""

from typing import Literal

DenialCode = Literal[
    "order_not_found",
    "status_processed",
    "status_items_modified",
    "status_cancelled",
    "status_return_requested",
    "status_exchange_requested",
    "status_delivered_not_pending",
    "status_pending_not_delivered",
    "no_eligible_orders",
    "cancel_reason_not_allowed",
    "payment_history_not_single",
    "returns_blocked",
    "product_change_not_allowed",
    "store_rejected",
]

_TEXT: dict[str, str] = {
    "order_not_found": "I couldn't find order {order_id} on your account.",
    "status_processed": (
        "Order {order_id} is already being processed, so it can't be changed, cancelled, "
        "returned or exchanged."
    ),
    "status_items_modified": (
        "The items in order {order_id} have already been changed once, so the order can't be "
        "modified or cancelled any further."
    ),
    "status_cancelled": (
        "Order {order_id} has been cancelled, so there's nothing more I can change."
    ),
    "status_return_requested": (
        "A return has already been requested for order {order_id}, so it can't be changed further."
    ),
    "status_exchange_requested": (
        "An exchange has already been requested for order {order_id}, so it can't be changed "
        "further."
    ),
    "status_delivered_not_pending": (
        "Order {order_id} has already been delivered, so it can't be cancelled or modified. "
        "I can help you return or exchange items from it instead."
    ),
    "status_pending_not_delivered": (
        "Order {order_id} hasn't been delivered yet, so it can't be returned or exchanged. "
        "I can help you cancel or modify it instead."
    ),
    "no_eligible_orders": "You don't have any orders that can be {action_verb} right now.",
    "cancel_reason_not_allowed": (
        "I'm sorry, I can only cancel an order if it's no longer needed or was ordered by mistake."
    ),
    "payment_history_not_single": (
        "The payment on order {order_id} can't be changed because it has more than one payment "
        "on record."
    ),
    "returns_blocked": (
        "Returns on order {order_id} can't be requested because an earlier return on it "
        "wasn't approved."
    ),
    "product_change_not_allowed": (
        "I can only swap an item for a different option of the same product, not for a "
        "different product."
    ),
    "store_rejected": "I'm sorry, that change couldn't be completed: {reason}",
}


def denial_text(code: str, **params: object) -> str:
    return _TEXT[code].format(**params)
