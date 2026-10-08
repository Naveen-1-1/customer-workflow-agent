"""The interface the workflow uses to talk to an LLM, and the LiteLLM implementation."""

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.rate_limiters import InMemoryRateLimiter
from langsmith import traceable
from litellm import Router
from pydantic import BaseModel

from customer_workflow_agent.llm.client import PRIMARY, build_router, make_rate_limiter
from customer_workflow_agent.llm.observe import first_line
from customer_workflow_agent.llm.structured import (
    LLMUnavailable,
    content,
    parse_or_raise,
    response_format,
)
from customer_workflow_agent.obs.metrics import (
    LLM_RATE_LIMITER_WAIT,
    LLM_REQUESTS,
    start_llm_series,
)
from customer_workflow_agent.settings import Settings

log = logging.getLogger(__name__)


@traceable(run_type="llm", process_inputs=lambda inputs: {"messages": inputs.get("messages")})
async def _traced_completion(router: Router, **kwargs):
    """One logical LLM call (LiteLLM's retries and fallback happen inside), as a LangSmith span."""
    return await router.acompletion(**kwargs)


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    # The customer's own words this prompt is about (used by the scripted test LLM).
    customer_text: str = ""
    # Which prompt this is and its wording version (see llm/prompts.py), for traces and metrics.
    name: str = "prompt"
    version: int = 0

    @property
    def label(self) -> str:
        return f"{self.name}@v{self.version}"


class LLMService(Protocol):
    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        """Fill `schema` from the prompt. Raises LLMUnavailable when every attempt fails."""
        ...

    async def write(self, prompt: Prompt) -> str:
        """Write a short free-text reply. Raises LLMUnavailable when every attempt fails."""
        ...


class RouterLLMService:
    """Calls the primary model through the LiteLLM Router (which handles retries and fallback),
    after waiting for the shared rate limiter."""

    def __init__(
        self,
        settings: Settings,
        router: Router | None = None,
        rate_limiter: InMemoryRateLimiter | None = None,
        model: str = PRIMARY,  # FALLBACK calls only the fallback model (live checks)
    ):
        self._settings = settings
        self._model = model
        self._router = router or build_router(settings)
        self._limiter = rate_limiter or make_rate_limiter(settings)
        from customer_workflow_agent.llm.prompts import VERSIONS  # prompts imports this module

        start_llm_series(
            [f"{name}@v{version}" for name, version in VERSIONS.items()],
            [settings.llm_primary_model, settings.llm_fallback_model],
        )

    async def _complete(self, prompt: Prompt, schema: type[BaseModel] | None) -> str:
        s = self._settings
        extra = {"response_format": response_format(schema)} if schema else {}
        waited = time.monotonic()
        await self._limiter.aacquire()
        LLM_RATE_LIMITER_WAIT.observe(time.monotonic() - waited)
        try:
            response = await _traced_completion(
                self._router,
                model=self._model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                temperature=s.llm_temperature_extract if schema else s.llm_temperature_reply,
                metadata={"prompt": prompt.label},  # read by llm/observe.py
                langsmith_extra={
                    "name": prompt.name,
                    "metadata": {
                        "prompt": prompt.name,
                        "prompt_version": prompt.version,
                        "schema": schema.__name__ if schema else None,
                    },
                },
                **extra,
            )
        except Exception as e:
            LLM_REQUESTS.labels(prompt.label, "none").inc()
            log.warning(
                "LLM %s: every model failed: %s: %s", prompt.label, type(e).__name__, first_line(e)
            )
            raise LLMUnavailable(str(e)) from e
        served_by = "primary" if response.model == s.llm_primary_model else "fallback"
        LLM_REQUESTS.labels(prompt.label, served_by).inc()
        return content(response)

    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        text = await self._complete(prompt, schema)
        try:
            return parse_or_raise(schema, text)
        except OutputParserException as e:
            raise LLMUnavailable(str(e)) from e

    async def write(self, prompt: Prompt) -> str:
        return await self._complete(prompt, None)


class UnavailableLLM:
    """Used when no NVIDIA key is configured: every call fails, so the chat apologizes."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        raise LLMUnavailable(self.reason)

    async def write(self, prompt: Prompt) -> str:
        raise LLMUnavailable(self.reason)
