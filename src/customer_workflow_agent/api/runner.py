"""Runs the graph for each chat: one run per chat at a time, in the background, pushing
snapshots to subscribers. Every resume is checked against what the chat is waiting for.

Capacity: at most `max_open_chats` chats are open at once. A chat is open until it ends or has
been idle for `chat_idle_minutes`. When full, new chats and messages from idle chats are refused
(503 at_capacity); popup answers and supervisor decisions always go through."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from fastapi.sse import ServerSentEvent
from langgraph.types import Command

from customer_workflow_agent.api.appdb import AppDB
from customer_workflow_agent.api.events import EventBus
from customer_workflow_agent.api.schemas import ApprovalsView, ApprovalView, ChatView
from customer_workflow_agent.api.views import approval_view, chat_view
from customer_workflow_agent.graph.builder import read_chat, run_config
from customer_workflow_agent.obs.logs import chat_id_var
from customer_workflow_agent.obs.metrics import (
    AGENT_REPLY_SECONDS,
    APPROVALS_PENDING,
    CHATS_REFUSED,
    OLDEST_APPROVAL_WAIT,
    OPEN_CHATS,
)
from customer_workflow_agent.obs.tracing import Tracing
from customer_workflow_agent.settings import Settings
from customer_workflow_agent.store import RetailStore

log = logging.getLogger(__name__)
SUPERVISOR = "supervisor"
# What the customer (or supervisor) did, by the kind of answer the chat was waiting for.
ACTIONS = {"await_customer": "message", "confirm": "confirm", "supervisor_approval": "approval"}
BUSY_MESSAGE = "We're busy right now, please try again in a minute."


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str = "", headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code
        self.headers = headers


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def chat_topic(chat_id: str) -> str:
    return f"chat:{chat_id}"


class ChatRunner:
    def __init__(
        self,
        graph,
        saver,
        store: RetailStore,
        appdb: AppDB,
        bus: EventBus,
        settings: Settings,
        tracing: Tracing | None = None,
    ) -> None:
        self.graph, self.saver, self.store, self.appdb = graph, saver, store, appdb
        self.bus, self.settings = bus, settings
        self.tracing = tracing or Tracing(settings)
        self._busy: set[str] = set()
        self._tasks: dict[str, asyncio.Task] = {}
        self._errors: dict[str, str] = {}
        self._approvals: dict[str, ApprovalView] = {}
        self._resetting = False
        self._capacity = asyncio.Lock()  # counting open chats and taking a slot happen together
        APPROVALS_PENDING.set_function(lambda: len(self._approvals))
        OLDEST_APPROVAL_WAIT.set_function(self._oldest_approval_wait)

    # ---- reading ---------------------------------------------------------------------

    async def created_at(self, chat_id: str) -> str:
        created = await self.appdb.get(chat_id)
        if created is None:
            raise ApiError(404, "chat_not_found", "Chat not found")
        return created

    async def view(self, chat_id: str, *, running: bool | None = None) -> ChatView:
        created = await self.created_at(chat_id)
        values, interrupts = await read_chat(self.graph, self._cfg(chat_id))
        if running is None:
            running = chat_id in self._busy
        return chat_view(
            chat_id, created, values, interrupts, running=running, error=self._errors.get(chat_id)
        )

    def approvals(self) -> ApprovalsView:
        items = sorted(self._approvals.values(), key=lambda a: a.requested_at)
        return ApprovalsView(approvals=items)

    def _cfg(self, chat_id: str) -> dict:
        return run_config(chat_id, self.settings)

    def _oldest_approval_wait(self) -> float:
        if not self._approvals:
            return 0.0
        oldest = min(datetime.fromisoformat(a.requested_at) for a in self._approvals.values())
        return (datetime.now(UTC) - oldest).total_seconds()

    # ---- capacity ----------------------------------------------------------------------

    def _active_since(self) -> str:
        idle = timedelta(minutes=self.settings.chat_idle_minutes)
        return (datetime.now(UTC) - idle).isoformat(timespec="seconds")

    async def open_chats(self) -> int:
        count = await self.appdb.count_open(self._active_since())
        OPEN_CHATS.set(count)
        return count

    def _refuse(self) -> NoReturn:
        CHATS_REFUSED.inc()
        raise ApiError(503, "at_capacity", BUSY_MESSAGE, headers={"Retry-After": "60"})

    async def _claim_slot(self, chat_id: str) -> None:
        """An idle chat needs a free slot again before it can continue."""
        async with self._capacity:
            if not await self.appdb.is_open(chat_id, self._active_since()):
                if await self.open_chats() >= self.settings.max_open_chats:
                    self._refuse()
            await self.appdb.touch(chat_id, _now())

    # ---- running ---------------------------------------------------------------------

    async def create(self) -> ChatView:
        self._check_not_resetting()
        chat_id = uuid.uuid4().hex
        async with self._capacity:
            if await self.open_chats() >= self.settings.max_open_chats:
                self._refuse()
            await self.appdb.add(chat_id, _now())  # takes the slot
        task = self._launch(chat_id, {}, action="start")
        await asyncio.shield(task)
        return await self.view(chat_id)

    async def resume(
        self, chat_id: str, *, expect: str, interrupt_id: str, value: dict, wait: bool = False
    ) -> ChatView:
        self._check_not_resetting()
        await self.created_at(chat_id)
        if chat_id in self._busy:
            raise ApiError(409, "busy", "This chat is already working on something")
        self._busy.add(chat_id)  # no await between the check and the add
        try:
            view = await self.view(chat_id, running=False)
            if view.ended:
                raise ApiError(409, "ended", "This chat has ended")
            if view.pending is None or view.pending.type != expect:
                raise ApiError(
                    409, "wrong_state", f"The chat isn't waiting for {expect.replace('_', ' ')}"
                )
            if view.pending.interrupt_id != interrupt_id:
                raise ApiError(409, "stale_interrupt", "That question was already answered")
            if expect == "await_customer":
                await self._claim_slot(chat_id)
        except BaseException:
            self._busy.discard(chat_id)
            raise
        if expect == "supervisor_approval" and chat_id in self._approvals:
            self._approvals[chat_id] = self._approvals[chat_id].model_copy(
                update={"status": "processing"}
            )
            self._publish_approvals()
        task = self._launch(
            chat_id, Command(resume=value), action=ACTIONS.get(expect, expect), already_busy=True
        )
        if wait:
            await asyncio.shield(task)
        return await self.view(chat_id)

    async def retry(self, chat_id: str, wait: bool = False) -> ChatView:
        self._check_not_resetting()
        await self.created_at(chat_id)
        if chat_id in self._busy:
            raise ApiError(409, "busy", "This chat is already working on something")
        if chat_id not in self._errors:
            raise ApiError(409, "not_failed", "Nothing to retry")
        task = self._launch(chat_id, None, action="retry")
        if wait:
            await asyncio.shield(task)
        return await self.view(chat_id)

    def _launch(
        self, chat_id: str, graph_input, *, action: str, already_busy: bool = False
    ) -> asyncio.Task:
        if not already_busy:
            self._busy.add(chat_id)
        self._errors.pop(chat_id, None)
        task = asyncio.create_task(self._run(chat_id, graph_input, action))
        self._tasks[chat_id] = task
        return task

    async def _run(self, chat_id: str, graph_input, action: str) -> None:
        chat_id_var.set(chat_id)  # this task's own context: every log line names the chat
        try:
            await self._publish_chat(chat_id)  # running=true: show "typing…"
            started = time.monotonic()
            with self.tracing.chat(chat_id):
                await self.graph.ainvoke(graph_input, self._cfg(chat_id))
            AGENT_REPLY_SECONDS.labels(action).observe(time.monotonic() - started)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("chat %s: run failed", chat_id)
            self._errors[chat_id] = f"Something went wrong: {e}"
        finally:
            self._busy.discard(chat_id)
            self._tasks.pop(chat_id, None)
            if not self._resetting:
                await self._sync_approval(chat_id)
                if view := await self._publish_chat(chat_id):
                    await self.appdb.touch(chat_id, _now(), ended=view.ended)
                    await self.open_chats()

    # ---- approvals index (rebuilt from checkpoints) -----------------------------------

    async def _sync_approval(self, chat_id: str) -> None:
        _, interrupts = await read_chat(self.graph, self._cfg(chat_id))
        existing = self._approvals.get(chat_id)
        found = approval_view(chat_id, interrupts, existing.requested_at if existing else _now())
        changed = (found is None) != (existing is None) or (
            found
            and existing
            and (found.interrupt_id, found.status) != (existing.interrupt_id, existing.status)
        )
        if found:
            self._approvals[chat_id] = found
        else:
            self._approvals.pop(chat_id, None)
        if changed:
            self._publish_approvals()

    async def rebuild_approvals(self) -> None:
        for chat_id, created in await self.appdb.all():
            _, interrupts = await read_chat(self.graph, self._cfg(chat_id))
            if found := approval_view(chat_id, interrupts, created):
                self._approvals[chat_id] = found

    # ---- events ------------------------------------------------------------------------

    async def _publish_chat(self, chat_id: str) -> ChatView | None:
        try:
            view = await self.view(chat_id)
        except ApiError:
            return None
        self.bus.publish(chat_topic(chat_id), ServerSentEvent(event="state", data=view))
        return view

    def _publish_approvals(self) -> None:
        self.bus.publish(SUPERVISOR, ServerSentEvent(event="approvals", data=self.approvals()))

    # ---- admin -------------------------------------------------------------------------

    def _check_not_resetting(self) -> None:
        if self._resetting:
            raise ApiError(409, "resetting", "The demo is being reset")

    async def reset(self) -> int:
        """Restore the store and delete every chat."""
        self._check_not_resetting()
        self._resetting = True
        try:
            tasks = list(self._tasks.values())
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=30)
                for t in pending:
                    t.cancel()
            await asyncio.to_thread(self.store.reset)
            chats = await self.appdb.all()
            for chat_id, _ in chats:
                await self.saver.adelete_thread(chat_id)
            await self.appdb.clear()
            OPEN_CHATS.set(0)
            self._approvals.clear()
            self._errors.clear()
            self._busy.clear()
            self.bus.publish_all(ServerSentEvent(event="reset", data={}))
            return len(chats)
        finally:
            self._resetting = False

    async def shutdown(self) -> None:
        self.bus.close_all()
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await asyncio.to_thread(self.tracing.flush)
