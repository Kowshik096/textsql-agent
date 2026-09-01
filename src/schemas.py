"""
schemas.py — the validated shape of an agent response.

Pydantic is doing two different jobs here and it is worth keeping them apart.

Against the API (Day 5) it is an ordinary response contract: FastAPI derives
the OpenAPI schema from it, and clients get a stable shape.

Against the agent it is the last assertion that the pipeline produced
something coherent - that a successful answer actually carries rows, that a
blocked one carries a reason, that the attempt count is not negative. These
are invariants the nodes are supposed to maintain; validating them here means
a bug in a node surfaces as a loud error at the boundary instead of a
malformed dict travelling into the UI.

Note what this is *not*: validating the model's own output. Nothing here
constrains what the LLM writes. Structure is guaranteed because the SQL is
parsed and the rows come from SQLite - the model only ever contributes prose,
and prose is checked by grounding.py, not by a schema.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class AttemptRecord(BaseModel):
    """One pass through generate+execute."""
    sql: str
    error: Optional[str] = None


class AgentResponse(BaseModel):
    """What a caller gets back for one question."""

    status: Literal["ok", "failed", "blocked"]
    question: str
    answer: str

    sql: Optional[str] = Field(
        default=None,
        description="The query that succeeded, after guard rewriting.",
    )
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0

    attempts: int = Field(default=0, ge=0)
    attempt_history: list[AttemptRecord] = Field(default_factory=list)

    blocked_layer: Optional[Literal["input", "sql", "output"]] = None
    blocked_reason: Optional[str] = None

    grounded: bool = Field(
        default=True,
        description="False when the grounding check rejected the model's prose. "
                    "The rows are still valid - only the narration was replaced.",
    )
    grounding_reason: Optional[str] = None

    chart_path: Optional[str] = None

    session_id: Optional[str] = Field(
        default=None,
        description="Present on /chat responses - pass it back to continue "
                    "the conversation.",
    )

    @model_validator(mode="after")
    def _check_invariants(self):
        if self.status == "blocked" and not self.blocked_reason:
            raise ValueError("a blocked response must say which guard fired and why")
        if self.status == "ok" and self.sql is None:
            raise ValueError("a successful response must carry the SQL that produced it")
        if self.row_count != len(self.rows):
            raise ValueError(
                f"row_count {self.row_count} does not match {len(self.rows)} rows"
            )
        if not self.grounded and self.grounding_reason is None:
            raise ValueError("a grounding failure must say what was ungrounded")
        return self


def from_state(state: dict) -> AgentResponse:
    """Build the response from the graph's final state."""
    attempts = state.get("attempts") or []
    successful = next(
        (a["sql"] for a in reversed(attempts) if a.get("error") is None), None
    )
    rows = [list(r) for r in (state.get("result") or [])]
    blocked = state.get("blocked") or {}

    return AgentResponse(
        status=state.get("status", "failed"),
        question=state.get("question", ""),
        answer=state.get("answer", ""),
        sql=successful,
        columns=state.get("columns") or [],
        rows=rows,
        row_count=len(rows),
        attempts=len(attempts),
        attempt_history=[AttemptRecord(**a) for a in attempts],
        blocked_layer=blocked.get("layer"),
        blocked_reason=blocked.get("reason"),
        grounded=state.get("grounded", True),
        grounding_reason=state.get("grounding_reason"),
        chart_path=state.get("chart_path"),
    )
