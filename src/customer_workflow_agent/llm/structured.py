"""Structured extraction with retries and a fallback model.

We bind OpenAI-style `response_format` (strict JSON schema) directly instead of using
`ChatNVIDIA.with_structured_output`: that helper retries up to three request formats on any
error (a rate-limited call becomes three requests) and returns None on a parse failure, which
would silently skip the fallback. Here bad output raises, so retry and fallback both work.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


class LLMError(Exception):
    """One failed LLM call."""


class RateLimited(LLMError):
    pass


class TransientLLMError(LLMError):
    pass


class FatalLLMError(LLMError):
    pass


class LLMTimeout(LLMError):
    """The model didn't answer in time: go to the fallback model rather than wait again."""


class LLMUnavailable(Exception):
    """Every attempt on every model failed."""


RETRYABLE = (RateLimited, TransientLLMError, OutputParserException)
_STATUS = re.compile(r"^\[(\d{3})\]")


def classify_error(exc: BaseException) -> LLMError:
    """ChatNVIDIA raises plain `Exception("[429] ...")`; sort errors by retryability."""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, TimeoutError):
        return LLMTimeout(str(exc) or "timed out")
    if isinstance(exc, (ConnectionError, OSError)):
        return TransientLLMError(str(exc) or type(exc).__name__)
    name = type(exc).__name__
    if "Timeout" in name or "Connect" in name or "ClientError" in name:
        return TransientLLMError(f"{name}: {exc}")
    match = _STATUS.match(str(exc))
    if match:
        status = int(match.group(1))
        if status == 429:
            return RateLimited(str(exc))
        if status >= 500 or status == 408:
            return TransientLLMError(str(exc))
    return FatalLLMError(str(exc))


def response_format(schema: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema.__name__,
            "schema": schema.model_json_schema(),
            "strict": True,
        },
    }


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_or_raise[T: BaseModel](schema: type[T], text: str) -> T:
    cleaned = _FENCE.sub("", text.strip())
    try:
        return schema.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise OutputParserException(f"Bad {schema.__name__} output: {e}", llm_output=text) from e


def _content(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):  # content blocks
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _attempt(
    model: BaseChatModel,
    *,
    schema: type[BaseModel] | None,
    max_attempts: int,
    timeout_s: float,
    backoff_initial: float,
) -> Runnable:
    bound = model.bind(response_format=response_format(schema)) if schema else model
    name = getattr(model, "model", None) or type(model).__name__
    task = schema.__name__ if schema else "reply"

    async def call(messages: list[BaseMessage]) -> Any:
        start = time.monotonic()
        try:
            reply = await asyncio.wait_for(bound.ainvoke(messages), timeout=timeout_s)
            text = _content(reply)
            return parse_or_raise(schema, text) if schema else text
        except Exception as e:
            err = e if isinstance(e, OutputParserException) else classify_error(e)
            log.warning(
                "LLM %s (%s) failed after %.1fs: %s: %s",
                name,
                task,
                time.monotonic() - start,
                type(err).__name__,
                str(err)[:200],
            )
            if err is e:
                raise
            raise err from e

    return RunnableLambda(call).with_retry(
        retry_if_exception_type=RETRYABLE,
        stop_after_attempt=max_attempts,
        wait_exponential_jitter=True,
        exponential_jitter_params={
            "initial": backoff_initial,
            "max": 20,
            "jitter": backoff_initial,
        },
    )


def build_runnable(
    models: list[BaseChatModel],
    *,
    schema: type[BaseModel] | None,
    max_attempts: int = 3,
    timeout_s: float = 30.0,
    backoff_initial: float = 1.0,
) -> Runnable:
    """Primary model with retries, then each fallback model with retries."""
    attempts = [
        _attempt(
            m,
            schema=schema,
            max_attempts=max_attempts,
            timeout_s=timeout_s,
            backoff_initial=backoff_initial,
        )
        for m in models
    ]
    if len(attempts) == 1:
        return attempts[0]
    return attempts[0].with_fallbacks(attempts[1:], exceptions_to_handle=(Exception,))
