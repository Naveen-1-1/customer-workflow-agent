"""Drive the graph through its pauses with a scripted LLM."""

import types
import typing

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.builder import build_graph, read_chat, run_config
from customer_workflow_agent.llm.fake import ScriptedLLM
from customer_workflow_agent.llm.schemas import (
    AuthExtraction,
    Classification,
    RequestItem,
)
from customer_workflow_agent.settings import Settings
from customer_workflow_agent.store import InMemoryBackend, RetailStore
from customer_workflow_agent.store.models import WorkingDB


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, llm_reply_writer_enabled=False, **overrides)


def make(schema: type[BaseModel], **values) -> BaseModel:
    """Build an LLM result, filling unspecified fields with 'not said' defaults."""
    data = {}
    for name, field in schema.model_fields.items():
        if name in values:
            data[name] = values[name]
            continue
        ann = field.annotation
        origin = typing.get_origin(ann)
        args = typing.get_args(ann)
        if name == "relation":
            data[name] = "answer"
        elif ann is bool:
            data[name] = False
        elif origin is list:
            data[name] = []
        elif origin in (typing.Union, types.UnionType) and type(None) in args:
            data[name] = None
        else:
            raise ValueError(f"{schema.__name__}.{name} needs a value")
    return schema.model_validate(data)


def req(type: str, order_id: str | None = None, details: str = "", all_eligible: bool = False):
    return RequestItem(
        type=type, order_id=order_id, all_eligible_orders=all_eligible, details=details or type
    )


def classified(*requests: RequestItem, goodbye=False, other=False) -> Classification:
    return Classification(requests=list(requests), goodbye=goodbye, about_other_person=other)


def auth_ex(**kw) -> AuthExtraction:
    return make(AuthExtraction, **kw)  # type: ignore[return-value]


class ChatDriver:
    def __init__(self, deps: Deps, checkpointer=None, thread: str = "t1"):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer or InMemorySaver())
        self.cfg = run_config(thread, deps.settings)
        self.pause: dict | None = None
        self.values: dict = {}

    async def _run(self, inp) -> dict | None:
        await self.graph.ainvoke(inp, self.cfg)
        self.values, interrupts = await read_chat(self.graph, self.cfg)
        self.pause = interrupts[0].value if interrupts else None
        return self.pause

    async def start(self):
        return await self._run({})

    async def reload(self):
        """Read the chat back from the checkpointer (e.g. after a restart)."""
        self.values, interrupts = await read_chat(self.graph, self.cfg)
        self.pause = interrupts[0].value if interrupts else None
        return self.pause

    async def say(self, text: str):
        assert self.pause and self.pause["type"] == "await_customer", self.pause
        return await self._run(Command(resume={"text": text}))

    async def click(self, yes: bool):
        assert self.pause and self.pause["type"] == "confirm", self.pause
        return await self._run(Command(resume={"confirmed": yes}))

    async def supervise(self, approved: bool, note: str | None = None):
        assert self.pause and self.pause["type"] == "supervisor_approval", self.pause
        return await self._run(Command(resume={"approved": approved, "note": note}))

    def agent_texts(self) -> list[str]:
        return [m["text"] for m in self.values.get("messages", []) if m["role"] == "agent"]

    def last_agent(self) -> str:
        return self.agent_texts()[-1]

    @property
    def ended(self) -> bool:
        return self.values.get("phase") == "ended" and self.pause is None


def new_store(db: WorkingDB) -> tuple[RetailStore, InMemoryBackend]:
    backend = InMemoryBackend(db)
    return RetailStore(backend), backend


def deps_for(db: WorkingDB, llm: ScriptedLLM, **settings_overrides) -> Deps:
    store, _ = new_store(db)
    return Deps(store=store, llm=llm, settings=settings(**settings_overrides))
