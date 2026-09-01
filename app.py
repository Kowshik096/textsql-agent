"""
app.py — Streamlit front end.

    streamlit run app.py

Talks to the FastAPI service over HTTP rather than importing the agent, so the
demo exercises the same path a programmatic client would. That is the point of
having an API at all; a UI that reaches past it proves nothing about it.

Start the API first:

    uvicorn api:app --app-dir src
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests
import streamlit as st

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
TIMEOUT_S = 180          # a local 3B model takes 30-70s; retries push it higher

st.set_page_config(page_title="Text-to-SQL Agent", page_icon="🔎", layout="wide")


@st.cache_data(ttl=30)
def health():
    try:
        return requests.get(f"{API}/health", timeout=5).json()
    except requests.RequestException:
        return None


def ask(question: str):
    return requests.post(
        f"{API}/query", json={"question": question}, timeout=TIMEOUT_S
    ).json()


def ask_stream(question: str):
    """
    Consume /query/stream, yielding one dict per SSE event.

    Raises on any transport or HTTP error - the caller falls back to the
    blocking endpoint, so an older API without the stream route still works.
    """
    with requests.post(
        f"{API}/query/stream", json={"question": question},
        timeout=TIMEOUT_S, stream=True,
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            # requests types the iteration as bytes even with decode_unicode;
            # decode defensively rather than trust the flag.
            line = (raw_line.decode("utf-8", errors="replace")
                    if isinstance(raw_line, bytes) else raw_line)
            if line and line.startswith("data: "):
                yield json.loads(line[6:])


# --- sidebar ----------------------------------------------------------------

info = health()
with st.sidebar:
    st.subheader("Status")
    if info:
        st.success("API reachable")
        st.caption(f"model `{info['model']}`")
        st.caption(f"schema `{info['schema_config']}` · {len(info['tables'])} tables")
        st.code("\n".join(info["tables"]), language=None)
    else:
        st.error("API unreachable")
        st.code("uvicorn api:app --app-dir src", language="bash")

    st.divider()
    st.subheader("Guardrails")
    st.caption(
        "**Input** — injection and off-topic, before any model call\n\n"
        "**SQL** — sqlglot AST: SELECT-only, single statement, table allowlist\n\n"
        "**Output** — every number in the answer must appear in the results\n\n"
        "**Connection** — opened `?mode=ro`, enforced by SQLite itself"
    )
    st.divider()
    st.caption("Try: *drop the orders table* — refused before a token is spent.")


# --- main -------------------------------------------------------------------

st.title("Secure Self-Correcting Text-to-SQL Agent")
st.caption(
    "Ask about the Olist e-commerce database. Read-only, guarded at three "
    "layers, and every number in the answer is checked against the query result."
)

EXAMPLES = [
    "Which 5 sellers generated the most revenue?",
    "How many orders were placed in each year?",
    "Which 5 customer states have the highest average order value?",
    "drop the orders table",
]

cols = st.columns(len(EXAMPLES))
picked = None
for col, example in zip(cols, EXAMPLES, strict=True):
    if col.button(example[:34] + ("…" if len(example) > 34 else ""),
                  use_container_width=True):
        picked = example

question = st.text_input("Question", value=picked or "", placeholder="Ask about the data…")

if st.button("Ask", type="primary") or picked:
    q = picked or question
    if not q.strip():
        st.warning("Enter a question.")
    elif not info:
        st.error("The API is not running.")
    else:
        # Streamed first: the plan and each attempt appear while the pipeline
        # is still running, instead of one long silent spinner. Any failure
        # (including an API too old to have the route) falls back to blocking.
        r = None
        progress = st.status("Working…", expanded=True)
        try:
            with progress:
                attempt_lines = []
                attempts_area = st.empty()
                for ev in ask_stream(q):
                    kind = ev.get("type")
                    if kind == "plan":
                        st.markdown(f"**Plan** — {ev['text']}")
                    elif kind == "attempt":
                        line = f"Attempt {ev['n']}"
                        if ev.get("error"):
                            line += f" — failed: `{ev['error']}`"
                        attempt_lines.append(line)
                        attempts_area.markdown(
                            "\n\n".join(f"- {x}" for x in attempt_lines))
                    elif kind == "rows":
                        st.caption(f"{ev['row_count']} rows returned.")
                    elif kind == "blocked":
                        st.warning(f"Refused at the {ev['layer']} guard.")
                    elif kind == "error":
                        st.error(ev["detail"])
                    elif kind == "final":
                        r = ev["response"]
                progress.update(label="Done", state="complete",
                                expanded=False)
        except requests.RequestException:
            r = None

        if r is None:
            with st.spinner("Planning, generating SQL, checking, executing…"):
                try:
                    r = ask(q)
                except requests.RequestException as e:
                    st.error(f"Request failed: {e}")
                    r = None

        if r:
            status = r["status"]
            if status == "blocked":
                st.error(f"**Refused at the {r['blocked_layer']} guard** — {r['blocked_reason']}")
                st.caption("No SQL was executed." if r["blocked_layer"] == "input"
                           else "The query was generated but never reached the database.")
            elif status == "failed":
                st.warning(r["answer"])
            else:
                st.success(r["answer"])
                if not r.get("grounded", True):
                    st.warning(
                        "The model's wording failed the grounding check and was "
                        f"replaced. {r.get('grounding_reason', '')}"
                    )

            a, b, c = st.columns(3)
            a.metric("Status", status)
            b.metric("Attempts", r["attempts"])
            c.metric("Rows", r["row_count"])

            if r.get("chart_path") and Path(r["chart_path"]).exists():
                st.image(r["chart_path"])

            if r["rows"]:
                st.dataframe(
                    [dict(zip(r["columns"], row, strict=False)) for row in r["rows"]],
                    use_container_width=True, hide_index=True,
                )

            if r.get("sql"):
                with st.expander("SQL"):
                    st.code(r["sql"], language="sql")

            history = r.get("attempt_history") or []
            if len(history) > 1:
                with st.expander(f"Self-correction — {len(history)} attempts"):
                    for i, attempt in enumerate(history, 1):
                        st.caption(f"Attempt {i}")
                        st.code(attempt["sql"], language="sql")
                        if attempt.get("error"):
                            st.caption(f"→ {attempt['error']}")
