import itertools
from pathlib import Path

import pytest

from customer_workflow_agent.store import InMemoryBackend, RetailStore
from customer_workflow_agent.store.models import (
    CreditCard,
    GiftCard,
    Order,
    OrderItem,
    OrderPayment,
    Product,
    User,
    UserAddress,
    UserName,
    Variant,
    WorkingDB,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DB = PROJECT_ROOT / "data" / "db.json"

_ops = itertools.count()


def op() -> str:
    """A fresh op id for a store write."""
    return f"op-{next(_ops)}"


SARA_ADDRESS = UserAddress(
    address1="123 Main St",
    address2="Apt 1",
    city="San Francisco",
    state="CA",
    country="USA",
    zip="94105",
)


def sara_db() -> WorkingDB:
    """The small fixture from τ²-bench's retail tool tests, plus a second product."""
    return WorkingDB(
        users={
            "sara_doe_496": User(
                user_id="sara_doe_496",
                name=UserName(first_name="Sara", last_name="Doe"),
                address=SARA_ADDRESS,
                email="sara.doe@example.com",
                payment_methods={
                    "credit_card_0000000": CreditCard(
                        source="credit_card",
                        brand="visa",
                        last_four="1234",
                        id="credit_card_0000000",
                    ),
                    "gift_card_0000000": GiftCard(
                        source="gift_card", balance=100.0, id="gift_card_0000000"
                    ),
                },
                orders=["#W0000000"],
            )
        },
        orders={
            "#W0000000": Order(
                order_id="#W0000000",
                user_id="sara_doe_496",
                status="pending",
                items=[
                    OrderItem(
                        name="Classic T-Shirt",
                        product_id="6086499569",
                        item_id="1008292230",
                        price=29.99,
                        options={"size": "M", "color": "blue"},
                    )
                ],
                address=SARA_ADDRESS,
                fulfillments=[],
                payment_history=[
                    OrderPayment(
                        transaction_type="payment",
                        amount=29.99,
                        payment_method_id="credit_card_0000000",
                    )
                ],
            )
        },
        products={
            "6086499569": Product(
                product_id="6086499569",
                name="Classic T-Shirt",
                variants={
                    "1008292230": Variant(
                        item_id="1008292230",
                        price=29.99,
                        available=True,
                        options={"size": "M", "color": "blue"},
                    ),
                    "1008292231": Variant(
                        item_id="1008292231",
                        price=29.99,
                        available=True,
                        options={"size": "L", "color": "blue"},
                    ),
                },
            )
        },
    )


@pytest.fixture
def sara_store() -> RetailStore:
    return RetailStore(InMemoryBackend(sara_db()))


@pytest.fixture(scope="session")
def real_db() -> WorkingDB:
    return WorkingDB.model_validate_json(DATA_DB.read_bytes())


@pytest.fixture
def real_store(real_db: WorkingDB) -> RetailStore:
    return RetailStore(InMemoryBackend(real_db))
