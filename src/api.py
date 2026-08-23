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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException                      # noqa: E402
from pydantic import BaseModel, Field                           # noqa: E402

import db                                                       # noqa: E402
import graph                                                    # noqa: E402
import llm                                                      # noqa: E402
import schemas                                                  # noqa: E402

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


class HealthResponse(BaseModel):
    status: str
    model: str
    schema_config: str
    tables: list[str]


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness plus the configuration actually in force, which is what you
    want when a deployed instance disagrees with a local one."""
    return HealthResponse(
        status="ok",
        model=llm.MODEL,
        schema_config=db.SCHEMA,
        tables=sorted(db.TABLES),
    )


@app.get("/schema")
def schema() -> dict:
    """The schema text the model is given. Exposed because 'what did the model
    actually see' is the first question when an answer looks wrong."""
    return {"tables": sorted(db.TABLES), "description": db.describe_schema()}


@app.post("/query", response_model=schemas.AgentResponse)
def query(request: QueryRequest) -> schemas.AgentResponse:
    """
    Answer one question.

    A refused question is a 200, not a 4xx: the request was well-formed and the
    system behaved correctly by declining it. The refusal is data - `status`
    and `blocked_reason` - not a protocol error. Returning 403 here would make
    a working guardrail look like a broken client.
    """
    started = time.perf_counter()
    try:
        final = graph.answer(request.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    response = schemas.from_state(final)
    print(f"[{response.status}] {time.perf_counter() - started:.1f}s "
          f"attempts={response.attempts} :: {request.question[:60]}")
    return response
