"""Swap items for other options of the same product: modify a pending order's items (once),
or exchange a delivered order's items. Policy: same product, different option, available;
a payment method for the price difference; a gift card must cover it; all items in one go."""

from customer_workflow_agent.graph.action import Ask, Ctx, Plan, Ready
from customer_workflow_agent.graph.subgraphs.base import (
    catalog_for,
    merge_order_id,
    pick_payment,
    resolve_order,
    summary,
)
from customer_workflow_agent.llm.schemas import ItemsTurn
from customer_workflow_agent.policy.rules import Denial, gift_card_covers
from customer_workflow_agent.resolve.items import resolve_lines
from customer_workflow_agent.resolve.text import mentions, norm
from customer_workflow_agent.resolve.variants import option_schema, resolve_variant
from customer_workflow_agent.store.models import Order
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates import messages as M


def _merge_changes(existing: list[dict], new: list) -> list[dict]:
    changes = [dict(c) for c in existing]
    for ch in new:
        d = ch.model_dump()
        twin = next(
            (
                c
                for c in changes
                if norm(c["item"]["product"]) == norm(d["item"]["product"])
                and (
                    not d["item"]["options"]
                    or not c["item"]["options"]
                    or sorted(c["item"]["options"]) == sorted(d["item"]["options"])
                )
            ),
            None,
        )
        if twin is None:
            twin = next(
                (c for c in changes if mentions(c["item"]["product"], d["item"]["product"])), None
            )
        if twin is None:
            changes.append({**d, "line": None, "new_item_id": None})
            continue
        if d["item"]["options"]:
            twin["item"] = d["item"]
        if d["desired"]:
            by_key = {(p.get("name") or p["value"]).lower(): p for p in twin["desired"]}
            for p in d["desired"]:
                by_key[(p.get("name") or p["value"]).lower()] = p
            twin["desired"] = list(by_key.values())
            twin["new_item_id"] = None
    return changes


