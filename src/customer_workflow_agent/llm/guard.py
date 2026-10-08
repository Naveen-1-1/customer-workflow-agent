"""Checks on LLM-written text: it may phrase questions and small talk, never state facts."""

import re

_FORBIDDEN = [
    re.compile(r"#\s*W\d"),  # order ids
    re.compile(r"\$\s*\d"),  # amounts
    re.compile(r"\d{3,}"),  # ids, zips, card digits, long numbers
    re.compile(r"[\w.+-]+@[\w-]+\.\w"),  # emails
    re.compile(r"\b(refund(ed)?|cancel(l)?ed|approved|processed|delivered)\b", re.I),  # outcomes
]
MAX_LEN = 400


def safe_reply(text: str | None) -> str | None:
    """Return the cleaned text if it is safe to show, else None (caller uses a template)."""
    if not text:
        return None
    cleaned = text.strip().strip('"').strip()
    if not cleaned or len(cleaned) > MAX_LEN:
        return None
    if any(p.search(cleaned) for p in _FORBIDDEN):
        return None
    return cleaned
