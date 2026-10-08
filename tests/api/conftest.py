from contextlib import asynccontextmanager

import httpx
import pytest

from customer_workflow_agent.api.app import create_app
from customer_workflow_agent.llm.fake import ScriptedLLM
from customer_workflow_agent.llm.schemas import (
    AuthExtraction,
    CancelTurn,
    Classification,
    PaymentRef,
    ReturnTurn,
)
from customer_workflow_agent.settings import Settings
from tests.conftest import PROJECT_ROOT
from tests.graph.helpers import auth_ex, classified, make, req

IVAN = "ivan.santos3158@example.com"
IVAN_PENDING = "#W8770097"
RAJ = "raj.sanchez2046@example.com"
RAJ_BIG = "#W1067251"


def scripted() -> ScriptedLLM:
    llm = ScriptedLLM()
    llm.on(AuthExtraction, IVAN, auth_ex(email=IVAN))
    llm.on(AuthExtraction, RAJ, auth_ex(email=RAJ))
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", make(CancelTurn, reason="no longer needed"))
    llm.on(Classification, "return", classified(req("return_items", RAJ_BIG)))
    llm.on(
        ReturnTurn,
        "return",
        make(
            ReturnTurn,
            all_items=True,
            refund_to=PaymentRef(kind="credit_card", brand=None, last_four=None),
        ),
    )
    llm.on(Classification, "bye", classified(goodbye=True))
    return llm


def api_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=PROJECT_ROOT / "data",
        var_dir=tmp_path / "var",
        llm_reply_writer_enabled=False,
        **overrides,
    )


@asynccontextmanager
async def running_app(settings: Settings, llm):
    app = create_app(settings, llm=llm, frontend_dist=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


@pytest.fixture
async def api(tmp_path):
    async with running_app(api_settings(tmp_path), scripted()) as (client, app):
        yield client, app


@pytest.fixture
async def approval_api(tmp_path):
    settings = api_settings(tmp_path, approval_enabled=True)
    async with running_app(settings, scripted()) as (client, app):
        yield client, app


class Chat:
    """Small helper over the HTTP API."""

    def __init__(self, client: httpx.AsyncClient, view: dict):
        self.client, self.view = client, view

    @property
    def id(self) -> str:
        return self.view["chat_id"]

    @property
    def pending(self) -> dict | None:
        return self.view["pending"]

    async def say(self, text: str) -> httpx.Response:
        r = await self.client.post(
            f"/api/chats/{self.id}/messages?wait=true",
            json={"interrupt_id": self.pending["interrupt_id"], "text": text},
        )
        if r.status_code == 202:
            self.view = r.json()
        return r

    async def click(self, yes: bool) -> httpx.Response:
        r = await self.client.post(
            f"/api/chats/{self.id}/confirm?wait=true",
            json={"interrupt_id": self.pending["interrupt_id"], "confirmed": yes},
        )
        if r.status_code == 202:
            self.view = r.json()
        return r

    async def refresh(self) -> dict:
        self.view = (await self.client.get(f"/api/chats/{self.id}")).json()
        return self.view


async def new_chat(client: httpx.AsyncClient) -> Chat:
    r = await client.post("/api/chats")
    assert r.status_code == 201, r.text
    return Chat(client, r.json())
