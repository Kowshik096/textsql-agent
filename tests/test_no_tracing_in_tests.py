"""
A guard on the guard: the suite must never ship traces to a real project.

Once real keys sit in .env, every stubbed graph.answer() in this suite becomes
indistinguishable from production traffic in the dashboard - dozens of
zero-second traces burying the real ones. conftest.py blanks the keys, and
this asserts it actually took effect.

The first attempt did not. It popped the variables, and env.py's
load_dotenv(override=False) put them back from the file, because override=False
only declines to overwrite a key that is already present. Blank counts as
present; absent does not.
"""

import os

import tracing


def test_the_suite_is_not_tracing():
    assert tracing.ENABLED is False, (
        "tests are emitting traces to a real Langfuse project - "
        "check conftest.py blanks the keys rather than removing them"
    )


def test_the_keys_are_present_but_blank_not_absent():
    """Absent would let load_dotenv re-populate them from .env."""
    assert os.environ.get("LANGFUSE_PUBLIC_KEY") == ""
    assert os.environ.get("LANGFUSE_SECRET_KEY") == ""
