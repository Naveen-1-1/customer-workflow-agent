"""HTTP endpoints. Customer actions and supervisor decisions resume the graph in the
background; pages follow along through server-sent events (full snapshots, never deltas)."""

from collections.abc import AsyncIterable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from customer_workflow_agent.api.events import CLOSE
from customer_workflow_agent.api.runner import SUPERVISOR, ChatRunner, chat_topic
from customer_workflow_agent.api.schemas import (
    ApprovalsView,
    ChatView,
    ConfirmBody,
    DecisionBody,
    Meta,
    ResetResult,
    SendMessage,
)

router = APIRouter()


def services(request: Request):
    return request.app.state.services


def runner(request: Request) -> ChatRunner:
    return request.app.state.services.runner


Runner = Annotated[ChatRunner, Depends(runner)]


async def existing_chat(chat_id: str, r: Runner) -> str:
    await r.created_at(chat_id)  # 404 before any stream starts
    return chat_id


ChatId = Annotated[str, Depends(existing_chat)]


@router.get("/meta")
async def meta(request: Request) -> Meta:
    svc = services(request)
    s = svc.settings
    return Meta(
        approval_enabled=s.approval_enabled,
        approval_threshold=s.approval_threshold,
        llm_configured=svc.llm_configured,
        primary_model=s.llm_primary_model,
        fallback_model=s.llm_fallback_model,
    )


@router.post("/chats", status_code=201)
async def create_chat(r: Runner) -> ChatView:
    return await r.create()


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: ChatId, r: Runner) -> ChatView:
    return await r.view(chat_id)


@router.post("/chats/{chat_id}/messages", status_code=202)
async def send_message(
    chat_id: ChatId, body: SendMessage, r: Runner, wait: bool = False
) -> ChatView:
    return await r.resume(
        chat_id,
        expect="await_customer",
        interrupt_id=body.interrupt_id,
        value={"text": body.text.strip()},
        wait=wait,
    )


@router.post("/chats/{chat_id}/confirm", status_code=202)
async def confirm(chat_id: ChatId, body: ConfirmBody, r: Runner, wait: bool = False) -> ChatView:
    return await r.resume(
        chat_id,
        expect="confirm",
        interrupt_id=body.interrupt_id,
        value={"confirmed": body.confirmed},
        wait=wait,
    )


@router.post("/chats/{chat_id}/retry", status_code=202)
async def retry(chat_id: ChatId, r: Runner, wait: bool = False) -> ChatView:
    return await r.retry(chat_id, wait=wait)


@router.get("/chats/{chat_id}/events", response_class=EventSourceResponse)
async def chat_events(chat_id: ChatId, r: Runner) -> AsyncIterable[ServerSentEvent]:
    q = r.bus.subscribe(chat_topic(chat_id))  # subscribe before the snapshot: nothing is lost
    try:
        yield ServerSentEvent(event="state", data=await r.view(chat_id), retry=2000)
        while (event := await q.get()) is not CLOSE:
            yield event
    finally:
        r.bus.unsubscribe(chat_topic(chat_id), q)


@router.get("/supervisor/approvals")
async def approvals(r: Runner) -> ApprovalsView:
    return r.approvals()


@router.post("/supervisor/approvals/{chat_id}/decision", status_code=202)
async def decide(chat_id: ChatId, body: DecisionBody, r: Runner, wait: bool = False) -> ChatView:
    return await r.resume(
        chat_id,
        expect="supervisor_approval",
        interrupt_id=body.interrupt_id,
        value={"approved": body.approved, "note": body.note or None},
        wait=wait,
    )


@router.get("/supervisor/events", response_class=EventSourceResponse)
async def supervisor_events(r: Runner) -> AsyncIterable[ServerSentEvent]:
    q = r.bus.subscribe(SUPERVISOR)
    try:
        yield ServerSentEvent(event="approvals", data=r.approvals(), retry=2000)
        while (event := await q.get()) is not CLOSE:
            yield event
    finally:
        r.bus.unsubscribe(SUPERVISOR, q)


@router.post("/admin/reset")
async def reset(r: Runner) -> ResetResult:
    return ResetResult(chats_deleted=await r.reset())
