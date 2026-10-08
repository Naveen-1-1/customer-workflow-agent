"""Return items from a delivered order (policy: status delivered; refund to the original
payment method or an existing gift card). Large returns may need supervisor approval."""

from customer_workflow_agent.graph.action import Ask, Check, Ctx, Found, Ready
from customer_workflow_agent.graph.subgraphs.base import (
    catalog_for,
    merge_order_id,
    order_steps,
    pick_payment,
    summary,
    the_order,
)
from customer_workflow_agent.llm.schemas import ReturnTurn
from customer_workflow_agent.policy.rules import Denial, refund_destinations
from customer_workflow_agent.resolve.items import resolve_lines
from customer_workflow_agent.resolve.text import norm
from customer_workflow_agent.templates import format as F


def check_returns_not_blocked(slots: dict, found: dict, ctx: Ctx) -> Check:
    """No more returns on an order after a supervisor rejected one."""
    order_id = found["order_id"]
    if ctx.store.returns_blocked(order_id):
        return Denial("returns_blocked", {"order_id": order_id})
    return Found()


def find_items(slots: dict, found: dict, ctx: Ctx) -> Check:
    """Which lines of the order the customer means ("the watch", "everything")."""
    order = the_order(found, ctx)
    item_list = [F.item_text(i) for i in order.items]
    lines: list[int] = []
    if slots.get("all_items"):
        lines = list(range(len(order.items)))
    else:
        for idx, entry in enumerate(slots.get("items") or []):
            if entry.get("lines") is not None:
                lines += [i for i in entry["lines"] if i not in lines]
                continue
            m = resolve_lines(order, entry["ref"], set(lines))
            if m.status == "ok":
                lines += m.lines
            elif m.status == "ambiguous":
                return Ask(
                    question=f"Which {entry['ref']['product']} would you like to return?",
                    options=[F.item_text(order.items[i]) for i in m.lines],
                    slot="items",
                    choices={"kind": "return_line", "values": m.lines, "index": idx},
                )
            else:
                return Ask(
                    question="Which items would you like to return? Its items are:",
                    preface=f'I couldn\'t find "{entry["ref"]["product"]}" in order '
                    f"{order.order_id}.",
                    options=item_list,
                    slot="items",
                )
    if not lines:
        return Ask(
            question="Which items from this order would you like to return?",
            options=item_list,
            slot="items",
        )
    return Found({"lines": sorted(lines)})


def choose_refund_method(slots: dict, found: dict, ctx: Ctx) -> Check:
    """Refunds go to the original payment method or an existing gift card (policy)."""
    pm_id, ask = pick_payment(
        slots,
        ctx.user,
        allowed=refund_destinations(the_order(found, ctx), ctx.user),
        slot="refund_to",
        picked_key="refund_pm_id",
        ref_key="refund_to",
        choice_kind="refund",
        question="Where should the refund go? It can go to the original payment method or "
        "a gift card.",
    )
    return ask or Found({"pm_id": pm_id})


class ReturnItems:
    name = "return_items"
    turn_schema = ReturnTurn
    reminder = None
    needs_approval_step = True  # large refunds may wait for a supervisor
    steps = (
        *order_steps("return_items"),
        ("check_returns_not_blocked", check_returns_not_blocked),
        ("find_items", find_items),
        ("choose_refund_method", choose_refund_method),
    )

    def merge(self, slots, turn, text, ctx):
        slots = merge_order_id(slots, turn, text)
        slots = dict(slots)
        if turn.all_items:
            slots["all_items"] = True
        if turn.items:
            items = [dict(i) for i in slots.get("items", [])]
            for ref in turn.items:
                ref_d = ref.model_dump()
                twin = next(
                    (
                        i
                        for i in items
                        if norm(i["ref"]["product"]) == norm(ref.product)
                        and sorted(i["ref"]["options"]) == sorted(ref.options)
                    ),
                    None,
                )
                if twin:
                    twin["ref"] = ref_d
                else:
                    items.append({"ref": ref_d, "lines": None})
            slots["items"] = items
        if turn.refund_to:
            slots["refund_to"] = turn.refund_to.model_dump()
            slots["refund_pm_id"] = None
        return slots

    def catalog(self, slots, ctx):
        if not slots.get("order_id"):
            return None
        try:
            order = ctx.store.get_order(slots["order_id"])
        except Exception:
            return None
        return catalog_for(order, ctx) if order.user_id == ctx.user.user_id else None

    def prepare(self, slots, found, ctx: Ctx) -> Ready:
        order = the_order(found, ctx)
        pm_id, lines = found["pm_id"], found["lines"]
        pm = ctx.user.payment_methods[pm_id]
        items = [order.items[i] for i in sorted(lines)]
        total = round(sum(i.price for i in items), 2)
        return Ready(
            summary=summary(
                f"Return items from order {order.order_id}",
                [
                    *(F.item_text(i) for i in items),
                    f"Refund {F.usd(total)} to your {F.pm_text(pm)}",
                    "You'll receive an email explaining how to return the items.",
                ],
                total,
                "Refund",
            ),
            call={
                "fn": "return_delivered_order_items",
                "args": {
                    "order_id": order.order_id,
                    "item_ids": [i.item_id for i in items],
                    "payment_method_id": pm_id,
                },
            },
            result={"total": total, "pm": pm_id},
            refund_total=total,
            approval={
                "order_id": order.order_id,
                "user_id": ctx.user.user_id,
                "items": [{"name": i.name, "options": i.options, "price": i.price} for i in items],
                "refund_total": total,
                "payment_method_id": pm_id,
                "payment_method": F.pm_text(pm),
            },
        )

    def result_text(self, returned, ready, ctx):
        pm = ctx.store.get_user(ctx.user.user_id).payment_methods[ready.result["pm"]]
        return (
            f"Done — the return for order {returned.order_id} is requested. "
            f"{F.usd(ready.result['total'])} will be refunded to your {F.pm_text(pm)}. "
            "You'll receive an email explaining how to return the items."
        )


SPEC = ReturnItems()
