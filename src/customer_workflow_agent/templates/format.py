"""Formatting store facts for customers. All amounts, ids and statuses go through here."""

from collections.abc import Mapping, Sequence

from customer_workflow_agent.store.models import (
    CreditCard,
    GiftCard,
    Order,
    OrderItem,
    PaymentMethod,
    Paypal,
    User,
    UserAddress,
    Variant,
)


def usd(amount: float) -> str:
    return f"${amount:,.2f}"


def address_text(a: UserAddress | Mapping) -> str:
    d = a.model_dump() if isinstance(a, UserAddress) else dict(a)
    street = ", ".join(p for p in (d.get("address1"), d.get("address2")) if p)
    return f"{street}, {d.get('city')}, {d.get('state')} {d.get('zip')}, {d.get('country')}"


def options_text(options: Mapping[str, str]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in options.items())


def item_text(item: OrderItem) -> str:
    return f"{item.name} ({options_text(item.options)}) — {usd(item.price)}"


def variant_text(variant: Variant) -> str:
    stock = "" if variant.available else " (out of stock)"
    return f"{options_text(variant.options)} — {usd(variant.price)}{stock}"


def pm_text(pm: PaymentMethod) -> str:
    match pm:
        case CreditCard():
            return f"{pm.brand.title()} card ending {pm.last_four}"
        case GiftCard():
            return f"gift card (balance {usd(pm.balance)})"
        case Paypal():
            return "PayPal"
    raise TypeError(pm)


def refund_timing(pm: PaymentMethod) -> str:
    if isinstance(pm, GiftCard):
        return "added to the gift card immediately"
    return "back within 5–7 business days"


def order_total(order: Order) -> float:
    return round(sum(i.price for i in order.items), 2)


def order_summary_line(order: Order) -> str:
    n = len(order.items)
    items = f"{n} item{'s' if n != 1 else ''}"
    return f"{order.order_id} — {order.status}, {items}, {usd(order_total(order))}"


def numbered(lines: Sequence[str]) -> str:
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))


def user_pm(user: User, pm_id: str) -> PaymentMethod:
    return user.payment_methods[pm_id]
