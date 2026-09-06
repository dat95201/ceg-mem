# Experiment plan to make this paper competitive

Written against the verified SANER 2027 calls, the four simulated reviews, and
what the frozen run can and cannot already answer. Every cost below is
estimated from this study's own measured rates.

## The deadline decision comes first

| Track | Abstract (mandatory) | Paper | Fit |
|---|---|---|---|
| Research | **21 Sep 2026** | 25 Sep 2026 | good |
| **Agentic AI4SE** | **19 Oct 2026** | **23 Oct 2026** | better |

Both impose the identical 10+2 IEEEtran rule. The Agentic AI4SE track is new
for 2027, its scope is agents that "plan, act, use tools… and adapt", and its
review criteria explicitly include artifact quality — which is this paper's
strength. It also buys **four more weeks**, and it does not mandate a Data
Availability section (we keep ours anyway).

**Recommendation: target Agentic AI4SE, 23 Oct.** That is 48 days, which is
enough for items 1–3 below and not enough for all five. Going for the Research
Track on 25 Sep means shipping with item 1 only.

## Prerequisite, not an experiment: the page cut

`make ieee` reports **21 pages**; the limit is 10 + 2 references-only. Nothing
below matters until this is fixed, and no experiment changes it. Current page
map and the target:

| Section | Now | Target | How |
|---|---|---|---|
| Intro | 2 | 1.25 | fold the five findings into three; the itemize becomes prose |
| Related | 1.5 | 1 | keep Table I, cut the per-subsection "How we differ" to one clause each |
| Architecture | 2 | 1.25 | Table II (notation) → supplementary; keep Eq. 1–5 and Alg. 1 |
| Theory | 2 | 1 | keep Thm 2, Thm 3, Cor 5 statements; Thm 4 and Prop 6 → supplementary, cite as such |
| Implementation | 1 | 0.5 | the correction paragraph shrinks to three sentences |
| Design | 2 | 1.25 | attrition table → supplementary, keep two sentences of it |
| **Results** | **7** | **3.5** | see below |
| Discussion | 1 | 0.75 | merge into Results' finding boxes |
| Threats | 1.5 | 0.75 | keep construct + external + the unit-of-analysis paragraph |
| Conclusion + Data Avail. | 1 | 0.5 | |
| References | 1.5 | 2 | fine as is |

Results is where the 3.5 pages come from. Of 8 figures and 6 tables, keep
**3 figures** (budget curve; per-band; guard-cost scatter) and **2 tables**
(main comparison; task-level inference). Fold total-test-work into the main
table as two extra rows rather than its own table. Everything else —
budget-both, typing-dose, oracle-depth, prompt-growth, wall-clock, ablation,
per-band, free-guard, integrity, attrition — goes to the supplementary archive
with a one-line pointer each. The `\suppmattertrue` switch already reroutes the
references.

Budget: **2–3 days of editing.** Do it before running anything, because it
tells you how much room a new result can occupy (answer: about a third of a
page each).

---

## 1. The candidate × case verdict matrix, and the baselines it unlocks

