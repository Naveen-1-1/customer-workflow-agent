"""Conversation flows through the real graph, with a scripted LLM and the real τ²-bench data."""

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from customer_workflow_agent.deps import Deps
from customer_workflow_agent.graph.subgraphs.cancel_order import gave_reason
from customer_workflow_agent.llm.fake import FailingLLM, ScriptedLLM
from customer_workflow_agent.llm.schemas import (
    AddressFields,
    AddressTurn,
    CancelTurn,
    Classification,
    InfoQuery,
    ItemChange,
    ItemRef,
    ItemsTurn,
    OptionPair,
    PaymentRef,
    PaymentTurn,
    ReturnTurn,
)
from customer_workflow_agent.store.models import WorkingDB
from customer_workflow_agent.templates import messages as M
from tests.conftest import sara_db
from tests.graph.helpers import (
    ChatDriver,
    auth_ex,
    classified,
    make,
    new_store,
    req,
    settings,
)

IVAN = (
    "ivan.santos3158@example.com"  # pending #W8770097 (office chair, PayPal), delivered #W6893533
)
IVAN_PENDING = "#W8770097"
YUSUF_ORDER = "#W2378156"  # someone else's order


def ivan_llm() -> ScriptedLLM:
    return ScriptedLLM().on(type(auth_ex()), IVAN, auth_ex(email=IVAN))


async def verified_chat(db: WorkingDB, llm, email: str = IVAN, **overrides):
    store, backend = new_store(db)
    chat = ChatDriver(Deps(store, llm, settings(**overrides)))
    await chat.start()
    await chat.say(f"hi, my email is {email}")
    assert chat.agent_texts()[-1] == M.HOW_CAN_I_HELP
    return chat, store, backend


# The words a customer uses for each reason in these tests (the agent keeps a reason only if
# its quote is in the customer's message).
QUOTES = {
    "no longer needed": "no longer needed",
    "ordered by mistake": "by mistake",
    "other": "cheaper",
}


def cancel_turn(text_reason=None, quote=None, **kw):
    return make(CancelTurn, reason=text_reason, reason_quote=quote or QUOTES.get(text_reason), **kw)


@pytest.mark.parametrize(
    "quote", [None, "I don't need it anymore"], ids=["no-quote", "quote-not-in-message"]
)
async def test_a_reason_the_customer_never_gave_is_not_used(real_db, quote):
    # ISSUES.md #1: Lightning returned reason "no longer needed" for "please cancel order #W…".
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn("no longer needed", quote=quote or ""))
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"please cancel order {IVAN_PENDING}")
    assert "why you'd like to cancel" in chat.last_agent()
    assert chat.pause["type"] == "await_customer"


@pytest.mark.parametrize(
    "reason,quote,text,ok",
    [
        ("no longer needed", "no longer needed", "it's no longer needed", True),
        ("no longer needed", "I don't need it", "cancel it, I don't need it", True),
        ("no longer needed", "changed my mind", "I changed my mind", True),
        ("no longer needed", "don't want it anymore", "I don't want it anymore", True),
        ("ordered by mistake", "by accident", "I bought it by accident", True),
        ("ordered by mistake", "ordered the wrong one", "I ordered the wrong one", True),
        ("other", "found it cheaper", "cancel it because I found it cheaper", True),
        # what Lightning returned when no reason was given: the request itself as the "quote"
        (
            "no longer needed",
            "please cancel order #W8770097",
            "please cancel order #W8770097",
            False,
        ),
        ("other", "please cancel order #W8770097", "please cancel order #W8770097", False),
        ("no longer needed", "I don't need it", "please cancel it", False),  # not in the text
        ("no longer needed", None, "please cancel it", False),
        ("no longer needed", "I need to cancel this", "I need to cancel this", False),
    ],
)
def test_gave_reason(reason, quote, text, ok):
    assert gave_reason(reason, quote, text) is ok


