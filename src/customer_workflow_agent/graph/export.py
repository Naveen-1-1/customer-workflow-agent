"""Export the compiled graph as Mermaid: `python -m customer_workflow_agent.graph.export`.

Writes docs/workflow-graph.mmd, so the diagram always matches the code.
"""

from langgraph.checkpoint.memory import InMemorySaver

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.builder import build_graph
from customer_workflow_agent.llm.service import UnavailableLLM
from customer_workflow_agent.settings import PROJECT_ROOT, Settings
from customer_workflow_agent.store import InMemoryBackend, RetailStore
from customer_workflow_agent.store.models import WorkingDB


def compiled_graph():
    """The real graph, built without data or an LLM (structure only)."""
    store = RetailStore(InMemoryBackend(WorkingDB(products={}, users={}, orders={})))
    deps = Deps(store=store, llm=UnavailableLLM("export"), settings=Settings(_env_file=None))
    return build_graph(deps, InMemorySaver())


def main() -> None:
    out = PROJECT_ROOT / "docs" / "workflow-graph.mmd"
    out.parent.mkdir(exist_ok=True)
    out.write_text(compiled_graph().get_graph(xray=True).draw_mermaid())
    print(f"wrote {out.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
