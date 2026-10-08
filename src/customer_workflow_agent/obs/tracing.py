"""LangSmith tracing, switched on per chat run (no global environment variables).

Each graph run happens inside `Tracing.chat(chat_id)`. LangGraph then traces every node, and
the LLM calls (`traceable` in llm/service.py) nest under the node that made them. The chat id is
sent as `thread_id`, so LangSmith shows one chat as one thread.
"""

import logging
from contextlib import AbstractContextManager

import langsmith

from customer_workflow_agent.settings import Settings

log = logging.getLogger(__name__)


class Tracing:
    def __init__(self, settings: Settings) -> None:
        self.project = settings.langsmith_project
        self.client: langsmith.Client | None = None
        if settings.langsmith_tracing:
            if settings.langsmith_api_key is None:
                log.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is not set: tracing off")
            else:
                self.client = langsmith.Client(
                    api_key=settings.langsmith_api_key.get_secret_value()
                )
                log.info("LangSmith tracing on (project %s)", self.project)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def chat(self, chat_id: str) -> AbstractContextManager:
        # enabled=False also overrides any LANGSMITH_TRACING in the environment.
        return langsmith.tracing_context(
            enabled=self.enabled,
            client=self.client,
            project_name=self.project,
            metadata={"thread_id": chat_id},
        )

    def flush(self) -> None:
        if self.client is not None:
            self.client.flush()
