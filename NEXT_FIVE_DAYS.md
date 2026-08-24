# If there were five more days

Written at the close of Day 5: 44.4% execution accuracy, 129 tests, 35 commits,
0 harmful executions, 0 false positives.

The ordering principle is **what a placement interviewer can be shown**, not
what is most fun to build. Every day below produces a number, a diff, or a
document. Days 1 and 2 are the ones I would refuse to drop.

---

## Day 1 — Read the failures instead of guessing at them

**Why first.** 35 of 63 functional cases fail and nobody has looked at them
one by one. Every remaining idea in this file is a guess until that happens.
Buying accuracy without knowing what is broken is how you spend three days on
a fix worth 1.5 points.

**Do:**
- Dump all 35 failures with question, generated SQL, reference SQL and error.
- Hand-classify each into a taxonomy - likely: wrong join path, wrong
  aggregate, hallucinated column, missing GROUP BY, timeout on correct SQL,
  right answer scored wrong by exact-match.
- Count the categories. Write the histogram into `evals/FAILURES.md`.

**Deliverable.** A failure taxonomy with counts, and one sentence per category
saying what would fix it.

**Why it matters in an interview.** "Why is it 44%?" answered with a
distribution rather than a shrug is the difference between someone who ran a
benchmark and someone who understands their system. The categories are the
whole answer.

**Watch for:** cases scored wrong that are actually right. Exact-match scoring
punishes a correct query that returns `SP` where the reference returns
`Sao Paulo`. If that count is material, the accuracy number is understated and
that is worth knowing before quoting it.

---

## Day 2 — The comparison the project is currently missing

**Why.** The README says "local model, and the comparison is the point" and
then never makes the comparison. A single run against a hosted model turns
every number in this project from an absolute into a *delta*, and the delta is
the defensible claim.

**Do:**
- Run the identical 78-case suite against a hosted model (Claude Haiku, or
  GPT-4o-mini) - same prompts, same scoring, same guard, only the client
  changes. `llm.py` is already the only place that touches the model.
- Report cost per query and latency alongside accuracy.
- If time allows, also run the 1.5B locally for the third point on the curve.

**Deliverable.** A three-row table: 1.5B / 3B / hosted, each with accuracy,
P50, and cost.

**Why it matters.** It converts "44.4%" into "44.4% on a 3B model at zero
marginal cost, versus X% at $Y per thousand queries" - which is an engineering
trade-off you can defend, not a low score you have to explain. It also settles
whether the architecture or the model is the bottleneck, which Day 1's
taxonomy will already have hinted at.

**Watch for:** this costs real money, though pennies at this volume. Cap the
spend and put the exact figure in the README - "$0.14 for the full suite" is a
better line than "cheap".

---

## Day 3 — Route the planner, and fix the top failure category

**Two things, both already justified by data you now have.**

**Route the planner.** The ablation says it is worth +10.7 on hard questions
and *costs* 4.6 on medium. So do not run it unconditionally. Classify the
question first - cheaply, on a heuristic, not a model call - and plan only when
it looks like it needs a join. Measure. If routing beats both fixed
configurations, that is a genuinely strong result: you found it by measuring,
not by reading a blog post.

**Fix the largest failure category from Day 1.** Whatever it turns out to be,
it is now known rather than guessed. Prompt or schema notes first, since Day 1
and Day 4 both showed those are the highest-leverage surface on a small model.

**Deliverable.** Accuracy before and after, per tier, plus the routing rule
written down in DECISIONS.md.

**Watch for:** the temptation to fix categories in order of how interesting
they are rather than how common. Fix the tall bar.

---

## Day 4 — Make the retry loop actually work

**Why.** This is the honest weak point of the whole project. The loop is what
makes it "an agent" and it recovers **1 case out of 9 retries**. The
architecture diagram implies far more than the measurement supports, and a good
interviewer will find that gap.

**Do:**
- From Day 1's taxonomy, isolate which failure categories are *recoverable in
  principle* - a hallucinated column name is; a fundamentally misunderstood
  question is not.
- Give the retry prompt the specific thing it needs for those categories. A
  SQLite "no such column" error currently goes back as raw text; sending back
  the actual column list for that table is a different quality of hint.
- Re-measure recovery rate, not just accuracy.

**Deliverable.** Recovery rate before and after, and an honest sentence in
DECISIONS.md if it stays low.

**Why it matters.** Either you improve it, or you can say precisely why
retrying does not help *this* class of problem - and the second is a perfectly
good interview answer, much better than the current silence.

---

## Day 5 — Harden the guard, then stop

**Why.** 11 blocked, 4 deflected, 0 harmful across 15 adversarial cases is a
good result on a suite you wrote yourself. Fifteen cases is thin, and a guard
only tested against attacks its author imagined is a guard with a blind spot
shaped like its author.

**Do:**
- Add 15-20 adversarial cases in categories the current suite does not cover:
  unicode and homoglyph tricks, SQL comments splitting keywords, instructions
  nested in what looks like data, multi-step social engineering across a
  question.
- Anything that gets through goes into the taxonomy and into DECISIONS.md,
  fixed or not.
- Re-run the gate.

**Deliverable.** An adversarial suite roughly double its current size, with any
new gaps documented.

**Then stop building.** Spend the last hours on the demo GIF, the README, and
rehearsing the four numbers you will be asked about: 44.4%, 0 false positives,
+3.1 for 3x latency, 1 recovery in 9 retries.

---

## Deliberately not on this list

**A vector store / RAG over the schema.** Seven tables fit in the context
window with room to spare. Adding retrieval would be architecture for its own
sake, and an interviewer who asks "why?" would be right to.

**A bigger local model.** The machine has 8 GB of RAM and Day 5 already hit a
disk wall. The hosted comparison on Day 2 answers the same question - does
model size fix this - for less money and less risk.

**A nicer UI.** Streamlit works. Polish there buys nothing an interviewer
weighs, and it costs a day.

**Multi-agent anything.** DECISIONS.md section 1 argues against it. Building it
now would contradict the document that is the strongest thing in the repo.

---

## If only one day existed

Day 1. The failure taxonomy. Everything else in this file is a hypothesis until
those 35 failures have been read, and "I read every failure and here is the
distribution" is worth more in an interview than two more points of accuracy.
