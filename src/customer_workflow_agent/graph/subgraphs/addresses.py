"""Change a pending order's shipping address, or the account's default address."""

from customer_workflow_agent.graph.action import Ask, Check, Ctx, Found, Ready
from customer_workflow_agent.graph.subgraphs.base import (
    merge_order_id,
    order_steps,
    summary,
    the_order,
)
from customer_workflow_agent.llm.schemas import AddressTurn
from customer_workflow_agent.resolve.addresses import complete_address, merge_fields, same_address
from customer_workflow_agent.store.models import UserAddress
from customer_workflow_agent.templates import format as F


def _merge_address(slots: dict, turn: AddressTurn) -> dict:
    slots = dict(slots)
    if turn.use_profile_address:
        slots["use_profile_address"] = True
    if turn.address:
        slots["address"] = merge_fields(slots.get("address"), turn.address.model_dump())
        slots["use_profile_address"] = False
    return slots


def _new_address(slots: dict, ctx: Ctx, current: UserAddress, what: str) -> UserAddress | Ask:
    if slots.get("use_profile_address"):
        address = ctx.user.address
    else:
        address, missing = complete_address(slots.get("address") or {})
        if address is None:
            asked_before = bool(slots.get("address"))
            question = (
                f"What's the {' and '.join(missing)} for the new address?"
                if asked_before
                else f"What's the new {what}? Please include the street, city, state and zip code."
            )
            return Ask(question=question, slot="address")
    if same_address(address, current):
        return Ask(
            question=f"What new {what} would you like?",
            preface=f"That's already the {what}: {F.address_text(current)}.",
            slot="address",
        )
    return address


def check_new_shipping_address(slots: dict, found: dict, ctx: Ctx) -> Check:
    """A complete address, different from where the order ships now."""
    new = _new_address(slots, ctx, the_order(found, ctx).address, "shipping address")
    return new if isinstance(new, Ask) else Found({"address": new.model_dump()})


def check_new_default_address(slots: dict, found: dict, ctx: Ctx) -> Check:
    """A complete address, different from the current default."""
    slots = {**slots, "use_profile_address": False}
    new = _new_address(slots, ctx, ctx.user.address, "default address")
    return new if isinstance(new, Ask) else Found({"address": new.model_dump()})


class ModifyOrderAddress:
    name = "modify_order_address"
    turn_schema = AddressTurn
    reminder = None
    needs_approval_step = False
    steps = (
        *order_steps("modify_order_address"),
        ("check_new_address", check_new_shipping_address),
    )

    def merge(self, slots, turn, text, ctx):
        return _merge_address(merge_order_id(slots, turn, text), turn)

    def catalog(self, slots, ctx):
        return None

    def prepare(self, slots, found, ctx: Ctx) -> Ready:
        order = the_order(found, ctx)
        new = UserAddress(**found["address"])
        return Ready(
            summary=summary(
                f"Change shipping address for order {order.order_id}",
                [f"From: {F.address_text(order.address)}", f"To: {F.address_text(new)}"],
            ),
            call={
                "fn": "modify_pending_order_address",
                "args": {"order_id": order.order_id, "address": new.model_dump()},
            },
        )

    def result_text(self, returned, ready, ctx):
        return (
            f"Done — order {returned.order_id} will now ship to {F.address_text(returned.address)}."
        )


class ModifyDefaultAddress:
    name = "modify_default_address"
    turn_schema = AddressTurn
    reminder = None
    needs_approval_step = False
    steps = (("check_new_address", check_new_default_address),)

    def merge(self, slots, turn, text, ctx):
        return _merge_address(slots, turn)

    def catalog(self, slots, ctx):
        return None

    def prepare(self, slots, found, ctx: Ctx) -> Ready:
        new = UserAddress(**found["address"])
        return Ready(
            summary=summary(
                "Change your default address",
                [f"From: {F.address_text(ctx.user.address)}", f"To: {F.address_text(new)}"],
            ),
            call={
                "fn": "modify_user_address",
                "args": {"user_id": ctx.user.user_id, "address": new.model_dump()},
            },
        )

    def result_text(self, returned, ready, ctx):
        return f"Done — your default address is now {F.address_text(returned.address)}."


ORDER_SPEC = ModifyOrderAddress()
DEFAULT_SPEC = ModifyDefaultAddress()
