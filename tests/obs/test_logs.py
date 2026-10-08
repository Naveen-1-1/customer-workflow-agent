import logging

from customer_workflow_agent.llm.schemas import AuthExtraction
from customer_workflow_agent.obs.logs import ChatIdFilter, ChatIdMiddleware, chat_id_var
from tests.api.conftest import IVAN, api_settings, new_chat, running_app, scripted

CHAT = "0123456789abcdef0123456789abcdef"


def record() -> logging.LogRecord:
    return logging.LogRecord("x", logging.INFO, __file__, 1, "message", None, None)


def test_filter_adds_the_current_chat_id():
    r = record()
    ChatIdFilter().filter(r)
    assert r.chat_id == "-"
    token = chat_id_var.set(CHAT)
    try:
        r = record()
        ChatIdFilter().filter(r)
        assert r.chat_id == CHAT
    finally:
        chat_id_var.reset(token)


async def test_middleware_sets_the_chat_id_for_requests_about_one_chat():
    seen: list[str] = []

    async def app(scope, receive, send):
        seen.append(chat_id_var.get())

    middleware = ChatIdMiddleware(app)
    for path in (
        f"/api/chats/{CHAT}/messages",
        f"/api/chats/{CHAT}",
        f"/api/supervisor/approvals/{CHAT}/decision",
        "/api/chats",
        "/api/meta",
    ):
        await middleware({"type": "http", "path": path}, None, None)
    assert seen == [CHAT, CHAT, CHAT, "-", "-"]
    assert chat_id_var.get() == "-"


async def test_graph_runs_carry_their_chat_id(tmp_path):
    llm = scripted()
    seen: list[str] = []
    real_extract = llm.extract

    async def extract(schema, prompt):
        if schema is AuthExtraction:
            seen.append(chat_id_var.get())
        return await real_extract(schema, prompt)

    llm.extract = extract
    async with running_app(api_settings(tmp_path), llm) as (client, _):
        chat = await new_chat(client)
        await chat.say(f"hi, I'm {IVAN}")
    assert seen == [chat.id]
