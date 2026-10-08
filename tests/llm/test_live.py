"""Live checks against NVIDIA's API. Run with: pytest -m live (needs NVIDIA_API_KEY in .env)."""

import pytest

from customer_workflow_agent.graph.subgraphs.cancel_order import SPEC as CANCEL
from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.client import FALLBACK, PRIMARY
from customer_workflow_agent.llm.guard import safe_reply
from customer_workflow_agent.llm.schemas import CancelTurn, Classification, ItemsTurn
from customer_workflow_agent.llm.service import RouterLLMService
from customer_workflow_agent.resolve.ids import literal_in
from customer_workflow_agent.settings import Settings

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_settings() -> Settings:
    s = Settings()
    if s.nvidia_api_key is None:
        pytest.skip("NVIDIA_API_KEY not set")
    return s


@pytest.mark.parametrize("model", [PRIMARY, FALLBACK])
async def test_each_model_accepts_strict_json_schema(live_settings, model):
    llm = RouterLLMService(live_settings, model=model)
    p = prompts.classification(
        "Please cancel order #W1234567, and also what's my gift card balance?", None
    )
    out = await llm.extract(Classification, p)
    types = [r.type for r in out.requests]
    assert "cancel_order" in types and "info" in types


async def test_service_extracts_turns_and_writes_safe_text(live_settings):
    llm = RouterLLMService(live_settings)
    cancel = await llm.extract(
        CancelTurn,
        prompts.turn("cancel_order", "I bought it by accident", last_agent=None, known={}),
    )
    assert cancel.reason == "ordered by mistake"
    assert literal_in(cancel.reason_quote, "I bought it by accident")
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


@pytest.mark.parametrize("model", [PRIMARY, FALLBACK])
async def test_no_reason_is_kept_when_none_was_given(live_settings, model):
    """ISSUES.md #1: Lightning used to fill in "no longer needed" here."""
    text = "please cancel order #W8770097"
    llm = RouterLLMService(live_settings, model=model)
    turn = await llm.extract(
        CancelTurn, prompts.turn("cancel_order", text, last_agent=None, known={})
    )
    assert "reason" not in CANCEL.merge({}, turn, text, None)
