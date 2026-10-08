"""Read-only questions about the customer's own orders and profile, or products' options.
The LLM only picks the topic; every answer is a template over store data."""

from langgraph.graph import END, START, StateGraph

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.state import ChatState, Outcome, agent_msg
from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.schemas import InfoQuery
from customer_workflow_agent.llm.structured import LLMUnavailable
from customer_workflow_agent.resolve.ids import normalize_order_id
from customer_workflow_agent.resolve.text import match_one
from customer_workflow_agent.store import RetailStore, StoreError
from customer_workflow_agent.store.models import Order, User
from customer_workflow_agent.templates import format as F
from customer_workflow_agent.templates import messages as M
from customer_workflow_agent.templates.denials import denial_text

NO_INFO = "I'm sorry, I don't have that information."


def order_details(order: Order, user: User) -> str:
    lines = [f"Order {order.order_id} — status: {order.status}"]
    lines += [f"• {F.item_text(i)}" for i in order.items]
    paid = [p for p in order.payment_history if p.transaction_type == "payment"]
    refunds = [p for p in order.payment_history if p.transaction_type == "refund"]
    for p in paid:
        pm = user.payment_methods.get(p.payment_method_id)
        lines.append(f"Paid {F.usd(p.amount)}" + (f" with your {F.pm_text(pm)}" if pm else ""))
    for p in refunds:
        pm = user.payment_methods.get(p.payment_method_id)
        lines.append(f"Refunded {F.usd(p.amount)}" + (f" to your {F.pm_text(pm)}" if pm else ""))
    lines.append(f"Shipping to: {F.address_text(order.address)}")
    tracking = [t for f in order.fulfillments for t in f.tracking_id]
    if tracking:
        lines.append(f"Tracking: {', '.join(tracking)}")
    return "\n".join(lines)


def answer(query: InfoQuery, text: str, store: RetailStore, user: User) -> str:
    orders = store.get_user_orders(user.user_id)
    if query.topic == "order_details":
        ids = [oid for raw in query.order_ids if (oid := normalize_order_id(raw, text))]
        if not ids:
            query = query.model_copy(update={"topic": "order_list"})
        else:
            parts = []
            for oid in ids:
                try:
                    order = store.get_order(oid)
                except StoreError:
                    order = None
                if order is None or order.user_id != user.user_id:
                    parts.append(denial_text("order_not_found", order_id=oid))
                else:
                    parts.append(order_details(order, user))
            return "\n\n".join(parts)
    if query.topic == "order_list":
        if not orders:
            return "You don't have any orders on your account."
        return "Your orders:\n" + F.numbered([F.order_summary_line(o) for o in orders])
    if query.topic == "profile":
        return (
            f"Name: {user.name.first_name} {user.name.last_name}\nEmail: {user.email}\n"
            f"Default address: {F.address_text(user.address)}"
        )
    if query.topic == "payment_methods":
        return "Your payment methods:\n" + "\n".join(
            f"• {F.pm_text(pm)}" for pm in user.payment_methods.values()
        )
    if query.topic == "product_variants" and query.product:
        types = store.list_all_product_types()
        name = match_one(query.product, types)
        if name is None:
            return NO_INFO
        product = store.get_product(types[name])
        available = [v for v in product.variants.values() if v.available]
        head = (
            f"{product.name}: {len(available)} of {len(product.variants)} options are "
            "available right now."
        )
        return head + (
            "\n" + F.numbered([F.variant_text(v) for v in available]) if available else ""
        )
    return NO_INFO


def build_info_subgraph(deps: Deps):
    async def run(state: ChatState) -> dict:
        req = state["current"]
        text = req["source_text"]
        user = deps.store.get_user(state["user_id"])
        try:
            query = await deps.llm.extract(
                InfoQuery,
                prompts.info(
                    f"{text}\n(Question: {req['details']})",
                    list(deps.store.list_all_product_types()),
                ),
            )
            reply, status = answer(query, text, deps.store, user), "answered"
        except LLMUnavailable:
            reply, status = M.LLM_TROUBLE, "failed"
        outcome: Outcome = {
            "request_id": req["id"],
            "type": "info",
            "order_id": None,
            "status": status,
            "code": None,
        }
        return {
            "messages": [agent_msg(reply, "info")],
            "current": None,
            "work": {},
            "outcomes": [*(state.get("outcomes") or []), outcome],
        }

    g = StateGraph(ChatState)
    g.add_node("answer", run)
    g.add_edge(START, "answer")
    g.add_edge("answer", END)
    return g.compile()
