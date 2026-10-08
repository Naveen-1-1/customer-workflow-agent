"""Sees every single model attempt LiteLLM makes (retries and fallbacks included): records
the attempt metrics and logs failures.

LiteLLM's own loggers are turned down because they dump whole schemas on every fallback; this
callback logs one compact line per failed attempt instead.
"""

import logging
from datetime import datetime

import litellm
from litellm.integrations.custom_logger import CustomLogger

from customer_workflow_agent.obs.metrics import LLM_CALL_SECONDS, LLM_CALLS, outcome_of

log = logging.getLogger(__name__)

for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)


def _seconds(start: datetime | float, end: datetime | float) -> float:
    if isinstance(start, datetime) and isinstance(end, datetime):
        return (end - start).total_seconds()
    return float(end) - float(start)  # type: ignore[arg-type]


def first_line(error: BaseException | None, limit: int = 200) -> str:
    """LiteLLM's error texts run over several lines (and can include whole schemas)."""
    text = str(error or "").strip()
    return text.splitlines()[0][:limit] if text else ""


def _meta(kwargs: dict) -> dict:
    return (kwargs.get("litellm_params") or {}).get("metadata") or {}


def _model(kwargs: dict) -> str:
    return str(kwargs.get("model") or "?").removeprefix("openai/")


def _record(kwargs: dict, error: BaseException | None, seconds: float) -> None:
    model = _model(kwargs)
    LLM_CALLS.labels(model, _meta(kwargs).get("prompt", "?"), outcome_of(error)).inc()
    LLM_CALL_SECONDS.labels(model).observe(seconds)


class AttemptObserver(CustomLogger):
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        seconds = _seconds(start_time, end_time)
        _record(kwargs, None, seconds)
        meta = _meta(kwargs)
        log.debug(
            "LLM %s via %s (%s) ok in %.1fs",
            meta.get("prompt", "?"),
            meta.get("model_group", "?"),
            _model(kwargs),
            seconds,
        )

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        seconds = _seconds(start_time, end_time)
        error = kwargs.get("exception")
        _record(kwargs, error, seconds)
        meta = _meta(kwargs)
        log.warning(
            "LLM %s via %s (%s) failed after %.1fs: %s: %s",
            meta.get("prompt", "?"),
            meta.get("model_group", "?"),
            _model(kwargs),
            seconds,
            type(error).__name__,
            first_line(error),
        )


OBSERVER = AttemptObserver()


def install() -> None:
    """Register the observer once per process."""
    if OBSERVER not in litellm.callbacks:
        litellm.callbacks.append(OBSERVER)
