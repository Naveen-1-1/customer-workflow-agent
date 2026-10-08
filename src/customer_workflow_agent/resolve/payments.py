"""Which of the customer's payment methods they mean ("my Visa", "the gift card")."""

from customer_workflow_agent.resolve.text import norm
from customer_workflow_agent.store.models import CreditCard, User


def payment_candidates(ref: dict | None, user: User, allowed: list[str] | None = None) -> list[str]:
    """Payment method ids matching the reference, among `allowed` (default: all the user's)."""
    ids = list(allowed) if allowed is not None else list(user.payment_methods)
    if not ref:
        return ids
    kind, brand, last_four = ref.get("kind"), ref.get("brand"), ref.get("last_four")
    out = []
    for pid in ids:
        pm = user.payment_methods.get(pid)
        if pm is None:
            continue
        if kind and pm.source != kind:
            continue
        if brand and not (isinstance(pm, CreditCard) and norm(brand) in norm(pm.brand)):
            if not (brand.lower() == "paypal" and pm.source == "paypal"):
                continue
        if last_four and not (isinstance(pm, CreditCard) and pm.last_four == last_four.strip()):
            continue
        out.append(pid)
    return out


def has_reference(ref: dict | None) -> bool:
    return bool(ref and any(ref.get(k) for k in ("kind", "brand", "last_four")))
