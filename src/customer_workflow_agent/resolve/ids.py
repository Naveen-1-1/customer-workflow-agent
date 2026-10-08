"""Ids and identity details the customer typed. Anything the LLM returns must appear in
the customer's own text, so an invented id can never reach the store."""

import re

_ORDER_DIGITS = re.compile(r"(?<!\d)(\d{7})(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ZIP = re.compile(r"(?<!\d)(\d{5})(?!\d)")


def order_ids_in(text: str) -> list[str]:
    return [f"#W{d}" for d in dict.fromkeys(_ORDER_DIGITS.findall(text or ""))]


def normalize_order_id(raw: str | None, customer_text: str) -> str | None:
    """'W1234567' / '#w1234567' / '1234567' -> '#W1234567', only if the customer typed it."""
    if not raw:
        return None
    m = _ORDER_DIGITS.search(raw)
    if not m:
        return None
    oid = f"#W{m.group(1)}"
    return oid if oid in order_ids_in(customer_text) else None


def email_in(text: str) -> str | None:
    m = EMAIL.search(text or "")
    return m.group(0) if m else None


def zip_in(text: str) -> str | None:
    m = ZIP.search(text or "")
    return m.group(1) if m else None


def literal_in(value: str | None, text: str) -> str | None:
    """Keep an extracted value only if it literally appears in the text (case-insensitive)."""
    if value and value.strip() and value.strip().lower() in (text or "").lower():
        return value.strip()
    return None
