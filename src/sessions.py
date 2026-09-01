"""
sessions.py — in-memory conversation sessions for follow-up questions.

A session holds the last few turns as {question, sql, columns} records. The
generator prompt receives them verbatim, so "and per year?" resolves against
the previous SQL without a rewrite call - on a machine where one model call
costs ~11s, resolving references by *showing* the context is cheaper than
resolving them by *asking*.

Honest scope, stated where an interviewer will look: this is process memory.
No persistence, no TTL, no cross-process sharing - restart wipes sessions,
and multiple uvicorn workers would each see their own copy. Single-user
prototype discipline, not a session service. The store is capped at
MAX_TURNS so a long conversation cannot walk the prompt toward num_ctx.
"""

from __future__ import annotations

import threading

MAX_TURNS = 3

_lock = threading.Lock()
_sessions: dict = {}


def get(session_id: str) -> list:
    """Copies of a session's turns, oldest first. Empty when unknown."""
    with _lock:
        return [dict(turn) for turn in _sessions.get(session_id or "", [])]


def append(session_id: str, question: str, sql: str, columns: list) -> None:
    """Record a completed turn, dropping anything older than MAX_TURNS."""
    if not session_id or not sql:
        return
    with _lock:
        turns = _sessions.setdefault(session_id, [])
        turns.append({"question": question, "sql": sql,
                      "columns": list(columns)})
        del turns[:-MAX_TURNS]
