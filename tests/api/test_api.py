import asyncio

from customer_workflow_agent.llm.fake import ScriptedLLM
from customer_workflow_agent.llm.schemas import AuthExtraction
from customer_workflow_agent.templates import messages as M
from tests.api.conftest import (
    IVAN,
    IVAN_PENDING,
    RAJ,
    RAJ_BIG,
    api_settings,
    new_chat,
    running_app,
    scripted,
)
from tests.conftest import DATA_DB


async def test_meta(api):
    client, _ = api
    meta = (await client.get("/api/meta")).json()
    assert meta["approval_enabled"] is False and meta["approval_threshold"] == 1000
    assert meta["llm_configured"] is True


async def test_create_chat_greets_and_waits_for_customer(api):
    client, _ = api
    chat = await new_chat(client)
    assert chat.view["messages"][0]["text"] == M.GREETING
    assert chat.pending["type"] == "await_customer" and chat.view["running"] is False


async def test_cancel_over_http(api):
    client, app = api
    chat = await new_chat(client)
    await chat.say(f"my email is {IVAN}")
    await chat.say(f"cancel {IVAN_PENDING}")
    assert chat.pending["type"] == "confirm"
    assert chat.pending["summary"]["title"] == f"Cancel order {IVAN_PENDING}"
    await chat.click(True)
    store = app.state.services.store
    assert store.get_order(IVAN_PENDING).status == "cancelled"
    await chat.say("bye")
    assert chat.view["ended"] and chat.view["end_reason"] == "goodbye"
    r = await chat.client.post(
        f"/api/chats/{chat.id}/messages", json={"interrupt_id": "x", "text": "hello?"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ended"


async def test_wrong_state_and_stale_interrupt_are_rejected(api):
    client, _ = api
    chat = await new_chat(client)
    r = await client.post(
        f"/api/chats/{chat.id}/confirm",
        json={"interrupt_id": chat.pending["interrupt_id"], "confirmed": True},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "wrong_state"
    old = chat.pending["interrupt_id"]
    await chat.say(f"my email is {IVAN}")
    r = await client.post(
        f"/api/chats/{chat.id}/messages", json={"interrupt_id": old, "text": "again"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_interrupt"


async def test_double_click_writes_once(api):
    client, app = api
    chat = await new_chat(client)
    await chat.say(f"my email is {IVAN}")
    await chat.say(f"cancel {IVAN_PENDING}")
    body = {"interrupt_id": chat.pending["interrupt_id"], "confirmed": True}
    r1, r2 = await asyncio.gather(
        client.post(f"/api/chats/{chat.id}/confirm?wait=true", json=body),
        client.post(f"/api/chats/{chat.id}/confirm?wait=true", json=body),
    )
    assert sorted([r1.status_code, r2.status_code]) == [202, 409]
    order = app.state.services.store.get_order(IVAN_PENDING)
    assert [p.transaction_type for p in order.payment_history] == ["payment", "refund"]


async def test_unknown_chat_is_404(api):
    client, _ = api
    r = await client.get("/api/chats/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "chat_not_found"
    assert (await client.get("/api/chats/nope/events")).status_code == 404


async def _large_return(client) -> object:
    chat = await new_chat(client)
    await chat.say(f"my email is {RAJ}")
    await chat.say(f"return everything in {RAJ_BIG}")
    await chat.click(True)
    assert chat.pending["type"] == "supervisor_approval"
    return chat


async def test_supervisor_approves(approval_api):
    client, app = approval_api
    chat = await _large_return(client)
    approvals = (await client.get("/api/supervisor/approvals")).json()["approvals"]
    assert [a["chat_id"] for a in approvals] == [chat.id]
    a = approvals[0]
    assert a["request"]["refund_total"] == 1201.55 and a["status"] == "pending"
    r = await client.post(
        f"/api/supervisor/approvals/{chat.id}/decision?wait=true",
        json={"interrupt_id": a["interrupt_id"], "approved": True},
    )
    assert r.status_code == 202
    assert app.state.services.store.get_order(RAJ_BIG).status == "return requested"
    assert (await client.get("/api/supervisor/approvals")).json()["approvals"] == []
    view = await chat.refresh()
    assert M.APPROVAL_GRANTED in [m["text"] for m in view["messages"]]


async def test_supervisor_rejects_with_note(approval_api):
    client, app = approval_api
    chat = await _large_return(client)
    a = (await client.get("/api/supervisor/approvals")).json()["approvals"][0]
    await client.post(
        f"/api/supervisor/approvals/{chat.id}/decision?wait=true",
        json={"interrupt_id": a["interrupt_id"], "approved": False, "note": "Over the limit"},
    )
    view = await chat.refresh()
    assert M.approval_rejected("Over the limit") in [m["text"] for m in view["messages"]]
    assert view["pending"]["type"] == "confirm" and view["pending"]["action"] == "transfer"
    assert app.state.services.store.returns_blocked(RAJ_BIG)


async def test_decision_must_match_pending_approval(approval_api):
    client, _ = approval_api
    chat = await new_chat(client)
    r = await client.post(
        f"/api/supervisor/approvals/{chat.id}/decision",
        json={"interrupt_id": chat.pending["interrupt_id"], "approved": True},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "wrong_state"


async def test_restart_keeps_chats_and_approvals(tmp_path):
    settings = api_settings(tmp_path, approval_enabled=True)
    async with running_app(settings, scripted()) as (client, _):
        chat = await _large_return(client)
        chat_id = chat.id
    async with running_app(settings, scripted()) as (client, app):  # restarted server
        view = (await client.get(f"/api/chats/{chat_id}")).json()
        assert view["pending"]["type"] == "supervisor_approval"
        approvals = (await client.get("/api/supervisor/approvals")).json()["approvals"]
        assert [a["chat_id"] for a in approvals] == [chat_id]
        await client.post(
            f"/api/supervisor/approvals/{chat_id}/decision?wait=true",
            json={"interrupt_id": approvals[0]["interrupt_id"], "approved": True},
        )
        assert app.state.services.store.get_order(RAJ_BIG).status == "return requested"


async def test_reset_restores_store_and_deletes_chats(api, tmp_path):
    client, app = api
    chat = await new_chat(client)
    await chat.say(f"my email is {IVAN}")
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.click(True)
    settings = app.state.services.settings
    assert settings.db_working.read_bytes() != DATA_DB.read_bytes()
    r = await client.post("/api/admin/reset")
    assert r.json() == {"chats_deleted": 1}
    assert settings.db_working.read_bytes() == DATA_DB.read_bytes()
    assert app.state.services.store.get_order(IVAN_PENDING).status == "pending"
    assert (await client.get(f"/api/chats/{chat.id}")).status_code == 404


class FlakyAuthLLM(ScriptedLLM):
    """Fails with an unexpected error once (a bug, not an LLM outage)."""

    failed = False

    async def extract(self, schema, prompt):
        if schema is AuthExtraction and not self.failed:
            self.failed = True
            raise RuntimeError("boom")
        return await super().extract(schema, prompt)


async def test_failed_run_shows_error_and_retry_continues(tmp_path):
    llm = FlakyAuthLLM()
    llm._rules = scripted()._rules
    async with running_app(api_settings(tmp_path), llm) as (client, _):
        chat = await new_chat(client)
        await chat.say(f"my email is {IVAN}")
        assert chat.view["error"] and "boom" in chat.view["error"]
        r = await client.post(f"/api/chats/{chat.id}/retry?wait=true")
        assert r.status_code == 202
        view = r.json()
        assert view["error"] is None
        assert view["messages"][-1]["text"] == M.HOW_CAN_I_HELP


async def test_original_data_is_never_modified(api):
    client, _ = api
    before = DATA_DB.read_bytes()
    chat = await new_chat(client)
    await chat.say(f"my email is {IVAN}")
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.click(True)
    assert DATA_DB.read_bytes() == before


# ---- live-update events, checked in-process through the event bus ----------------------


def drain(q: asyncio.Queue) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_events_published_for_customer_and_supervisor(approval_api):
    client, app = approval_api
    bus = app.state.services.bus
    sup = bus.subscribe("supervisor")
    chat = await new_chat(client)
    cust = bus.subscribe(f"chat:{chat.id}")
    await chat.say(f"my email is {RAJ}")
    states = [e for e in drain(cust) if e.event == "state"]
    assert [s.data.running for s in states] == [True, False]  # typing… then the reply
    await chat.say(f"return everything in {RAJ_BIG}")
    await chat.click(True)
    listed = [e.data.approvals for e in drain(sup) if e.event == "approvals"]
    assert [a.chat_id for a in listed[-1]] == [chat.id]
    a = listed[-1][0]
    drain(cust)
    await client.post(
        f"/api/supervisor/approvals/{chat.id}/decision?wait=true",
        json={"interrupt_id": a.interrupt_id, "approved": True},
    )
    statuses = [[x.status for x in e.data.approvals] for e in drain(sup)]
    assert statuses == [["processing"], []]
    final = [e for e in drain(cust) if e.event == "state"][-1].data
    assert final.running is False and any("return for order" in m.text for m in final.messages)


async def test_reset_event_reaches_subscribers(api):
    client, app = api
    bus = app.state.services.bus
    chat = await new_chat(client)
    q1, q2 = bus.subscribe(f"chat:{chat.id}"), bus.subscribe("supervisor")
    await client.post("/api/admin/reset")
    assert [e.event for e in drain(q1)] == ["reset"]
    assert [e.event for e in drain(q2)] == ["reset"]


async def test_stream_endpoints_send_snapshot_then_updates(api):
    from customer_workflow_agent.api.routes import chat_events, supervisor_events

    client, app = api
    runner = app.state.services.runner
    chat = await new_chat(client)
    stream = chat_events(chat.id, runner)
    first = await stream.__anext__()
    assert first.event == "state" and first.data.pending.type == "await_customer"
    await chat.say(f"my email is {IVAN}")
    assert (await stream.__anext__()).data.running is True
    assert (await stream.__anext__()).data.messages[-1].text == M.HOW_CAN_I_HELP
    await stream.aclose()

    sup = supervisor_events(runner)
    assert (await sup.__anext__()).event == "approvals"
    app.state.services.bus.close_all()  # server shutdown ends open streams
    try:
        await sup.__anext__()
        raise AssertionError("stream should have ended")
    except StopAsyncIteration:
        pass
