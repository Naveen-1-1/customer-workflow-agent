"""Live checks against NVIDIA's API. Run with: pytest -m live (needs NVIDIA_API_KEY in .env)."""

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.client import build_chat_models, make_rate_limiter
from customer_workflow_agent.llm.guard import safe_reply
from customer_workflow_agent.llm.schemas import CancelTurn, Classification, ItemsTurn
from customer_workflow_agent.llm.service import NvidiaLLMService
from customer_workflow_agent.llm.structured import build_runnable
from customer_workflow_agent.settings import Settings

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    s = Settings()
    if s.nvidia_api_key is None:
        pytest.skip("NVIDIA_API_KEY not set")
    return s


@pytest.mark.parametrize("which", [0, 1], ids=["primary", "fallback"])
async def test_each_model_accepts_strict_json_schema(live_settings, which):
    model = build_chat_models(
        live_settings, temperature=0.1, rate_limiter=make_rate_limiter(live_settings)
    )[which]
    runnable = build_runnable([model], schema=Classification, max_attempts=2)
    p = prompts.classification(
        "Please cancel order #W1234567, and also what's my gift card balance?", None
    )
    out = await runnable.ainvoke([SystemMessage(p.system), HumanMessage(p.user)])
    types = [r.type for r in out.requests]
    assert "cancel_order" in types and "info" in types


async def test_service_extracts_turns_and_writes_safe_text(live_settings):
    llm = NvidiaLLMService(live_settings)
    cancel = await llm.extract(
        CancelTurn,
        prompts.turn("cancel_order", "I bought it by accident", last_agent=None, known={}),
    )
    assert cancel.reason == "ordered by mistake"
    items = await llm.extract(
        ItemsTurn,
        prompts.turn(
            "exchange_items",
            "swap the thermostat for the Google Home one",
            last_agent=None,
            known={"order_id": "#W2378156"},
            catalog=[
                {
                    "product": "Smart Thermostat",
                    "current_options": {"compatibility": "Apple HomeKit", "color": "black"},
                    "available_option_values": {
                        "compatibility": ["Amazon Alexa", "Apple HomeKit", "Google Assistant"],
                        "color": ["black", "stainless steel", "white"],
                    },
                }
            ],
        ),
    )
    assert items.changes and any(
        p.value == "Google Assistant" for c in items.changes for p in c.desired
    )
    text = await llm.write(prompts.ask("Which order would you like to cancel?", "cancel please"))
    assert safe_reply(text)
