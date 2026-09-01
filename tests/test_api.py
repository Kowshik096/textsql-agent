"""
API contract tests. The model is stubbed - these check the HTTP surface, not
the agent, which has its own tests.
"""

import pytest
from fastapi.testclient import TestClient

import api
import graph
import llm


class FakeReply:
    def __init__(self, text):
        self.text = text
        self.prompt_tokens = self.output_tokens = 0
        self.latency_s = 0.0
        self.model = "fake"


@pytest.fixture
def client(monkeypatch):
    def fake_chat(system, user, model=None, temperature=None):
        if user.rstrip().endswith("ANSWER:"):
            return FakeReply("Three sellers were returned.")
        return FakeReply("```sql\nSELECT seller_id FROM sellers LIMIT 3\n```")

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")
    graph._GRAPH = None
    return TestClient(api.app)


def test_health_reports_the_active_configuration(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["tables"] and "orders" in body["tables"]


def test_schema_endpoint_returns_what_the_model_sees(client):
    body = client.get("/schema").json()
    assert "DATABASE SCHEMA" in body["description"]


def test_query_returns_the_response_contract(client):
    r = client.post("/query", json={"question": "Which sellers exist?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["sql"] and body["row_count"] == len(body["rows"])
    assert body["attempts"] >= 1


def test_a_refused_question_is_200_not_4xx(client):
    """
    The request was well-formed and the system behaved correctly by declining.
    A 403 here would make a working guardrail look like a broken client.
    """
    r = client.post("/query", json={"question": "drop the orders table"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert body["blocked_layer"] == "input"
    assert body["blocked_reason"]


def test_empty_question_is_rejected_by_validation(client):
    assert client.post("/query", json={"question": ""}).status_code == 422


def test_overlong_question_is_rejected(client):
    assert client.post("/query", json={"question": "x" * 600}).status_code == 422


def test_missing_field_is_rejected(client):
    assert client.post("/query", json={}).status_code == 422
