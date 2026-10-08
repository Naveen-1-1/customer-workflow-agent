"""The retail store: reads and the operations that change records.

Adapted from τ²-bench `src/tau2/domains/retail/tools.py` (MIT, Copyright (c) 2025 Sierra
Research). Differences from the original:
- No τ²-bench toolkit framework; plain methods that raise `StoreError` with a code.
- Fixed: `modify_pending_order_items` gave every changed item the last new variant's price
  and options; each item now gets its own variant.
- Fixed: `cancel_pending_order` refunded every payment-history entry (including refunds);
  it now refunds the net amount paid per payment method.
- Tightened to match the policy: address/payment changes need status exactly "pending";
  exchanges need a different item; item lists must be non-empty.
- Added: an op-id ledger (a repeated write is not applied twice), a transfer log,
  and orders blocked from further returns.
- `calculate` is not ported (code does the math).

Writes validate everything before changing anything. They run under one lock, mutate the
in-memory copy, and save the working copy; if anything raises, the last saved copy is reloaded.
"""

import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager

from customer_workflow_agent.store.backend import StorageBackend
from customer_workflow_agent.store.errors import StoreError
from customer_workflow_agent.store.models import (
    CANCEL_REASONS,
    GiftCard,
    OpRecord,
    Order,
    OrderPayment,
    PaymentMethod,
    Product,
    TransferRecord,
    User,
    UserAddress,
    Variant,
    WorkingDB,
)


def money(x: float) -> float:
    return round(x + 0.0, 2)


