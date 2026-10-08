"""Structured output: the strict JSON schema we send, and parsing what comes back.

Retries and the fallback model are LiteLLM's job (see client.py). LiteLLM also checks the
output against the schema (`enable_json_schema_validation`), so bad output is retried there;
`parse_or_raise` then turns the JSON into our Pydantic model.
"""

import json
import re
from typing import Any

from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError


class LLMUnavailable(Exception):
    """Every attempt on every model failed."""


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


def content(response: Any) -> str:
    """The text of the first choice of a chat completion."""
    text = response.choices[0].message.content
    return text if isinstance(text, str) else ""
