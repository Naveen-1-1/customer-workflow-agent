import pytest

from customer_workflow_agent.resolve.addresses import complete_address
from customer_workflow_agent.resolve.ids import normalize_order_id, order_ids_in
from customer_workflow_agent.resolve.items import resolve_lines
from customer_workflow_agent.resolve.payments import payment_candidates
from customer_workflow_agent.resolve.text import match_one, mentions
from customer_workflow_agent.resolve.variants import resolve_variant
from customer_workflow_agent.store.models import OrderItem
from tests.conftest import sara_db


@pytest.mark.parametrize(
    "raw,text,expected",
    [
        ("#W2378156", "order #W2378156 please", "#W2378156"),
        ("W2378156", "it's w2378156", "#W2378156"),
        ("#W2378156", "my order", None),  # not typed by the customer: dropped
        (None, "#W2378156", None),
    ],
)
def test_normalize_order_id(raw, text, expected):
    assert normalize_order_id(raw, text) == expected


def test_order_ids_in_text():
    assert order_ids_in("#W1234567 and W7654321, again #W1234567") == ["#W1234567", "#W7654321"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("full-size", "full size"),
        ("fullsize", "full size"),
        ("Space Gray", "space grey"),
        ("google assistant", "Google Assistant"),
        ("purple", None),
    ],
)
def test_match_one(value, expected):
    cands = ["full size", "80%", "space grey", "Google Assistant", "silver"]
    assert match_one(value, cands) == expected


def test_mentions_products():
    assert mentions("t-shirts", "T-Shirt")
    assert mentions("watch", "Smart Watch")
    assert not mentions("keyboard", "Smart Watch")


def test_complete_address_normalizes_and_reports_missing():
    addr, missing = complete_address(
        {"address1": "1 Main St", "city": "Austin", "state": "texas", "zip": "78701-1234"}
    )
    assert missing == [] and addr.state == "TX" and addr.zip == "78701" and addr.country == "USA"
    _, missing = complete_address({"address1": "1 Main St"})
    assert missing == ["city", "state", "zip code"]


def test_payment_candidates():
    user = sara_db().users["sara_doe_496"]
    assert payment_candidates({"kind": "gift_card", "brand": None, "last_four": None}, user) == [
        "gift_card_0000000"
    ]
    assert payment_candidates({"kind": None, "brand": "visa", "last_four": "1234"}, user) == [
        "credit_card_0000000"
    ]
    assert payment_candidates({"kind": "paypal", "brand": None, "last_four": None}, user) == []


def test_resolve_lines_and_variants():
    db = sara_db()
    order = db.orders["#W0000000"]
    assert resolve_lines(order, {"product": "tshirt", "options": ["blue"]}).lines == [0]
    assert resolve_lines(order, {"product": "mug", "options": []}).status == "none"
    product = db.products["6086499569"]
    line: OrderItem = order.items[0]
    assert (
        resolve_variant(product, line, [{"name": None, "value": "large"}]).status
        == "unknown_option"
    )
    assert resolve_variant(product, line, [{"name": "size", "value": "l"}]).item_id == "1008292231"
    assert resolve_variant(product, line, [{"name": "size", "value": "M"}]).status == "same"
    assert resolve_variant(product, line, []).status == "need_desired"


@pytest.mark.parametrize("value", ["compatibility: Apple HomeKit", ".compatibility: Apple HomeKit"])
def test_option_values_with_a_name_prefix(value):
    assert (
        match_one(value, ["Amazon Alexa", "Apple HomeKit", "Google Assistant"]) == "Apple HomeKit"
    )
