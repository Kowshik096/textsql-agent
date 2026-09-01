"""
llm.py — single place where the model is configured and called.

Everything that talks to the LLM goes through here. That matters for two
reasons:
  1. The eval harness must use identical settings to the live app, or the
     benchmark measures a different system than the one you demo.
  2. Token counts get logged in one place, so silent context truncation
     is visible instead of mysterious.
"""

import os
import time
from dataclasses import dataclass, field

import ollama

import tracing

# MODEL=qwen2.5-coder:1.5b runs the size ablation. An env var rather than an
# edited constant, matching SCHEMA, ANSWER and PLANNER: an ablation that needs
# a source edit is one where the two runs can differ by more than the single
# thing being measured.
MODEL = os.environ.get("MODEL", "qwen2.5-coder:3b")

# Same tuning, half the parameters. Coder-1.5B rather than general-1.5B so the
# comparison varies size alone - swapping to qwen2.5:1.5b would vary size and
# instruction-tuning together and measure neither.
BASELINE_MODEL = "qwen2.5-coder:1.5b"

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

# Retries run hotter than the first attempt, and this is the fix for a measured
# failure rather than a preference. At temperature 0 the model regenerated
# byte-identical SQL on every retry: twelve retries in the Day 3 baseline, zero
# recoveries. Moving the error above the schema did not shift it, and neither
# did a hint naming the column and the tables that actually have it. The
# distribution is simply too peaked for appended context to change the argmax.
#
# The cost is real and bounded: first attempts stay at temperature 0, so the
# common path is still reproducible and regression gating still works. Only
# retries vary, and a query that reached a retry was already wrong.
RETRY_TEMPERATURE = 0.3

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


# --- providers ---------------------------------------------------------------
#
# The hosted comparison (NEXT_FIVE_DAYS Day 2) needs the identical benchmark -
# same prompts, same guards, same scoring - against a different model, and
# `chat()` is the only place the model is touched. So the switch lives here:
#
#     LLM_PROVIDER=anthropic MODEL=claude-3-5-haiku-latest python evals/harness.py
#
# Hosted clients are hand-rolled over `requests` rather than SDK imports: two
# POST calls need no dependency, and a project that just pruned three dead
# packages is not going to add two more for a comparison run. API keys are
# read at call time so tests can inject them and so .env changes do not need
# a process restart.

PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()

# Anthropic requires an explicit ceiling; OpenAI defaults to inf. One generous
# constant covers both - SQL and prose answers are short.
HOSTED_MAX_TOKENS = 1024


def _require_key(env_var: str) -> str:
    key = os.environ.get(env_var, "")
    if not key:
        raise RuntimeError(
            f"LLM_PROVIDER={PROVIDER} requires {env_var} in the environment "
            f"or .env"
        )
    return key


def _anthropic_body(system: str, user: str, model: str,
                    temperature: float) -> dict:
    return {
        "model": model,
        "max_tokens": HOSTED_MAX_TOKENS,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }


def _openai_body(system: str, user: str, model: str,
                 temperature: float) -> dict:
    return {
        "model": model,
        "max_tokens": HOSTED_MAX_TOKENS,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def _parse_anthropic_response(raw: dict) -> tuple:
    text = "".join(block.get("text", "") for block in raw.get("content", []))
    usage_in = raw.get("usage", {})
    return text, usage_in.get("input_tokens", 0), usage_in.get("output_tokens", 0)


def _parse_openai_response(raw: dict) -> tuple:
    choices = raw.get("choices") or [{}]
    text = choices[0].get("message", {}).get("content", "")
    usage_in = raw.get("usage", {})
    return (text, usage_in.get("prompt_tokens", 0),
            usage_in.get("completion_tokens", 0))


def _call_ollama(system: str, user: str, model: str,
                 temperature: float | None) -> tuple:
    options = OPTIONS if temperature is None else {**OPTIONS, "temperature": temperature}
    raw = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        keep_alive=KEEP_ALIVE,
        options=options,
    )
    return (raw["message"]["content"], raw.get("prompt_eval_count", 0),
            raw.get("eval_count", 0))


