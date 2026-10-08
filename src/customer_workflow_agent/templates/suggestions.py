"""Fixed replies offered as buttons where the agent asks an open question.

Questions that list options (orders, items, payment methods) offer those options instead;
see `Ask.options` in graph/action.py.
"""


def suggestion(label: str, text: str | None = None) -> dict:
    """A Suggestion (contract.py) as plain state data."""
    return {"label": label, "text": text or label}


# τ²-bench customers (tasks 0, 16 and 19, also played in tests/graph/test_task_parity.py).
# Each gives identity and a request in one message; the request is held until verified.
DEMO_SCENARIOS = [
    suggestion(
        "Exchange two items",
        "I'm Yusuf Rossi, zip 19122. From order #W2378156, I'd like to exchange the mechanical "
        "keyboard for one with clicky switches, and the smart thermostat for one that works "
        "with Google Assistant",
    ),
    suggestion(
        "Cancel orders and return a watch",
        "Fatima Johnson, 78712. Please cancel all my pending orders, I no longer need them, "
        "and return the watch I received",
    ),
    suggestion(
        "Return one item, exchange two",
        "Mei Davis 80217. Two things: return my water bottle, and also exchange my pet bed "
        "and my office chair for their cheapest versions",
    ),
]

REQUEST_TYPES = [
    suggestion("Cancel an order", "I'd like to cancel an order"),
    suggestion("Return an item", "I'd like to return an item"),
    suggestion("Exchange an item", "I'd like to exchange an item"),
]

ANYTHING_ELSE = [suggestion("No, that's all, thanks"), *REQUEST_TYPES[1:]]

# The only two reasons the policy accepts.
CANCEL_REASONS = [suggestion("I no longer need it"), suggestion("I ordered it by mistake")]

SKIP_REQUEST = [suggestion("Skip this request", "skip")]
DEFAULT_ADDRESS = [suggestion("Use my default address")]
