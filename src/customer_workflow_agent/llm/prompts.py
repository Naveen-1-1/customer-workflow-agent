"""Prompt builders. Each prompt sees only what its task needs, and never another user's data.

Every prompt has a name and a version. Bump the version in VERSIONS whenever you change a
prompt's wording; tests/llm/test_prompt_versions.py fails until you do.
"""

import json

from customer_workflow_agent.llm.service import Prompt

VERSIONS = {
    "classification": 1,
    "auth": 1,
    "turn.cancel_order": 2,
    "turn.modify_order_address": 1,
    "turn.modify_order_payment": 1,
    "turn.modify_order_items": 1,
    "turn.exchange_items": 1,
    "turn.return_items": 1,
    "turn.modify_default_address": 1,
    "info": 1,
    "ask": 1,
    "small_talk": 1,
}


def _prompt(name: str, system: str, user: str, customer_text: str) -> Prompt:
    return Prompt(system, user, customer_text, name=name, version=VERSIONS[name])


_JSON_RULES = (
    "Reply with a JSON object that matches the given schema exactly. Use null for anything "
    "the customer did not say. Never guess or invent ids, names, emails, numbers or options."
)

REQUEST_TYPE_GUIDE = """Request types:
- cancel_order: cancel an order that hasn't shipped (pending)
- modify_order_address: change the shipping address of a pending order
- modify_order_payment: change the payment method of a pending order
- modify_order_items: change options (size, color, ...) of items in a pending order
- return_items: return items from a delivered order
- exchange_items: swap delivered items for a different option of the same product
- modify_default_address: change the default address on the customer's account
- info: questions about their own orders (status, items, tracking), profile, payment methods,
  or which options/prices a product has
- transfer: they ask for a human agent, or want something about their orders/account that
  none of the types above covers (e.g. undo a cancellation, place a new order, a complaint)"""


def classification(customer_text: str, last_agent: str | None) -> Prompt:
    system = f"""You read one message from a retail customer and list what they want done.
{REQUEST_TYPE_GUIDE}

Rules:
- One entry per distinct request, in the order the customer said them.
- order_id: only if the customer wrote an order number in this message.
- all_eligible_orders: true only for requests about all of their orders of a kind
  ("cancel all my pending orders").
- details: a short restatement of that request in the customer's words, including any items,
  options, reasons, addresses or payment methods they mentioned.
- goodbye: true if they say they are done or say goodbye.
- about_other_person: true if they ask about someone else's account or orders.
- Small talk, thanks, or unclear messages: return an empty requests list.
{_JSON_RULES}"""
    user = f"Agent's last message: {last_agent or '(none)'}\nCustomer message: {customer_text}"
    return _prompt("classification", system, user, customer_text)


def auth(customer_text: str) -> Prompt:
    system = f"""A retail customer must verify their identity with either their email, or their
first name, last name and zip code. Extract those from the message.
- wants_human: they ask for a human agent.
- goodbye: they want to end the chat.
- has_request: they also mention something they need help with (an order, return, etc.).
{_JSON_RULES}"""
    return _prompt("auth", system, f"Customer message: {customer_text}", customer_text)


