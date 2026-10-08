"""Backpressure: at most MAX_OPEN_CHATS chats at once; an ended or idle chat frees its slot."""

import aiosqlite
from prometheus_client import REGISTRY

from customer_workflow_agent.api.appdb import AppDB
from tests.api.conftest import (
    CANCEL,
    IVAN,
    IVAN_PENDING,
    RAJ,
    RAJ_BIG,
    api_settings,
    new_chat,
    running_app,
    scripted,
)

LONG_AGO = "2000-01-01T00:00:00+00:00"


def full_app(tmp_path, max_open: int, **settings):
    return running_app(api_settings(tmp_path, max_open_chats=max_open, **settings), scripted())


async def go_idle(app, chat_id: str) -> None:
    await app.state.services.runner.appdb.touch(chat_id, LONG_AGO)


def refused_total() -> float:
    return REGISTRY.get_sample_value("agent_chats_refused_total") or 0.0


async def test_new_chats_are_refused_when_full(tmp_path):
    async with full_app(tmp_path, 2) as (client, _):
        await new_chat(client)
        await new_chat(client)
        before = refused_total()
        r = await client.post("/api/chats")
        assert r.status_code == 503
        assert r.headers["retry-after"] == "60"
        assert r.json()["error"] == {
            "code": "at_capacity",
            "message": "We're busy right now, please try again in a minute.",
        }
        assert refused_total() == before + 1
        metrics = (await client.get("/metrics")).text
        assert "agent_open_chats 2.0" in metrics


async def test_an_ended_chat_frees_its_slot(tmp_path):
    async with full_app(tmp_path, 1) as (client, _):
        chat = await new_chat(client)
        await chat.say(f"my email is {IVAN}")
        await chat.say("bye")
        assert chat.view["ended"]
        await new_chat(client)


async def test_an_idle_chat_frees_its_slot_and_needs_one_to_continue(tmp_path):
    async with full_app(tmp_path, 1) as (client, app):
        first = await new_chat(client)
        await go_idle(app, first.id)
        second = await new_chat(client)  # the idle chat no longer counts

        r = await first.say(f"my email is {IVAN}")  # full again: the idle chat must wait
        assert r.status_code == 503 and r.json()["error"]["code"] == "at_capacity"
        assert first.pending["type"] == "await_customer"  # nothing changed in the chat

        await go_idle(app, second.id)
        r = await first.say(f"my email is {IVAN}")
        assert r.status_code == 202 and "verified" in first.view["messages"][-2]["text"]


async def test_an_active_chat_is_never_refused(tmp_path):
    async with full_app(tmp_path, 1) as (client, _):
        chat = await new_chat(client)
        assert (await client.post("/api/chats")).status_code == 503
        assert (await chat.say(f"my email is {IVAN}")).status_code == 202


async def test_popup_answers_go_through_when_full(tmp_path):
    async with full_app(tmp_path, 1) as (client, app):
        chat = await new_chat(client)
        await chat.say(f"my email is {IVAN}")
        await chat.say(CANCEL)
        assert chat.pending["type"] == "confirm"
        await go_idle(app, chat.id)
        await new_chat(client)  # takes the only slot
        assert (await chat.click(True)).status_code == 202
        assert app.state.services.store.get_order(IVAN_PENDING).status == "cancelled"


async def test_supervisor_decisions_go_through_when_full(tmp_path):
    async with full_app(tmp_path, 1, approval_enabled=True) as (client, app):
        chat = await new_chat(client)
        await chat.say(f"I'm {RAJ}")
        await chat.say(f"return everything in {RAJ_BIG}")
        await chat.click(True)
        assert chat.view["pending"]["type"] == "supervisor_approval"
        await go_idle(app, chat.id)
        await new_chat(client)
        a = (await client.get("/api/supervisor/approvals")).json()["approvals"][0]
        r = await client.post(
            f"/api/supervisor/approvals/{chat.id}/decision?wait=true",
            json={"interrupt_id": a["interrupt_id"], "approved": True},
        )
        assert r.status_code == 202
        assert app.state.services.store.get_order(RAJ_BIG).status == "return requested"


async def test_reset_frees_every_slot(tmp_path):
    async with full_app(tmp_path, 1) as (client, _):
        await new_chat(client)
        assert (await client.post("/api/chats")).status_code == 503
        await client.post("/api/admin/reset")
        await new_chat(client)


async def test_older_chat_databases_get_the_new_columns(tmp_path):
    path = tmp_path / "app.sqlite"
    async with aiosqlite.connect(path) as conn:
        await conn.execute("CREATE TABLE chats (chat_id TEXT PRIMARY KEY, created_at TEXT)")
        await conn.execute("INSERT INTO chats VALUES ('old', '2026-10-01T00:00:00+00:00')")
        await conn.commit()
    db = await AppDB.open(path)
    try:
        assert await db.count_open("2026-09-30T00:00:00+00:00") == 1
        assert await db.count_open("2026-10-02T00:00:00+00:00") == 0
        await db.touch("old", "2026-10-03T00:00:00+00:00", ended=True)
        assert await db.count_open("2026-09-30T00:00:00+00:00") == 0
    finally:
        await db.close()
