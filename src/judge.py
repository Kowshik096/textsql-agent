"""
judge.py — offline LLM-as-judge for ANSWER PROSE quality.

Scope, stated where an interviewer will probe it: the judge grades the
sentences, never the SQL. Correctness stays rows-vs-rows (DECISIONS #15) —
execution accuracy needs no opinions. Prose is the one output with no
ground-truth rows to compare against, which is exactly the gap a judge fills:
grounding.py catches invented NUMBERS; the judge catches "answered a
different question", "narrated the breakdown instead of answering", and
similar failure classes that deterministic checks cannot see.

Independence is structural: the judge runs on a hosted model (default
Gemini - free tier, and judge != generator in vendor AND weights) while the
agent runs on local Qwen via Ollama. JUDGE_MODEL unset means the judge
never executes and the benchmark is unchanged - the same no-op-by-default
construction as tracing.

A malformed judge reply is recorded as a judge failure, never as a score
against the agent: a broken grader must not look like bad answers.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional, Sequence

import llm

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "").strip()
JUDGE_PROVIDER = os.environ.get("JUDGE_PROVIDER", "").strip().lower()

ENABLED = bool(JUDGE_MODEL)

_MAX_ROWS_SHOWN = 20

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

JUDGE_SYSTEM = """You are grading a data analyst's written answer against the result rows it was based on.

Return ONLY a JSON object, no other text:
{"faithful": true|false, "answers_question": true|false, "issue": "..."}

- faithful: every factual claim in the ANSWER is supported by the RESULT
  rows (values, comparisons like "the top seller", row counts). Formatting
  differences (229237.63 written as 229,238) are faithful.
- answers_question: the ANSWER responds to what the QUESTION asked, not to
  something adjacent. Reporting a breakdown when the question asked "how
  many" in total is answers_question: false.
- issue: when either is false, one short sentence saying what is wrong.
  Empty string when both are true.
- Do NOT judge the SQL, the query design, or the data itself. Only the
  answer text against the rows and the question."""


@dataclass
class JudgeVerdict:
    faithful: bool
    answers_question: bool
    issue: str
    parse_ok: bool = True


def build_judge_prompt(question: str, columns: Sequence,
                       rows: Sequence, answer: str) -> str:
    """Question + the rows the answer was based on + the answer. No SQL."""
    header = " | ".join(columns or [])
    body = "\n".join(
        " | ".join("" if cell is None else str(cell) for cell in row)
        for row in (rows or [])[:_MAX_ROWS_SHOWN]
    )
    total = len(rows or [])
    note = "" if total <= _MAX_ROWS_SHOWN else \
        f"\n... ({total} rows in total, first {_MAX_ROWS_SHOWN} shown)"
    return (
        f"QUESTION: {question}\n\n"
        f"RESULT ({total} row(s)):\n{header}\n{body}{note}\n\n"
        f"ANSWER: {answer}\n\n"
        "Verdict JSON:"
    )


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a reply, fences or chatter aside."""
    if "```" in text:
        for block in text.split("```")[1::2]:
            block = block.strip()
            if block.startswith("json"):
                block = block[4:]
            try:
                parsed = json.loads(block.strip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def judge_answer(question: str, columns: Sequence, rows: Sequence,
                 answer: str) -> Optional[JudgeVerdict]:
    """
    Grade one answer. Returns None when the judge is disabled - the caller
    treats that as "no opinion recorded", never as a pass or a fail.
    """
    if not ENABLED:
        return None

    reply = llm.chat(
        JUDGE_SYSTEM,
        build_judge_prompt(question, columns, rows, answer),
        model=JUDGE_MODEL,
        provider=JUDGE_PROVIDER or None,
        temperature=0,
    )
    parsed = _extract_json(reply.text)
    if parsed is None or "faithful" not in parsed:
        return JudgeVerdict(faithful=False, answers_question=False,
                            issue="judge returned unparseable verdict",
                            parse_ok=False)
    return JudgeVerdict(
        faithful=bool(parsed.get("faithful")),
        answers_question=bool(parsed.get("answers_question", True)),
        issue=str(parsed.get("issue", "")),
        parse_ok=True,
    )
