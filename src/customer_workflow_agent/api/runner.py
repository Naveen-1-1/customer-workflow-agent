"""Runs the graph for each chat: one run per chat at a time, in the background, pushing
snapshots to subscribers. Every resume is checked against what the chat is waiting for."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from fastapi.sse import ServerSentEvent
from langgraph.types import Command

from customer_workflow_agent.api.appdb import AppDB
from customer_workflow_agent.api.events import EventBus
from customer_workflow_agent.api.schemas import ApprovalsView, ApprovalView, ChatView
from customer_workflow_agent.api.views import approval_view, chat_view
from customer_workflow_agent.graph.builder import read_chat, run_config
from customer_workflow_agent.settings import Settings
from customer_workflow_agent.store import RetailStore

log = logging.getLogger(__name__)
SUPERVISOR = "supervisor"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def chat_topic(chat_id: str) -> str:
    return f"chat:{chat_id}"


class ChatRunner:
    def __init__(
        self, graph, saver, store: RetailStore, appdb: AppDB, bus: EventBus, settings: Settings
    ) -> None:
        self.graph, self.saver, self.store, self.appdb = graph, saver, store, appdb
        self.bus, self.settings = bus, settings
        self._busy: set[str] = set()
        self._tasks: dict[str, asyncio.Task] = {}
        self._errors: dict[str, str] = {}
        self._approvals: dict[str, ApprovalView] = {}
        self._resetting = False

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

    # ---- running ---------------------------------------------------------------------

    async def create(self) -> ChatView:
        self._check_not_resetting()
        chat_id = uuid.uuid4().hex
        await self.appdb.add(chat_id, _now())
        task = self._launch(chat_id, {})
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
        except BaseException:
            self._busy.discard(chat_id)
            raise
        if expect == "supervisor_approval" and chat_id in self._approvals:
            self._approvals[chat_id] = self._approvals[chat_id].model_copy(
                update={"status": "processing"}
            )
            self._publish_approvals()
        task = self._launch(chat_id, Command(resume=value), already_busy=True)
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
        task = self._launch(chat_id, None)
        if wait:
            await asyncio.shield(task)
        return await self.view(chat_id)

    def _launch(self, chat_id: str, graph_input, already_busy: bool = False) -> asyncio.Task:
        if not already_busy:
            self._busy.add(chat_id)
        self._errors.pop(chat_id, None)
        task = asyncio.create_task(self._run(chat_id, graph_input))
        self._tasks[chat_id] = task
        return task

    async def _run(self, chat_id: str, graph_input) -> None:
        try:
            await self._publish_chat(chat_id)  # running=true: show "typing…"
            await self.graph.ainvoke(graph_input, self._cfg(chat_id))
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
                await self._publish_chat(chat_id)

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

    async def _publish_chat(self, chat_id: str) -> None:
        try:
            view = await self.view(chat_id)
        except ApiError:
            return
        self.bus.publish(chat_topic(chat_id), ServerSentEvent(event="state", data=view))

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
