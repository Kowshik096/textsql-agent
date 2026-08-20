"""
graph.py — the agent's control flow.

Day 1 skeleton: the graph shape is real, the node bodies are stubs. Nothing
here touches the model or the database yet. The point is to prove the routing
works — the retry loop, the guardrail exit, and retry exhaustion — before Day 2
fills in the logic, so that debugging LangGraph's API and debugging prompt
behaviour never happen at the same time.

    START -> plan -> generate_and_execute -> verify -> END
                          ^________|
                       retry while error, max 3
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

MAX_ATTEMPTS = 3


class Attempt(TypedDict):
    """One pass through generate+execute. `error` is None if the SQL ran."""
    sql: str
    error: Optional[str]


class Blocked(TypedDict):
    """
    Set when a guardrail refuses the request. Read by the exit path, by
    Langfuse, and by the eval harness — the 15 adversarial cases are scored
    on exactly this field, so it is not just diagnostics.
    """
    layer: Literal["input", "sql", "output"]
    reason: str


class AgentState(TypedDict, total=False):
    """
    The dictionary that travels through the graph. Every field is here because
    one node writes it and another reads it.

    question  entry            -> planner, generator, verifier
    schema    entry            -> planner, generator   (also traced, so there is
                                  a record of exactly what the model saw)
    plan      planner          -> generator
    attempts  generator        -> retry edge, generator (the error text is what
                                  makes the next attempt different — at
                                  temperature 0, an unchanged prompt regenerates
                                  the identical broken query)
    result    executor         -> verifier (grounding), charts
    blocked   guardrails       -> exit path, metrics, evals
    answer    verifier         -> API / UI
    status    verifier         -> caller

    `attempts` replaces a separate counter: the count is len(attempts), so it
    cannot drift out of sync with the actual history.
    """
    question: str
    schema: str
    plan: str
    attempts: Annotated[list[Attempt], operator.add]
    result: Optional[list]
    blocked: Optional[Blocked]
    answer: str
    status: Literal["ok", "failed", "blocked"]


def last_error(state: AgentState) -> Optional[str]:
    attempts = state.get("attempts") or []
    return attempts[-1]["error"] if attempts else None


def n_attempts(state: AgentState) -> int:
    return len(state.get("attempts") or [])


# --- stub scaffolding -------------------------------------------------------
# Delete these on Day 2. They exist only so the demo below can drive every
# path through the graph without a model or a database.
STUB_BLOCK_INPUT = False
STUB_FAILURES = 0


# --- nodes ------------------------------------------------------------------

def plan(state: AgentState) -> dict:
    """
    Day 2: input guard (injection + scope filter), then one LLM call turning
    the question into a plain-English statement of what data is needed.

    The guard sits here rather than in its own node only because the doc
    specifies three nodes; if it grows, split it out.
    """
    if STUB_BLOCK_INPUT:
        return {
            "blocked": {"layer": "input", "reason": "stub: prompt injection"},
            "status": "blocked",
        }
    return {"plan": f"STUB PLAN for: {state['question']}"}


def generate_and_execute(state: AgentState) -> dict:
    """
    Day 2: build the prompt from schema + plan + (on retry) the previous SQL
    and its error, call the model, extract the SQL, run it past the sqlglot
    SELECT-only guard, then execute it read-only with a row limit and timeout.

    Appends one Attempt per pass, so the full history survives for Langfuse
    and for the first-pass-vs-final accuracy metric.
    """
    n = n_attempts(state)
    prev = last_error(state)
    note = f" (retrying after: {prev})" if prev else ""
    print(f"    generate_and_execute: attempt {n + 1}{note}")

    sql = f"SELECT 'stub attempt {n + 1}';"
    if n < STUB_FAILURES:
        return {"attempts": [{"sql": sql, "error": f"stub SQL error on attempt {n + 1}"}]}
    return {"attempts": [{"sql": sql, "error": None}], "result": [("stub_row", 1)]}


def verify(state: AgentState) -> dict:
    """
    The single exit path. Three cases, and only the last one calls the model.
    """
    # 1. a guardrail refused the request
    if state.get("blocked"):
        b = state["blocked"]
        return {
            "status": "blocked",
            "answer": f"Refused at the {b['layer']} guard: {b['reason']}",
        }

    # 2. retries exhausted — no rows to ground and no answer to write, so
    #    return without inference rather than spending ~11s narrating a
    #    query that never ran
    if last_error(state) is not None:
        return {
            "status": "failed",
            "answer": (
                f"Could not answer after {n_attempts(state)} attempts. "
                f"Last error: {last_error(state)}"
            ),
        }

    # 3. success — Day 3: the model writes the answer from the rows, then the
    #    grounding check confirms every number in it appears in `result`
    return {"status": "ok", "answer": "STUB ANSWER grounded in result rows"}


# --- routing ----------------------------------------------------------------

def route_after_plan(state: AgentState) -> Literal["generate_and_execute", "verify"]:
    return "verify" if state.get("blocked") else "generate_and_execute"


def route_after_execute(state: AgentState) -> Literal["retry", "verify"]:
    """The self-correction edge. Retry only while there is an error AND budget."""
    if state.get("blocked"):
        return "verify"
    if last_error(state) is None:
        return "verify"
    if n_attempts(state) >= MAX_ATTEMPTS:
        return "verify"
    return "retry"


def build_graph():
    b = StateGraph(AgentState)
    b.add_node("plan", plan)
    b.add_node("generate_and_execute", generate_and_execute)
    b.add_node("verify", verify)

    b.add_edge(START, "plan")
    b.add_conditional_edges(
        "plan", route_after_plan,
        {"generate_and_execute": "generate_and_execute", "verify": "verify"},
    )
    b.add_conditional_edges(
        "generate_and_execute", route_after_execute,
        {"retry": "generate_and_execute", "verify": "verify"},
    )
    b.add_edge("verify", END)
    return b.compile()


if __name__ == "__main__":
    graph = build_graph()

    scenarios = [
        ("happy path, no retry",      False, 0),
        ("recovers on attempt 3",     False, 2),
        ("exhausts all 3 attempts",   False, 9),
        ("blocked at the input guard", True, 0),
    ]

    for title, block, failures in scenarios:
        STUB_BLOCK_INPUT, STUB_FAILURES = block, failures
        print(f"\n--- {title} ---")
        final = graph.invoke({
            "question": "Which 5 sellers generated the most revenue?",
            "schema": "<stub schema>",
            "attempts": [],
        })
        print(f"    status : {final['status']}")
        print(f"    attempts: {len(final.get('attempts', []))}")
        print(f"    answer : {final['answer']}")

    print("\nGraph skeleton runs. Nodes are stubs; Day 2 fills them in.")
