"""τ²-bench tasks played through our graph (LLM answers scripted): the final store must match
replaying the task's expected actions on a fresh store."""

import json

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.llm.fake import ScriptedLLM
from customer_workflow_agent.llm.schemas import (
    AuthExtraction,
    CancelTurn,
    Classification,
    ItemChange,
    ItemRef,
    ItemsTurn,
    OptionPair,
    PaymentRef,
    ReturnTurn,
)
from customer_workflow_agent.store.models import WorkingDB
from tests.conftest import PROJECT_ROOT
from tests.graph.helpers import ChatDriver, auth_ex, classified, make, new_store, req, settings
from tests.store.test_tasks_replay import apply_action, write_actions

TASKS = {t["id"]: t for t in json.loads((PROJECT_ROOT / "data" / "tasks.json").read_text())}
BOOKKEEPING = {"ledger", "transfers", "return_blocked_orders"}


def expected_db(task_id: str, db: WorkingDB) -> dict:
    store, _ = new_store(db)
    for i, action in enumerate(write_actions(TASKS[task_id])):
        apply_action(store, action["name"], action["arguments"], f"gold-{i}")
    return store._db.model_dump(exclude=BOOKKEEPING)


def choice_index(text: str, needle: str) -> int:
    for line in text.splitlines():
        if needle in line and line[:1].isdigit():
            return int(line.split(".")[0])
    raise AssertionError(f"{needle!r} not offered in:\n{text}")


async def start_verified(db: WorkingDB, llm: ScriptedLLM, intro: str):
    store, _ = new_store(db)
    chat = ChatDriver(Deps(store, llm, settings()))
    await chat.start()
    await chat.say(intro)
    assert chat.values.get("user_id"), chat.agent_texts()
    return chat, store


async def test_task_0_exchange_keyboard_and_thermostat(real_db):
    msg = "I'd like to exchange the keyboard and the thermostat from order #W2378156"
    llm = ScriptedLLM()
    llm.on(AuthExtraction, "Yusuf", auth_ex(first_name="Yusuf", last_name="Rossi", zip="19122"))
    llm.on(Classification, "exchange", classified(req("exchange_items", "#W2378156")))
    llm.on(
        ItemsTurn,
        "exchange",
        make(
            ItemsTurn,
            changes=[
                ItemChange(
                    item=ItemRef(product="mechanical keyboard", options=[], quantity=None),
                    desired=[OptionPair(name="switch type", value="clicky")],
                ),
                ItemChange(
                    item=ItemRef(product="smart thermostat", options=[], quantity=None),
                    desired=[OptionPair(name="compatibility", value="Google Assistant")],
                ),
            ],
        ),
    )
    chat, store = await start_verified(real_db, llm, "I'm Yusuf Rossi, zip 19122")
    await chat.say(msg)

    # No clicky + RGB + full size in stock: the customer picks full size, no backlight.
    n = choice_index(chat.last_agent(), "switch type: clicky, backlight: none, size: full size")
    llm.on(ItemsTurn, "no backlight", make(ItemsTurn, choice_number=n))
    await chat.say("the full size one with no backlight")
    assert "Which payment method" in chat.last_agent()
    llm.on(
        ItemsTurn,
        "credit card",
        make(ItemsTurn, payment=PaymentRef(kind="credit_card", brand=None, last_four=None)),
    )
    await chat.say("my credit card")
    assert chat.pause["type"] == "confirm"
    await chat.click(True)
    assert store._db.model_dump(exclude=BOOKKEEPING) == expected_db("0", real_db)


async def test_task_16_cancel_all_pending_and_return_watch(real_db):
    msg = (
        "Please cancel all my pending orders, I no longer need them, and return the watch "
        "I received"
    )
    llm = ScriptedLLM()
    llm.on(AuthExtraction, "Fatima", auth_ex(first_name="Fatima", last_name="Johnson", zip="78712"))
    llm.on(
        Classification,
        "cancel all",
        classified(req("cancel_order", all_eligible=True), req("return_items")),
    )
    llm.on(CancelTurn, "cancel all", make(CancelTurn, reason="no longer needed"))
    llm.on(
        ReturnTurn,
        "cancel all",
        make(ReturnTurn, items=[ItemRef(product="watch", options=[], quantity=None)]),
    )
    chat, store = await start_verified(real_db, llm, "Fatima Johnson, 78712")
    await chat.say(msg)

    titles = []
    for _ in range(2):  # one popup per pending order
        titles.append(chat.pause["summary"]["title"])
        await chat.click(True)
    assert titles == ["Cancel order #W5199551", "Cancel order #W8665881"]

    assert "Which order would you like to return items from?" in chat.last_agent()
    llm.on(ReturnTurn, "delivered", make(ReturnTurn, choice_number=1))
    await chat.say("the delivered one")
    llm.on(
        ReturnTurn,
        "paypal",
        make(ReturnTurn, refund_to=PaymentRef(kind="paypal", brand=None, last_four=None)),
    )
    await chat.say("back to my paypal")
    assert chat.pause["summary"]["lines"][0].startswith("Smart Watch")
    await chat.click(True)
    assert store._db.model_dump(exclude=BOOKKEEPING) == expected_db("16", real_db)


async def test_task_19_return_then_exchange_on_same_order_is_denied(real_db):
    msg = (
        "I want to return the water bottle and exchange the pet bed and office chair "
        "for the cheapest versions"
    )
    llm = ScriptedLLM()
    llm.on(AuthExtraction, "Mei", auth_ex(first_name="Mei", last_name="Davis", zip="80217"))
    llm.on(Classification, "water bottle", classified(req("return_items"), req("exchange_items")))
    llm.on(
        ReturnTurn,
        "water bottle",
        make(ReturnTurn, items=[ItemRef(product="water bottle", options=[], quantity=None)]),
    )
    llm.on(ItemsTurn, "water bottle", make(ItemsTurn))
    chat, store = await start_verified(real_db, llm, "Mei Davis 80217")
    await chat.say(msg)

    llm.on(ReturnTurn, "first", make(ReturnTurn, choice_number=1))
    await chat.say("the first one")
    llm.on(
        ReturnTurn,
        "card",
        make(ReturnTurn, refund_to=PaymentRef(kind="credit_card", brand=None, last_four=None)),
    )
    await chat.say("to my card")
    await chat.click(True)
    # Return or exchange happens only once per delivered order: the exchange is denied.
    assert "don't have any orders that can be exchanged" in chat.agent_texts()[-2]
    assert store._db.model_dump(exclude=BOOKKEEPING) == expected_db("19", real_db)
