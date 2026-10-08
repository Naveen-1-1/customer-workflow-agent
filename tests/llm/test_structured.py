import asyncio

import litellm
import pytest
from litellm.integrations.custom_logger import CustomLogger
from litellm.router import Router
from prometheus_client import REGISTRY

from customer_workflow_agent.llm.client import FALLBACK, PRIMARY, build_router
from customer_workflow_agent.llm.guard import safe_reply
from customer_workflow_agent.llm.schemas import Classification
from customer_workflow_agent.llm.service import Prompt, RouterLLMService
from customer_workflow_agent.llm.structured import LLMUnavailable, parse_or_raise, response_format
from customer_workflow_agent.settings import Settings

GOOD = '{"requests": [], "goodbye": true, "about_other_person": false}'
PROMPT = Prompt("system", "Customer message: bye", "bye")


class Attempts(CustomLogger):
    """Records every model call LiteLLM makes: (model group, error type or "ok")."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []
        self.params: list[dict] = []

    def _group(self, kwargs) -> str:
        return (kwargs.get("litellm_params") or {}).get("metadata", {}).get("model_group", "?")

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self.seen.append((self._group(kwargs), "ok"))
        self.params.append(kwargs.get("optional_params") or {})

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self.seen.append((self._group(kwargs), type(kwargs.get("exception")).__name__))


# LiteLLM copies callbacks into its own lists the first time it uses them, so one recorder is
# registered for the whole module and cleared before each test.
_ATTEMPTS = Attempts()


@pytest.fixture
def attempts(monkeypatch):
    if _ATTEMPTS not in litellm.callbacks:
        litellm.callbacks.append(_ATTEMPTS)
    _ATTEMPTS.seen.clear()
    _ATTEMPTS.params.clear()
    monkeypatch.setattr(Router, "_time_to_sleep_before_retry", lambda *a, **k: 0)
    return _ATTEMPTS


def service(primary: dict, fallback: dict | None = None, **settings) -> RouterLLMService:
    s = Settings(_env_file=None, nvidia_api_key="test", llm_requests_per_minute=6000, **settings)
    router = build_router(s, {PRIMARY: primary, FALLBACK: fallback or {"mock_response": GOOD}})
    return RouterLLMService(s, router=router)


async def settle():
    # LiteLLM runs its logging callbacks in the background.
    await asyncio.sleep(0.2)


async def test_parses_valid_json_and_turns_thinking_off(attempts):
    out = await service({"mock_response": GOOD}).extract(Classification, PROMPT)
    assert isinstance(out, Classification) and out.goodbye
    await settle()
    assert attempts.seen == [(PRIMARY, "ok")]
    params = attempts.params[0]
    assert params["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert params["temperature"] == 0.1


@pytest.mark.parametrize("bad", ["not json", '{"requests": "oops"}'], ids=["not-json", "wrong"])
async def test_bad_output_is_retried_then_fallback(attempts, bad):
    out = await service({"mock_response": bad}).extract(Classification, PROMPT)
    assert out.goodbye
    await settle()
    assert [g for g, _ in attempts.seen] == [PRIMARY] * 3 + [FALLBACK]


@pytest.mark.parametrize("error", ["litellm.RateLimitError", "litellm.InternalServerError"])
async def test_rate_limits_and_server_errors_are_retried_then_fallback(attempts, error):
    out = await service({"mock_response": error}).extract(Classification, PROMPT)
    assert out.goodbye
    await settle()
    assert attempts.seen[:3] == [(PRIMARY, attempts.seen[0][1])] * 3
    assert attempts.seen[3] == (FALLBACK, "ok")


async def test_timeout_goes_straight_to_fallback(attempts):
    llm = service({"mock_response": GOOD, "mock_timeout": True}, llm_timeout_s=0.05)
    assert (await llm.extract(Classification, PROMPT)).goodbye
    await settle()
    assert attempts.seen == [(PRIMARY, "Timeout"), (FALLBACK, "ok")]


async def test_everything_failing_raises_unavailable(attempts):
    llm = service(
        {"mock_response": "litellm.InternalServerError"},
        {"mock_response": "litellm.InternalServerError"},
    )
    with pytest.raises(LLMUnavailable):
        await llm.extract(Classification, PROMPT)


async def test_free_text_mode(attempts):
    out = await service({"mock_response": "Sure — which order is it?"}).write(PROMPT)
    assert out == "Sure — which order is it?"
    await settle()
    assert "response_format" not in attempts.params[0]
    assert attempts.params[0]["temperature"] == 0.5


def test_parse_strips_code_fences():
    assert parse_or_raise(Classification, f"```json\n{GOOD}\n```").goodbye


def test_response_format_is_strict_and_forbids_extra_keys():
    rf = response_format(Classification)["json_schema"]
    assert rf["strict"] is True and rf["schema"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "text,ok",
    [
        ("Could you tell me which order you mean?", True),
        ("Your order #W1234567 is on its way", False),
        ("That will be $12.50", False),
        ("Your zip 94105 matches", False),
        ("I've refunded you", False),
        ("Email me at a@b.com", False),
        ("", False),
    ],
)
def test_guard(text, ok):
    assert (safe_reply(text) is not None) == ok


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_metrics_count_attempts_and_who_answered(attempts):
    label = PROMPT.label
    s = Settings(_env_file=None)
    rate_limited = dict(model=s.llm_primary_model, prompt=label, outcome="rate_limited")
    before = (
        sample("llm_calls_total", **rate_limited),
        sample("llm_requests_total", prompt=label, served_by="fallback"),
        sample("llm_requests_total", prompt=label, served_by="none"),
    )
    await service({"mock_response": "litellm.RateLimitError"}).extract(Classification, PROMPT)
    with pytest.raises(LLMUnavailable):
        await service(
            {"mock_response": "litellm.InternalServerError"},
            {"mock_response": "litellm.InternalServerError"},
        ).extract(Classification, PROMPT)
    await settle()
    assert sample("llm_calls_total", **rate_limited) - before[0] == 3
    assert sample("llm_requests_total", prompt=label, served_by="fallback") - before[1] == 1
    assert sample("llm_requests_total", prompt=label, served_by="none") - before[2] == 1
