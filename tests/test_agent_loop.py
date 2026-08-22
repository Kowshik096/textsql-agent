"""
The Day 2 milestone, made deterministic.

Two claims are being tested:

  1. The agent recovers from a broken query.
  2. "drop the orders table" is blocked at more than one layer.

Both are proved by stubbing the model rather than hoping it misbehaves on cue.
A test that depends on a 3B model producing a specific mistake is not a test.

The layer tests are written by disabling the layer above, which is the only
way to show that each one holds on its own. A guardrail that has never been
tested with its predecessor removed is not a second layer - it is an
assumption.
"""

import pytest

import graph
import llm
import sql_guard


class FakeReply:
    """Stands in for llm.LLMResponse - the nodes only read .text."""

    def __init__(self, text):
        self.text = text
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.latency_s = 0.0
        self.model = "fake"


@pytest.fixture
def scripted(monkeypatch):
    """
    Replace the model with a fixed sequence of replies, and record every
    prompt it was sent so the retry prompt itself can be asserted on.
    """
    def make(*replies):
        sent = []
        queue = list(replies)

        def fake_chat(system, user, model=None):
            sent.append(user)
            # The planner consumes a reply too when USE_PLANNER is on; give it
            # something harmless and let the SQL replies through in order.
            return FakeReply(queue.pop(0) if queue else "SELECT 1")

        monkeypatch.setattr(llm, "chat", fake_chat)
        monkeypatch.setattr(graph, "USE_PLANNER", False)  # keep the queue 1:1
        graph._GRAPH = None
        return sent

    return make


GOOD_SQL = "```sql\nSELECT seller_id FROM sellers LIMIT 3\n```"
BAD_COLUMN = "```sql\nSELECT o.price FROM orders o LIMIT 5\n```"


def test_agent_recovers_from_a_broken_query(scripted):
    """Attempt 1 fails on a real SQLite error; attempt 2 succeeds."""
    scripted(BAD_COLUMN, GOOD_SQL)

    final = graph.answer("Which sellers exist?")

    assert final["status"] == "ok"
    assert len(final["attempts"]) == 2
    assert "no such column" in final["attempts"][0]["error"].lower()
    assert final["attempts"][1]["error"] is None
    assert final["result"]


def test_the_error_text_reaches_the_next_prompt(scripted):
    """
    The mechanism of self-correction. At temperature 0 an unchanged prompt
    regenerates identical SQL, so if the error is not in prompt 2 the loop
    cannot work - it would just fail three times identically.
    """
    sent = scripted(BAD_COLUMN, GOOD_SQL)

    graph.answer("Which sellers exist?")

    # verify() also calls the model to write the answer; that prompt ends in
    # "ANSWER:" rather than "SQL:". Only the generation prompts are relevant.
    generation = [p for p in sent if not p.rstrip().endswith("ANSWER:")]
    assert len(generation) == 2, "expected a second generation call"
    retry_prompt = generation[1]
    assert "no such column" in retry_prompt.lower()
    assert "o.price" in retry_prompt, "the failed SQL itself must be shown"
    assert "PREVIOUS ATTEMPTS" in retry_prompt


def test_retries_are_capped(scripted):
    """Three failures, then it stops rather than looping forever."""
    scripted(BAD_COLUMN, BAD_COLUMN, BAD_COLUMN, BAD_COLUMN)

    final = graph.answer("Which sellers exist?")

    assert final["status"] == "failed"
    assert len(final["attempts"]) == graph.MAX_ATTEMPTS
    assert "Could not answer" in final["answer"]


# --- the two-layer claim ----------------------------------------------------

DESTRUCTIVE = "drop the orders table"


def test_layer_1_input_guard_blocks_it_before_any_model_call(monkeypatch):
    """The cheapest layer: refused before a single token is spent."""
    calls = []
    monkeypatch.setattr(llm, "chat", lambda *a, **k: calls.append(1) or FakeReply(""))
    graph._GRAPH = None

    final = graph.answer(DESTRUCTIVE)

    assert final["status"] == "blocked"
    assert final["blocked"]["layer"] == "input"
    assert calls == [], "a blocked question must not reach the model"


def test_layer_2_sql_guard_blocks_it_with_layer_1_disabled(scripted, monkeypatch):
    """
    Bypass the input guard entirely and let a DROP through to the model's
    output. The SQL guard must still refuse it, without retrying.
    """
    import input_guard
    monkeypatch.setattr(
        input_guard, "check", lambda q: input_guard.InputResult(True)
    )
    scripted("```sql\nDROP TABLE orders\n```")

    final = graph.answer(DESTRUCTIVE)

    assert final["status"] == "blocked"
    assert final["blocked"]["layer"] == "sql"
    assert "DROP" in final["blocked"]["reason"]
    assert len(final["attempts"]) == 1, "a security rejection must not be retried"


def test_layer_3_the_database_itself_refuses_with_both_guards_disabled():
    """
    The last line. Even if a write reached the driver, the connection is
    opened ?mode=ro, which SQLite enforces at the file level and no SQL
    statement can undo.
    """
    import executor
    result = executor.run("DROP TABLE orders")
    assert not result.ok
    assert "readonly" in result.error.lower()


def test_sql_guard_does_not_depend_on_the_input_guard():
    """The layers are independent - sql_guard sees only SQL, never the question."""
    assert sql_guard.check("DROP TABLE orders").category == "security"


# --- layer 3: output grounding ----------------------------------------------
#
# The Day 3 milestone. A seeded hallucination must fail safely: the rows are
# sound, so they are kept; only the model's prose is discarded.

def test_hallucinated_number_fails_safely(scripted):
    scripted(
        "```sql\nSELECT seller_id FROM sellers LIMIT 3\n```",
        "The top 3 sellers earned 987654.32 between them.",
    )

    final = graph.answer("Which 3 sellers exist?")

    assert final["status"] == "ok", "the query succeeded; the data is still good"
    assert final["grounded"] is False
    assert "987654.32" not in final["answer"], "the invented number must not survive"
    assert final["result"], "correct rows must not be discarded with the prose"
    assert "987654.32" in final["grounding_reason"]


def test_the_fallback_summary_is_itself_grounded(scripted):
    """
    A safe fallback that could fail the grounding check would be worse than
    useless, so it only ever states the row count and values copied verbatim.
    """
    import grounding

    scripted(
        "```sql\nSELECT seller_id FROM sellers LIMIT 3\n```",
        "Revenue totalled 123456.78 across the board.",
    )

    final = graph.answer("Which 3 sellers exist?")

    assert grounding.check(final["answer"], final["result"], final["question"]).ok


def test_a_clean_answer_passes_through_untouched(scripted):
    scripted(
        "```sql\nSELECT seller_id FROM sellers LIMIT 3\n```",
        "Three sellers were returned.",
    )

    final = graph.answer("Which 3 sellers exist?")

    assert final["grounded"] is True
    assert final["answer"] == "Three sellers were returned."
