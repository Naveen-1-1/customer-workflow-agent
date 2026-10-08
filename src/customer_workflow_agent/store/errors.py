"""Errors raised by the store when a read or write is not allowed."""

from typing import Any, Literal

StoreErrorCode = Literal[
    "user_not_found",
    "order_not_found",
    "product_not_found",
    "item_not_found",
    "variant_not_found",
    "payment_method_not_found",
    "invalid_status",
    "invalid_reason",
    "item_not_in_order",
    "item_count_mismatch",
    "empty_items",
    "same_item",
    "variant_unavailable",
    "insufficient_gift_card_balance",
    "refund_method_not_allowed",
    "payment_history_not_single",
    "same_payment_method",
    "returns_blocked",
]


class StoreError(Exception):
    """A store operation was rejected. Nothing was changed."""

    def __init__(self, code: StoreErrorCode, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def __repr__(self) -> str:
        return f"StoreError({self.code!r}, {self.message!r})"
