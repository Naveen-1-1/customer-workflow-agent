"""Which line(s) of an order the customer means ("the blue t-shirt", "both mugs")."""

from dataclasses import dataclass, field

from customer_workflow_agent.resolve.text import compact, match_one, mentions
from customer_workflow_agent.store.models import Order


@dataclass
class LineMatch:
    status: str  # "ok" | "ambiguous" | "none"
    lines: list[int] = field(default_factory=list)  # ok: chosen lines; ambiguous: one per item id


def _candidates(order: Order, ref: dict, exclude: set[int]) -> list[int]:
    product = (ref.get("product") or "").strip()
    out = []
    for i, line in enumerate(order.items):
        if i in exclude:
            continue
        if product and not mentions(product, line.name):
            continue
        values = list(line.options.values())
        # A bare option name ("size") doesn't say which one, so it doesn't rule a line out.
        names = {compact(n) for n in line.options}
        if all(
            match_one(opt, values) is not None or compact(opt) in names
            for opt in ref.get("options") or []
        ):
            out.append(i)
    return out


def resolve_lines(order: Order, ref: dict, exclude: set[int] | None = None) -> LineMatch:
    """Lines for one item reference. Several identical lines are fine; different items are not."""
    exclude = exclude or set()
    cands = _candidates(order, ref, exclude)
    if not cands:
        return LineMatch("none")
    by_item: dict[str, list[int]] = {}
    for i in cands:
        by_item.setdefault(order.items[i].item_id, []).append(i)
    if len(by_item) > 1:
        return LineMatch("ambiguous", [idxs[0] for idxs in by_item.values()])
    (idxs,) = by_item.values()
    qty = ref.get("quantity") or 1
    if qty > len(idxs):
        return LineMatch("none")
    return LineMatch("ok", idxs[:qty])


def same_item_lines(order: Order, line: int, exclude: set[int]) -> list[int]:
    """All unused lines with the same item id as `line` (for 'return both')."""
    item_id = order.items[line].item_id
    return [i for i, it in enumerate(order.items) if it.item_id == item_id and i not in exclude]
