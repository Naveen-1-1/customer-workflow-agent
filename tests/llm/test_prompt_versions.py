"""A prompt's wording can't change without its version changing.

Each prompt is rendered from fixed sample inputs and hashed. The hashes and versions are
recorded in prompt_fingerprints.json. After changing a prompt:
1. bump its number in `VERSIONS` (llm/prompts.py);
2. run `make prompt-fingerprints` to record the new hash.
"""

import hashlib
import json
import os
from pathlib import Path

from customer_workflow_agent.llm import prompts
from customer_workflow_agent.llm.service import Prompt

RECORD = Path(__file__).with_name("prompt_fingerprints.json")
TURN_TYPES = [name.removeprefix("turn.") for name in prompts.VERSIONS if name.startswith("turn.")]


def samples() -> list[Prompt]:
    text, last = "SAMPLE CUSTOMER TEXT", "SAMPLE AGENT TEXT"
    return [
        prompts.classification(text, last),
        prompts.auth(text),
        *(
            prompts.turn(t, text, last_agent=last, known={"key": "value"}, catalog=[{"p": 1}])
            for t in TURN_TYPES
        ),
        prompts.info(text, ["Product A", "Product B"]),
        prompts.ask("SAMPLE QUESTION?", text),
        prompts.small_talk(text),
    ]


def fingerprint(p: Prompt) -> str:
    return hashlib.sha256(f"{p.system}\n---\n{p.user}".encode()).hexdigest()[:16]


def current() -> dict[str, dict]:
    return {p.name: {"version": p.version, "fingerprint": fingerprint(p)} for p in samples()}


def test_every_prompt_has_a_version_and_a_sample():
    assert sorted(current()) == sorted(prompts.VERSIONS)


def test_changed_wording_has_a_new_version():
    recorded = json.loads(RECORD.read_text()) if RECORD.exists() else {}
    now = current()
    not_bumped = [
        f"{name}: wording changed, so bump VERSIONS[{name!r}] to {old['version'] + 1}"
        for name, cur in now.items()
        if (old := recorded.get(name))
        and cur["fingerprint"] != old["fingerprint"]
        and cur["version"] <= old["version"]
    ]
    assert not not_bumped, "\n".join(not_bumped)
    if now != recorded and os.environ.get("UPDATE_PROMPT_FINGERPRINTS"):
        RECORD.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n")
        return
    assert now == recorded, "prompt versions changed: run `make prompt-fingerprints` to record"
