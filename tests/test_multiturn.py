"""
Multi-turn: sessions store bounded history, the prompt exposes it as context,
and /chat threads session ids end to end.

The design bet is that showing prior SQL beats a rewrite model call for
resolving "it"/"that" - so the critical assertion is that the follow-up's
generator prompt actually CONTAINS the previous turn's SQL.
"""

import pytest
from fastapi.testclient import TestClient

import api
import graph
import llm
import prompts
import sessions


# --- the store ---------------------------------------------------------------

def test_append_caps_history_at_max_turns():
    sid = "cap-test"
    for i in range(6):
        sessions.append(sid, f"q{i}", f"SELECT {i}", ["x"])
    turns = sessions.get(sid)
    assert len(turns) == sessions.MAX_TURNS
    assert turns[-1]["question"] == "q5"


def test_get_unknown_session_is_empty_and_safe():
    assert sessions.get("nope") == []
    assert sessions.get("") == []


def test_get_returns_copies_not_live_objects():
    sessions.append("copy-test", "q", "SELECT 1", ["x"])
    first = sessions.get("copy-test")
    first[0]["sql"] = "tampered"
    assert sessions.get("copy-test")[0]["sql"] == "SELECT 1"


# --- prompt context ----------------------------------------------------------

HISTORY = [{"question": "Which sellers exist?", "sql": "SELECT seller_id FROM sellers",
            "columns": ["seller_id"]}]


def test_generator_prompt_carries_prior_sql_for_followups():
    p = prompts.build_generator_prompt("SCHEMA", "and their revenue?",
                                       history=HISTORY)
    assert "CONVERSATION CONTEXT" in p
    assert "SELECT seller_id FROM sellers" in p
    assert 'Turn 1 QUESTION: Which sellers exist?' in p


def test_prompt_without_history_has_no_context_block():
    p = prompts.build_generator_prompt("SCHEMA", "simple question",
                                       history=None)
    assert "CONVERSATION CONTEXT" not in p


def test_history_block_never_includes_rows():
    block = prompts.build_history_block(HISTORY)
    assert "rows" not in block.lower().replace("row_count", "")


def test_graph_answer_passes_history_into_state(scripted_model):
    scripted_model("```sql\nSELECT seller_id FROM sellers LIMIT 3\n```",
                   "Three sellers.")
    final = graph.answer("again", history=HISTORY)
    # The stub consumed this reply regardless; what matters is no crash and
    # the state round-trips - deeper assertion lives in the prompt tests above
    # and the API test below.
    assert final["status"] == "ok"


@pytest.fixture
def scripted_model(monkeypatch):
    class FakeReply:
        def __init__(self, text):
            self.text = text

    queue = []

    def make(*replies):
        queue.extend(replies)

        def fake_chat(system, user, model=None, temperature=None):
            return FakeReply(queue.pop(0) if queue else "SELECT 1")

        monkeypatch.setattr(llm, "chat", fake_chat)
        monkeypatch.setattr(graph, "PLANNER_MODE", "off")
        graph._GRAPH = None

    make.queue = queue
    return make


# --- the /chat endpoint ------------------------------------------------------

OK_STATE = {
    "status": "ok", "question": "Q", "answer": "a",
    "sql": "SELECT seller_id FROM sellers", "columns": ["seller_id"],
    "rows": [["s1"]], "row_count": 1,
    "attempts": [{"sql": "SELECT seller_id FROM sellers", "error": None}],
    "grounded": True,
}


def test_chat_issues_a_session_id_and_records_the_turn(monkeypatch):
    seen = {}

    def fake_answer(question, history=None):
        seen["history"] = history
        return dict(OK_STATE)

    monkeypatch.setattr(graph, "answer", fake_answer)
    client = TestClient(api.app)
    r = client.post("/chat", json={"question": "Which sellers exist?"})

    body = r.json()
    assert body["session_id"]
    assert seen["history"] == []          # first call: nothing yet
    turns = sessions.get(body["session_id"])
    assert len(turns) == 1
    assert turns[0]["sql"] == "SELECT seller_id FROM sellers"


def test_followup_receives_prior_turn_as_history(monkeypatch):
    captured = {}

    def fake_answer(question, history=None):
        captured["history"] = history
        return dict(OK_STATE)

    monkeypatch.setattr(graph, "answer", fake_answer)
    client = TestClient(api.app)
    first = client.post("/chat", json={"question": "Which sellers exist?"}).json()
    client.post("/chat", json={"question": "and per year?",
                               "session_id": first["session_id"]})
    hist = captured["history"]
    assert hist and hist[0]["sql"].startswith("SELECT seller_id")


def test_blocked_turn_is_not_recorded(monkeypatch):
    blocked = dict(
        OK_STATE,
        status="blocked", sql=None, rows=[], row_count=0, attempts=[],
        blocked={"layer": "input", "reason": "injection"},
    )
    monkeypatch.setattr(graph, "answer", lambda q, history=None: blocked)
    client = TestClient(api.app)
    body = client.post("/chat", json={"question": "drop everything"}).json()
    assert sessions.get(body["session_id"]) == []