_TASKS = {
    "cancel_order": (
        "cancelling a pending order",
        "reason: 'no longer needed' or 'ordered by mistake' if the customer's reason means one of "
        "these; 'other' for any other reason; null if they gave none. reason_quote: the exact "
        "words from the customer's message that give the reason, copied character for character; "
        "null if they gave none. Never fill in a reason they didn't give.",
    ),
    "modify_order_address": (
        "changing the shipping address of a pending order",
        "address: the new address parts they gave. use_profile_address: true if they want the "
        "default address on their account.",
    ),
    "modify_order_payment": (
        "changing the payment method of a pending order",
        "payment: the payment method they want to use (kind, card brand, last four digits).",
    ),
    "modify_order_items": (
        "changing items in a pending order to different options of the same product",
        "changes: one entry per item to change. item.product: the product as they named it; "
        "item.options: option values they used to say which one they have now; desired: the new "
        "option values they want, using the exact option names and values from the product list "
        "below when they match. payment: the payment method for any price difference.",
    ),
    "exchange_items": (
        "exchanging delivered items for different options of the same product",
        "changes: one entry per item to exchange. item.product: the product as they named it; "
        "item.options: option values they used to say which one they have; desired: the new "
        "option values they want, using the exact option names and values from the product list "
        "below when they match. payment: the payment method for any price difference.",
    ),
    "return_items": (
        "returning items from a delivered order",
        "items: one entry per item to return (product as they named it, distinguishing options, "
        "quantity if they said a number). all_items: true if they want to return everything. "
        "refund_to: the payment method they want the refund on.",
    ),
    "modify_default_address": (
        "changing the default address on the customer's account",
        "address: the new address parts they gave.",
    ),
}

_RELATIONS = """relation:
- answer: they are answering or continuing this request
- also_new_request: they also ask for something different; put it in new_requests
- skip: they explicitly want to drop this request ("skip", "never mind", "forget it")
- goodbye: they want to end the chat
- wants_human: they ask for a human agent
- unclear: none of the above
choice_number: if they pick a numbered option from the agent's last message, its number."""


def turn(
    request_type: str,
    customer_text: str,
    *,
    last_agent: str | None,
    known: dict,
    catalog: list[dict] | None = None,
) -> Prompt:
    what, fields = _TASKS[request_type]
    system = f"""You are helping with {what}. Read the customer's latest message and extract
details for this request.
{fields}
order_id: only if the customer wrote an order number in this message.
{_RELATIONS}
new_requests: other requests they mention, using these types:
{REQUEST_TYPE_GUIDE}
{_JSON_RULES}"""
    parts = [f"Details collected so far: {json.dumps(known, ensure_ascii=False)}"]
    if catalog:
        parts.append(
            "Items in this order and each product's available options:\n"
            + json.dumps(catalog, ensure_ascii=False)
        )
    parts.append(f"Agent's last message: {last_agent or '(none)'}")
    parts.append(f"Customer message: {customer_text}")
    return _prompt(f"turn.{request_type}", system, "\n".join(parts), customer_text)


def info(customer_text: str, product_names: list[str]) -> Prompt:
    system = f"""A verified retail customer asks a question. Pick the topic:
- order_details: about specific orders (status, items, tracking, address, payment)
- order_list: which orders they have
- profile: their name, email or default address
- payment_methods: their payment methods or gift card balance
- product_variants: which options/prices/availability a product has
- unknown: anything else
order_ids: order numbers they wrote. product: the product they ask about, using a name from
this list if one matches: {", ".join(product_names)}.
{_JSON_RULES}"""
    return _prompt("info", system, f"Customer message: {customer_text}", customer_text)


_WRITER_RULES = (
    "Write one or two short, friendly sentences. Do not mention any numbers, ids, amounts, "
    "prices, dates, order details, statuses or policies, and do not promise anything. "
    "Reply with the sentences only."
)


def ask(question: str, customer_text: str) -> Prompt:
    system = f"""You are a retail customer-service agent. Rephrase the question below naturally
for the customer, keeping its meaning exactly. {_WRITER_RULES}"""
    user = f"Question to ask: {question}\nCustomer's last message: {customer_text}"
    return _prompt("ask", system, user, customer_text)


def small_talk(customer_text: str) -> Prompt:
    system = f"""You are a retail customer-service agent. The customer's message isn't a request
you can act on. Reply politely and briefly, and offer help with their orders (cancellations,
changes, returns, exchanges) or their account address. Do not answer general-knowledge or
off-topic questions. {_WRITER_RULES}"""
    return _prompt("small_talk", system, f"Customer message: {customer_text}", customer_text)
