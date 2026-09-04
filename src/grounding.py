"""
grounding.py — layer 3, the output guardrail.

Every number in the final answer must be traceable to the query results. This
is the check that kills invented statistics, which is the characteristic
failure of a data agent: the SQL is right, the rows are right, and then the
model writes a summary containing a number that was never in them.

It is a code-level check, not a prompt instruction. "Only use numbers from the
results" is a request the model can drift away from; this is arithmetic.

The hard part is not finding numbers in the answer - it is deciding which
legitimate ones would otherwise be flagged. A check that fires on correct
answers is worse than no check, because you learn to ignore it. Four sources
count as grounded:

  1. Any number in the result rows. The obvious case.
  2. The row count. "5 sellers" is true and useful, but 5 may appear nowhere
     in the rows themselves - it is the LIMIT.
  3. Any number in the question. "orders in 2017" echoes 2017 back.
  4. Small integers up to 12. Ordinals and counts in prose - "the top 3",
     "both of them" - are language, not statistics. Above that threshold a
     bare integer is much more likely to be a claim.

Matching allows for formatting - 229237.63 written as "229,238", "229237.6"
or "about 229,000" is the same number presented to a human, not a
hallucination. It deliberately does not allow for magnitude; see _matches().
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

# Integers at or below this are treated as prose rather than data.
SMALL_INT_CEILING = 12

# Matches 1234, 1,234.56, .5, -3 - and deliberately not the digits inside
# an identifier like `order_items2`.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![A-Za-z0-9_])")


@dataclass
class GroundingResult:
    ok: bool
    ungrounded: list = field(default_factory=list)
    reason: Optional[str] = None


def extract_numbers(text: str) -> list:
    """Pull every numeric literal out of prose, as floats."""
    out = []
    for match in _NUMBER.finditer(text or ""):
        try:
            out.append(float(match.group().replace(",", "")))
        except ValueError:
            continue
    return out


def _numbers_in_rows(rows: Sequence) -> list:
    """Every numeric cell in the result set, including numeric-looking strings."""
    out = []
    for row in rows or []:
        for cell in row:
            if isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float)):
                out.append(float(cell))
            elif isinstance(cell, str):
                # A cell like '2017-05-16' contributes 2017, 5 and 16, so a
                # date mentioned in the answer is grounded by the data.
                out.extend(extract_numbers(cell))
    return out


def _round_sig(value: float, sig: int) -> float:
    """Round to `sig` significant figures. 229237.63 at 3sf is 229000."""
    if value == 0:
        return 0.0
    exponent = math.floor(math.log10(abs(value)))
    return round(value, -(exponent - sig + 1))


def _matches(candidate: float, source: float) -> bool:
    """
    True if `candidate` is `source` as a human would write it.

    Covers decimal rounding ("229237.6") and significant-figure rounding
    ("about 229,000"), and nothing else.

    A relative tolerance was the obvious approach here and it is wrong: 1% of
    a 200,000 revenue figure is +/- 2,000, so 198,450 would have counted as a
    match for 200,326. That is exactly the plausible-but-invented number this
    module exists to catch - the tolerance has to absorb formatting, not
    magnitude. Significant-figure rounding does, because 198,450 is not any
    sig-fig rounding of 200,326 while 229,000 is one of 229,237.63.

    Non-finite sources (inf/nan in a result cell, e.g. from SELECT 9e999)
    never match approximately: round() and log10() raise on them, and this
    check must never raise - it is a guardrail, and a guardrail that crashes
    the pipeline on hostile input is worse than none. Only exact equality
    counts there (inf == inf grounds; nan never does).
    """
    if not math.isfinite(candidate) or not math.isfinite(source):
        return candidate == source
    if candidate == source:
        return True
    if abs(candidate - source) < 1e-9:
        return True
    for digits in (0, 1, 2):
        if round(source, digits) == candidate:
            return True
    for sig in range(2, 7):
        if _round_sig(source, sig) == candidate:
            return True
    return False


def _format(value: float) -> str:
    """
    Render a number for the refusal message exactly as it appeared.

    `f"{n:g}"` looks right and is not: it caps at six significant digits, so
    987654.32 prints as 987654 - a message about a wrong number that quietly
    rounds the number.
    """
    return str(int(value)) if float(value).is_integer() else repr(value)


def check(answer: str, rows: Sequence, question: str = "") -> GroundingResult:
    """
    Verify every number in `answer` traces back to the data, the question,
    or ordinary prose.
    """
    candidates = extract_numbers(answer)
    if not candidates:
        return GroundingResult(True)

    allowed = _numbers_in_rows(rows)
    allowed.append(float(len(rows or [])))
    allowed.extend(extract_numbers(question))

    ungrounded = []
    for candidate in candidates:
        if candidate.is_integer() and 0 <= candidate <= SMALL_INT_CEILING:
            continue
        if any(_matches(candidate, source) for source in allowed):
            continue
        ungrounded.append(candidate)

    if ungrounded:
        shown = ", ".join(_format(n) for n in ungrounded[:5])
        return GroundingResult(
            False,
            ungrounded=ungrounded,
            reason=f"the answer contains {len(ungrounded)} number(s) that do "
                   f"not appear in the query results: {shown}",
        )

    return GroundingResult(True)
