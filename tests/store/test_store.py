"""Store tests. The first block is adapted from τ²-bench's
`tests/test_domains/test_retail/test_tools_retail.py` (MIT, Copyright (c) 2025 Sierra Research)."""

import pytest

from customer_workflow_agent.store import FileBackend, InMemoryBackend, RetailStore, StoreError
from customer_workflow_agent.store.models import (
    GiftCard,
    Order,
    OrderItem,
    OrderPayment,
    Product,
    UserAddress,
    Variant,
)
from tests.conftest import DATA_DB, op, sara_db

ORDER = "#W0000000"
NEW_ADDRESS = UserAddress(
    address1="456 New St",
    address2="Apt 2",
    city="San Francisco",
    state="CA",
    country="USA",
    zip="94106",
)


def persist(store: RetailStore) -> None:
    """Save direct test edits to `store._db`, so a reload after an error keeps them."""
    store._backend.save(store._db)


def set_status(store: RetailStore, status: str) -> None:
    store._db.orders[ORDER].status = status  # test-only shortcut
    persist(store)


# ---- ported from τ²-bench ------------------------------------------------------------


def test_cancel_pending_order(sara_store: RetailStore):
    order = sara_store.cancel_pending_order(ORDER, "no longer needed", op_id=op())
    assert order.status == "cancelled"
    assert order.cancel_reason == "no longer needed"
    assert len(order.payment_history) == 2
    assert order.payment_history[1].transaction_type == "refund"
    assert order.payment_history[1].amount == order.payment_history[0].amount


def test_exchange_delivered_order_items(sara_store: RetailStore):
    set_status(sara_store, "delivered")
    order = sara_store.exchange_delivered_order_items(
        ORDER, ["1008292230"], ["1008292231"], "credit_card_0000000", op_id=op()
    )
    assert order.status == "exchange requested"
    assert order.exchange_items == ["1008292230"]
    assert order.exchange_new_items == ["1008292231"]
    assert order.exchange_payment_method_id == "credit_card_0000000"


def test_find_user_id_by_name_zip(sara_store: RetailStore):
    assert sara_store.find_user_id_by_name_zip("Sara", "Doe", "94105") == "sara_doe_496"
    assert sara_store.find_user_id_by_name_zip("sara", "DOE", " 94105 ") == "sara_doe_496"
    with pytest.raises(StoreError) as e:
        sara_store.find_user_id_by_name_zip("John", "Doe", "94105")
    assert e.value.code == "user_not_found"


def test_find_user_id_by_email(sara_store: RetailStore):
    assert sara_store.find_user_id_by_email("Sara.Doe@example.com") == "sara_doe_496"
    with pytest.raises(StoreError):
        sara_store.find_user_id_by_email("nonexistent@example.com")


def test_get_details_not_found(sara_store: RetailStore):
    for call, code in [
        (lambda: sara_store.get_order("#NONEXISTENT"), "order_not_found"),
        (lambda: sara_store.get_product("NONEXISTENT"), "product_not_found"),
        (lambda: sara_store.get_user("NONEXISTENT"), "user_not_found"),
        (lambda: sara_store.get_item("NONEXISTENT"), "item_not_found"),
    ]:
        with pytest.raises(StoreError) as e:
            call()
        assert e.value.code == code


def test_list_all_product_types(sara_store: RetailStore):
    assert sara_store.list_all_product_types() == {"Classic T-Shirt": "6086499569"}


def test_modify_pending_order_address(sara_store: RetailStore):
    order = sara_store.modify_pending_order_address(ORDER, NEW_ADDRESS, op_id=op())
    assert order.address.address1 == "456 New St"
    assert order.address.zip == "94106"
    set_status(sara_store, "delivered")
    with pytest.raises(StoreError) as e:
        sara_store.modify_pending_order_address(ORDER, NEW_ADDRESS, op_id=op())
    assert e.value.code == "invalid_status"


def test_modify_pending_order_items(sara_store: RetailStore):
    order = sara_store.modify_pending_order_items(
        ORDER, ["1008292230"], ["1008292231"], "credit_card_0000000", op_id=op()
    )
    assert order.status == "pending (item modified)"
    assert order.items[0].item_id == "1008292231"
    with pytest.raises(StoreError) as e:  # only once
        sara_store.modify_pending_order_items(
            ORDER, ["1008292231"], ["1008292230"], "credit_card_0000000", op_id=op()
        )
    assert e.value.code == "invalid_status"


def test_modify_pending_order_payment(sara_store: RetailStore):
    order = sara_store.modify_pending_order_payment(ORDER, "gift_card_0000000", op_id=op())
    assert len(order.payment_history) == 3  # original payment + new payment + refund
    gift = sara_store.get_user("sara_doe_496").payment_methods["gift_card_0000000"]
    assert isinstance(gift, GiftCard) and gift.balance == pytest.approx(70.01)


