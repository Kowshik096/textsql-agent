# Resume bullets

Three lengths of the same four claims — pick whichever fits your resume's
line width. Every number is measured and in the repo; nothing here is rounded
up or asserted without a run behind it. Character counts are exact (`len()`),
not eyeballed.

The four claims, in order, are deliberately different *kinds* of result:
system + accuracy, safety under a worse model, honest measurement of a
component's cost, and shipped engineering. A reader who only gets one line
in should get the safety one - it's the rarest claim on this list.

---

## 125-character limit

1. Built a self-correcting text-to-SQL agent (LangGraph, 3-layer guardrails) on a local 3B model; 44.4% accuracy, 78 cases. *(120)*
2. Proved guardrails are deterministic: halving the model to 1.5B cut accuracy 14pts while harmful executions stayed at zero. *(122)*
3. Ran a planner ablation: +3.1 accuracy pts for 3x latency, gain concentrated entirely in hard multi-join queries. *(112)*
4. Shipped FastAPI+Streamlit+Docker behind a CI gate that fails the build on 1 harmful execution, even at 100% accuracy. *(117)*

## 140-character limit

1. Built a self-correcting text-to-SQL agent (LangGraph, 3-layer guardrails) on a local 3B model; 44.4% accuracy on a 78-case benchmark. *(133)*
2. Proved guardrails are deterministic, not model judgment: halving the model to 1.5B cut accuracy 14pts, harmful executions stayed zero. *(134)*
3. Ran a planner ablation over 78 cases: +3.1 accuracy points for 3x latency, gain concentrated entirely in hard multi-join queries. *(129)*
4. Shipped FastAPI+Streamlit+Docker behind a CI gate that fails the build on 1 harmful SQL execution, even at 100% functional accuracy. *(132)*

## 160-character limit

1. Designed a self-correcting text-to-SQL agent (LangGraph, 3-layer guardrails) on a local 3B model; 44.4% execution accuracy on a 78-case hand-verified benchmark. *(160)*
2. Proved guardrails are deterministic, not model judgment: halving the model to 1.5B cut accuracy 14pts (44.4% to 30.2%) while harmful executions stayed at zero. *(159)*
3. Ran a planner ablation across 78 cases: +3.1 accuracy points for 3x the latency, with the entire gain concentrated in hard, multi-join queries only. *(148)*
4. Shipped FastAPI, Streamlit and Docker behind a CI gate that fails the build on any harmful SQL execution, even at 100% functional accuracy; 129 tests. *(150)*

---

## Why these four and not others

**#1 is the headline, and it carries its own constraint.** "44.4%" bare reads
as a failing grade; "44.4% on a local 3B model, hand-verified benchmark" reads
as an engineering trade-off. The constraint has to be in the same clause as
the number or the sentence lies by omission.

**#2 is the strongest claim in the project and the one most resumes don't
have.** Most people report a block rate, which any guard that refuses
everything scores 100% on. This says something stronger: safety held while a
worse model was substituted in, because the guards are regex, `sqlglot`
parsing and an allowlist, not model judgment. That's provable, not asserted -
see `evals/results_1_5b.txt` and DECISIONS.md section 23.

**#3 is measurement discipline, not a feature.** "I used a planner" is a
design choice. "I measured what the planner costs and where the accuracy
gain actually comes from" is a different thing to say in an interview, and
it's the one worth having ready if asked "why does this take 38 seconds?"

**#4 is the part that makes the other three checkable.** A CI gate that fails
a build at 100% functional accuracy is unusual enough that it's worth a
sentence, and it signals the project has tests, not just a demo.

## If you only want three

Drop #3 (the ablation) before you drop #2 (the safety result under a worse
model) - the ablation is interesting engineering judgment, but the safety
result is the one that says something a hiring manager cannot get from most
other portfolio projects.

## Sources, if asked to defend a number

| Claim | Where it's measured |
|---|---|
| 44.4% / 78 cases | [README.md](README.md) Results; [evals/results.txt](evals/results.txt) |
| 1.5B safety-flat result | [DECISIONS.md](DECISIONS.md) sections 23-24; `evals/results_1_5b.txt` |
| Planner ablation | [DECISIONS.md](DECISIONS.md) section 20; `evals/results_planner_on.txt` / `results_planner_off.txt` |
| CI gate, 129 tests | [tests/test_gate.py](tests/test_gate.py); `scripts/check_gate.py` |
