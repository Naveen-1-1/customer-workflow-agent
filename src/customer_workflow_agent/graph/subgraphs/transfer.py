"""Transfer to a human agent: record it, send the policy's exact message, end the chat."""

from langgraph.graph import END, START, StateGraph

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.state import ChatState, Outcome, agent_msg
from customer_workflow_agent.templates.messages import TRANSFER_MESSAGE


def transfer_summary(state: ChatState) -> str:
    req = state["current"]
    done = [
        f"{o['type']} {o.get('order_id') or ''}: {o['status']}".strip()
        for o in state.get("outcomes") or []
    ]
    parts = [f"User: {state.get('user_id') or 'not verified'}", f"Reason: {req['details']}"]
    if done:
        parts.append("Handled in this chat: " + "; ".join(done))
    return ". ".join(parts)


def build_transfer_subgraph(deps: Deps):
    async def run(state: ChatState) -> dict:
        req = state["current"]
        deps.store.transfer_to_human_agents(
            state.get("user_id"), transfer_summary(state), op_id=f"{req['id']}:transfer"
        )
        outcome: Outcome = {
            "request_id": req["id"],
            "type": "transfer",
            "order_id": None,
            "status": "transferred",
            "code": None,
        }
        return {
            "messages": [agent_msg(TRANSFER_MESSAGE, "transfer")],
            "current": None,
            "work": {},
            "queue": [],
            "phase": "ended",
            "end_reason": "transferred",
            "outcomes": [*(state.get("outcomes") or []), outcome],
        }

    g = StateGraph(ChatState)
    g.add_node("transfer", run)
    g.add_edge(START, "transfer")
    g.add_edge("transfer", END)
    return g.compile()
