"""
The answer-quality judge and its Gemini plumbing.

Pinned properties: the judge is off unless JUDGE_MODEL is set; it grades
prose against rows and never sees SQL; a malformed verdict is a judge
failure recorded as such, not a score against the agent; and judge calls
route to the judge's provider/model, not the benchmark's.
"""

import pytest

import judge
import llm


# --- gemini provider pieces -------------------------------------------------

def test_gemini_body_uses_system_instruction():
    body = llm._gemini_body("SYS", "USER", "gemini-x", 0.0)
    assert body["system_instruction"] == {"parts": [{"text": "SYS"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "USER"}]}]
    assert body["generationConfig"]["maxOutputTokens"] == llm.HOSTED_MAX_TOKENS


def test_gemini_response_parsing():
    raw = {
        "candidates": [{"content": {"parts": [{"text": "hi"}, {"text": "there"}]}}],
        "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 3},
    }
    text, ptok, otok = llm._parse_gemini_response(raw)
    assert text == "hithere"
    assert (ptok, otok) == (50, 3)


def test_gemini_missing_candidates_is_safe():
    text, ptok, otok = llm._parse_gemini_response({})
    assert text == "" and ptok == 0 and otok == 0


def test_gemini_registered_as_a_provider():
    assert "gemini" in llm._CALLERS


# --- judge gating -----------------------------------------------------------

def test_judge_disabled_without_model(monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_MODEL", "")
    monkeypatch.setattr(judge, "ENABLED", False)
    assert judge.judge_answer("q", ["c"], [[1]], "a") is None


def test_judge_prompt_has_rows_and_answer_but_never_sql():
    p = judge.build_judge_prompt(
        "How many sellers?", ["seller_id", "revenue"],
        [["s1", 100.0], ["s2", 90.0]],
        "Two sellers were returned, led by s1.",
    )
    assert "QUESTION: How many sellers?" in p
    assert "s1 | 100.0" in p
    assert "ANSWER: Two sellers were returned" in p
    assert "SELECT" not in p.upper().replace("RESULT", "")


def test_judge_routes_to_judge_model_and_provider(monkeypatch):
    seen = {}

    def fake_chat(system, user, model=None, temperature=None, provider=None):
        seen["model"] = model
        seen["provider"] = provider
        seen["temperature"] = temperature

        class R:
            text = '{"faithful": true, "answers_question": true, "issue": ""}'

        return R()

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(judge, "JUDGE_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setattr(judge, "JUDGE_PROVIDER", "gemini")
    monkeypatch.setattr(judge, "ENABLED", True)

    v = judge.judge_answer("q", ["c"], [[1]], "a")
    assert v.faithful and v.answers_question and v.parse_ok
    assert seen["model"] == "gemini-2.5-flash-lite"
    assert seen["provider"] == "gemini"
    assert seen["temperature"] == 0


# --- verdict parsing --------------------------------------------------------

@pytest.mark.parametrize("raw,faithful", [
    ('{"faithful": true, "answers_question": true, "issue": ""}', True),
    ('```json\n{"faithful": false, "answers_question": true, '
     '"issue": "invented total"}\n```', False),
    ('Sure! {"faithful": true, "answers_question": false, '
     '"issue": "wrong focus"} - hope that helps', True),
])
def test_verdict_parsing_survives_fences_and_chatter(raw, faithful, monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_MODEL", "m")
    monkeypatch.setattr(judge, "ENABLED", True)

    class R:
        text = raw

    monkeypatch.setattr(llm, "chat", lambda *a, **k: R())
    v = judge.judge_answer("q", ["c"], [[1]], "a")
    assert v.faithful is faithful
    assert v.parse_ok


def test_unparseable_verdict_is_a_judge_failure_not_a_score(monkeypatch):
    monkeypatch.setattr(judge, "JUDGE_MODEL", "m")
    monkeypatch.setattr(judge, "ENABLED", True)

    class R:
        text = "I cannot grade this."

    monkeypatch.setattr(llm, "chat", lambda *a, **k: R())
    v = judge.judge_answer("q", ["c"], [[1]], "a")
    assert v.parse_ok is False
    assert "unparseable" in v.issue