class SwapItems:
    turn_schema = ItemsTurn

    def __init__(self, name: str, fn: str, reminder: str, verb: str):
        self.name = name
        self.fn = fn
        self.reminder = reminder
        self.verb = verb

    def merge(self, slots, turn, text, ctx):
        slots = dict(merge_order_id(slots, turn, text))
        if turn.changes:
            slots["changes"] = _merge_changes(slots.get("changes") or [], turn.changes)
        if turn.payment:
            slots["payment"] = turn.payment.model_dump()
            slots["payment_method_id"] = None
        return slots

    def catalog(self, slots, ctx):
        if not slots.get("order_id"):
            return None
        try:
            order = ctx.store.get_order(slots["order_id"])
        except Exception:
            return None
        return catalog_for(order, ctx) if order.user_id == ctx.user.user_id else None

    def _resolve_change(
        self, order: Order, idx: int, change: dict, used: set[int], ctx: Ctx
    ) -> tuple[int, str] | Ask | Denial:
        item_list = F.numbered([F.item_text(i) for i in order.items])
        line = change.get("line")
        if line is None:
            m = resolve_lines(order, {**change["item"], "quantity": 1}, used)
            if m.status == "ambiguous":
                return Ask(
                    question=f"Which {change['item']['product']} do you mean?",
                    details=F.numbered([F.item_text(order.items[i]) for i in m.lines]),
                    slot=f"line{idx}",
                    choices={"kind": "line", "values": m.lines, "index": idx},
                )
            if m.status == "none":
                return Ask(
                    question=f"Which item would you like to {self.verb}? Its items are:",
                    preface=f'I couldn\'t find "{change["item"]["product"]}" in order '
                    f"{order.order_id}.",
                    details=item_list,
                    slot=f"line{idx}",
                )
            line = m.lines[0]
        item = order.items[line]
        product = ctx.store.get_product(item.product_id)
        if change.get("new_item_id") in product.variants:
            return line, change["new_item_id"]
        schema = option_schema(product)
        options_help = "\n".join(f"- {k}: {', '.join(v)}" for k, v in schema.items())
        vm = resolve_variant(product, item, change.get("desired") or [])
        if vm.status == "ok":
            assert vm.item_id
            return line, vm.item_id
        if vm.status == "need_desired":
            return Ask(
                question=f"What would you like your {F.item_text(item)} changed to? "
                "Its options are:",
                details=options_help,
                slot=f"desired{idx}",
            )
        if vm.status == "unknown_option":
            if vm.unknown and any(
                mentions(vm.unknown, name)
                for name in ctx.store.list_all_product_types()
                if name != item.name
            ):
                return Denial("product_change_not_allowed")
            return Ask(
                question=f"Which option would you like for the {item.name}? Its options are:",
                preface=f'"{vm.unknown}" isn\'t an option for the {item.name}.',
                details=options_help,
                slot=f"desired{idx}",
            )
        if vm.status == "same":
            return Ask(
                question=f"What would you like the {item.name} changed to? Its options are:",
                preface=f"That's the same as the {item.name} you have.",
                details=options_help,
                slot=f"desired{idx}",
            )
        variants = [product.variants[i] for i in vm.item_ids]
        if not variants:
            return Ask(
                question=f"Would you like a different option for the {item.name}, or "
                "should we skip this item?",
                preface=f"Sorry, no other {item.name} options are in stock right now.",
                slot=f"desired{idx}",
            )
        if vm.status == "choices":
            question, preface = (
                f"Which {item.name} would you like? These match what you asked for:",
                "",
            )
        else:
            question = f"Which {item.name} would you like instead? These are in stock:"
            preface = "Sorry, that exact option isn't available."
        return Ask(
            question=question,
            preface=preface,
            details=F.numbered([F.variant_text(v) for v in variants]),
            slot=f"variant{idx}",
            choices={"kind": "variant", "values": vm.item_ids, "index": idx},
        )

    def plan(self, slots, ctx: Ctx) -> Plan:
        order, other = resolve_order(self.name, slots, ctx)
        if other:
            return other
        assert order is not None
        changes = slots.get("changes") or []
        if not changes:
            return Ask(
                question=f"Which items in order {order.order_id} would you like to {self.verb}, "
                "and what would you like each changed to? Its items are:",
                details=F.numbered([F.item_text(i) for i in order.items]),
                slot="changes",
            )
        used: set[int] = set()
        swaps: list[tuple[int, str]] = []
        for idx, change in enumerate(changes):
            r = self._resolve_change(order, idx, change, used, ctx)
            if not isinstance(r, tuple):
                return r
            used.add(r[0])
            swaps.append(r)

        lines, diff = [], 0.0
        for line, new_id in swaps:
            old = order.items[line]
            new = ctx.store.get_product(old.product_id).variants[new_id]
            diff += new.price - old.price
            lines.append(
                f"{old.name}: {F.options_text(old.options)} → {F.options_text(new.options)}"
                f" ({F.usd(old.price)} → {F.usd(new.price)})"
            )
        diff = round(diff, 2)

        pm_id, ask = pick_payment(
            slots,
            ctx.user,
            allowed=None,
            question="Which payment method should be used for any price difference?",
        )
        if ask:
            return ask
        assert pm_id is not None
        pm = ctx.user.payment_methods[pm_id]
        if diff > 0 and not gift_card_covers(pm, diff):
            others = [p for p in ctx.user.payment_methods if p != pm_id]
            _, ask = pick_payment(
                {},
                ctx.user,
                allowed=others or None,
                preface=f"That gift card's balance doesn't cover the {F.usd(diff)} difference.",
                question="Which payment method should be used instead?",
            )
            assert ask
            return ask
        if diff > 0:
            lines.append(f"Price difference: charge {F.usd(diff)} to your {F.pm_text(pm)}")
            amount, label = diff, "Charge"
        elif diff < 0:
            lines.append(f"Price difference: refund {F.usd(-diff)} to your {F.pm_text(pm)}")
            amount, label = -diff, "Refund"
        else:
            lines.append(f"No price difference (payment method on file: {F.pm_text(pm)})")
            amount, label = None, None
        if self.name == "modify_order_items":
            lines.append("Items in this order can't be changed again after this.")
        else:
            lines.append(
                "You'll get an email on returning the original items. No new order is needed."
            )
        title = (
            f"Change items in order {order.order_id}"
            if self.name == "modify_order_items"
            else f"Exchange items in order {order.order_id}"
        )
        return Ready(
            summary=summary(title, lines, amount, label),
            call={
                "fn": self.fn,
                "args": {
                    "order_id": order.order_id,
                    "item_ids": [order.items[line].item_id for line, _ in swaps],
                    "new_item_ids": [new_id for _, new_id in swaps],
                    "payment_method_id": pm_id,
                },
            },
            result={"diff": diff, "pm": pm_id},
        )

    def result_text(self, returned, ready, ctx):
        pm = ctx.store.get_user(ctx.user.user_id).payment_methods[ready.result["pm"]]
        diff = ready.result["diff"]
        money = ""
        if diff > 0:
            money = f" {F.usd(diff)} will be charged to your {F.pm_text(pm)}."
        elif diff < 0:
            money = f" {F.usd(-diff)} will be refunded to your {F.pm_text(pm)}."
        if self.name == "modify_order_items":
            return (
                f"Done — the items in order {returned.order_id} have been updated.{money} "
                "This order's items can't be changed again."
            )
        return (
            f"Done — the exchange for order {returned.order_id} is requested.{money} "
            "You'll receive an email explaining how to return the original items."
        )


MODIFY_SPEC = SwapItems(
    "modify_order_items", "modify_pending_order_items", M.ITEMS_ONCE_REMINDER, "change"
)
EXCHANGE_SPEC = SwapItems(
    "exchange_items", "exchange_delivered_order_items", M.EXCHANGE_REMINDER, "exchange"
)
