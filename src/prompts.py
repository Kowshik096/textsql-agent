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
        avoid a query it has already tried. The cost is bounded - three
        attempts add roughly 300-400 tokens to a 966-token schema prompt,
        well inside num_ctx 8192 - and that headroom is exactly why 8192 was
        chosen over 4096 on Day 1.
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
