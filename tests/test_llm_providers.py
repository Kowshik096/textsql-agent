"""
The provider switch in llm.py.

The hosted comparison is only honest if the ONLY thing that changes is the
HTTP call. These tests pin the pieces that guarantee that: request bodies
carry the same system/user split and temperature, response parsing maps both
vendors' shapes onto the same tuple, the default path stays Ollama, and a
missing key fails loudly instead of silently falling back.
"""

import pytest

import llm


def test_default_provider_is_ollama():
    assert llm.PROVIDER == "ollama"


def test_dispatch_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "skynet")
    with pytest.raises(RuntimeError, match="not one of"):
        llm.chat("s", "u")


def test_hosted_provider_without_model_name_fails_loudly(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "anthropic")
    # MODEL still names the local qwen - refusing beats quietly asking a
    # 3B model's name against an Anthropic endpoint.
    with pytest.raises(RuntimeError, match="MODEL"):
        llm.chat("s", "u")


# --- request bodies ---------------------------------------------------------

def test_anthropic_body_keeps_system_out_of_messages():
    body = llm._anthropic_body("SYS", "USER", "claude-x", 0.3)
    assert body["system"] == "SYS"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert body["max_tokens"] == llm.HOSTED_MAX_TOKENS
    assert body["temperature"] == 0.3


def test_openai_body_uses_messages_array():
    body = llm._openai_body("SYS", "USER", "gpt-x", None if False else 0)
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "USER"}
    assert body["max_tokens"] == llm.HOSTED_MAX_TOKENS


# --- response parsing -------------------------------------------------------

def test_parse_anthropic_response():
    raw = {
        "content": [{"type": "text", "text": "```sql\nSELECT 1\n```"}],
        "usage": {"input_tokens": 1010, "output_tokens": 42},
    }
    text, ptok, otok = llm._parse_anthropic_response(raw)
    assert "SELECT 1" in text
    assert (ptok, otok) == (1010, 42)


def test_parse_anthropic_missing_usage_is_zero_not_crash():
    _, ptok, otok = llm._parse_anthropic_response({"content": []})
    assert (ptok, otok) == (0, 0)


def test_parse_openai_response():
    raw = {
        "choices": [{"message": {"content": "plain text"}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 7},
    }
    text, ptok, otok = llm._parse_openai_response(raw)
    assert text == "plain text"
    assert (ptok, otok) == (900, 7)


def test_parse_openai_empty_choices_is_safe():
    text, ptok, otok = llm._parse_openai_response({})
    assert text == ""
    assert (ptok, otok) == (0, 0)


# --- dispatch ---------------------------------------------------------------

class _FakeResponse:
    text = "fake"
    prompt_tokens = 1
    output_tokens = 2
    latency_s = 0.0
    model = "fake"


def test_chat_routes_through_the_selected_caller(monkeypatch):
    seen = {}

    def fake_caller(system, user, model, temperature):
        seen["args"] = (system, user, model, temperature)
        return ("routed", 11, 22)

    monkeypatch.setitem(llm._CALLERS, llm.PROVIDER, fake_caller)
    reply = llm.chat("S", "U", temperature=0.5)
    assert reply.text == "routed"
    assert (reply.prompt_tokens, reply.output_tokens) == (11, 22)
    assert seen["args"][:2] == ("S", "U")
    assert isinstance(reply, llm.LLMResponse)


def test_missing_api_key_names_the_variable(monkeypatch):
    monkeypatch.setattr(llm, "PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm._require_key("ANTHROPIC_API_KEY")
