"""
llm.py — single place where the model is configured and called.

Everything that talks to the LLM goes through here. That matters for two
reasons:
  1. The eval harness must use identical settings to the live app, or the
     benchmark measures a different system than the one you demo.
  2. Token counts get logged in one place, so silent context truncation
     is visible instead of mysterious.
"""

import time
from dataclasses import dataclass, field

import ollama

MODEL = "qwen2.5-coder:3b"
BASELINE_MODEL = "qwen2.5-coder:1.5b"   # size ablation: same tuning as MODEL, half the params

# temperature=0 : SQL generation should be deterministic. Same question,
#                 same SQL — otherwise the eval harness measures noise.
# num_ctx=8192  : schema (~1k tokens) + question + accumulated retry history.
#                 Retries are where context grows; 4096 risks silent truncation.
# keep_alive    : hold the model in RAM between queries so a 55-query eval run
#                 does not pay a 2.2 GB reload each time.
OPTIONS = {
    "num_ctx": 8192,
    "temperature": 0,
    "repeat_penalty": 1.0,
}
KEEP_ALIVE = "30m"

# Warn well before the hard limit — truncation is silent, so we want notice.
CTX_WARN_THRESHOLD = int(OPTIONS["num_ctx"] * 0.8)


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int
    output_tokens: int
    latency_s: float
    model: str


@dataclass
class UsageLog:
    """Cheap in-process accounting. Langfuse replaces this on Day 4."""
    calls: list = field(default_factory=list)

    def record(self, r: LLMResponse):
        self.calls.append(r)

    def summary(self) -> str:
        if not self.calls:
            return "no calls recorded"
        total = sum(c.latency_s for c in self.calls)
        peak = max(c.prompt_tokens for c in self.calls)
        return (
            f"{len(self.calls)} calls | "
            f"{total:.1f}s total | "
            f"{total / len(self.calls):.1f}s avg | "
            f"peak prompt {peak} tokens"
        )


usage = UsageLog()


def chat(system: str, user: str, model: str = MODEL) -> LLMResponse:
    """Send one prompt and return the reply plus timing and token counts."""
    start = time.perf_counter()
    raw = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        keep_alive=KEEP_ALIVE,
        options=OPTIONS,
    )
    latency = time.perf_counter() - start

    prompt_tokens = raw.get("prompt_eval_count", 0)
    output_tokens = raw.get("eval_count", 0)

    # Silent truncation is the failure mode you cannot see from the output,
    # so make it loud here.
    if prompt_tokens >= CTX_WARN_THRESHOLD:
        print(
            f"  [warn] prompt is {prompt_tokens} tokens, nearing the "
            f"{OPTIONS['num_ctx']} limit — schema may get truncated"
        )

    response = LLMResponse(
        text=raw["message"]["content"],
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        latency_s=latency,
        model=model,
    )
    usage.record(response)
    return response


def extract_sql(text: str) -> str:
    """
    Pull SQL out of the model's reply.

    Models wrap SQL in ```sql fences and often add commentary around it.
    This is deliberately simple — the guardrail layer validates properly,
    so this only needs to find the statement, not judge it.
    """
    if "```" in text:
        blocks = text.split("```")
        for block in blocks[1::2]:          # odd indices are inside fences
            body = block.lstrip()
            if body.lower().startswith("sql"):
                body = body[3:]
            body = body.strip()
            if body:
                return body
    return text.strip()


if __name__ == "__main__":
    # Day 1 milestone: one question -> SQL -> result.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    import db

    SYSTEM = """You are a SQL expert working with a SQLite database.
Given a database schema and a question, write ONE SQLite SELECT query that answers it.

Rules:
- Output only the SQL inside a ```sql code block. No explanation.
- Use only tables and columns that appear in the schema.
- Never write DROP, DELETE, UPDATE, INSERT or ALTER.
- Always add a LIMIT unless the question asks for a single aggregate value."""

    schema = db.describe_schema()
    question = "Which 5 sellers generated the most revenue?"

    prompt = f"{schema}\n\nQUESTION: {question}\n\nSQL:"

    print(f"Question: {question}\n")
    reply = chat(SYSTEM, prompt)
    sql = extract_sql(reply.text)

    print("Generated SQL:")
    print(sql)
    print(f"\n[{reply.prompt_tokens} prompt tokens, "
          f"{reply.output_tokens} output tokens, {reply.latency_s:.1f}s]\n")

    conn = db.get_connection(read_only=True)
    try:
        rows = conn.execute(sql).fetchall()
        print(f"Result ({len(rows)} rows):")
        for row in rows[:10]:
            print("  ", row)
    except Exception as e:
        print(f"Query failed: {e}")
        print("\n(This is exactly the error the Day 2 retry loop will catch.)")
    finally:
        conn.close()

    print(f"\n{usage.summary()}")