def test_modify_user_address(sara_store: RetailStore):
    user = sara_store.modify_user_address("sara_doe_496", NEW_ADDRESS, op_id=op())
    assert user.address.address1 == "456 New St"
    with pytest.raises(StoreError) as e:
        sara_store.modify_user_address("NONEXISTENT", NEW_ADDRESS, op_id=op())
    assert e.value.code == "user_not_found"


def test_return_delivered_order_items(sara_store: RetailStore):
    set_status(sara_store, "delivered")
    order = sara_store.return_delivered_order_items(
        ORDER, ["1008292230"], "credit_card_0000000", op_id=op()
    )
    assert order.status == "return requested"
    assert order.return_items == ["1008292230"]
    assert order.return_payment_method_id == "credit_card_0000000"
    with pytest.raises(StoreError) as e:
        sara_store.return_delivered_order_items(
            ORDER, ["1008292230"], "credit_card_0000000", op_id=op()
        )
    assert e.value.code == "invalid_status"


def test_transfer_to_human_agents(sara_store: RetailStore):
    assert sara_store.transfer_to_human_agents("sara_doe_496", "wants a human", op_id=op()) == (
        "Transfer successful"
    )
    assert sara_store.transfers()[0].summary == "wants a human"


# ---- fixes and tightenings -----------------------------------------------------------


def _two_product_store() -> RetailStore:
    db = sara_db()
    db.products["2222222222"] = Product(
        product_id="2222222222",
        name="Mug",
        variants={
            "3000000001": Variant(
                item_id="3000000001", price=10.0, available=True, options={"color": "white"}
            ),
            "3000000002": Variant(
                item_id="3000000002", price=12.5, available=True, options={"color": "black"}
            ),
        },
    )
    db.products["6086499569"].variants["1008292231"].price = 31.0
    order = db.orders[ORDER]
    order.items.append(
        OrderItem(
            name="Mug",
            product_id="2222222222",
            item_id="3000000001",
            price=10.0,
            options={"color": "white"},
        )
    )
    order.payment_history[0].amount = 39.99
    return RetailStore(InMemoryBackend(db))


def test_fix_modify_items_each_item_gets_its_own_variant():
    store = _two_product_store()
    order = store.modify_pending_order_items(
        ORDER,
        ["1008292230", "3000000001"],
        ["1008292231", "3000000002"],
        "credit_card_0000000",
        op_id=op(),
    )
    shirt, mug = order.items
    assert (shirt.item_id, shirt.price, shirt.options) == (
        "1008292231",
        31.0,
        {"size": "L", "color": "blue"},
    )
    assert (mug.item_id, mug.price, mug.options) == ("3000000002", 12.5, {"color": "black"})
    last = order.payment_history[-1]
    assert (last.transaction_type, last.amount) == ("payment", 3.51)


def test_fix_cancel_refunds_net_amount_once():
    store = RetailStore(InMemoryBackend(sara_db()))
    store.modify_pending_order_payment(ORDER, "gift_card_0000000", op_id=op())
    order = store.cancel_pending_order(ORDER, "ordered by mistake", op_id=op())
    refunds = [p for p in order.payment_history if p.transaction_type == "refund"]
    # earlier refund to the card + one cancel refund of 29.99 to the gift card only
    assert [(r.payment_method_id, r.amount) for r in refunds] == [
        ("credit_card_0000000", 29.99),
        ("gift_card_0000000", 29.99),
    ]
    gift = store.get_user("sara_doe_496").payment_methods["gift_card_0000000"]
    assert isinstance(gift, GiftCard) and gift.balance == pytest.approx(100.0)


def test_address_change_requires_exactly_pending(sara_store: RetailStore):
    set_status(sara_store, "pending (item modified)")
    with pytest.raises(StoreError) as e:
        sara_store.modify_pending_order_address(ORDER, NEW_ADDRESS, op_id=op())
    assert e.value.code == "invalid_status"


def test_exchange_requires_different_item(sara_store: RetailStore):
    set_status(sara_store, "delivered")
    with pytest.raises(StoreError) as e:
        sara_store.exchange_delivered_order_items(
            ORDER, ["1008292230"], ["1008292230"], "credit_card_0000000", op_id=op()
        )
    assert e.value.code == "same_item"


def test_empty_item_list_rejected(sara_store: RetailStore):
    set_status(sara_store, "delivered")
    with pytest.raises(StoreError) as e:
        sara_store.return_delivered_order_items(ORDER, [], "credit_card_0000000", op_id=op())
    assert e.value.code == "empty_items"


def test_cancel_rejects_other_reason(sara_store: RetailStore):
    with pytest.raises(StoreError) as e:
        sara_store.cancel_pending_order(ORDER, "found it cheaper", op_id=op())
    assert e.value.code == "invalid_reason"


