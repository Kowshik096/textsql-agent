"""
tracing.py — Langfuse instrumentation, optional by construction.

    LANGFUSE_PUBLIC_KEY=pk-... LANGFUSE_SECRET_KEY=sk-... uvicorn api:app --app-dir src

Without those keys this module is a no-op: `traced` returns the function
unchanged, so there is no wrapper, no import cost at call time, and no
behavioural difference at all. That property is the point rather than a
convenience - observability that can break the thing it observes is a bad
trade, and a project whose whole argument is layered safety should not make
its own agent depend on a telemetry service being reachable.

It also means CI and the benchmark run untraced without special handling, and
the 223 tests exercise the same code path either way.

What a trace contains, once enabled:

    one question           = one trace
      plan                 = span   (input guard verdict, the plan text)
      generate_and_execute = span   (per attempt: SQL, guard verdict, error)
        the model call     = generation (prompt, tokens, latency, temperature)
      verify               = span   (grounding verdict, chart path)

The retry count and which attempt succeeded are the direct evidence that the
loop is doing work - or, in this project's case, the direct evidence that it
is not: the Day 4 baseline showed twelve retries and zero recoveries, and a
trace makes that visible per query rather than only in aggregate.
"""

from __future__ import annotations

import os
from typing import Any, Callable

import env  # noqa: F401  - loads .env before the keys are read

# Tracing is on only when a key is present. Checked once at import: flipping it
# per-call would mean a branch on every model call for a setting that cannot
# change while the process runs.
ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))

# The SDK's `observe` is an overloaded decorator factory whose exact callable
# shape changes between releases, so it is held in an Any-typed slot rather
# than pinned here. The import binds a throwaway name; the holder assignment
# is what the rest of the module reads.
_observe: Any = None
if ENABLED:
    try:
        from langfuse import observe as _langfuse_observe
        _observe = _langfuse_observe
    except Exception:                                # pragma: no cover
        # An import failure must not take the agent down with it.
        ENABLED = False
        _observe = None


def traced(name: str, as_type: str = "span") -> Callable:
    """
    Decorate a pipeline stage so it appears as a span in the trace.

    Returns the function untouched when tracing is off, so the traced and
    untraced code paths are the same object rather than merely equivalent.
    """
    def decorate(fn: Callable) -> Callable:
        if not ENABLED or _observe is None:
            return fn
        return _observe(name=name, as_type=as_type)(fn)
    return decorate


def annotate(**fields: Any) -> None:
    """
    Attach metadata to the current span - guard verdicts, retry counts, the
    grounding result. Silent when tracing is off, and silent on failure:
    a telemetry backend being unreachable must not surface as an agent error.
    """
    if not ENABLED:
        return
    try:
        from langfuse import get_client
        get_client().update_current_span(metadata=fields)
    except Exception:                                    # pragma: no cover
        pass


def flush() -> None:
    """
    Force delivery of buffered events. Needed before a short-lived process
    exits - the benchmark harness and any CLI run - because the SDK batches
    in a background thread and would otherwise drop the tail.
    """
    if not ENABLED:
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:                                    # pragma: no cover
        pass


def status() -> dict:
    """Reported by /health, so a deployed instance can be asked whether it is
    actually tracing rather than assumed to be."""
    # Langfuse's own setup snippet writes LANGFUSE_BASE_URL while its docs
    # elsewhere use LANGFUSE_HOST. The SDK accepts either, so reporting only
    # one of them makes a correctly-configured instance look misconfigured.
    host = (os.environ.get("LANGFUSE_HOST")
            or os.environ.get("LANGFUSE_BASE_URL")
            or "https://cloud.langfuse.com")
    return {"enabled": ENABLED, "host": host if ENABLED else None}
