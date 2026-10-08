"""What the LLM may return. Every field is required (null when the customer didn't say it),
extra keys are forbidden, and choices are closed lists, so code can validate everything.

The LLM never returns user, product, item or payment-method ids: it returns what the
customer said (an email, "the blue one", "my Visa"), and code resolves that against the store.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

RequestType = Literal[
    "cancel_order",
    "modify_order_address",
    "modify_order_payment",
    "modify_order_items",
    "return_items",
    "exchange_items",
    "modify_default_address",
    "info",
    "transfer",
]

REQUEST_TYPES: tuple[str, ...] = RequestType.__args__  # type: ignore[attr-defined]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestItem(Schema):
    type: RequestType
    order_id: str | None
    all_eligible_orders: bool
    details: str


class Classification(Schema):
    requests: list[RequestItem]
    goodbye: bool
    about_other_person: bool


class AuthExtraction(Schema):
    email: str | None
    first_name: str | None
    last_name: str | None
    zip: str | None
    wants_human: bool
    goodbye: bool
    has_request: bool


Relation = Literal["answer", "also_new_request", "skip", "goodbye", "wants_human", "unclear"]


class Turn(Schema):
    """Fields every in-request turn returns."""

    relation: Relation
    new_requests: list[RequestItem]
    choice_number: int | None
    order_id: str | None


class CancelTurn(Turn):
    reason: Literal["no longer needed", "ordered by mistake", "other"] | None


class AddressFields(Schema):
    address1: str | None
    address2: str | None
    city: str | None
    state: str | None
    zip: str | None
    country: str | None


class AddressTurn(Turn):
    address: AddressFields | None
    use_profile_address: bool


class PaymentRef(Schema):
    kind: Literal["gift_card", "credit_card", "paypal"] | None
    brand: str | None
    last_four: str | None


class PaymentTurn(Turn):
    payment: PaymentRef | None


class ItemRef(Schema):
    product: str
    options: list[str]
    quantity: int | None


class OptionPair(Schema):
    name: str | None
    value: str


class ItemChange(Schema):
    item: ItemRef
    desired: list[OptionPair]


class ItemsTurn(Turn):
    changes: list[ItemChange]
    payment: PaymentRef | None


class ReturnTurn(Turn):
    all_items: bool
    items: list[ItemRef]
    refund_to: PaymentRef | None


InfoTopic = Literal[
    "order_details",
    "order_list",
    "profile",
    "payment_methods",
    "product_variants",
    "unknown",
]


class InfoQuery(Schema):
    topic: InfoTopic
    order_ids: list[str]
    product: str | None