async def test_cancel_happy_path_writes_only_after_yes(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(CancelTurn, "longer", cancel_turn("no longer needed"))
    llm.on(Classification, "bye", classified(goodbye=True))
    chat, store, backend = await verified_chat(real_db, llm)

    await chat.say(f"I want to cancel order {IVAN_PENDING}")
    assert "why you'd like to cancel" in chat.last_agent()
    await chat.say("it's no longer needed")
    assert chat.pause["type"] == "confirm"
    assert chat.pause["summary"]["title"] == f"Cancel order {IVAN_PENDING}"
    assert chat.pause["summary"]["amount"] == 544.29
    assert backend.saves == 0  # nothing written before the yes

    await chat.click(True)
    assert store.get_order(IVAN_PENDING).status == "cancelled"
    assert "is cancelled" in chat.agent_texts()[-2]
    assert chat.last_agent() == M.ANYTHING_ELSE

    await chat.say("that's all, bye")
    assert chat.ended and chat.values["end_reason"] == "goodbye"
    assert [o["status"] for o in chat.values["outcomes"]] == ["done"]


async def test_no_in_popup_then_change_then_yes(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn("no longer needed"))
    llm.on(CancelTurn, "mistake", cancel_turn("ordered by mistake"))
    chat, store, _ = await verified_chat(real_db, llm)

    await chat.say(f"cancel {IVAN_PENDING}, no longer needed")
    await chat.click(False)
    assert chat.last_agent() == M.DECLINED
    assert store.get_order(IVAN_PENDING).status == "pending"
    await chat.say("actually I ordered it by mistake")
    assert "Reason: ordered by mistake" in chat.pause["summary"]["lines"]
    await chat.click(True)
    assert store.get_order(IVAN_PENDING).cancel_reason == "ordered by mistake"


async def test_cancel_with_other_reason_is_denied(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn("other"))
    chat, store, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING} because I found it cheaper")
    assert "no longer needed or was ordered by mistake" in chat.agent_texts()[-2]
    assert chat.last_agent() == M.ANYTHING_ELSE
    assert store.get_order(IVAN_PENDING).status == "pending"


async def test_other_users_order_looks_not_found(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", YUSUF_ORDER)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel order {YUSUF_ORDER}")
    text = chat.last_agent()
    assert f"I couldn't find order {YUSUF_ORDER} on your account." in text
    assert IVAN_PENDING in text  # his own pending orders are offered instead
    assert "Yusuf" not in text and "Office" not in text.split("\n")[0]


async def test_wrong_status_denied_and_chat_continues(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", "#W6893533")))
    llm.on(CancelTurn, "cancel", cancel_turn())
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say("cancel #W6893533")
    assert "already been delivered" in chat.agent_texts()[-2]
    assert chat.last_agent() == M.ANYTHING_ELSE
    assert chat.values["outcomes"][-1]["code"] == "status_delivered_not_pending"


async def test_choose_order_from_numbered_list(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order")))
    llm.on(CancelTurn, "cancel an order", cancel_turn())
    llm.on(CancelTurn, "second", cancel_turn(choice_number=2))
    llm.on(CancelTurn, "mistake", cancel_turn("ordered by mistake"))
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say("I want to cancel an order")
    assert "Which order would you like to cancel?" in chat.last_agent()
    pending = [line.split(" — ")[0][3:] for line in chat.last_agent().splitlines()[1:]]
    await chat.say("the second one")
    await chat.say("ordered by mistake")
    assert chat.pause["summary"]["title"] == f"Cancel order {pending[1]}"


async def test_auth_by_name_and_zip_across_turns(real_db):
    llm = ScriptedLLM()
    llm.on(type(auth_ex()), "Yusuf", auth_ex(first_name="Yusuf", last_name="Rossi"))
    llm.on(type(auth_ex()), "19122", auth_ex(zip="19122"))
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, llm, settings()))
    await chat.start()
    await chat.say("I'm Yusuf Rossi")
    assert chat.last_agent() == M.auth_missing(["zip code"])
    await chat.say("19122")
    assert chat.values["user_id"] == "yusuf_rossi_9620"


async def test_three_failed_verifications_offer_human(real_db):
    llm = ScriptedLLM().on(
        type(auth_ex()), "@", lambda p: auth_ex(email=p.customer_text.split()[-1])
    )
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, llm, settings()))
    await chat.start()
    for i in range(2):
        await chat.say(f"email is nobody{i}@example.com")
        assert "couldn't find an account" in chat.last_agent()
    await chat.say("email is nobody9@example.com")
    assert chat.pause["type"] == "confirm" and chat.pause["action"] == "transfer"
    await chat.click(False)  # keep trying
    assert chat.last_agent() == M.AUTH_RETRY
    await chat.say(f"email is {IVAN}")
    assert chat.values["user_id"] == "ivan_santos_6635"


async def test_failed_verification_then_accept_transfer(real_db):
    llm = ScriptedLLM().on(
        type(auth_ex()), "@", lambda p: auth_ex(email=p.customer_text.split()[-1])
    )
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, llm, settings(auth_max_attempts=1)))
    await chat.start()
    await chat.say("email is nobody@example.com")
    await chat.click(True)
    assert chat.last_agent() == M.TRANSFER_MESSAGE
    assert chat.ended and chat.values["end_reason"] == "transferred"
    assert store.transfers()[0].user_id is None


