"""Retail data model.

Adapted from τ²-bench `src/tau2/domains/retail/data_model.py`
(MIT, Copyright (c) 2025 Sierra Research). The τ²-bench `DB` base class is replaced by a
plain Pydantic model, and `WorkingDB` adds the fields this app keeps alongside the store.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Variant(StrictModel):
    """A specific variant of a product with its options, availability and price."""

    item_id: str
    options: dict[str, str]
    available: bool
    price: float


class Product(StrictModel):
    """A product with its variants, indexed by item id."""

    name: str
    product_id: str
    variants: dict[str, Variant]


class UserName(StrictModel):
    first_name: str
    last_name: str


class UserAddress(StrictModel):
    address1: str
    address2: str
    city: str
    country: str
    state: str
    zip: str


class CreditCard(StrictModel):
    source: Literal["credit_card"]
    id: str
    brand: str
    last_four: str


class Paypal(StrictModel):
    source: Literal["paypal"]
    id: str


class GiftCard(StrictModel):
    source: Literal["gift_card"]
    id: str
    balance: float


PaymentMethod = Annotated[CreditCard | GiftCard | Paypal, Field(discriminator="source")]


class User(StrictModel):
    user_id: str
    name: UserName
    address: UserAddress
    email: str
    payment_methods: dict[str, PaymentMethod]
    orders: list[str]


class OrderFullfilment(StrictModel):
    tracking_id: list[str]
    item_ids: list[str]


class OrderItem(StrictModel):
    name: str
    product_id: str
    item_id: str
    price: float
    options: dict[str, str]


OrderPaymentType = Literal["payment", "refund"]


class OrderPayment(StrictModel):
    transaction_type: OrderPaymentType
    amount: float
    payment_method_id: str


OrderStatus = Literal[
    "processed",
    "pending",
    "pending (item modified)",
    "delivered",
    "cancelled",
    "exchange requested",
    "return requested",
]

CancelReason = Literal["no longer needed", "ordered by mistake"]
CANCEL_REASONS: tuple[str, ...] = ("no longer needed", "ordered by mistake")


class Order(StrictModel):
    order_id: str
    user_id: str
    address: UserAddress
    items: list[OrderItem]
    status: OrderStatus
    fulfillments: list[OrderFullfilment]
    payment_history: list[OrderPayment]
    cancel_reason: CancelReason | None = None
    exchange_items: list[str] | None = None
    exchange_new_items: list[str] | None = None
    exchange_payment_method_id: str | None = None
    exchange_price_difference: float | None = None
    return_items: list[str] | None = None
    return_payment_method_id: str | None = None


class RetailDB(StrictModel):
    """The τ²-bench store: products, users and orders."""

    products: dict[str, Product]
    users: dict[str, User]
    orders: dict[str, Order]


class OpRecord(StrictModel):
    """A completed write, keyed by op id, so a replayed write is not applied twice."""

    op: str
    target_id: str


class TransferRecord(StrictModel):
    op_id: str
    user_id: str | None
    summary: str


class WorkingDB(RetailDB):
    """The working copy: the store plus app bookkeeping (all default to empty)."""

    ledger: dict[str, OpRecord] = Field(default_factory=dict)
    transfers: list[TransferRecord] = Field(default_factory=list)
    return_blocked_orders: list[str] = Field(default_factory=list)
