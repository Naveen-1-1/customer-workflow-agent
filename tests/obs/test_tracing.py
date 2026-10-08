import langsmith
import pytest
from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.client import FALLBACK, PRIMARY, build_router
from customer_workflow_agent.llm.schemas import AuthExtraction
from customer_workflow_agent.llm.service import RouterLLMService
from customer_workflow_agent.obs import tracing as tracing_module
from customer_workflow_agent.obs.tracing import Tracing
from customer_workflow_agent.settings import Settings

AUTH = (
    '{"email": "a@b.com", "first_name": null, "last_name": null, "zip": null,'
    ' "wants_human": false, "goodbye": false, "has_request": false}'
)


class RecordingClient(langsmith.Client):
    """Keeps the runs it would send to LangSmith."""

    def __init__(self, **_ignored) -> None:
        super().__init__(api_key="test", api_url="http://127.0.0.1:9", auto_batch_tracing=False)
        self.recorded: list[dict] = []

    def create_run(self, name, inputs, run_type, *args, **kwargs):
        self.recorded.append({"name": name, "run_type": run_type, "inputs": inputs, **kwargs})

    def update_run(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch):
    """Tracing builds this instead of a real LangSmith client (which would go online)."""
    monkeypatch.setattr(tracing_module.langsmith, "Client", RecordingClient)


def settings(**overrides) -> Settings:
    return Settings(
        _env_file=None, nvidia_api_key="test", llm_requests_per_minute=6000, **overrides
    )


def test_off_by_default_and_without_a_key():
    assert not Tracing(settings()).enabled
    assert not Tracing(settings(langsmith_tracing=True)).enabled
    assert Tracing(settings(langsmith_tracing=True, langsmith_api_key="k")).enabled


class State(TypedDict):
    email: str


async def test_a_chat_run_is_one_thread_with_the_llm_call_inside_its_node():
    s = settings(langsmith_tracing=True, langsmith_api_key="k")
    tracing = Tracing(s)
    llm = RouterLLMService(
        s, router=build_router(s, {PRIMARY: {"mock_response": AUTH}, FALLBACK: {}})
    )

    async def verify(state: State) -> State:
        out = await llm.extract(AuthExtraction, prompts.auth("I'm a@b.com"))
        return {"email": out.email or ""}

    g = StateGraph(State)
    g.add_node("verify", verify)
    g.set_entry_point("verify")
    graph = g.compile()

    with tracing.chat("chat-1"):
        assert (await graph.ainvoke({"email": ""}))["email"] == "a@b.com"
    wait_for_all_tracers()

    runs = {r["name"]: r for r in tracing.client.recorded}
    llm_run = runs["auth"]
    assert llm_run["run_type"] == "llm"
    metadata = llm_run["extra"]["metadata"]
    assert metadata["prompt_version"] == prompts.VERSIONS["auth"]
    assert metadata["thread_id"] == "chat-1"
    assert llm_run["inputs"]["messages"][1]["content"] == "Customer message: I'm a@b.com"
    assert llm_run["parent_run_id"] == runs["verify"]["id"]  # nested under the graph node
    assert runs["verify"]["extra"]["metadata"]["thread_id"] == "chat-1"


async def test_disabled_tracing_sends_nothing():
    tracing = Tracing(settings())
    client = RecordingClient()
    with langsmith.tracing_context(client=client), tracing.chat("chat-1"):
        g = StateGraph(State)
        g.add_node("n", lambda s: s)
        g.set_entry_point("n")
        await g.compile().ainvoke({"email": ""})
    wait_for_all_tracers()
    assert client.recorded == []