def _call_anthropic(system: str, user: str, model: str,
                    temperature: float | None) -> tuple:
    import requests

    key = _require_key("ANTHROPIC_API_KEY")
    base = os.environ.get("ANTHROPIC_BASE_URL",
                          "https://api.anthropic.com").rstrip("/")
    response = requests.post(
        f"{base}/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json=_anthropic_body(system, user, model,
                             OPTIONS["temperature"] if temperature is None
                             else temperature),
        timeout=120,
    )
    response.raise_for_status()
    return _parse_anthropic_response(response.json())


def _call_openai(system: str, user: str, model: str,
                 temperature: float | None) -> tuple:
    import requests

    key = _require_key("OPENAI_API_KEY")
    base = os.environ.get("OPENAI_BASE_URL",
                          "https://api.openai.com/v1").rstrip("/")
    response = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=_openai_body(system, user, model,
                          OPTIONS["temperature"] if temperature is None
                          else temperature),
        timeout=120,
    )
    response.raise_for_status()
    return _parse_openai_response(response.json())


def _gemini_body(system: str, user: str, model: str,
                 temperature: float) -> dict:
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": HOSTED_MAX_TOKENS,
        },
    }


def _parse_gemini_response(raw: dict) -> tuple:
    candidates = raw.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts)
    usage = raw.get("usageMetadata", {})
    return (text, usage.get("promptTokenCount", 0),
            usage.get("candidatesTokenCount", 0))


def _call_gemini(system: str, user: str, model: str,
                 temperature: float | None) -> tuple:
    import requests

    key = _require_key("GEMINI_API_KEY")
    base = os.environ.get(
        "GEMINI_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta",
    ).rstrip("/")
    response = requests.post(
        f"{base}/models/{model}:generateContent",
        headers={"x-goog-api-key": key},
        json=_gemini_body(system, user, model,
                          OPTIONS["temperature"] if temperature is None
                          else temperature),
        timeout=120,
    )
    response.raise_for_status()
    return _parse_gemini_response(response.json())


_CALLERS = {
    "ollama": _call_ollama,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


@tracing.traced("llm", as_type="generation")
def chat(system: str, user: str, model: str = MODEL,
         temperature: float | None = None,
         provider: str | None = None) -> LLMResponse:
    """
    Send one prompt and return the reply plus timing and token counts.

    `temperature` overrides the default for this call only. It exists for the
    retry path: at temperature 0 the model is locked onto its first answer and
    regenerates it byte-for-byte no matter what the prompt says about the
    error, so a deterministic retry cannot recover. See RETRY_TEMPERATURE.

    `provider` overrides LLM_PROVIDER for this call only. It exists for the
    offline judge: the benchmark runs on Ollama while the judge grades from a
    hosted model, so judge ≠ generator is structural rather than claimed.
    The provider switch lives here, where the hosted comparison (NEXT_FIVE_DAYS
    Day 2) needs it: same prompts, same guards, only the client changes.
    """
    used_provider = (provider or PROVIDER).strip().lower()
    caller = _CALLERS.get(used_provider)
    if caller is None:
        raise RuntimeError(
            f"LLM_PROVIDER={used_provider!r} is not one of {sorted(_CALLERS)}"
        )
    if used_provider != "ollama" and model.startswith("qwen"):
        raise RuntimeError(
            f"LLM_PROVIDER={used_provider} needs MODEL set to a hosted model "
            f"name (e.g. MODEL=gemini-2.5-flash-lite)"
        )

    start = time.perf_counter()
    text, prompt_tokens, output_tokens = caller(system, user, model, temperature)
    latency = time.perf_counter() - start

    # Silent truncation is the failure mode you cannot see from the output,
    # so make it loud here. Only meaningful on Ollama, where num_ctx exists;
    # hosted APIs truncate server-side and say so.
    if used_provider == "ollama" and prompt_tokens >= CTX_WARN_THRESHOLD:
        print(
            f"  [warn] prompt is {prompt_tokens} tokens, nearing the "
            f"{OPTIONS['num_ctx']} limit — schema may get truncated"
        )

    response = LLMResponse(
        text=text,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        latency_s=latency,
        model=model,
    )
    tracing.annotate(model=model, prompt_tokens=prompt_tokens,
                     output_tokens=output_tokens, latency_s=round(latency, 2),
                     temperature=temperature if temperature is not None
                     else OPTIONS["temperature"],
                     provider=used_provider)
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
