import asyncio
from typing import Any

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from customer_workflow_agent.llm.guard import safe_reply
from customer_workflow_agent.llm.schemas import Classification
from customer_workflow_agent.llm.structured import (
    FatalLLMError,
    LLMTimeout,
    RateLimited,
    TransientLLMError,
    build_runnable,
    classify_error,
    parse_or_raise,
    response_format,
)

GOOD = '{"requests": [], "goodbye": true, "about_other_person": false}'


class ScriptModel(BaseChatModel):
    """Returns (or raises) the scripted items in order and records request kwargs."""

    script: list[Any]
    seen_kwargs: list[dict]

    @property
    def _llm_type(self) -> str:
        return "script"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen_kwargs.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=item))])


def run(models, schema=Classification, attempts=3):
    r = build_runnable(models, schema=schema, max_attempts=attempts, backoff_initial=0.001)
    return r.ainvoke([HumanMessage("bye")])


async def test_parses_valid_json_and_sends_strict_schema():
    m = ScriptModel(script=[GOOD], seen_kwargs=[])
    out = await run([m])
    assert isinstance(out, Classification) and out.goodbye
    rf = m.seen_kwargs[0]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True


async def test_bad_json_is_retried():
    m = ScriptModel(script=["not json", '{"requests": []}', GOOD], seen_kwargs=[])
    assert (await run([m])).goodbye
    assert len(m.seen_kwargs) == 3


async def test_rate_limit_retried_then_fallback_used():
    primary = ScriptModel(script=[Exception("[429] Too Many Requests")] * 3, seen_kwargs=[])
    fallback = ScriptModel(script=[GOOD], seen_kwargs=[])
    assert (await run([primary, fallback])).goodbye
    assert len(primary.seen_kwargs) == 3 and len(fallback.seen_kwargs) == 1


async def test_fatal_error_skips_retries_but_falls_back():
    primary = ScriptModel(script=[Exception("[400] Bad Request")], seen_kwargs=[])
    fallback = ScriptModel(script=[GOOD], seen_kwargs=[])
    assert (await run([primary, fallback])).goodbye
    assert len(primary.seen_kwargs) == 1


async def test_everything_fails_raises():
    primary = ScriptModel(script=["x"] * 2, seen_kwargs=[])
    fallback = ScriptModel(script=["y"] * 2, seen_kwargs=[])
    with pytest.raises(OutputParserException):
        await run([primary, fallback], attempts=2)


async def test_free_text_mode():
    m = ScriptModel(script=["Sure — which order is it?"], seen_kwargs=[])
    out = await run([m], schema=None)
    assert out == "Sure — which order is it?"
    assert "response_format" not in m.seen_kwargs[0]


def test_classify_error():
    assert isinstance(classify_error(Exception("[429] slow down")), RateLimited)
    assert isinstance(classify_error(Exception("[503] unavailable")), TransientLLMError)
    assert isinstance(classify_error(TimeoutError()), LLMTimeout)
    assert isinstance(classify_error(Exception("[401] Unauthorized")), FatalLLMError)


def test_parse_strips_code_fences():
    assert parse_or_raise(Classification, f"```json\n{GOOD}\n```").goodbye


def test_response_format_forbids_extra_keys():
    schema = response_format(Classification)["json_schema"]["schema"]
    assert schema["additionalProperties"] is False


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


class SlowModel(ScriptModel):
    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_kwargs.append(kwargs)
        await asyncio.sleep(1)
        return self._generate(messages, **kwargs)


async def test_slow_primary_goes_straight_to_fallback():
    slow = SlowModel(script=[GOOD] * 3, seen_kwargs=[])
    fallback = ScriptModel(script=[GOOD], seen_kwargs=[])
    r = build_runnable(
        [slow, fallback],
        schema=Classification,
        max_attempts=3,
        timeout_s=0.05,
        backoff_initial=0.001,
    )
    assert (await r.ainvoke([HumanMessage("bye")])).goodbye
    assert len(slow.seen_kwargs) == 1  # not retried: a timeout hands over to the fallback
