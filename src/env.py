"""
env.py — load .env before anything reads an environment variable.

Several modules read os.environ at import time - db.py picks SCHEMA and
DATA_SOURCE, tracing.py picks the Langfuse keys - so .env has to be loaded
before those imports run, not merely somewhere in the process. Importing this
module first is what guarantees that ordering.

A .env that is written but never read is worse than none: the setting looks
configured, behaves as though it is not, and nothing reports the discrepancy.
That is precisely what happened here - .env.example shipped before anything
called load_dotenv().
"""

from pathlib import Path

try:
    from dotenv import load_dotenv
    # override=False so a real environment variable always beats the file.
    # In a container or CI the environment is the source of truth.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except Exception:       # pragma: no cover - the agent must run without it
    pass