async def test_request_before_verification_is_kept(real_db):
    llm = ScriptedLLM()
    llm.on(type(auth_ex()), "cancel", auth_ex(email=IVAN, has_request=True))
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn("no longer needed"))
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, llm, settings()))
    await chat.start()
    await chat.say(f"cancel {IVAN_PENDING}, no longer needed. my email is {IVAN}")
    assert chat.pause["type"] == "confirm"
    assert any(t.startswith("Thanks, Ivan") for t in chat.agent_texts())


async def test_two_requests_in_one_message_run_in_order(real_db):
    llm = ivan_llm()
    llm.on(
        Classification,
        "and",
        classified(req("cancel_order", IVAN_PENDING), req("modify_default_address")),
    )
    llm.on(CancelTurn, "and", cancel_turn("no longer needed"))
    llm.on(
        AddressTurn,
        "and",
        make(
            AddressTurn,
            address=AddressFields(
                address1="1 Main St",
                address2=None,
                city="Austin",
                state="Texas",
                zip="78701",
                country=None,
            ),
        ),
    )
    chat, store, _ = await verified_chat(real_db, llm)
    await chat.say(
        f"cancel {IVAN_PENDING} since it's no longer needed and change my address "
        "to 1 Main St, Austin, Texas 78701"
    )
    assert chat.pause["summary"]["title"].startswith("Cancel order")
    await chat.click(True)
    assert chat.pause["summary"]["title"] == "Change your default address"
    assert "To: 1 Main St, Austin, TX 78701, USA" in chat.pause["summary"]["lines"]
    await chat.click(True)
    assert store.get_user("ivan_santos_6635").address.city == "Austin"


