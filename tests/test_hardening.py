"""
Hardening — auth, rate limiting, caching. All three are opt-in.
"""

import time

import pytest
from fastapi.testclient import TestClient

import api
import graph
import hardening
import schemas


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    # Ensure each test starts with hardening disabled and stores empty,
    # and with the planner disabled so /query tests don't pay for a model
    # call unless they explicitly want one.
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("RATE_LIMIT", raising=False)
    monkeypatch.delenv("CACHE_TTL", raising=False)
    monkeypatch.delenv("CACHE_MAX_SIZE", raising=False)
    hardening.clear_rate_limit_store()
    hardening.clear_cache()
    yield
    hardening.clear_rate_limit_store()
    hardening.clear_cache()


def _client():
    return TestClient(api.app)


# --- auth ---------------------------------------------------------------

def test_auth_disabled_by_default_allows_queries(monkeypatch):
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")

    def fake_answer(q, history=None):
        return {"status": "ok", "question": q, "answer": "a",
                "sql": "SELECT 1", "columns": ["x"], "rows": [[1]],
                "attempts": [{"sql": "SELECT 1", "error": None}]}

    monkeypatch.setattr(graph, "answer", fake_answer)
    monkeypatch.setattr(graph, "stream_answer", lambda q, history=None: iter([]))
    r = _client().post("/query", json={"question": "hi"})
    assert r.status_code == 200


def test_auth_rejects_missing_token_when_enabled(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cr3t")
    r = _client().post("/query", json={"question": "hi"})
    assert r.status_code == 401


def test_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cr3t")
    r = _client().post("/query", json={"question": "hi"},
                       headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_auth_accepts_valid_token(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cr3t")
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")

    def fake_answer(q, history=None):
        return {"status": "ok", "question": q, "answer": "a",
                "sql": "SELECT 1", "columns": ["x"], "rows": [[1]],
                "attempts": [{"sql": "SELECT 1", "error": None}]}

    monkeypatch.setattr(graph, "answer", fake_answer)
    r = _client().post("/query", json={"question": "hi"},
                       headers={"Authorization": "Bearer s3cr3t"})
    assert r.status_code == 200


def test_health_stays_public_even_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cr3t")
    r = _client().get("/health")
    assert r.status_code == 200


# --- rate limiting ------------------------------------------------------

def test_rate_limit_disabled_by_default(monkeypatch):
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")

    def fake_answer(q, history=None):
        return {"status": "ok", "question": q, "answer": "a",
                "sql": "SELECT 1", "columns": ["x"], "rows": [[1]],
                "attempts": [{"sql": "SELECT 1", "error": None}]}

    monkeypatch.setattr(graph, "answer", fake_answer)
    client = _client()
    for _ in range(5):
        r = client.post("/query", json={"question": "hi"})
        assert r.status_code != 429


def test_rate_limit_blocks_after_threshold(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT", "2/minute")
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")

    def fake_answer(q, history=None):
        return {"status": "ok", "question": q, "answer": "a",
                "sql": "SELECT 1", "columns": ["x"], "rows": [[1]],
                "attempts": [{"sql": "SELECT 1", "error": None}]}

    monkeypatch.setattr(graph, "answer", fake_answer)
    client = _client()
    assert client.post("/query", json={"question": "q1"}).status_code == 200
    assert client.post("/query", json={"question": "q2"}).status_code == 200
    r = client.post("/query", json={"question": "q3"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_rate_limit_is_per_ip(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT", "1/minute")
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")

    def fake_answer(q, history=None):
        return {"status": "ok", "question": q, "answer": "a",
                "sql": "SELECT 1", "columns": ["x"], "rows": [[1]],
                "attempts": [{"sql": "SELECT 1", "error": None}]}

    monkeypatch.setattr(graph, "answer", fake_answer)
    client = _client()
    assert client.post("/query", json={"question": "hi"}).status_code == 200
    # Different spoofed IP should not be limited
    r = client.post("/query", json={"question": "hi"},
                    headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 200


# --- caching ------------------------------------------------------------

def test_cache_disabled_by_default_returns_none(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "0")
    hardening.set_cached("q", object())
    assert hardening.get_cached("q") is None


def test_cache_stores_and_returns(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "60")
    resp = schemas.AgentResponse(
        status="ok", question="q", answer="a", sql="SELECT 1",
        columns=["x"], rows=[[1]], row_count=1, attempts=1,
        attempt_history=[{"sql": "SELECT 1", "error": None}],
    )
    hardening.set_cached("  q  ", resp)
    # key is stripped, so lookup with different whitespace hits
    assert hardening.get_cached("q") is resp


def test_cache_respects_ttl(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "1")
    resp = schemas.AgentResponse(
        status="ok", question="q", answer="a", sql="SELECT 1",
        columns=["x"], rows=[[1]], row_count=1, attempts=1,
        attempt_history=[{"sql": "SELECT 1", "error": None}],
    )
    hardening.set_cached("q", resp)
    time.sleep(1.2)
    assert hardening.get_cached("q") is None


def test_cache_lru_evicts_oldest(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "60")
    monkeypatch.setenv("CACHE_MAX_SIZE", "2")
    for q in ["q1", "q2", "q3"]:
        resp = schemas.AgentResponse(
            status="ok", question=q, answer="a", sql="SELECT 1",
            columns=["x"], rows=[[1]], row_count=1, attempts=1,
            attempt_history=[{"sql": "SELECT 1", "error": None}],
        )
        hardening.set_cached(q, resp)
    assert hardening.get_cached("q1") is None
    assert hardening.get_cached("q2") is not None
    assert hardening.get_cached("q3") is not None


def test_query_uses_cache_to_avoid_reinvoking_graph(monkeypatch):
    monkeypatch.setenv("CACHE_TTL", "60")
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")
    calls = {"n": 0}

    def fake_answer(q, history=None):
        calls["n"] += 1
        return {"status": "ok", "question": q, "answer": "a",
                "sql": "SELECT 1", "columns": ["x"], "rows": [[1]],
                "attempts": [{"sql": "SELECT 1", "error": None}]}

    monkeypatch.setattr(graph, "answer", fake_answer)
    client = _client()
    assert client.post("/query", json={"question": "same"}).status_code == 200
    assert calls["n"] == 1
    assert client.post("/query", json={"question": "same"}).status_code == 200
    # second identical question should not call graph again
    assert calls["n"] == 1
    # different question does call again
    assert client.post("/query", json={"question": "different"}).status_code == 200
    assert calls["n"] == 2