**Why this is first.** Reviews 1 and 3 both make the same demand and it is the
one that decides the novelty question: compare against
`fault-recorded test prioritization` [Qi'13] and `modification-point aware
prioritization` [Venugopal'20], the two mechanisms closest to ours. Without
them, Table I's positioning is argued rather than measured.

**Why it is also nearly free.** Those baselines change *which tests run in what
order*, not what the model proposes. Under our nonce scheme the proposal
sequence is a function of (task, seed, round) only, so a prioritization arm
draws the **byte-identical candidates** the no-memory arm drew — every model
call is a cache hit. The only new cost is sandbox execution.

**The measurement.** Re-execute each frozen candidate against the full shipped
pool once and record the pass/fail bit per (candidate, case). That matrix makes
every ordering policy computable offline, with no further runs:

- fault-recorded prioritization: order cases by how many earlier candidates
  they killed
- modification-point aware: the same, keyed on the candidate's edit location
- regression-test selection: only cases reaching changed lines
- our guard: does any stored counterexample still fail?
- the oracle as reported: up to k cases in fixed order

All five then get the same denominators — executions per episode, executions to
first accept, and repair at a matched execution budget — paired per cell.

**Cost.** The no-memory arm has ≈4,700 refuted candidates over 495 cells; the
pool averages 39 cases. At the measured ≈0.15 s per case that is ≈50 CPU-hours
single-threaded, **≈7 hours on 8 workers**, one time. Model calls: zero.
Then each policy is seconds of Python.

**What it buys.** The paper's central claim moves from "no LLM system decides
which validations to skip" (true but weak) to "against the two classical
mechanisms that do, on identical candidates, here is the trade". It also
answers Review 3's request for a combined test-work metric at the *case* level
rather than the episode level. This single experiment addresses the largest
objection in three of the four reviews.

**Risk.** If prioritization turns out to reduce executions as much as the guard
does, that is a genuine negative result about our contribution — and better
learned now than from a reviewer. Note that it cannot reduce *oracle calls*,
which stay one per candidate, so the two mechanisms are not substitutes; the
honest framing survives either outcome.

## 2. A second proposer, to test the one claim that is scale-dependent

**Why.** Every reviewer flags one model as the external-validity limit, and the
paper itself predicts that the guard result transfers (it is a property of the
runtime) while the steering null may not (a larger model might obey an
exclusion instruction). That prediction is testable and its failure would be
the most interesting thing in the paper.

**Design.** Three arms — no-memory, untyped, typed — on a second open-weight
coder model, same protocol, same nonce scheme, so round 1 is again identical
within the new model. Do **not** run all five arms and do not re-run the
sweeps; the question is only whether the guard/steer split holds.

**Scope choice.** Full corpus × 5 seeds is 1,485 cells and ≈15,000
uncached calls. The 55 primary-band tasks × 3 seeds × 3 arms is **495 cells,
≈4,700 calls**, and the primary bands are where a budget binds — the same
restriction the paper already uses for its pooled test. At the measured 8 s per
call that is **≈11 hours**, plus sandbox.

**Model choice.** A same-family, larger model isolates scale:
`qwen2.5-coder:14b` or `:32b` quantized. A different family confounds scale
with training data, which weakens the inference — pick the same family unless
the hardware forbids it.

**Reporting.** One table: guard effect and steer effect at both scales, with the
interaction. Two sentences in Threats become one sentence in Results.

## 3. The interaction test the dead-band claim needs

**Why.** Review 3 is right that "steering is the whole effect in the dead band"
rests on one band of five with a per-task *p* = 0.047. Reporting band-wise
*p*-values is not a test of the interaction.

**How.** No new runs. Fit repair ~ arm × band on the existing 1,485 cells
(mixed-effects logistic with a task random effect, or a permutation test on the
arm × band statistic if you prefer to stay assumption-free), and report the
interaction term with a confidence interval. Adjust within the band family.

**Cost.** Half a day of analysis. If the interaction is not significant, the
adaptive-steering recommendation in Discussion becomes a hypothesis with a
stated power limitation — which is what it should have been.

## 4. Run the guard on the faults we filtered out

**Why.** `tables/attrition.tex` now discloses that the slow-reference filter
removed the 48 faults whose oracle is most expensive (reference worst case
10.0 s vs 1.16 s; rating 2042 vs 940) — exactly the regime where a guard is
worth most. A reviewer will ask whether the mechanism was validated on the easy
half of the distribution. Answering with data turns the study's biggest
selection bias into a supporting result.

**Design.** no-memory, untyped, guard-only on the 48 excluded faults, 3 seeds =
432 cells. New tasks, so no cache hits: ≈4,000 model calls (**≈9 hours**) plus
a deliberately expensive oracle — budget 2–3 days of wall-clock, and cap the
per-episode budget at 10 rather than 20 to keep it bounded.

**Prediction to state in advance.** Oracle-call reduction ≥ the corpus figure
(×5.2) and wall-clock reduction strictly greater, because each avoided
validation is worth more seconds; repair rate lower in every arm.

## 5. Repository scale — name it, do not attempt it

SWE-bench-scale evaluation is the obvious next question and it is a different
paper: localization dominates, the oracle is partial, and the type map would
have to be redesigned over stack traces and changed files. Attempting a thin
version of it in seven weeks would produce a result too weak to help and would
consume the time items 1–3 need. Keep it as the closing paragraph of
Conclusion, where it already is.

---

## What to do in what order

| | Item | Effort | Blocking? |
|---|---|---|---|
| Week 1 | Page cut to 10+2 | 2–3 days | **yes — desk reject without it** |
| Week 1 | Item 3, interaction test | 0.5 day | no |
| Weeks 2–3 | Item 1, verdict matrix + 4 baseline policies | 7 h compute, 3 days analysis | no |
| Weeks 3–5 | Item 2, second model on 495 primary-band cells | 11 h compute, 2 days analysis | no |
| Week 6 | Item 4, the 48 excluded faults | 2–3 days compute | no |
| Week 7 | Rewrite Results around items 1–2, artifact, anonymize | 4 days | |

If only two weeks are available, do the page cut and item 3, submit to the
Research Track, and put items 1, 2 and 4 in Future Work with their
pre-registered predictions — which is what `sections/10-future.tex` already
does and why it was written that way.

## Two loose ends from earlier work, both cheap

- **E8 audit for the full corpus.** Typed's *caught*-redundancy count is 52%
  censored; the `--audit-guarded` condition that recovers the missing types is
  implemented but only covers the 90-cell sweep. Extending it to the 495-cell
  grid is cached on the model side and costs only oracle calls. Removes a
  paragraph of apology from Threats.
- **E9's nine missing cells.** 60/57/54 of a targeted 60 per arm. Finishing
  them makes the free-guarded condition a balanced design and turns two
  suggestive *p*-values into one pooled test that does not need the
  shared-tasks caveat.

---

# Experiments still outstanding after three reviews

Ordered by value per unit of cost. The first two need **no model calls at
all** — the response cache already holds every completion, and the arms below
build prompts identical to ones already drawn — so they cost sandbox time only.

## A. Guard soundness below full depth — *cheap, closes a named gap*

Review 2's residual hazard: `src/oracle.py` redraws its check set per call, so
a stored counterexample can fall outside a later draw. At $k = 100$ the draw
exhausts the pool for 97 of 99 faults, which is why the existing audit
(0 acceptances in 4,411 blocked rounds) is uninformative — and below $k = 100$,
where the hazard is real, the audit was never run.

**Run:** `--audit-guarded` at $k \in \{20, 8, 3\}$ over the 90-cell depth-sweep
universe. **Cost:** model calls all cached; roughly 3 × 90 × 1.7 oracle calls of
sandbox work. **Outcome:** either a Clopper–Pearson bound on a real hazard, or
the first observed guard-soundness violation — both publishable.

## B. Class-exclusive refutation — *cheap, closes Review 2's C4*

Theorems 3 and 4 need a counterexample of class $\tau$ to refute **no**
candidate of another class; Assumption 2 now says so and admits our index does
not meet it. Nothing measures how badly.

**Run:** the offline cross-refutation study already built for informativeness,
but scored *across* classes rather than within. **Cost:** comparable to the
26,048 executions that study already spent. **Outcome:** the number that turns
"directional prediction" into a calibrated one.

## C. The two closest baselines — *moderate, the #1 ask of Reviews 1 and 3*

Both reviews want the guard measured against the mechanism it most resembles.
Three arms are implemented and pass the round-1 pairing test.

1. **Per-modification-point prioritizer** (Venugopal et al.): a per-edit-location
   ordered test suite, no counterexample store. Isolates *the key*.
2. **Fault-recorded prioritizer** (Qi et al.): order by which cases killed
   earlier candidates. Isolates *the evidence*.
3. **Dedup-only guard**: the store with no ordering at all. Isolates *the index*.

**Cost:** these arms do not steer, so their prompts match the no-memory arm and
every draw is a cache hit — sandbox only, ~500 cells each at roughly no
memory's 73 s of sandbox per cell. **Outcome:** the positioning table stops
being argued mechanically.

## D. A second proposer — *expensive, the largest external-validity gap*

Every number is `qwen2.5-coder:7b` at $T = 1.0$. The steering null is the
result most likely to be scale-dependent: a larger model might obey an
instruction a 7B ignores.

**Run:** the three main arms on one larger open-weight model. **Cost:** this is
the one that breaks the cache — roughly 4,700 completions per arm, ~14k total.
**Outcome:** either the steering null generalizes, or the paper's headline
recommendation is scoped to small proposers, which is worth knowing either way.

## E. A repository-scale benchmark — *out of scope; disclose instead*

ConDefects faults are single-file competitive-programming submissions with a
rich shipped pool, which is exactly what makes a clean counterexample oracle
possible. Transplanting the loop to SWE-bench-style issues is a different
paper's worth of harness work. §IX says so plainly rather than gesturing at it.
