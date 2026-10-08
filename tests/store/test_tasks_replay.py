"""Every write in τ²-bench's 114 retail tasks replays cleanly against a fresh store."""

import json

import pytest

from customer_workflow_agent.store import InMemoryBackend, RetailStore, StoreError
from customer_workflow_agent.store.models import UserAddress, WorkingDB
from tests.conftest import PROJECT_ROOT

TASKS = json.loads((PROJECT_ROOT / "data" / "tasks.json").read_text())
ADDRESS_KEYS = ("address1", "address2", "city", "state", "country", "zip")

# Expected actions that τ²-bench's own tools also reject (the task expects the attempt to fail).
EXPECTED_FAILURES = {
    ("64", 0): "invalid_status",  # exchange on an order that is still pending
    ("105", 0): "insufficient_gift_card_balance",  # $17.00 gift card vs $21.10 difference
}


def apply_action(store: RetailStore, name: str, args: dict, op_id: str) -> None:
    """Run one τ²-bench expected action against our store (reads are skipped)."""
    a = dict(args)
    if name in ("modify_pending_order_address", "modify_user_address"):
        address = UserAddress(**{k: a.pop(k) for k in ADDRESS_KEYS})
        target = a.pop("order_id", None) or a.pop("user_id")
        getattr(store, name)(target, address, op_id=op_id)
    elif name == "transfer_to_human_agents":
        store.transfer_to_human_agents(None, a["summary"], op_id=op_id)
    elif name in (
        "cancel_pending_order",
        "modify_pending_order_payment",
        "modify_pending_order_items",
        "return_delivered_order_items",
        "exchange_delivered_order_items",
    ):
        getattr(store, name)(**a, op_id=op_id)


def write_actions(task: dict) -> list[dict]:
    actions = (task.get("evaluation_criteria") or {}).get("actions") or []
    return [
        a
        for a in actions
        if a["name"].startswith(("cancel", "modify", "return", "exchange", "transfer"))
    ]


@pytest.mark.parametrize("task", TASKS, ids=[t["id"] for t in TASKS])
def test_task_writes_replay(task: dict, real_db: WorkingDB):
    store = RetailStore(InMemoryBackend(real_db))
    for i, action in enumerate(write_actions(task)):
        expected = EXPECTED_FAILURES.get((task["id"], i))
        if expected is None:
            apply_action(store, action["name"], action["arguments"], f"{task['id']}-{i}")
        else:
            with pytest.raises(StoreError) as e:
                apply_action(store, action["name"], action["arguments"], f"{task['id']}-{i}")
            assert e.value.code == expected
