"""
input_guard.py — layer 1, in front of the planner.

Catches two things before a single token is spent: prompt-injection attempts,
and questions that are not about the database.

Running first is what makes the adversarial suite cheap. A blocked question
costs zero model calls, so the 15 attack cases on Day 3 add almost nothing to
a benchmark run - which matters more than usual on a free API tier where the
daily request quota is the binding constraint.

What this does not do:

    This is a pattern list, not a solved problem. It catches the obvious and
    well-known phrasings. A novel or obfuscated injection gets through, and
    the honest defence is that layer 2 does not trust layer 1 - sql_guard
    re-checks whatever the model produces regardless of how the question was
    worded. Defence in depth means each layer holds when the one above it
    fails, and this is the layer most likely to fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import prompts

_INJECTION = [re.compile(p, re.IGNORECASE) for p in prompts.INJECTION_PATTERNS]
_OFF_TOPIC = [re.compile(p, re.IGNORECASE) for p in prompts.OFF_TOPIC_PATTERNS]


@dataclass
class InputResult:
    ok: bool
    reason: Optional[str] = None
    kind: Optional[str] = None  # "injection" | "off_topic"


def check(question: str) -> InputResult:
    """Screen a question before it reaches the planner."""
    text = (question or "").strip()

    if not text:
        return InputResult(False, reason="the question is empty", kind="off_topic")

    for pattern in _INJECTION:
        if pattern.search(text):
            return InputResult(
                False,
                reason="this looks like an attempt to change the agent's "
                       "instructions rather than a question about the data",
                kind="injection",
            )

    for pattern in _OFF_TOPIC:
        if pattern.search(text):
            return InputResult(
                False,
                reason="this agent only answers questions about the "
                       "e-commerce database",
                kind="off_topic",
            )

    return InputResult(True)
