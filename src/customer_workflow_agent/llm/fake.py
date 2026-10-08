"""Deterministic LLM stand-ins for tests (no network)."""

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from customer_workflow_agent.llm.service import Prompt
from customer_workflow_agent.llm.structured import LLMUnavailable

Match = str | Callable[[Prompt], bool] | None


@dataclass
class _Rule:
    schema: type[BaseModel]
    match: Match
    result: BaseModel | Callable[[Prompt], BaseModel]
    once: bool


def _matches(match: Match, prompt: Prompt) -> bool:
    if match is None:
        return True
    if callable(match):
        return match(prompt)
    return match.lower() in prompt.customer_text.lower()


@dataclass
class ScriptedLLM:
    """Returns scripted results by schema and a substring of the customer's text.

    Unscripted calls fail the test. `write` raises LLMUnavailable unless `reply` is set,
    so the workflow falls back to its template wording (deterministic output).
    """

    reply: str | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    _rules: list[_Rule] = field(default_factory=list)

    def on(
        self,
        schema: type[BaseModel],
        match: Match,
        result: BaseModel | Callable[[Prompt], BaseModel],
        *,
        once: bool = False,
    ) -> "ScriptedLLM":
        self._rules.append(_Rule(schema, match, result, once))
        return self

    async def extract[T: BaseModel](self, schema: type[T], prompt: Prompt) -> T:
        self.calls.append((schema.__name__, prompt.customer_text))
        for rule in self._rules:
            if rule.schema is schema and _matches(rule.match, prompt):
                if rule.once:
                    self._rules.remove(rule)
                result = rule.result(prompt) if callable(rule.result) else rule.result
                assert isinstance(result, schema)
                return result.model_copy(deep=True)
        raise AssertionError(f"unscripted {schema.__name__} call for {prompt.customer_text!r}")

    async def write(self, prompt: Prompt) -> str:
        self.calls.append(("write", prompt.customer_text))
        if self.reply is None:
            raise LLMUnavailable("scripted: no reply text")
        return self.reply


class FailingLLM:
    """Every call fails, as if the NVIDIA API were down."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, schema, prompt):
        self.calls += 1
        raise LLMUnavailable("down")

    async def write(self, prompt):
        self.calls += 1
        raise LLMUnavailable("down")
