"""Helpers shared by the write subgraphs: finding the order, payment choices, summaries."""

from customer_workflow_agent.graph.action import Ask, Check, Ctx, Found, Step
from customer_workflow_agent.llm.schemas import Turn
from customer_workflow_agent.policy.rules import (
    ACTION_VERB,
    REQUIRED_STATUS,
    Denial,
    check_owner,
    check_status,
    eligible_orders,
)
from customer_workflow_agent.resolve.ids import normalize_order_id
from customer_workflow_agent.resolve.payments import has_reference, payment_candidates
from customer_workflow_agent.store import StoreError
from customer_workflow_agent.store.models import Order, User
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates.denials import denial_text

ORDER_QUESTION = {
    "cancel_order": "Which order would you like to cancel?",
    "modify_order_address": "Which order's shipping address would you like to change?",
    "modify_order_payment": "Which order's payment method would you like to change?",
    "modify_order_items": "Which order would you like to change items in?",
    "return_items": "Which order would you like to return items from?",
    "exchange_items": "Which order would you like to exchange items from?",
}


def merge_order_id(slots: dict, turn: Turn, text: str) -> dict:
    oid = normalize_order_id(turn.order_id, text)
    if oid and oid != slots.get("order_id"):
        # A different order: anything item-specific no longer applies.
        keep = {k: v for k, v in slots.items() if k in ("reason", "payment", "refund_to")}
        return {**keep, "order_id": oid}
    return slots


def find_order(action: str) -> Step:
    """Which order: one the customer named and owns, or ask them to pick one of theirs.

    An order on another account reads exactly like a missing one.
    """

    def step(slots: dict, found: dict, ctx: Ctx) -> Check:
        candidates = eligible_orders(action, ctx.store.get_user_orders(ctx.user.user_id))
        oid = slots.get("order_id")
        preface = ""
        if oid:
            try:
                order = ctx.store.get_order(oid)
            except StoreError:
                order = None
            denial = check_owner(order, ctx.user.user_id, oid)
            if denial is None:
                return Found({"order_id": oid})
            preface = denial_text(denial.code, **denial.params)
            if not candidates:
                return denial
        if not candidates:
            return Denial("no_eligible_orders", {"action_verb": ACTION_VERB[action]})
        return Ask(
            question=ORDER_QUESTION[action],
            preface=preface,
            details=F.numbered([F.order_summary_line(o) for o in candidates]),
            slot="order_id",
            choices={"kind": "order", "values": [o.order_id for o in candidates], "index": None},
        )

    return step


def check_order_status(action: str) -> Step:
    """Pending for cancel/modify, delivered for return/exchange (policy)."""

    def step(slots: dict, found: dict, ctx: Ctx) -> Check:
        return check_status(action, the_order(found, ctx)) or Found()

    return step


def order_steps(action: str) -> list[tuple[str, Step]]:
    """find_order → check_pending / check_delivered."""
    return [
        ("find_order", find_order(action)),
        (f"check_{REQUIRED_STATUS[action]}", check_order_status(action)),
    ]


def the_order(found: dict, ctx: Ctx) -> Order:
    """The order `find_order` settled on."""
    return ctx.store.get_order(found["order_id"])


def pick_payment(
    slots: dict,
    user: User,
    *,
    allowed: list[str] | None,
    question: str,
    slot: str = "payment",
    picked_key: str = "payment_method_id",
    ref_key: str = "payment",
    choice_kind: str = "payment",
    preface: str = "",
) -> tuple[str | None, Ask | None]:
    """A payment method id from a pick or a reference, or a question listing the options."""
    pool = allowed if allowed is not None else list(user.payment_methods)
    picked = slots.get(picked_key)
    if picked in pool:
        return picked, None
    ref = slots.get(ref_key)
    cands = payment_candidates(ref, user, pool) if has_reference(ref) else []
    if len(cands) == 1 and not preface:
        return cands[0], None
    options = cands if len(cands) > 1 else pool
    if has_reference(ref) and not cands and not preface:
        preface = "I couldn't match that to a payment method I can use here."
    return None, Ask(
        question=question,
        preface=preface,
        details=F.numbered([F.pm_text(user.payment_methods[p]) for p in options]),
        slot=slot,
        choices={"kind": choice_kind, "values": options, "index": None},
    )


def summary(
    title: str, lines: list[str], amount: float | None = None, label: str | None = None
) -> dict:
    return {"title": title, "lines": lines, "amount": amount, "amount_label": label}


def catalog_for(order: Order, ctx: Ctx) -> list[dict]:
    """The order's items with each product's option names/values (public catalog data)."""
    from customer_workflow_agent.resolve.variants import option_schema

    out = []
    for item in order.items:
        product = ctx.store.get_product(item.product_id)
        out.append(
            {
                "product": item.name,
                "current_options": item.options,
                "available_option_values": option_schema(product),
            }
        )
    return out