def test_duplicate_item_ids_map_to_distinct_lines():
    db = sara_db()
    order = db.orders[ORDER]
    order.items.append(order.items[0].model_copy())
    store = RetailStore(InMemoryBackend(db))
    result = store.modify_pending_order_items(
        ORDER,
        ["1008292230", "1008292230"],
        ["1008292231", "1008292231"],
        "credit_card_0000000",
        op_id=op(),
    )
    assert [i.item_id for i in result.items] == ["1008292231", "1008292231"]
    with pytest.raises(StoreError):
        RetailStore(InMemoryBackend(sara_db())).modify_pending_order_items(
            ORDER,
            ["1008292230", "1008292230"],
            ["1008292231", "1008292231"],
            "credit_card_0000000",
            op_id=op(),
        )


def test_return_refund_destination_rules(sara_store: RetailStore):
    db = sara_store._db
    db.orders[ORDER].status = "delivered"
    db.users["sara_doe_496"].payment_methods["credit_card_9999999"] = (
        db.users["sara_doe_496"]
        .payment_methods["credit_card_0000000"]
        .model_copy(update={"id": "credit_card_9999999", "last_four": "9999"})
    )
    persist(sara_store)
    with pytest.raises(StoreError) as e:
        sara_store.return_delivered_order_items(
            ORDER, ["1008292230"], "credit_card_9999999", op_id=op()
        )
    assert e.value.code == "refund_method_not_allowed"
    order = sara_store.return_delivered_order_items(
        ORDER, ["1008292230"], "gift_card_0000000", op_id=op()
    )
    assert order.return_payment_method_id == "gift_card_0000000"


def test_gift_card_must_cover_payment(sara_store: RetailStore):
    db = sara_store._db
    gift = db.users["sara_doe_496"].payment_methods["gift_card_0000000"]
    assert isinstance(gift, GiftCard)
    gift.balance = 5.0
    with pytest.raises(StoreError) as e:
        sara_store.modify_pending_order_payment(ORDER, "gift_card_0000000", op_id=op())
    assert e.value.code == "insufficient_gift_card_balance"


def test_payment_change_needs_single_payment(sara_store: RetailStore):
    sara_store._db.orders[ORDER].payment_history.append(
        OrderPayment(transaction_type="refund", amount=1.0, payment_method_id="credit_card_0000000")
    )
    with pytest.raises(StoreError) as e:
        sara_store.modify_pending_order_payment(ORDER, "gift_card_0000000", op_id=op())
    assert e.value.code == "payment_history_not_single"


# ---- ledger, blocking, rollback, persistence -----------------------------------------


def test_same_op_id_is_applied_once():
    backend = InMemoryBackend(sara_db())
    store = RetailStore(backend)
    first = store.modify_pending_order_payment(ORDER, "gift_card_0000000", op_id="x")
    again = store.modify_pending_order_payment(ORDER, "gift_card_0000000", op_id="x")
    assert first == again and len(again.payment_history) == 3
    assert backend.saves == 1


def test_block_returns(sara_store: RetailStore):
    set_status(sara_store, "delivered")
    sara_store.block_returns(ORDER, op_id=op())
    assert sara_store.returns_blocked(ORDER)
    with pytest.raises(StoreError) as e:
        sara_store.return_delivered_order_items(
            ORDER, ["1008292230"], "credit_card_0000000", op_id=op()
        )
    assert e.value.code == "returns_blocked"


def test_failed_write_changes_nothing():
    backend = InMemoryBackend(sara_db())
    store = RetailStore(backend)
    before = store.get_order(ORDER)
    with pytest.raises(StoreError):
        store.modify_pending_order_items(
            ORDER, ["1008292230"], ["9999999999"], "credit_card_0000000", op_id=op()
        )
    assert store.get_order(ORDER) == before
    assert backend.saves == 0


def test_reads_are_copies(sara_store: RetailStore):
    order: Order = sara_store.get_order(ORDER)
    order.status = "cancelled"
    assert sara_store.get_order(ORDER).status == "pending"


def test_file_backend_working_copy_and_reset(tmp_path):
    working = tmp_path / "var" / "db.working.json"
    store = RetailStore(FileBackend(DATA_DB, working))
    original_bytes = DATA_DB.read_bytes()
    order_id = next(o for o, v in store._db.orders.items() if v.status == "pending")
    store.cancel_pending_order(order_id, "no longer needed", op_id=op())
    # survives a restart, original untouched
    reopened = RetailStore(FileBackend(DATA_DB, working))
    assert reopened.get_order(order_id).status == "cancelled"
    assert DATA_DB.read_bytes() == original_bytes
    reopened.reset()
    assert reopened.get_order(order_id).status == "pending"
    assert working.read_bytes() == original_bytes
    assert not list(working.parent.glob("*.tmp"))
