"""
prompts.py — every prompt the agent sends, in one file.

Kept separate from the graph for two reasons. Day 1 established that schema
and prompt wording are the primary accuracy lever on a 3B model — two column
notes worth 25 tokens fixed two silent semantic errors — so Day 4's failure
fixing will land here rather than in the agent logic. Having them in one file
makes those changes greppable and diffable rather than scattered across nodes.
"""

from __future__ import annotations

from typing import Optional, Sequence

# --- planner ----------------------------------------------------------------
#
# The planner exists to split "work out what data is needed" from "write
# correct syntax", because models do both worse when doing them at once.
# Whether that pays for its ~11s on a 3B model is an open question, settled
# by the Day 4 ablation - see USE_PLANNER in graph.py.

PLANNER_SYSTEM = """You are a data analyst planning how to answer a question from a SQLite database.

Given the schema and a question, describe in plain English what data is needed:
which tables to read, how to join them, what to filter, group and sort by.

Rules:
- Do NOT write SQL. Describe the steps only.
- Be specific about column names, taken from the schema.
- Three sentences at most."""


# --- generator --------------------------------------------------------------
#
# The "never write DROP..." line is a request, not a constraint - sql_guard is
# the constraint. It stays because it cheaply reduces how often the guard has
# to fire, not because it is load-bearing.

GENERATOR_SYSTEM = """You are a SQL expert working with a SQLite database.
Write ONE SQLite SELECT query that answers the question.

Rules:
- Output only the SQL inside a ```sql code block. No explanation.
- Use only tables and columns that appear in the schema.
- Never write DROP, DELETE, UPDATE, INSERT or ALTER.
- Always add a LIMIT unless the question asks for a single aggregate value."""


def build_generator_prompt(
    schema: str,
    question: str,
    plan: Optional[str] = None,
    attempts: Sequence[dict] = (),
) -> str:
    """
    Assemble the generator's user message.

    On the first pass this is schema + question (+ plan). On a retry it also
    carries every previous attempt and the error it produced.

    Why the error text has to be here at all:

        Generation runs at temperature 0, and Day 1 confirmed the model is
        byte-identical across runs. So a retry whose prompt is unchanged
        regenerates the same broken query, three times, and the loop is
        theatre. The error string is the only new information entering the
        prompt - it is the mechanism, not a diagnostic.

    Why every attempt rather than just the last one:

        With only the most recent failure visible, the model can oscillate:
        fix the join and break the filter, then fix the filter and reinstate
        the original join, forever. Showing the full history is what lets it
        avoid a query it has already tried. The cost is bounded - measured on
        Day 2 at roughly 90 tokens across three attempts on top of a 966-token
        schema prompt, peaking at 13% of num_ctx 8192.
    """
    parts = [schema, f"QUESTION: {question}"]

    if plan:
        parts.append(f"PLAN:\n{plan}")

    if attempts:
        lines = ["PREVIOUS ATTEMPTS - all failed. Do not repeat them:"]
        for i, attempt in enumerate(attempts, start=1):
            lines.append(f"\nAttempt {i}:")
            lines.append(f"  SQL:   {attempt['sql']}")
            lines.append(f"  Error: {attempt['error']}")
        lines.append(
            "\nWrite a DIFFERENT query that fixes the error above. "
            "Check column names against the schema."
        )
        parts.append("\n".join(lines))

    parts.append("SQL:")
    return "\n\n".join(parts)


# --- answer writer ----------------------------------------------------------
#
# The last model call in the pipeline. It only ever sees rows SQLite actually
# returned, so it cannot be wrong about the data - only about what it says
# regarding the data, which is what grounding.py checks afterwards.
#
# The "do not compute" rule is the highest-leverage line here. Left to itself
# the model likes to add a total, a percentage or an average that was never in
# the result set, and every one of those is an ungrounded number. Forbidding
# the behaviour up front is cheaper than catching it downstream - though the
# check still runs, because a prompt is a request.

ANSWER_SYSTEM = """You are a data analyst reporting a query result.

Write ONE or TWO sentences answering the question from the rows given.

Rules:
- Use ONLY numbers that appear in the rows. Copy them exactly.
- Do NOT compute totals, percentages, averages or differences. If a number is
  not in the rows, do not state it.
- If the result is empty, say so plainly.
- No preamble, no SQL, no bullet points."""


def build_answer_prompt(question: str, columns, rows, max_rows: int = 20) -> str:
    """
    Render the result set for the answer writer.

    Only the first `max_rows` are shown: a 1000-row result would dominate the
    context window, and the prose answer concerns the head of the data anyway.
    The total is stated separately so the model can refer to it without
    inventing it - the row count is one of the four sources grounding.py
    accepts as legitimate.
    """
    header = " | ".join(columns or [])
    body = "\n".join(
        " | ".join("" if cell is None else str(cell) for cell in row)
        for row in (rows or [])[:max_rows]
    )

    total = len(rows or [])
    note = (
        ""
        if total <= max_rows
        else f"\n... ({total} rows in total, first {max_rows} shown)"
    )

    return (
        f"QUESTION: {question}\n\n"
        f"RESULT ({total} row(s)):\n{header}\n{body}{note}\n\n"
        f"ANSWER:"
    )


# --- input guard ------------------------------------------------------------
#
# Layer 1, in front of the planner. Deliberately cheap: a small set of
# patterns rather than a classifier model, because a second model call per
# query would double latency on a machine that already takes 11s per call.
#
# This catches the obvious cases and nothing more. Stating that limit plainly
# is more defensible than implying the filter is comprehensive - prompt
# injection is not a solved problem, and a filter that claims otherwise is
# the kind of claim an interviewer will take apart.

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above)",
    r"forget\s+(everything|all|your\s+instructions)",
    r"you\s+are\s+now\s+",
    r"new\s+instructions?\s*:",
    r"system\s+prompt",
    r"\bdrop\s+(the\s+)?\w*\s*table\b",
    r"\bdelete\s+(all\s+|the\s+)?\w*\s*(rows?|records?|data|table)\b",
    r"\btruncate\b",
    r"\bshutdown\b",
]

# Rejected as out of scope: this agent answers questions about the database.
OFF_TOPIC_PATTERNS = [
    r"write\s+(me\s+)?(a|an)\s+(poem|story|essay|song|joke)",
    r"\btranslate\b.*\b(to|into)\s+\w+",
    r"what\s+is\s+your\s+(system\s+)?prompt",
]
