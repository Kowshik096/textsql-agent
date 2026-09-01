"""
Streaming: the same pipeline, delivered while it works.

Two claims are tested. The merged final state from stream_answer() is the
identical object answer() would have produced - streaming may not change
results. And the SSE endpoint wraps those events in a parseable protocol
whose terminal event carries the full AgentResponse contract.
"""

import json

import pytest
from fastapi.testclient import TestClient

import graph
import llm


class FakeReply:
    def __init__(self, text):
        self.text = text
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.latency_s = 0.0
        self.model = "fake"


@pytest.fixture
def scripted(monkeypatch):
    """Stub the model with a fixed reply queue; disable planning."""
    def make(*replies):
        queue = list(replies)

        def fake_chat(system, user, model=None, temperature=None):
            return FakeReply(queue.pop(0) if queue else "SELECT 1")

        monkeypatch.setattr(llm, "chat", fake_chat)
        monkeypatch.setattr(graph, "PLANNER_MODE", "off")
        graph._GRAPH = None

    return make


def test_stream_final_state_matches_answer_contract(scripted):
    scripted(
        "```sql\nSELECT o.price FROM orders o LIMIT 5\n```",
        "```sql\nSELECT seller_id FROM sellers LIMIT 3\n```",
        "Three sellers were returned.",
    )

    events = list(graph.stream_answer("Which sellers exist?"))
    kinds = [node for node, _ in events]
    assert "plan" in kinds and "generate_and_execute" in kinds
    assert kinds[-1] == "__final__"

    final = events[-1][1]
    assert final["status"] == "ok"
    assert len(final["attempts"]) == 2          # the broken first try recovered
    assert final["attempts"][0]["error"] is not None
    assert final["attempts"][1]["error"] is None
    assert final["result"]
    # Merged state must satisfy the same invariants answer() produces.
    response = __import__("schemas").from_state(final)
    assert response.status == "ok"


# --- the SSE endpoint -------------------------------------------------------

def _client():
    import api
    return TestClient(api.app)


def _parse_sse(body: str) -> list:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def test_endpoint_emits_parseable_events_with_final_contract(monkeypatch):
    scripted_final = {
        "status": "ok", "question": "Q?", "answer": "One row.",
        "sql": "SELECT 1 AS x", "columns": ["x"], "rows": [[1]],
        "row_count": 1,
        "attempts": [{"sql": "SELECT 1 AS x", "error": None}],
        "grounded": True,
    }

    def fake_stream(question):
        yield "plan", {"plan": "Read one table."}
        yield "generate_and_execute", {
            "attempts": [{"sql": "SELECT 1 AS x", "error": None}],
            "result": [[1]], "columns": ["x"],
        }
        yield "__final__", scripted_final

    monkeypatch.setattr(graph, "stream_answer", fake_stream)
    client = _client()
    response = client.post("/query/stream", json={"question": "Q?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert [e["type"] for e in events] == ["plan", "attempt", "rows", "final"]

    attempt = events[1]
    assert attempt["n"] == 1 and attempt["sql"] == "SELECT 1 AS x"

    rows = events[2]
    assert rows["row_count"] == 1 and rows["columns"] == ["x"]

    final = events[3]["response"]
    assert final["status"] == "ok" and final["answer"] == "One row."


def test_attempt_counter_increments_across_retries(monkeypatch):
    deltas = [
        ("generate_and_execute", {
            "attempts": [{"sql": "BAD", "error": "no such column"}]}),
        ("generate_and_execute", {
            "attempts": [{"sql": "GOOD", "error": None}],
            "result": [[1]], "columns": ["x"]}),
        ("__final__", {"status": "failed"}),
    ]
    monkeypatch.setattr(graph, "stream_answer", lambda q: iter(deltas))
    client = _client()
    events = _parse_sse(client.post("/query/stream",
                                    json={"question": "Q?"}).text)

    attempts = [e for e in events if e["type"] == "attempt"]
    assert [a["n"] for a in attempts] == [1, 2]
    assert attempts[0]["error"] == "no such column"


def test_pipeline_exception_becomes_an_error_event(monkeypatch):
    def boom(question):
        yield "plan", {"plan": "x"}
        raise RuntimeError("disk full again")

    monkeypatch.setattr(graph, "stream_answer", boom)
    client = _client()
    events = _parse_sse(client.post("/query/stream",
                                    json={"question": "Q?"}).text)
    assert events[-1]["type"] == "error"
    assert "RuntimeError" in events[-1]["detail"]
