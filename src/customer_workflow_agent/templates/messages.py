"""Fixed customer-facing wording. Facts (ids, amounts, statuses) only ever appear via these."""

from customer_workflow_agent.templates.format import usd

# Exact wording required by the τ²-bench retail policy.
TRANSFER_MESSAGE = "YOU ARE BEING TRANSFERRED TO A HUMAN AGENT. PLEASE HOLD ON."

GREETING = (
    "Hi! I can help with your orders — cancellations, changes, returns and exchanges — "
    "and with your account address. To get started, please share the email on your account, "
    "or your first and last name with your zip code."
)
ASK_AUTH = (
    "Before I can help, I need to verify your identity. Please share the email on your account, "
    "or your first and last name with your zip code."
)
AUTH_OFFER = (
    "I still couldn't verify your account. Would you like me to transfer you to a human agent?"
)
AUTH_RETRY = (
    "Okay, let's try again. Please share your email, or your first and last name with your "
    "zip code."
)
HOW_CAN_I_HELP = "How can I help you today?"
ANYTHING_ELSE = "Is there anything else I can help you with?"
GOODBYE = "Thanks for contacting us. Have a great day!"
LLM_TROUBLE = (
    "Sorry, I'm having trouble understanding messages right now. Could you try again in a moment?"
)
NOT_UNDERSTOOD = (
    "I can help with cancelling or changing pending orders, returns and exchanges of delivered "
    "orders, your account address, and questions about your orders. What would you like to do?"
)
OTHER_PERSON = (
    "I'm sorry, I can only help with the account you've verified in this chat. "
    "Is there anything I can do for you on your own account?"
)
QUEUED = "Got it — I'll help with that once we've finished this request."
DECLINED = (
    "Okay — I haven't changed anything. Tell me what you'd like to change, or say \"skip\" "
    "to drop this request."
)
TOO_MANY_DECLINES = "I've set this request aside without making any changes."
SKIPPED = "Okay, I've set that request aside without making any changes."
TOO_MANY_ASKS = "I wasn't able to get the details I need, so I've set this request aside."
ITEMS_ONCE_REMINDER = (
    "Please note: items in a pending order can only be changed once, so please tell me every "
    "item you'd like to change in this request."
)
EXCHANGE_REMINDER = (
    "Please tell me every item you'd like to exchange in this order, so I can do it all at once."
)
APPROVAL_WAIT = (
    "Because of its size, this return needs a supervisor's approval. Please wait a moment — "
    "I'll continue as soon as they decide."
)
APPROVAL_GRANTED = "A supervisor approved your return."
TRANSFER_OFFER = "Would you like me to transfer you to a human agent?"
NO_TRANSFER = "Okay, I won't transfer you."


def auth_failed(attempt: int, max_attempts: int) -> str:
    left = max_attempts - attempt
    tail = (
        f" ({left} attempt{'s' if left != 1 else ''} left before I offer a human agent.)"
        if left > 0
        else ""
    )
    return (
        "I couldn't find an account with those details. Please check and try again — "
        "your email, or your first and last name with your zip code." + tail
    )


def auth_missing(missing: list[str]) -> str:
    return f"Thanks. To find your account I also need your {' and '.join(missing)}."


def verified(first_name: str) -> str:
    return f"Thanks, {first_name} — your account is verified."


def approval_rejected(note: str | None) -> str:
    text = "I'm sorry — a supervisor couldn't approve this return."
    if note:
        text += f' Their note: "{note}"'
    return text


def refund_line(amount: float, pm_text: str, timing: str) -> str:
    return f"Refund {usd(amount)} to your {pm_text} — {timing}"
