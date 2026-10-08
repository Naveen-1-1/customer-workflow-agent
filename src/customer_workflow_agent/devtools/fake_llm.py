"""A fake OpenAI-compatible model server, for failure and load tests (`make fake-llm`, :8100).

Point the app at it with `LLM_BASE_URL=http://127.0.0.1:8100/v1` (any NVIDIA_API_KEY works).

Answers: structured requests are answered from a small scenario table keyed by schema name and
words in the customer's message (enough for the Locust customers: verify by email, ask about an
order, cancel, return, say bye). Anything else gets a valid "nothing said" object built from the
schema. Free-text requests get a short friendly sentence.

Faults, per model (or "*" for every model), set with `POST /_faults`:
    {"model": "*", "latency_s": 0.5, "jitter_s": 0.5, "rate_limit": 0.1, "server_error": 0,
     "timeout": 0, "bad_json": 0}
Rates are probabilities (0-1) per request. `DELETE /_faults` clears them; `GET /_stats` shows
how many requests each model got and how they ended.
"""

import asyncio
import json
import random
import re
import time
import uuid
from collections import Counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

TIMEOUT_SLEEP_S = 120.0  # a "timeout" just doesn't answer for this long

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ORDER = re.compile(r"#?W\d{7}")
_CUSTOMER = re.compile(r"Customer message: (.*)", re.DOTALL)


class Faults(BaseModel):
    latency_s: float = 0.0
    jitter_s: float = 0.0
    rate_limit: float = 0.0
    server_error: float = 0.0
    timeout: float = 0.0
    bad_json: float = 0.0


class FaultUpdate(Faults):
    model: str = "*"


# ---- answers ---------------------------------------------------------------------------


def _order(text: str) -> str | None:
    m = _ORDER.search(text)
    return None if m is None else "#" + m.group(0).lstrip("#")


def _said(text: str, *words: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in words)


def _request(kind: str, text: str) -> dict:
    return {"type": kind, "order_id": _order(text), "all_eligible_orders": False, "details": text}


def _classification(text: str) -> dict | None:
    if _said(text, "bye", "that's all"):
        return {"requests": [], "goodbye": True, "about_other_person": False}
    for words, kind in (
        (("cancel",), "cancel_order"),
        (("return",), "return_items"),
        (("status", "where is", "track"), "info"),
    ):
        if _said(text, *words):
            return {
                "requests": [_request(kind, text)],
                "goodbye": False,
                "about_other_person": False,
            }
    return None


def _auth(text: str) -> dict | None:
    m = _EMAIL.search(text)
    if m is None:
        return None
    return {
        "email": m.group(0).rstrip("."),
        "first_name": None,
        "last_name": None,
        "zip": None,
        "wants_human": False,
        "goodbye": False,
        "has_request": False,
    }


def _turn(text: str) -> dict:
    """The fields every in-request turn has."""
    stripped = text.strip().rstrip(".")
    return {
        "relation": "goodbye" if _said(text, "bye") else "answer",
        "new_requests": [],
        "choice_number": int(stripped) if stripped.isdigit() else None,
        "order_id": _order(text),
    }


_REASONS = (
    (re.compile(r"by (?:mistake|accident)", re.I), "ordered by mistake"),
    (re.compile(r"no longer need\w*|don't need it", re.I), "no longer needed"),
)


def _cancel(text: str) -> dict:
    for pattern, reason in _REASONS:
        if m := pattern.search(text):
            return {**_turn(text), "reason": reason, "reason_quote": m.group(0)}
    return {**_turn(text), "reason": None, "reason_quote": None}


def _return(text: str) -> dict:
    refund = None
    for words, kind in (
        (("gift card",), "gift_card"),
        (("paypal",), "paypal"),
        (("credit card", "card"), "credit_card"),
    ):
        if _said(text, *words):
            refund = {"kind": kind, "brand": None, "last_four": None}
            break
    return {
        **_turn(text),
        "all_items": _said(text, "everything", "all items", "all of it"),
        "items": [],
        "refund_to": refund,
    }


def _info(text: str) -> dict:
    order = _order(text)
    return {
        "topic": "order_details" if order else "order_list",
        "order_ids": [order] if order else [],
        "product": None,
    }


SCENARIOS = {
    "Classification": _classification,
    "AuthExtraction": _auth,
    "CancelTurn": _cancel,
    "ReturnTurn": _return,
    "InfoQuery": _info,
}


def empty_object(schema: dict, defs: dict | None = None) -> Any:
    """A valid instance that says nothing: null where allowed, empty lists, false, first enum."""
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return empty_object(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "anyOf" in schema:
        options = schema["anyOf"]
        if any(o.get("type") == "null" for o in options):
            return None
        return empty_object(options[0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {k: empty_object(v, defs) for k, v in schema.get("properties", {}).items()}
    return {"array": [], "boolean": False, "integer": 0, "number": 0, "string": ""}.get(kind)


def answer(body: dict) -> str:
    messages = body.get("messages") or []
    user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    match = _CUSTOMER.search(user)
    text = match.group(1).strip() if match else user
    fmt = body.get("response_format") or {}
    if fmt.get("type") != "json_schema":
        return "Sure, I can help with that."
    spec = fmt["json_schema"]
    scenario = SCENARIOS.get(spec.get("name", ""))
    result = scenario(text) if scenario else None
    return json.dumps(result if result is not None else empty_object(spec["schema"]))


# ---- server ----------------------------------------------------------------------------


def create_fake_llm(seed: int | None = None) -> FastAPI:
    app = FastAPI(title="Fake LLM")
    faults: dict[str, Faults] = {}
    stats: Counter[tuple[str, str]] = Counter()
    rng = random.Random(seed)

    def faults_for(model: str) -> Faults:
        return faults.get(model) or faults.get("*") or Faults()

    def error(status: int, message: str, kind: str) -> JSONResponse:
        return JSONResponse(
            {"error": {"message": message, "type": kind, "code": status}}, status_code=status
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        model = body.get("model", "?")
        f = faults_for(model)
        delay = f.latency_s + (rng.random() * f.jitter_s if f.jitter_s else 0.0)
        if delay:
            await asyncio.sleep(delay)
        roll = rng.random()
        if roll < f.rate_limit:
            stats[(model, "rate_limited")] += 1
            return error(429, "Too many requests (fake)", "rate_limit_error")
        roll -= f.rate_limit
        if roll < f.server_error:
            stats[(model, "server_error")] += 1
            return error(500, "Internal error (fake)", "server_error")
        roll -= f.server_error
        if roll < f.timeout:
            stats[(model, "timeout")] += 1
            await asyncio.sleep(TIMEOUT_SLEEP_S)
        roll -= f.timeout
        content = answer(body)
        if roll < f.bad_json:
            stats[(model, "bad_json")] += 1
            content = "Sorry, I can't produce JSON right now."
        else:
            stats[(model, "ok")] += 1
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    @app.post("/_faults")
    async def set_faults(update: FaultUpdate) -> dict:
        faults[update.model] = Faults(**update.model_dump(exclude={"model"}))
        return {m: f.model_dump() for m, f in faults.items()}

    @app.delete("/_faults")
    async def clear_faults() -> dict:
        faults.clear()
        return {}

    @app.get("/_stats")
    async def get_stats() -> dict:
        out: dict[str, dict[str, int]] = {}
        for (model, result), n in sorted(stats.items()):
            out.setdefault(model, {})[result] = n
        return out

    @app.delete("/_stats")
    async def clear_stats() -> dict:
        stats.clear()
        return {}

    return app


app = create_fake_llm()
