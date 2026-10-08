"""Loose text matching for what customers type ("t-shirts", "full-size", "space gray")."""

import difflib
import re
from collections.abc import Iterable

_SEP = re.compile(r"[\s\-_/]+")


def norm(s: str) -> str:
    tokens = [t for t in _SEP.split(s.lower().strip()) if t]
    # crude singular: "shirts" -> "shirt", but keep "glass", "mesh", "yes"
    return " ".join(
        t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in tokens
    )


def compact(s: str) -> str:
    return norm(s).replace(" ", "")


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, compact(a), compact(b)).ratio()


def strip_label(value: str) -> str:
    """'compatibility: Apple HomeKit' -> 'Apple HomeKit' (models sometimes add the name)."""
    head, sep, tail = value.partition(":")
    return tail.strip() if sep and tail.strip() and len(head) < 30 else value.strip(" .")


def match_one(value: str, candidates: Iterable[str], cutoff: float = 0.8) -> str | None:
    """The candidate `value` refers to, or None if no single candidate fits."""
    value = strip_label(value)
    cands = list(dict.fromkeys(candidates))
    if not value or not cands:
        return None
    exact = [c for c in cands if compact(c) == compact(value)]
    if len(exact) == 1:
        return exact[0]
    v_tokens = set(norm(value).split())
    contained = [
        c
        for c in cands
        if v_tokens and (v_tokens <= set(norm(c).split()) or set(norm(c).split()) <= v_tokens)
    ]
    if len(contained) == 1:
        return contained[0]
    scored = sorted(((similarity(value, c), c) for c in cands), reverse=True)
    if scored and scored[0][0] >= cutoff and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


def mentions(phrase: str, text: str) -> bool:
    """Does `text` refer to `phrase` (e.g. product name) loosely?"""
    p, t = norm(phrase), norm(text)
    if not p or not t:
        return False
    if p in t or t in p:
        return True
    cp, ct = p.replace(" ", ""), t.replace(" ", "")
    if len(cp) >= 3 and len(ct) >= 3 and (cp in ct or ct in cp):  # "tshirt" ~ "Classic T-Shirt"
        return True
    p_tokens, t_tokens = set(p.split()), set(t.split())
    if p_tokens & t_tokens and (p_tokens <= t_tokens or t_tokens <= p_tokens):
        return True
    return similarity(phrase, text) >= 0.75
