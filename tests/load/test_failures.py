"""The whole app (API, graph, LiteLLM Router) against the fake model server, with faults.

The fake server runs in-process: LiteLLM is handed an httpx client wired straight to it, so no
port is needed and these run everywhere.
"""

import asyncio

import httpx
import litellm
import pytest
from litellm.router import Router
from prometheus_client import REGISTRY

from customer_workflow_agent.devtools.fake_llm import create_fake_llm
from customer_workflow_agent.llm.service import RouterLLMService
from customer_workflow_agent.templates import messages as M
from tests.api.conftest import IVAN, IVAN_PENDING, api_settings, new_chat, running_app

PRIMARY = "nvidia/nemotron-3.5-lightning-30b-a3b"
FALLBACK = "nvidia/nemotron-3-super-120b-a12b"


class TimedASGITransport(httpx.ASGITransport):
    """An in-process transport that honours the request's read timeout like a real socket
    would (plain ASGITransport waits forever)."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        read = (request.extensions.get("timeout") or {}).get("read")
        try:
            return await asyncio.wait_for(super().handle_async_request(request), read)
        except TimeoutError as e:
            raise httpx.ReadTimeout("timed out", request=request) from e


@pytest.fixture
async def fake(monkeypatch):
    client = httpx.AsyncClient(
        transport=TimedASGITransport(app=create_fake_llm(seed=1)), base_url="http://fake"
    )
    monkeypatch.setattr(litellm, "aclient_session", client)
    monkeypatch.setattr(Router, "_time_to_sleep_before_retry", lambda *a, **k: 0)
    yield client
    await client.aclose()


@pytest.fixture
def app_against_fake(tmp_path, fake):
    settings = api_settings(
        tmp_path,
        nvidia_api_key="fake-key",
        llm_base_url="http://fake/v1",
        llm_requests_per_minute=6000,
        llm_timeout_s=0.5,
    )
    return running_app(settings, RouterLLMService(settings))


async def faults(fake, **settings) -> None:
    (await fake.post("/_faults", json=settings)).raise_for_status()


async def stats(fake) -> dict:
    return (await fake.get("/_stats")).json()


async def cancel_until_popup(client):
    chat = await new_chat(client)
    await chat.say(f"my email is {IVAN}")
    assert "verified" in chat.view["messages"][-2]["text"]
    await chat.say(f"please cancel order {IVAN_PENDING}, I ordered it by mistake")
    assert chat.pending["type"] == "confirm"
    assert "Reason: ordered by mistake" in chat.pending["summary"]["lines"]
    return chat


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_healthy_models_serve_a_whole_cancel(fake, app_against_fake):
    async with app_against_fake as (client, app):
        chat = await cancel_until_popup(client)
        await chat.click(True)
        assert app.state.services.store.get_order(IVAN_PENDING).status == "cancelled"
        assert (await client.get("/readyz")).status_code == 200
    s = await stats(fake)
    assert set(s) == {PRIMARY} and set(s[PRIMARY]) == {"ok"}


@pytest.mark.parametrize("fault", ["server_error", "rate_limit", "bad_json"])
async def test_a_failing_primary_is_retried_then_the_fallback_answers(
    fake, app_against_fake, fault
):
    await faults(fake, model=PRIMARY, **{fault: 1.0})
    outcome = {
        "server_error": "server_error",
        "rate_limit": "rate_limited",
        "bad_json": "bad_output",
    }
    before = metric("llm_calls_total", model=PRIMARY, prompt="auth@v1", outcome=outcome[fault])
    async with app_against_fake as (client, _):
        await cancel_until_popup(client)
    s = await stats(fake)
    failed = {"server_error": "server_error", "rate_limit": "rate_limited", "bad_json": "bad_json"}
    assert s[PRIMARY] == {failed[fault]: 3 * s[FALLBACK]["ok"]}  # 3 tries per question
    after = metric("llm_calls_total", model=PRIMARY, prompt="auth@v1", outcome=outcome[fault])
    assert after - before == 3


async def test_a_timing_out_primary_goes_straight_to_the_fallback(fake, app_against_fake):
    await faults(fake, model=PRIMARY, timeout=1.0)
    async with app_against_fake as (client, _):
        await cancel_until_popup(client)
    s = await stats(fake)
    assert s[PRIMARY] == {"timeout": s[FALLBACK]["ok"]}  # one try each, no retries


async def test_with_every_model_down_the_chat_apologizes_and_recovers(fake, app_against_fake):
    async with app_against_fake as (client, _):
        chat = await new_chat(client)
        await chat.say(f"my email is {IVAN}")  # verification itself doesn't need the LLM
        await faults(fake, model="*", server_error=1.0)
        before = metric("llm_requests_total", prompt="classification@v1", served_by="none")
        await chat.say(f"please cancel order {IVAN_PENDING}")
        assert chat.view["messages"][-1]["text"] == M.LLM_TROUBLE
        assert chat.pending["type"] == "await_customer"
        assert metric("llm_requests_total", prompt="classification@v1", served_by="none") > before

        await fake.delete("/_faults")
        await chat.say(f"please cancel order {IVAN_PENDING}, I ordered it by mistake")
        assert chat.pending["type"] == "confirm"