async def test_new_topic_mid_request_waits_for_current(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(
        CancelTurn,
        "address",
        cancel_turn(relation="also_new_request", new_requests=[req("modify_default_address")]),
    )
    llm.on(CancelTurn, "mistake", cancel_turn("ordered by mistake"))
    llm.on(AddressTurn, "address", make(AddressTurn))
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.say("oh and I also need to change my address")
    assert M.QUEUED in chat.agent_texts()[-2]
    assert "why you'd like to cancel" in chat.last_agent()  # current request continues
    await chat.say("ordered by mistake")
    await chat.click(True)
    assert "new default address" in chat.last_agent()  # then the queued one


async def test_skip_drops_current_request(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(CancelTurn, "skip", cancel_turn(relation="skip"))
    chat, store, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.say("skip it")
    assert M.SKIPPED in chat.agent_texts()
    assert chat.last_agent() == M.ANYTHING_ELSE
    assert store.get_order(IVAN_PENDING).status == "pending"


async def test_goodbye_mid_request_ends_without_change(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(CancelTurn, "bye", cancel_turn(relation="goodbye"))
    chat, store, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.say("never mind, bye")
    assert chat.ended and chat.last_agent() == M.GOODBYE
    assert store.get_order(IVAN_PENDING).status == "pending"


async def test_items_then_address_on_same_order_is_denied():
    db = sara_db()
    llm = ScriptedLLM().on(type(auth_ex()), "sara", auth_ex(email="sara.doe@example.com"))
    llm.on(
        Classification,
        "size",
        classified(
            req("modify_order_items", "#W0000000"), req("modify_order_address", "#W0000000")
        ),
    )
    llm.on(
        ItemsTurn,
        "size",
        make(
            ItemsTurn,
            changes=[
                ItemChange(
                    item=ItemRef(product="t-shirt", options=[], quantity=None),
                    desired=[OptionPair(name="size", value="L")],
                )
            ],
            payment=PaymentRef(kind="credit_card", brand="visa", last_four=None),
        ),
    )
    llm.on(
        AddressTurn,
        "size",
        make(
            AddressTurn,
            address=AddressFields(
                address1="9 Elm St",
                address2=None,
                city="Boston",
                state="MA",
                zip="02101",
                country=None,
            ),
        ),
    )
    chat, store, _ = await verified_chat(db, llm, email="sara.doe@example.com")
    await chat.say(
        "change the t-shirt in #W0000000 to size L with my visa, and ship it to "
        "9 Elm St, Boston MA 02101"
    )
    assert M.ITEMS_ONCE_REMINDER in chat.agent_texts()
    assert any("size: M" in line and "size: L" in line for line in chat.pause["summary"]["lines"])
    await chat.click(True)
    assert store.get_order("#W0000000").status == "pending (item modified)"
    assert "already been changed once" in chat.agent_texts()[-2]


async def test_modify_payment_to_gift_card(real_db):
    omar = "omar.kim8981@example.com"
    llm = ScriptedLLM().on(type(auth_ex()), omar, auth_ex(email=omar))
    llm.on(Classification, "gift", classified(req("modify_order_payment", "#W1080318")))
    llm.on(
        PaymentTurn,
        "gift",
        make(PaymentTurn, payment=PaymentRef(kind="gift_card", brand=None, last_four=None)),
    )
    chat, store, _ = await verified_chat(real_db, llm, email=omar)
    await chat.say("pay #W1080318 with my gift card instead")
    lines = chat.pause["summary"]["lines"]
    assert "New payment method: gift card (balance $91.00)" in lines
    await chat.click(True)
    gift = store.get_user("omar_kim_3528").payment_methods["gift_card_3749819"]
    assert gift.balance == pytest.approx(91.0 - 53.43)


RAJ = "raj.sanchez2046@example.com"
RAJ_BIG = "#W1067251"  # delivered, $1,201.55, paid by credit_card_3362387


def raj_return_llm() -> ScriptedLLM:
    llm = ScriptedLLM().on(type(auth_ex()), RAJ, auth_ex(email=RAJ))
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
    return llm


async def test_large_return_approved_by_supervisor(real_db):
    chat, store, _ = await verified_chat(
        real_db, raj_return_llm(), email=RAJ, approval_enabled=True
    )
    await chat.say(f"I want to return everything in {RAJ_BIG} to my card")
    assert chat.pause["summary"]["amount"] == 1201.55
    await chat.click(True)
    assert chat.pause["type"] == "supervisor_approval"
    assert chat.pause["request"]["refund_total"] == 1201.55
    assert chat.last_agent() == M.APPROVAL_WAIT
    assert store.get_order(RAJ_BIG).status == "delivered"
    await chat.supervise(True)
    assert store.get_order(RAJ_BIG).status == "return requested"
    assert M.APPROVAL_GRANTED in chat.agent_texts()


async def test_large_return_rejected_blocks_order_and_offers_transfer(real_db):
    chat, store, _ = await verified_chat(
        real_db, raj_return_llm(), email=RAJ, approval_enabled=True
    )
    await chat.say(f"return everything in {RAJ_BIG}")
    await chat.click(True)
    await chat.supervise(False, note="Items look used")
    assert M.approval_rejected("Items look used") in chat.agent_texts()
    assert chat.pause["action"] == "transfer"
    assert store.get_order(RAJ_BIG).status == "delivered"
    assert store.returns_blocked(RAJ_BIG)
    await chat.click(True)
    assert chat.last_agent() == M.TRANSFER_MESSAGE and chat.ended

    # A new chat can't retry a return on that order (blocked until reset).
    chat2 = ChatDriver(Deps(store, raj_return_llm(), settings(approval_enabled=True)), thread="t2")
    await chat2.start()
    await chat2.say(f"my email is {RAJ}")
    await chat2.say(f"return everything in {RAJ_BIG}")
    assert "earlier return on it wasn't approved" in chat2.agent_texts()[-2]


async def test_large_return_without_approval_setting_goes_through(real_db):
    chat, store, _ = await verified_chat(real_db, raj_return_llm(), email=RAJ)
    await chat.say(f"return everything in {RAJ_BIG}")
    await chat.click(True)
    assert chat.pause["type"] == "await_customer"
    assert store.get_order(RAJ_BIG).status == "return requested"


async def test_refund_must_go_to_original_or_gift_card(real_db):
    llm = ScriptedLLM().on(type(auth_ex()), RAJ, auth_ex(email=RAJ))
    llm.on(Classification, "return", classified(req("return_items", RAJ_BIG)))
    llm.on(
        ReturnTurn,
        "return",
        make(
            ReturnTurn,
            items=[ItemRef(product="earbuds", options=[], quantity=None)],
            refund_to=PaymentRef(kind="paypal", brand=None, last_four=None),
        ),
    )
    chat, _, _ = await verified_chat(real_db, llm, email=RAJ)
    await chat.say(f"return the earbuds from {RAJ_BIG} to paypal")
    text = chat.last_agent()
    assert "Where should the refund go?" in text
    assert "Mastercard" in text or "Visa" in text or "card ending" in text
    assert "gift card" in text


async def test_llm_outage_still_verifies_and_apologizes(real_db):
    store, _ = new_store(real_db)
    chat = ChatDriver(Deps(store, FailingLLM(), settings()))
    await chat.start()
    await chat.say(f"my email is {IVAN}")
    assert chat.values["user_id"] == "ivan_santos_6635"
    await chat.say("cancel my order")
    assert chat.last_agent() == M.LLM_TROUBLE
    await chat.say("get me a human please")
    assert chat.last_agent() == M.TRANSFER_MESSAGE


async def test_transfer_request_ends_chat(real_db):
    llm = ivan_llm().on(Classification, "human", classified(req("transfer")))
    chat, store, _ = await verified_chat(real_db, llm)
    await chat.say("I want to talk to a human")
    assert chat.last_agent() == "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."
    assert chat.ended and store.transfers()[0].user_id == "ivan_santos_6635"


async def test_info_answers_from_templates(real_db):
    llm = ivan_llm().on(Classification, "status", classified(req("info")))
    llm.on(
        InfoQuery,
        "status",
        InfoQuery(topic="order_details", order_ids=[IVAN_PENDING], product=None),
    )
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"what's the status of {IVAN_PENDING}?")
    assert f"Order {IVAN_PENDING} — status: pending" in chat.agent_texts()[-2]


async def test_info_never_shows_other_users_orders(real_db):
    llm = ivan_llm().on(Classification, "status", classified(req("info")))
    llm.on(
        InfoQuery, "status", InfoQuery(topic="order_details", order_ids=[YUSUF_ORDER], product=None)
    )
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"what's the status of {YUSUF_ORDER}?")
    assert chat.agent_texts()[-2] == f"I couldn't find order {YUSUF_ORDER} on your account."


async def test_unclear_message_gets_template(real_db):
    llm = ivan_llm().on(Classification, "weather", classified())
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say("how's the weather?")
    assert chat.last_agent() == M.NOT_UNDERSTOOD


async def test_messages_inside_subgraph_are_visible_while_paused(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    # The question was emitted inside the cancel subgraph, which is paused.
    assert "why you'd like to cancel" in chat.last_agent()
    assert chat.pause["messages"][-1]["text"] == chat.last_agent()


async def test_state_survives_sqlite_checkpointer(real_db, tmp_path):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn("no longer needed"))
    store, _ = new_store(real_db)
    deps = Deps(store, llm, settings())
    path = tmp_path / "cp.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        chat = ChatDriver(deps, saver)
        await chat.start()
        await chat.say(f"email {IVAN}")
        await chat.say(f"cancel {IVAN_PENDING}, no longer needed")
        assert chat.pause["type"] == "confirm"
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:  # "restart"
        chat = ChatDriver(deps, saver)
        assert (await chat.reload())["type"] == "confirm"
        await chat.click(True)
        assert store.get_order(IVAN_PENDING).status == "cancelled"


async def test_order_id_the_customer_never_typed_is_ignored(real_db):
    # The classifier's restatement mentions an order number the customer didn't type.
    llm = ivan_llm()
    llm.on(
        Classification,
        "cancel",
        classified(req("cancel_order", details=f"cancel order {IVAN_PENDING}")),
    )
    llm.on(CancelTurn, "cancel", cancel_turn(order_id=IVAN_PENDING))
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say("I want to cancel my order")
    assert "Which order would you like to cancel?" in chat.last_agent()


# ---- mislabels seen from the live models (code must not act on them) -------------------


async def test_skip_label_without_skip_words_is_treated_as_an_answer(real_db):
    # Lightning labelled "I bought it by accident" as relation=skip while extracting the reason.
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(
        CancelTurn,
        "accident",
        cancel_turn("ordered by mistake", quote="by accident", relation="skip"),
    )
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.say("I bought it by accident")
    assert chat.pause["type"] == "confirm"
    assert M.SKIPPED not in chat.agent_texts()


async def test_echo_of_current_request_is_not_queued_again(real_db):
    # Super returned the current cancel as a "new request" alongside the answer.
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(
        CancelTurn,
        "accident",
        cancel_turn(
            "ordered by mistake",
            quote="by accident",
            new_requests=[req("cancel_order", details="I bought it by accident")],
        ),
    )
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.say("I bought it by accident")
    assert M.QUEUED not in chat.agent_texts()
    await chat.click(True)
    assert chat.last_agent() == M.ANYTHING_ELSE  # no second cancel request
    assert chat.values["queue"] == []


async def test_human_label_needs_the_customer_to_ask(real_db):
    llm = ivan_llm()
    llm.on(Classification, "cancel", classified(req("cancel_order", IVAN_PENDING)))
    llm.on(CancelTurn, "cancel", cancel_turn())
    llm.on(CancelTurn, "mistake", cancel_turn("ordered by mistake", relation="wants_human"))
    chat, _, _ = await verified_chat(real_db, llm)
    await chat.say(f"cancel {IVAN_PENDING}")
    await chat.say("ordered by mistake")
    assert not any(r["type"] == "transfer" for r in chat.values.get("queue") or [])
