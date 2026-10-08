"""Which variant of the same product the customer wants instead ("clicky", "size L")."""

from dataclasses import dataclass, field

from customer_workflow_agent.resolve.text import match_one
from customer_workflow_agent.store.models import OrderItem, Product

MAX_CHOICES = 8


def option_schema(product: Product) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    for v in product.variants.values():
        for k, val in v.options.items():
            if val not in schema.setdefault(k, []):
                schema[k].append(val)
    return schema


@dataclass
class VariantMatch:
    # "ok" | "choices" | "unavailable" | "same" | "unknown_option" | "need_desired"
    status: str
    item_id: str | None = None
    item_ids: list[str] = field(default_factory=list)
    unknown: str | None = None


def _closeness(options: dict, target: dict) -> int:
    return sum(options.get(k) == v for k, v in target.items())


def resolve_variant(product: Product, line: OrderItem, desired: list[dict]) -> VariantMatch:
    if not desired:
        return VariantMatch("need_desired")
    schema = option_schema(product)
    target = dict(line.options)
    stated: set[str] = set()
    for pair in desired:
        value, name = (pair.get("value") or "").strip(), (pair.get("name") or "").strip()
        key = match_one(name, schema) if name else None
        if key is not None:
            canonical = match_one(value, schema[key])
        else:
            hits = [(k, match_one(value, vals)) for k, vals in schema.items()]
            hits = [(k, v) for k, v in hits if v is not None]
            # Prefer an option the change actually alters ("black" -> color, not frame color).
            changing = [(k, v) for k, v in hits if line.options.get(k) != v]
            pick = (changing or hits)[:1]
            key, canonical = pick[0] if pick else (None, None)
        if key is None or canonical is None:
            return VariantMatch("unknown_option", unknown=value or name)
        target[key] = canonical
        stated.add(key)

    others = [v for v in product.variants.values() if v.item_id != line.item_id]
    exact = next((v for v in product.variants.values() if v.options == target), None)
    if exact is not None and exact.item_id == line.item_id:
        return VariantMatch("same")
    if exact is not None and exact.available:
        return VariantMatch("ok", item_id=exact.item_id)

    cands = [
        v for v in others if v.available and all(v.options.get(k) == target[k] for k in stated)
    ]
    cands.sort(key=lambda v: (-_closeness(v.options, target), v.price))
    if len(cands) == 1:
        return VariantMatch("ok", item_id=cands[0].item_id)
    if cands:
        return VariantMatch("choices", item_ids=[v.item_id for v in cands[:MAX_CHOICES]])
    available = sorted(
        (v for v in others if v.available), key=lambda v: (-_closeness(v.options, target), v.price)
    )
    return VariantMatch("unavailable", item_ids=[v.item_id for v in available[:MAX_CHOICES]])
