"""The interface the workflow uses to talk to an LLM, and the NVIDIA implementation."""

from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from customer_workflow_agent.llm.client import build_chat_models, make_rate_limiter
from customer_workflow_agent.llm.structured import LLMUnavailable, build_runnable
from customer_workflow_agent.settings import Settings


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    # The customer's own words this prompt is about (used by the scripted test LLM).
    customer_text: str = ""


class LLMService(Protocol):
    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        """Fill `schema` from the prompt. Raises LLMUnavailable when every attempt fails."""
        ...

    async def write(self, prompt: Prompt) -> str:
        """Write a short free-text reply. Raises LLMUnavailable when every attempt fails."""
        ...


class NvidiaLLMService:
    def __init__(self, settings: Settings, rate_limiter: InMemoryRateLimiter | None = None):
        limiter = rate_limiter or make_rate_limiter(settings)
        self._settings = settings
        self._extract_models = build_chat_models(
            settings, temperature=settings.llm_temperature_extract, rate_limiter=limiter
        )
        self._reply_models = build_chat_models(
            settings, temperature=settings.llm_temperature_reply, rate_limiter=limiter
        )
        self._runnables: dict[type[BaseModel] | None, Runnable] = {}

    def _runnable(self, schema: type[BaseModel] | None) -> Runnable:
        if schema not in self._runnables:
            self._runnables[schema] = build_runnable(
                self._extract_models if schema else self._reply_models,
                schema=schema,
                max_attempts=self._settings.llm_max_attempts,
                timeout_s=self._settings.llm_timeout_s,
            )
        return self._runnables[schema]

    async def _run(self, schema: type[BaseModel] | None, prompt: Prompt):
        messages = [SystemMessage(prompt.system), HumanMessage(prompt.user)]
        try:
            return await self._runnable(schema).ainvoke(messages)
        except Exception as e:
            raise LLMUnavailable(str(e)) from e

    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        return await self._run(schema, prompt)

    async def write(self, prompt: Prompt) -> str:
        return await self._run(None, prompt)


class UnavailableLLM:
    """Used when no NVIDIA key is configured: every call fails, so the chat apologizes."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        raise LLMUnavailable(self.reason)

    async def write(self, prompt: Prompt) -> str:
        raise LLMUnavailable(self.reason)
