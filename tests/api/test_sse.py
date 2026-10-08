"""Server-sent events through a real uvicorn server (httpx's in-memory transport buffers
whole responses, so it can't test endless streams)."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from httpx_sse import aconnect_sse

from customer_workflow_agent.api.app import create_app
from tests.api.conftest import IVAN, RAJ, RAJ_BIG, api_settings, scripted

pytestmark = pytest.mark.sse


def free_port() -> int:
    try:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    except PermissionError:
        pytest.skip("binding a local port is not permitted here (sandbox)")


@asynccontextmanager
async def live_server(settings):
    port = free_port()
    app = create_app(settings, llm=scripted(), frontend_dist=None)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag, not an event
        await asyncio.sleep(0.02)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            yield client
    finally:
        server.should_exit = True
        await task


async def next_event(events, name: str, wait_s: float = 5.0):
    async def find():
        async for sse in events:
            if sse.event == name:
                return json.loads(sse.data)
        raise AssertionError("stream ended")

    return await asyncio.wait_for(find(), wait_s)


async def test_chat_stream_follows_a_turn(tmp_path):
    async with live_server(api_settings(tmp_path)) as client:
        chat = (await client.post("/api/chats")).json()
        async with aconnect_sse(client, "GET", f"/api/chats/{chat['chat_id']}/events") as es:
            events = es.aiter_sse()
            first = await next_event(events, "state")
            assert first["pending"]["type"] == "await_customer"
            await client.post(
                f"/api/chats/{chat['chat_id']}/messages",
                json={"interrupt_id": first["pending"]["interrupt_id"], "text": f"email {IVAN}"},
            )
            running = await next_event(events, "state")
            assert running["running"] is True and running["pending"] is None
            done = await next_event(events, "state")
            assert done["running"] is False and done["pending"]["type"] == "await_customer"


async def test_supervisor_stream_and_customer_stream_see_decision(tmp_path):
    async with live_server(api_settings(tmp_path, approval_enabled=True)) as client:
        chat = (await client.post("/api/chats")).json()
        cid = chat["chat_id"]

        async def say(text):
            view = (await client.get(f"/api/chats/{cid}")).json()
            r = await client.post(
                f"/api/chats/{cid}/messages?wait=true",
                json={"interrupt_id": view["pending"]["interrupt_id"], "text": text},
            )
            return r.json()

        async with aconnect_sse(client, "GET", "/api/supervisor/events") as sup:
            sup_events = sup.aiter_sse()
            assert (await next_event(sup_events, "approvals"))["approvals"] == []
            await say(f"email {RAJ}")
            view = await say(f"return everything in {RAJ_BIG}")
            await client.post(
                f"/api/chats/{cid}/confirm?wait=true",
                json={"interrupt_id": view["pending"]["interrupt_id"], "confirmed": True},
            )
            listed = (await next_event(sup_events, "approvals"))["approvals"]
            assert [a["chat_id"] for a in listed] == [cid]

            async with aconnect_sse(client, "GET", f"/api/chats/{cid}/events") as cust:
                cust_events = cust.aiter_sse()
                waiting = await next_event(cust_events, "state")
                assert waiting["pending"]["type"] == "supervisor_approval"
                await client.post(
                    f"/api/supervisor/approvals/{cid}/decision",
                    json={"interrupt_id": listed[0]["interrupt_id"], "approved": True},
                )
                processing = (await next_event(sup_events, "approvals"))["approvals"]
                assert processing[0]["status"] == "processing"
                assert (await next_event(sup_events, "approvals"))["approvals"] == []
                while (state := await next_event(cust_events, "state"))["running"]:
                    pass
                assert any("return for order" in m["text"] for m in state["messages"])


async def test_reset_reaches_every_stream(tmp_path):
    async with live_server(api_settings(tmp_path)) as client:
        chat = (await client.post("/api/chats")).json()
        async with (
            aconnect_sse(client, "GET", f"/api/chats/{chat['chat_id']}/events") as a,
            aconnect_sse(client, "GET", "/api/supervisor/events") as b,
        ):
            ea, eb = a.aiter_sse(), b.aiter_sse()
            await next_event(ea, "state")
            await next_event(eb, "approvals")
            await client.post("/api/admin/reset")
            assert await next_event(ea, "reset") == {}
            assert await next_event(eb, "reset") == {}
