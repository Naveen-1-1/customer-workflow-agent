"""Reply buttons: what each pause offers, and that they match what the agent just said."""

import pytest

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.llm.fake import ScriptedLLM
from customer_workflow_agent.llm.schemas import CancelTurn, Classification, ItemsTurn
from customer_workflow_agent.llm.structured import LLMUnavailable
from customer_workflow_agent.store import RetailStore
from customer_workflow_agent.templates import suggestions as S
from tests.graph.helpers import ChatDriver, auth_ex, classified, make, new_store, req, settings
from tests.graph.test_flows import IVAN_PENDING, ivan_llm, verified_chat


def numbered_lines(text: str) -> list[str]:
    return [line.split(". ", 1)[1] for line in text.splitlines() if line[:1].isdigit()]


def labels(pause: dict) -> list[str]:
    return [s["label"] for s in pause["suggestions"]]


async def test_the_greeting_offers_the_demo_scenarios(real_db):
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, ScriptedLLM(), settings()))
    await chat.start()
    assert chat.pause["suggestions"] == S.DEMO_SCENARIOS
    assert len(S.DEMO_SCENARIOS) == 3


@pytest.mark.parametrize(
    "first,last,zip_code,order_id",
    [
        ("Yusuf", "Rossi", "19122", "#W2378156"),
        ("Fatima", "Johnson", "78712", None),
        ("Mei", "Davis", "80217", None),
    ],
)
def test_demo_scenarios_name_real_customers(
    real_store: RetailStore, first, last, zip_code, order_id
):
    (scenario,) = [s for s in S.DEMO_SCENARIOS if first in s["text"]]
    assert last in scenario["text"] and zip_code in scenario["text"]
    user_id = real_store.find_user_id_by_name_zip(first, last, zip_code)
    if order_id:
        assert order_id in scenario["text"]
        assert real_store.get_order(order_id).user_id == user_id


async def test_a_demo_scenario_verifies_then_offers_that_orders_items(real_db):
    (yusuf,) = [s for s in S.DEMO_SCENARIOS if "Yusuf" in s["text"]]
    llm = ScriptedLLM()
    llm.on(
        type(auth_ex()),
        "Yusuf",
        auth_ex(first_name="Yusuf", last_name="Rossi", zip="19122", has_request=True),
    )
    llm.on(Classification, "exchange", classified(req("exchange_items", "#W2378156")))
    llm.on(ItemsTurn, "exchange", make(ItemsTurn))  # no items named yet
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, llm, settings()))
    await chat.start()
    await chat.say(yusuf["text"])

    # The order has 5 items: the message lists all of them, the buttons the first 3.
    listed = numbered_lines(chat.last_agent())
    assert len(listed) == 5
    assert labels(chat.pause) == listed[:3]
    assert all(s["text"] == s["label"] for s in chat.pause["suggestions"])


async def test_buttons_follow_the_conversation_and_clear_after_each_reply(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel an order", classified(req("cancel_order")))
    llm.on(CancelTurn, "cancel an order", make(CancelTurn, reason=None, reason_quote=None))
    llm.on(
        CancelTurn,
        IVAN_PENDING,
        make(CancelTurn, order_id=IVAN_PENDING, reason=None, reason_quote=None),
    )
    llm.on(
        CancelTurn,
        "no longer need",
        make(CancelTurn, reason="no longer needed", reason_quote="I no longer need it"),
    )
    llm.on(CancelTurn, "skip", make(CancelTurn, relation="skip", reason=None, reason_quote=None))
    chat, _, _ = await verified_chat(real_db, llm)
    assert chat.pause["suggestions"] == S.REQUEST_TYPES

    await chat.say("I'd like to cancel an order")
    # Each button shows an order's summary line from the list, and sends just its id.
    buttons = chat.pause["suggestions"]
    assert labels(chat.pause) == numbered_lines(chat.last_agent())
    assert [b["text"] for b in buttons] == [f"Order {b['label'].split()[0]}" for b in buttons]
    (order,) = [b for b in buttons if IVAN_PENDING in b["text"]]

    await chat.say(order["text"])
    assert chat.pause["suggestions"] == S.CANCEL_REASONS

    await chat.say("I no longer need it")
    assert chat.pause["type"] == "confirm" and "suggestions" not in chat.pause

    await chat.click(False)
    assert chat.pause["suggestions"] == S.SKIP_REQUEST

    await chat.say(S.SKIP_REQUEST[0]["text"])
    assert chat.pause["suggestions"] == S.ANYTHING_ELSE
    assert chat.values["suggestions"] == S.ANYTHING_ELSE


async def test_a_pause_with_no_question_offers_no_buttons(real_db):
    # The LLM is down after verification: the agent apologizes and offers nothing stale.
    def down(prompt):
        raise LLMUnavailable("down")

    llm = ivan_llm().on(Classification, "hmm", down)
    chat, _, _ = await verified_chat(real_db, llm)
    assert chat.pause["suggestions"] == S.REQUEST_TYPES
    await chat.say("hmm")
    assert chat.pause["type"] == "await_customer"
    assert chat.pause["suggestions"] == []
