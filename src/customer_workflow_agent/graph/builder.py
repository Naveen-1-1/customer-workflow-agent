"""Build the compiled graph and read chats back out of the checkpointer."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.parent import build_parent_graph
from customer_workflow_agent.settings import Settings


def build_graph(deps: Deps, checkpointer: BaseCheckpointSaver):
    return build_parent_graph(deps).compile(checkpointer=checkpointer)


def run_config(thread_id: str, settings: Settings) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.recursion_limit}


def deepest_values(snapshot: StateSnapshot) -> dict:
    """While paused inside a subgraph, the newest messages live in the subgraph's state."""
    values = dict(snapshot.values or {})
    current = snapshot
    while True:
        child = next(
            (
                t.state
                for t in current.tasks
                if isinstance(t.state, StateSnapshot) and (t.interrupts or t.state.tasks)
            ),
            None,
        )
        if child is None:
            return values
        values = {**values, **(child.values or {})}
        current = child


async def read_chat(graph, config: dict) -> tuple[dict, list]:
    """(state values incl. in-progress subgraph messages, pending interrupts)."""
    snapshot = await graph.aget_state(config, subgraphs=True)
    return deepest_values(snapshot), list(snapshot.interrupts)
