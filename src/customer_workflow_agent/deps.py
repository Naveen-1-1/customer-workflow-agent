from dataclasses import dataclass

from customer_workflow_agent.llm.service import LLMService
from customer_workflow_agent.settings import Settings
from customer_workflow_agent.store import RetailStore


@dataclass
class Deps:
    """What graph nodes need, closed over when the graph is built."""

    store: RetailStore
    llm: LLMService
    settings: Settings