class RetailStore:
    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend
        self._lock = threading.RLock()
        self._db = backend.load()

    # ---- internals -------------------------------------------------------------------

    @contextmanager
    def _write(self, op_id: str, op: str, target_id: str) -> Iterator[tuple[WorkingDB, bool]]:
        """Yield (db, already_done). Saves on success; reloads the saved copy on error."""
        with self._lock:
            done = op_id in self._db.ledger
            try:
                yield self._db, done
                if not done:
                    self._db.ledger[op_id] = OpRecord(op=op, target_id=target_id)
                    self._backend.save(self._db)
            except BaseException:
                self._db = self._backend.load()
                raise

    def _order(self, db: WorkingDB, order_id: str) -> Order:
        order = db.orders.get(order_id)
        if order is None:
            raise StoreError("order_not_found", "Order not found", order_id=order_id)
        return order

    def _user(self, db: WorkingDB, user_id: str) -> User:
        user = db.users.get(user_id)
        if user is None:
            raise StoreError("user_not_found", "User not found", user_id=user_id)
        return user

    def _product(self, db: WorkingDB, product_id: str) -> Product:
        product = db.products.get(product_id)
        if product is None:
            raise StoreError("product_not_found", "Product not found", product_id=product_id)
        return product

    def _variant(self, db: WorkingDB, product_id: str, item_id: str) -> Variant:
        variant = self._product(db, product_id).variants.get(item_id)
        if variant is None:
            raise StoreError("variant_not_found", "Variant not found", item_id=item_id)
        return variant

    def _payment_method(self, db: WorkingDB, user_id: str, pm_id: str) -> PaymentMethod:
        pm = self._user(db, user_id).payment_methods.get(pm_id)
        if pm is None:
            raise StoreError("payment_method_not_found", "Payment method not found", pm_id=pm_id)
        return pm

    @staticmethod
    def _require_status(order: Order, status: str, action: str) -> None:
        if order.status != status:
            raise StoreError(
                "invalid_status",
                f"Order with status '{order.status}' cannot be {action}",
                status=order.status,
            )

    @staticmethod
    def _match_lines(order: Order, item_ids: list[str]) -> list[int]:
        """Map each requested item id to a distinct order line (ids can repeat)."""
        if not item_ids:
            raise StoreError("empty_items", "No items given")
        used: set[int] = set()
        lines: list[int] = []
        for item_id in item_ids:
            idx = next(
                (
                    i
                    for i, line in enumerate(order.items)
                    if line.item_id == item_id and i not in used
                ),
                None,
            )
            if idx is None:
                raise StoreError(
                    "item_not_in_order", f"Item {item_id} not found in order", item_id=item_id
                )
            used.add(idx)
            lines.append(idx)
        return lines

    def _resolve_swaps(
        self, db: WorkingDB, order: Order, item_ids: list[str], new_item_ids: list[str]
    ) -> tuple[list[tuple[int, Variant]], float]:
        if len(item_ids) != len(new_item_ids):
            raise StoreError("item_count_mismatch", "The number of old and new items must match")
        lines = self._match_lines(order, item_ids)
        pairs: list[tuple[int, Variant]] = []
        diff = 0.0
        for idx, new_item_id in zip(lines, new_item_ids, strict=True):
            line = order.items[idx]
            if new_item_id == line.item_id:
                raise StoreError("same_item", "The new item must differ from the old item")
            variant = self._variant(db, line.product_id, new_item_id)
            if not variant.available:
                raise StoreError(
                    "variant_unavailable",
                    f"Item {new_item_id} is not available",
                    item_id=new_item_id,
                )
            pairs.append((idx, variant))
            diff += variant.price - line.price
        return pairs, money(diff)

    # ---- reads (return copies) ---------------------------------------------------------

    def find_user_id_by_email(self, email: str) -> str:
        email = email.strip().lower()
        with self._lock:
            for user_id, user in self._db.users.items():
                if user.email.lower() == email:
                    return user_id
        raise StoreError("user_not_found", "User not found")

    def find_user_id_by_name_zip(self, first_name: str, last_name: str, zip: str) -> str:
        first, last, zip = first_name.strip().lower(), last_name.strip().lower(), zip.strip()
        with self._lock:
            for user_id, user in self._db.users.items():
                if (
                    user.name.first_name.lower() == first
                    and user.name.last_name.lower() == last
                    and user.address.zip == zip
                ):
                    return user_id
        raise StoreError("user_not_found", "User not found")

    def get_user(self, user_id: str) -> User:
        with self._lock:
            return self._user(self._db, user_id).model_copy(deep=True)

    def get_order(self, order_id: str) -> Order:
        with self._lock:
            return self._order(self._db, order_id).model_copy(deep=True)

    def get_user_orders(self, user_id: str) -> list[Order]:
        with self._lock:
            user = self._user(self._db, user_id)
            return [
                self._db.orders[oid].model_copy(deep=True)
                for oid in user.orders
                if oid in self._db.orders
            ]

    def get_product(self, product_id: str) -> Product:
        with self._lock:
            return self._product(self._db, product_id).model_copy(deep=True)

    def get_item(self, item_id: str) -> tuple[str, Variant]:
        """Return (product_id, variant) for an item id."""
        with self._lock:
            for product in self._db.products.values():
                if item_id in product.variants:
                    return product.product_id, product.variants[item_id].model_copy(deep=True)
        raise StoreError("item_not_found", "Item not found", item_id=item_id)

    def list_all_product_types(self) -> dict[str, str]:
        with self._lock:
            return dict(sorted((p.name, p.product_id) for p in self._db.products.values()))

    def returns_blocked(self, order_id: str) -> bool:
        with self._lock:
            return order_id in self._db.return_blocked_orders

    def transfers(self) -> list[TransferRecord]:
        with self._lock:
            return [t.model_copy() for t in self._db.transfers]

    # ---- writes ------------------------------------------------------------------------

    def cancel_pending_order(self, order_id: str, reason: str, *, op_id: str) -> Order:
        with self._write(op_id, "cancel_pending_order", order_id) as (db, done):
            order = self._order(db, order_id)
            if done:
                return order.model_copy(deep=True)
            self._require_status(order, "pending", "cancelled")
            if reason not in CANCEL_REASONS:
                raise StoreError("invalid_reason", "Invalid cancellation reason", reason=reason)
            # Net amount paid per payment method (payments minus refunds).
            net: dict[str, float] = defaultdict(float)
            for p in order.payment_history:
                net[p.payment_method_id] += (
                    p.amount if p.transaction_type == "payment" else -p.amount
                )
            refunds = [
                OrderPayment(transaction_type="refund", amount=money(amt), payment_method_id=pm_id)
                for pm_id, amt in net.items()
                if money(amt) > 0
            ]
            methods = [
                self._payment_method(db, order.user_id, r.payment_method_id) for r in refunds
            ]
            for refund, pm in zip(refunds, methods, strict=True):
                if isinstance(pm, GiftCard):  # gift card refunds are immediate
                    pm.balance = money(pm.balance + refund.amount)
            order.status = "cancelled"
            order.cancel_reason = reason  # type: ignore[assignment]
            order.payment_history.extend(refunds)
            return order.model_copy(deep=True)

    def modify_pending_order_address(
        self, order_id: str, address: UserAddress, *, op_id: str
    ) -> Order:
        with self._write(op_id, "modify_pending_order_address", order_id) as (db, done):
            order = self._order(db, order_id)
            if done:
                return order.model_copy(deep=True)
            self._require_status(order, "pending", "modified")
            order.address = address.model_copy()
            return order.model_copy(deep=True)

    def modify_pending_order_payment(
        self, order_id: str, payment_method_id: str, *, op_id: str
    ) -> Order:
        with self._write(op_id, "modify_pending_order_payment", order_id) as (db, done):
            order = self._order(db, order_id)
            if done:
                return order.model_copy(deep=True)
            self._require_status(order, "pending", "modified")
            new_pm = self._payment_method(db, order.user_id, payment_method_id)
            history = order.payment_history
            if len(history) != 1 or history[0].transaction_type != "payment":
                raise StoreError(
                    "payment_history_not_single", "The order must have exactly one payment"
                )
            old_pm_id = history[0].payment_method_id
            if old_pm_id == payment_method_id:
                raise StoreError(
                    "same_payment_method", "The new payment method must differ from the current one"
                )
            amount = history[0].amount
            if isinstance(new_pm, GiftCard) and new_pm.balance < amount:
                raise StoreError(
                    "insufficient_gift_card_balance",
                    "Insufficient gift card balance to pay for the order",
                    balance=new_pm.balance,
                    needed=amount,
                )
            old_pm = self._payment_method(db, order.user_id, old_pm_id)
            history.extend(
                [
                    OrderPayment(
                        transaction_type="payment",
                        amount=amount,
                        payment_method_id=payment_method_id,
                    ),
                    OrderPayment(
                        transaction_type="refund", amount=amount, payment_method_id=old_pm_id
                    ),
                ]
            )
            if isinstance(new_pm, GiftCard):
                new_pm.balance = money(new_pm.balance - amount)
            if isinstance(old_pm, GiftCard):
                old_pm.balance = money(old_pm.balance + amount)
            return order.model_copy(deep=True)

    def modify_pending_order_items(
        self,
        order_id: str,
        item_ids: list[str],
        new_item_ids: list[str],
        payment_method_id: str,
        *,
        op_id: str,
    ) -> Order:
        with self._write(op_id, "modify_pending_order_items", order_id) as (db, done):
            order = self._order(db, order_id)
            if done:
                return order.model_copy(deep=True)
            self._require_status(order, "pending", "modified")
            pairs, diff = self._resolve_swaps(db, order, item_ids, new_item_ids)
            pm = self._payment_method(db, order.user_id, payment_method_id)
            if isinstance(pm, GiftCard) and pm.balance < diff:
                raise StoreError(
                    "insufficient_gift_card_balance",
                    "Insufficient gift card balance to pay for the new items",
                    balance=pm.balance,
                    needed=diff,
                )
            order.payment_history.append(
                OrderPayment(
                    transaction_type="payment" if diff > 0 else "refund",
                    amount=abs(diff),
                    payment_method_id=payment_method_id,
                )
            )
            if isinstance(pm, GiftCard):
                pm.balance = money(pm.balance - diff)
            for idx, variant in pairs:
                line = order.items[idx]
                line.item_id = variant.item_id
                line.price = variant.price
                line.options = dict(variant.options)
            order.status = "pending (item modified)"
            return order.model_copy(deep=True)

    def return_delivered_order_items(
        self, order_id: str, item_ids: list[str], payment_method_id: str, *, op_id: str
    ) -> Order:
        with self._write(op_id, "return_delivered_order_items", order_id) as (db, done):
            order = self._order(db, order_id)
            if done:
                return order.model_copy(deep=True)
            if order_id in db.return_blocked_orders:
                raise StoreError("returns_blocked", "Returns on this order are blocked")
            self._require_status(order, "delivered", "returned")
            pm = self._payment_method(db, order.user_id, payment_method_id)
            if (
                not isinstance(pm, GiftCard)
                and payment_method_id != order.payment_history[0].payment_method_id
            ):
                raise StoreError(
                    "refund_method_not_allowed",
                    "Refunds go to the original payment method or a gift card",
                )
            self._match_lines(order, item_ids)
            order.status = "return requested"
            order.return_items = sorted(item_ids)
            order.return_payment_method_id = payment_method_id
            return order.model_copy(deep=True)

    def exchange_delivered_order_items(
        self,
        order_id: str,
        item_ids: list[str],
        new_item_ids: list[str],
        payment_method_id: str,
        *,
        op_id: str,
    ) -> Order:
        with self._write(op_id, "exchange_delivered_order_items", order_id) as (db, done):
            order = self._order(db, order_id)
            if done:
                return order.model_copy(deep=True)
            self._require_status(order, "delivered", "exchanged")
            _, diff = self._resolve_swaps(db, order, item_ids, new_item_ids)
            pm = self._payment_method(db, order.user_id, payment_method_id)
            if isinstance(pm, GiftCard) and pm.balance < diff:
                raise StoreError(
                    "insufficient_gift_card_balance",
                    "Insufficient gift card balance to pay for the price difference",
                    balance=pm.balance,
                    needed=diff,
                )
            order.status = "exchange requested"
            order.exchange_items = sorted(item_ids)
            order.exchange_new_items = sorted(new_item_ids)
            order.exchange_payment_method_id = payment_method_id
            order.exchange_price_difference = diff
            return order.model_copy(deep=True)

    def modify_user_address(self, user_id: str, address: UserAddress, *, op_id: str) -> User:
        with self._write(op_id, "modify_user_address", user_id) as (db, done):
            user = self._user(db, user_id)
            if not done:
                user.address = address.model_copy()
            return user.model_copy(deep=True)

    def transfer_to_human_agents(self, user_id: str | None, summary: str, *, op_id: str) -> str:
        with self._write(op_id, "transfer_to_human_agents", user_id or "") as (db, done):
            if not done:
                db.transfers.append(TransferRecord(op_id=op_id, user_id=user_id, summary=summary))
            return "Transfer successful"

    def block_returns(self, order_id: str, *, op_id: str) -> None:
        """Block further returns on an order (after a supervisor rejected one)."""
        with self._write(op_id, "block_returns", order_id) as (db, done):
            self._order(db, order_id)
            if not done and order_id not in db.return_blocked_orders:
                db.return_blocked_orders.append(order_id)

    # ---- admin -------------------------------------------------------------------------

    def reset(self) -> None:
        """Restore the working copy to the original data."""
        with self._lock:
            self._db = self._backend.reset()
