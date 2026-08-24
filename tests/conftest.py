import os
import sys
from pathlib import Path

# Make `import sql_guard` work without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Tests must not ship traces to a real Langfuse project.
#
# The suite calls graph.answer() dozens of times with a stubbed model, and once
# real keys sit in .env those runs are indistinguishable from production
# traffic in the dashboard - dozens of 0-second traces burying the real ones.
# Set EMPTY rather than removed, which is the part that actually works.
# Removing them lets env.py's load_dotenv(override=False) put them straight
# back from .env, because override=False only declines to overwrite a key that
# is already present. An empty string is present, so the file loses.
#
# Done before any project module is imported, since tracing.py reads the key
# at import time.
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
