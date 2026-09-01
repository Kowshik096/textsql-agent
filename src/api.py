"""
api.py — the REST surface.

    uvicorn api:app --app-dir src --reload

Thin by design. Every decision that matters already lives in the agent; this
layer validates input, calls it, and returns the Pydantic response contract
that `schemas.py` defines. If a change is needed here to fix agent behaviour,
it belongs in the agent instead.

One deliberate detail: the query endpoint is `def`, not `async def`.

    A question takes 30-70 seconds on a local 3B model, and the graph is
    synchronous. FastAPI runs a plain `def` handler in a threadpool, so a slow
    request occupies a worker thread rather than blocking the event loop and
    stalling every other connection. Writing `async def` around a synchronous
    call is the common version of this mistake and it looks more modern while
    being strictly worse.

    The real fix is an async model client, which is on the list and not done -
    see the note in the README about what this is and is not.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import StreamingResponse                 # noqa: E402
from pydantic import BaseModel, Field                           # noqa: E402

import db                                                       # noqa: E402
import graph                                                    # noqa: E402
import hardening                                                # noqa: E402
import json                                                     # noqa: E402
import llm                                                      # noqa: E402
import schemas
import sessions                                                 # noqa: E402
import tracing                                                  # noqa: E402

app = FastAPI(
    title="Secure Self-Correcting Text-to-SQL Agent",
    description=(
        "Answers natural-language questions about an e-commerce database. "
        "Read-only, with input, SQL and output guardrails."
    ),
    version="1.0.0",
)


class QueryRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=500,
        description="A question about the database, in plain English.",
        examples=["Which 5 sellers generated the most revenue?"],
    )


class ChatRequest(BaseModel):
    """Multi-turn variant: pass back the session_id you were given."""
    question: str = Field(min_length=1, max_length=500)
    session_id: Optional[str] = Field(
        default=None,
        description="Omit to start a session; echo it to ask a follow-up.",
    )


class HealthResponse(BaseModel):
    status: str
    model: str
    schema_config: str
    tables: list[str]
    tracing: dict


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus the configuration actually in force, which is what you
    want when a deployed instance disagrees with a local one."""
    return HealthResponse(
        status="ok",
        model=llm.MODEL,
        schema_config=db.SCHEMA,
        tables=sorted(db.TABLES),
        tracing=tracing.status(),
    )


@app.get("/schema")
def schema() -> dict:
    """The schema text the model is given. Exposed because 'what did the model
    actually see' is the first question when an answer looks wrong."""
    return {"tables": sorted(db.TABLES), "description": db.describe_schema()}


@app.post("/query/stream")
def query_stream(
    request: QueryRequest,
    _auth: None = Depends(hardening.require_auth),
    _rate: None = Depends(hardening.check_rate_limit),
) -> StreamingResponse:
    """
    The same agent, delivered while it works.

    Server-Sent Events, one JSON object per event:
      plan     - the planner's plain-English plan
      attempt  - one generated SQL (and its error, when it failed)
      rows     - the result set that came back
      blocked  - a guardrail refusal, with layer and reason
      final    - the complete AgentResponse contract from schemas.py
      error    - an unexpected failure mid-pipeline

    `def`, not `async def`, for the same reason as /query - and additionally
    because a synchronous generator is drained by Starlette in a worker
    threadpool, which is exactly where a 30-70s pipeline belongs.
    """
    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, default=str)}\n\n"

    def generate():
        attempt_no = 0
        try:
            for node, delta in graph.stream_answer(request.question):
                if node == "__final__":
                    response = schemas.from_state(delta)
                    yield sse({"type": "final", "response": response.model_dump()})
                elif node == "plan":
                    if delta.get("blocked"):
                        b = delta["blocked"]
                        yield sse({"type": "blocked", "layer": b["layer"],
                                   "reason": b["reason"]})
                    elif delta.get("plan"):
                        yield sse({"type": "plan", "text": delta["plan"]})
                elif node == "generate_and_execute":
                    for attempt in delta.get("attempts") or []:
                        attempt_no += 1
                        event = {"type": "attempt", "n": attempt_no,
                                 "sql": attempt.get("sql")}
                        if attempt.get("error"):
                            event["error"] = attempt["error"]
                        yield sse(event)
                    if delta.get("result") is not None:
                        rows = delta["result"]
                        yield sse({
                            "type": "rows",
                            "columns": delta.get("columns") or [],
                            "rows": [list(r) for r in rows[:20]],
                            "row_count": len(rows),
                        })
                    if delta.get("blocked"):
                        b = delta["blocked"]
                        yield sse({"type": "blocked", "layer": b["layer"],
                                   "reason": b["reason"]})
                # verify emits nothing incremental; final carries it all
        except Exception as e:
            yield sse({"type": "error",
                       "detail": f"{type(e).__name__}: {e}"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat", response_model=schemas.AgentResponse)
def chat(
    request: ChatRequest,
    _auth: None = Depends(hardening.require_auth),
    _rate: None = Depends(hardening.check_rate_limit),
) -> schemas.AgentResponse:
    """
    Multi-turn entry point.

    The first call may omit session_id and gets one back; follow-ups echo it
    and their question is answered with the prior turns (question + SQL +
    columns, capped at three) visible as prompt context - so "and per year?"
    resolves without a rewrite model call. Sessions are in-process memory:
    restart clears them, which sessions.py states plainly rather than hiding.
    """
    sid = request.session_id or uuid.uuid4().hex[:12]
    history = sessions.get(sid)

    started = time.perf_counter()
    try:
        final = graph.answer(request.question, history=history)
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"{type(e).__name__}: {e}") from e

    response = schemas.from_state(final)
    response.session_id = sid
    if response.status == "ok" and response.sql:
        sessions.append(sid, request.question, response.sql, response.columns)
    print(f"[{response.status}] {time.perf_counter() - started:.1f}s "
          f"session={sid} attempts={response.attempts} "
          f":: {request.question[:60]}")
    return response


@app.post("/query", response_model=schemas.AgentResponse)
def query(
    request: QueryRequest,
    _auth: None = Depends(hardening.require_auth),
    _rate: None = Depends(hardening.check_rate_limit),
) -> schemas.AgentResponse:
    """
    Answer one question.

    A refused question is a 200, not a 4xx: the request was well-formed and the
    system behaved correctly by declining it. The refusal is data - `status`
    and `blocked_reason` - not a protocol error. Returning 403 here would make
    a working guardrail look like a broken client.
    """
    cached = hardening.get_cached(request.question)
    if cached is not None:
        return cached

    started = time.perf_counter()
    try:
        final = graph.answer(request.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    response = schemas.from_state(final)
    hardening.set_cached(request.question, response)
    print(f"[{response.status}] {time.perf_counter() - started:.1f}s "
          f"attempts={response.attempts} :: {request.question[:60]}")
    return response
