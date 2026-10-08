import pytest

from customer_workflow_agent.policy.rules import (
    REQUIRED_STATUS,
    check_cancel_reason,
    check_owner,
    check_status,
    needs_approval,
    refund_destinations,
)
from customer_workflow_agent.settings import Settings
from tests.conftest import sara_db

STATUSES = [
    "pending",
    "processed",
    "delivered",
    "cancelled",
    "pending (item modified)",
    "return requested",
    "exchange requested",
]


@pytest.mark.parametrize("action", sorted(REQUIRED_STATUS))
@pytest.mark.parametrize("status", STATUSES)
def test_status_matrix(action, status):
    order = sara_db().orders["#W0000000"].model_copy(update={"status": status})
    allowed = check_status(action, order) is None
    assert allowed == (status == REQUIRED_STATUS[action])


def test_owner_check_hides_other_users_orders():
    order = sara_db().orders["#W0000000"]
    assert check_owner(order, "sara_doe_496", order.order_id) is None
    other = check_owner(order, "someone_else_1", order.order_id)
    missing = check_owner(None, "sara_doe_496", order.order_id)
    assert other == missing  # same denial either way


def test_cancel_reasons():
    assert check_cancel_reason("no longer needed") is None
    assert check_cancel_reason("ordered by mistake") is None
    assert check_cancel_reason("other") is not None


def test_refund_destinations_original_then_gift_cards():
    db = sara_db()
    order, user = db.orders["#W0000000"], db.users["sara_doe_496"]
    assert refund_destinations(order, user) == ["credit_card_0000000", "gift_card_0000000"]


def test_approval_threshold_is_strictly_greater_and_off_by_default():
    on = Settings(_env_file=None, approval_enabled=True, approval_threshold=1000)
    assert not needs_approval(on, 1000.0) and needs_approval(on, 1000.01)
    assert not needs_approval(Settings(_env_file=None), 5000)
