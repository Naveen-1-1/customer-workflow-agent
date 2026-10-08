from prometheus_client.parser import text_string_to_metric_families

from tests.api.conftest import RAJ, RAJ_BIG, api_settings, new_chat, running_app, scripted


def samples(text: str) -> dict[tuple, float]:
    return {
        (s.name, tuple(sorted(s.labels.items()))): s.value
        for family in text_string_to_metric_families(text)
        for s in family.samples
    }


def value(text: str, name: str, **labels) -> float | None:
    return samples(text).get((name, tuple(sorted(labels.items()))))


async def test_healthz_and_readyz(api):
    client, _ = api
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {
        "ready": True,
        "checks": {"store": True, "chat_db": True, "checkpoints": True, "llm_configured": True},
    }


async def test_not_ready_without_an_llm_key(tmp_path):
    async with running_app(api_settings(tmp_path), llm=None) as (client, _):
        r = await client.get("/readyz")
        assert r.status_code == 503 and r.json()["checks"]["llm_configured"] is False
        assert value((await client.get("/metrics")).text, "agent_ready") == 0


async def test_metrics_cover_replies_requests_and_approvals(tmp_path):
    settings = api_settings(tmp_path, approval_enabled=True)
    async with running_app(settings, scripted()) as (client, _):
        chat = await new_chat(client)
        await chat.say(f"I'm {RAJ}")
        await chat.say(f"return everything in {RAJ_BIG}")
        await chat.click(True)  # over the threshold: waits for a supervisor
        text = (await client.get("/metrics")).text

    assert value(text, "agent_ready") == 1
    assert value(text, "agent_approvals_pending") == 1
    assert value(text, "agent_oldest_approval_wait_seconds") >= 0
    assert value(text, "agent_reply_seconds_count", action="start") >= 1
    assert value(text, "agent_reply_seconds_count", action="message") >= 2
    assert value(text, "agent_reply_seconds_count", action="confirm") >= 1
    http = [k for k in samples(text) if k[0] == "http_requests_total"]
    assert any(("handler", "/api/chats/{chat_id}/messages") in labels for _, labels in http)
    assert not any("/events" in dict(labels).get("handler", "") for _, labels in http)
