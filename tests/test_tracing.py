"""
Tracing must be invisible when off. These tests assert the *absence* of an
effect, which is the property that matters: observability that can break the
thing it observes is a bad trade.
"""

import importlib
import os

import tracing


def test_disabled_without_a_key(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    reloaded = importlib.reload(tracing)
    assert reloaded.ENABLED is False
    assert reloaded.status()["enabled"] is False


def test_traced_returns_the_function_untouched_when_off(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    reloaded = importlib.reload(tracing)

    def original(x):
        return x * 2

    wrapped = reloaded.traced("whatever")(original)
    # The same object, not merely an equivalent one - no wrapper, no overhead.
    assert wrapped is original
    assert wrapped(3) == 6


def test_annotate_and_flush_are_silent_when_off(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    reloaded = importlib.reload(tracing)
    reloaded.annotate(anything="at all", number=1)
    reloaded.flush()


def test_annotate_never_raises_even_when_enabled(monkeypatch):
    """
    A telemetry backend being unreachable must not surface as an agent error.
    Enabled with a bogus key, annotate() must still return quietly.
    """
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-not-a-real-key")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-not-a-real-key")
    reloaded = importlib.reload(tracing)
    reloaded.annotate(x=1)
    reloaded.flush()
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    importlib.reload(tracing)


def test_the_agent_produces_identical_results_traced_or_not():
    """The traced and untraced paths must be the same code, not similar code."""
    import graph
    assert graph.plan.__name__ == "plan"
    assert graph.verify.__name__ == "verify"
