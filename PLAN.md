# PLAN — from the synthetic study to a real-benchmark run

`paper/main_proposal.txt` is the authority. Its own evaluation is **synthetic**
(§5: *"no model is prompted and no test suite is executed"*), and §9 names the
next step:

> instantiate the type function and counterexample oracle on real repair
> benchmarks and measure how coherent real failure types actually are — the
> single quantity our theory identifies as decisive.

This repository is that instantiation, on the Python subset of ConDefects. Every
step below names the paper claim it serves. A step that serves no claim is not
run.

---

## 0. What the paper fixes

| | paper | here |
|---|---|---|
| conditions | no memory / untyped / typed (§5) | `--modes no_memory untyped typed` |
| ablations | guard-only, steering-only (§5, RQ3) | `--steer off` / `--guard off` |
| metrics | oracle calls, proposals, redundant attempts, guard evals, success@B, anchoring rate (§5) | `analyze.py` + `measure_anchoring.py` |
| strata | Easy [0.18,0.35] / Medium [0.08,0.18] / Hard [0.02,0.08] (§5) | same, plus `dead` / `too_easy` controls |
| budget | B = 10 (§5) | **B = 20** — deviation, see below |
| seeds | 30 proposer seeds (§5) | **5** — deviation, see below |
| master seed | 20260717 (§5, §10) | same |
| statistics | per-task mean over seeds → mean over tasks; 10⁴-resample bootstrap over tasks; Wilcoxon signed-rank paired on tasks; Vargha–Delaney A₁₂; Benjamini–Hochberg across the three primary strata (§5) | `analyze.py` |

### The two deviations, pre-registered here

**B = 20, not 10.** §5 sets B = 10 against a synthetic π. The primary metric —
oracle calls to accept — is conditioned on accepting, so a band that rarely
accepts supplies no datum to it. Pr[accept] = 1 − (1−π)^B:

| π | B = 10 | B = 20 |
|---|---|---|
| 0.02 | 0.183 | 0.332 |
| 0.05 | 0.401 | 0.642 |
| 0.08 | 0.566 | **0.811** |
| 0.20 | 0.893 | 0.988 |

At B = 10 the Hard band [0.02, 0.08) — where the paper predicts its **largest**
effect (A₁₂ = 1.00, oracle calls 23.07 → 6.50) — is barely estimable. B = 20
brings the whole [0.08, 0.35] interval the paper tabulates into range. The cost
is ~2× the calls on non-accepting episodes, paid mostly on the control strata.

**5 seeds, not 30.** 30 seeds is out of budget by ~6× at real API prices. The
statistical protocol is unaffected: the bootstrap resamples **tasks**, not
seeds, so the resampling unit is unchanged; seeds only sharpen each per-task
mean. Report the reduced seed count as a threat to validity.

---

## 1. Prerequisites — no API calls

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # API key, model, BUDGET_USD_CAP
python3 scripts/fetch_condefects.py --check-only
```

`Test.zip` (~6.4 GB) is downloaded by hand — `fetch_condefects.py` prints the
OneDrive and Baidu links and explains why it cannot be automated. Without
`external/ConDefects/Test/` there are no inputs, so there is no oracle and
nothing below runs.

**Commit the two working-tree fixes before spending anything.** Both corrupt
exactly the quantities the paper's claims are read off:

- `src/loop.py::cell_signature` included `budget`, so one cell got two episode
  ids at two budgets. `src.metrics.load_rounds` collapses on
  `(episode_id, round_index)`, so topping a cell up from B=12 to B=20 appended a
  second episode instead of rewriting the first — and every round-averaged
  estimator **counted the first 12 draws twice**, π̂ in `fit_theory.py` above all.
- `scripts/run_eval.py` built an 8-field resume key in the driver against a
  10-field key in the index, so no lookup ever matched and **every cell re-ran**
  at full price.

---

## 2. E0 — candidate pool and the oracle gate · no API calls

**Purpose.** Earn Assumption 1. The paper takes oracle soundness as given; on a
real benchmark it has to be demonstrated before a cent is spent through it.

**Anchor.** Assumption 1 (§4); oracle definition Eq. (1) (§3.1).

**[CORPUS.md](CORPUS.md) is the runbook** for this step and for step 4 below.

```bash
python3 scripts/validate_oracle.py \
    --programs $(python3 -c "import json;print(' '.join(c['name'] for c in json.load(open('data/candidates.json'))['candidates']))") \
    --corpus-size 360 --min-siblings 1 --data-dir data/pool --jobs 6
```

Writes `data/pool/tasks.json` (the frozen **candidate pool** — not the corpus)
and `data/pool/oracle_validation.json`.

`--corpus-size 360` is not optional: `select_corpus.py`'s quotas total 115, the
measured π̂ distribution is bimodal, and the gate drops candidates, so a smaller
pool cannot fill the middle bands. `--programs` hands the script the Stage-0
candidate list in its own seeded order — the order the screen was sharded on and
the order the band walk uses; left to itself it would build a different one over
faults the screen never measured.

Gate: a fault passes at ≥2/3 of its scoreable natural mutants caught; the pool
freezes at ≥75% of the cohort passing.

Run a publication freeze with `--jobs 1`: a timeout is a verdict, not a retry, so
a program near the sandbox wall-clock limit can fail under parallel load when it
would not have serially. Serial is ~6–12 h; `--jobs 6` is ~2 h.

This step needs no model calls and does not depend on the screen, so it can run
at any time on any spare machine — including while screening is still going.

## 3. E0b — screen π̂ · **BILLABLE**

**Purpose.** §5 stratifies by difficulty, and on this loop difficulty *is* π:
under the no-memory arm every round is an independent Bernoulli draw, so π alone
fixes both Pr[accept within B] and E[rounds | accept]. Nothing else predicts it —
AtCoder rating correlates at only ρ = −0.35, which cannot place a task in a band
0.10 wide.

**Anchor.** §5 Task suite and stratification; Theorem 4.3(a).

**[SCREENING.md](SCREENING.md) is the runbook.** It holds the protocol every
machine has to agree on, the per-machine rehearsal, and the merge audit. In
outline:

```bash
bash scripts/screen_shard.sh --from 1 --to 132   # one index range, one machine
python3 scripts/consolidate_screens.py           # -> data/screen_merged.json
```

The screen runs against a **local** `qwen2.5-coder:7b`, so it costs wall clock
rather than money: ~5,300 draws at `--calls 10` over the 527 candidates, ~29 h
on one machine and ~7 h each across four. It is cut into index ranges over
`data/candidates.json` — the seeded `K_proxy`-stratified order, so any
contiguous range is already balanced.

Two things carry over into every later step. π̂ lives on a grid of `1/k`, and
**at k = 10 the entire `hard` band [0.02, 0.08) is unreachable** — `0/10` is
`dead`, `1/10` is `medium`. `hard` is where the paper predicts A₁₂ = 1.00, so it
has to be deepened to k = 38 before the corpus is frozen; `consolidate_screens.py`
computes that and the per-task cost. Deepening is cheap because draws are nonced
`pi-pilot|{name}|seed{seed}|call{i}`, so a re-run at larger k replays `0..k-1`
from cache — but only if `--seed`, `--max-examples`, the model id and the
temperature are unchanged. Those are the cache key.

**π is a property of the model.** A corpus stratified on π̂ measured with
`qwen2.5-coder:7b` is not stratified for `gpt-4o-mini`. Whichever proposer the
reported runs use, the screen has to be the same one.

## 4. Freeze the corpus · no API calls

**Anchor.** §5 stratification; §5's demand argument — A₁₂ = 0.83/0.96/1.00 for
Easy/Medium/Hard, and Prop 4.5's guard gap that *"grows with task difficulty"*.

```bash
python3 scripts/select_corpus.py --pool data/pool/tasks.json \
        --screen data/screen_merged.json --min-calls 38
python3 scripts/build_strata.py
```

Writes `data/tasks.json` (the frozen corpus) and `data/screening.json` (every
candidate screened, its π̂, its band, and why it was or was not taken).

`--min-calls` must match the depth the screen actually reached, and that depth
decides which bands can be filled at all: π̂ lives on a grid of 1/K, so at K = 10
**no outcome lands in `hard`** and the primary band comes out empty. K = 38 is
the first depth with three interior points in it. [CORPUS.md](CORPUS.md) carries
the rest — quotas, the audit file, and the pre-freeze checklist.

| stratum | π̂ | quota | role |
|---|---|---|---|
| `dead` | [0.00, 0.02) | 20 | control — B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | 30 | primary — largest predicted effect |
| `medium` | [0.08, 0.18) | 20 | primary |
| `easy` | [0.18, 0.35] | 30 | primary |
| `too_easy` | (0.35, 1.00] | 15 | control — predicted null; nothing is ever stored |

Each band takes `min(quota, available)`, so ≤115 tasks. The two control bands are
kept, not discarded: an effect that appears only where the mechanism has room to
operate is stronger evidence than a uniform one. `too_easy` in particular is the
saturated class — the first proposal is accepted, no counterexample is produced,
and all three conditions coincide by construction. Because selection is banded on
π̂, that class is known *at selection time*; no post-hoc saturation screen is
needed.

**Keep the two π̂'s apart — this is the whole discipline.** The screen's draws
carry cache nonce `pi-pilot|…` and are spent here; the reported π̂ comes from E1,
whose draws carry `proposal|…`. Two independent samples, so conditioning on one
cannot inflate the other. Selection happens on the **no-memory arm before any
treatment**, which makes it dose-range choice, not outcome selection. Migration
between the two π̂'s is regression to the mean, and `build_strata.py` writes the
migration matrix out rather than hiding it, because its size *is* the risk.

## 5. Oracle blind spot · no API calls

**Purpose.** Bound how often a wrong patch can be *accepted*. The natural-mutant
gate cannot answer this: a natural mutant is a submission AtCoder rejected, so
the pool refutes it by construction.

**Anchor.** Theorem 4.1 — its guarantee is exactly *"acceptance implies the patch
passes every check the oracle can run"*, so the oracle's blind spot is the size
of the caveat on every result in the paper.

```bash
python3 scripts/measure_pool_strength.py --jobs 6
```

Measurement, not a gate: it reads the frozen corpus and drops nothing. Report the
`equivalent` rate as an **upper bound** on invisible patch overfitting — some
share of any planted edit is semantically inert rather than undetectable, and
separating the two needs coverage data this run does not collect.

---

## 6. E1 — the no-memory arm · **BILLABLE**

**Purpose.** Two jobs at once: the trivial baseline of §5, and the estimator for
π̂ and q̂ that every closed-form prediction is evaluated at.

**Anchor.** §5 Baselines; Theorem 4.3(a) (1/π); RQ3 (theory fit).

**[EXPERIMENT.md](EXPERIMENT.md) is the runbook** for this step and for steps 7–9
below. It holds the protocol E1–E5 must share with the screen, how the grid is
cut into shards, the trial that tests every flag before the grid does, the merge
audit, and the failure modes. `scripts/eval_shard.sh` runs one shard end to end —
it starts the local proposer, verifies the served context window, runs the grid,
and tears the server back down, the way `screen_shard.sh` does for the screen.

```bash
bash scripts/eval_shard.sh --exp E1 --from 1 --to 30    # one shard
bash scripts/eval_shard.sh --exp E1                     # or the whole corpus
```

`--force-full-budget` is what makes this an estimator. The no-memory prompt
carries no evidence and no exclusion block, so it is byte-identical across all 20
rounds of an episode (`src/proposer.py::_evidence_block` returns `""` for that
mode). Every round is therefore an independent draw of π, and 5 seeds × 20 rounds
gives **100 i.i.d. draws per task** — against the screen's 20–40. The summary step
later truncates the rounds back at the first accept, so the arm stays comparable
to the memory arms it is tabulated against.

Never run `no_memory` again without `--force-full-budget`: that is a different
cell, and `analyze.py` will refuse to pool the two.

Scale: 115 × 5 × 20 = 11,500 calls ≈ **$59**.

## 7. E2 — the memory arms · **BILLABLE**

**Purpose.** The paper's central comparison (Table 2).

**Anchor.** RQ1; Thm 4.3(a) rounds, 4.3(b) redundant attempts, Cor 4.4 budgeted
success, Prop 4.5 guard evaluations.

```bash
bash scripts/eval_shard.sh --exp E2
```

`--check-overfit` re-runs the full pool on every accept and writes the verdict to
`data/overfit_checks.jsonl` — the audit that separates *repaired* from *passed the
sampled oracle*.

Scale: ~10,200 calls ≈ **$61**.

## 8. E3 — the mechanism ablation · **BILLABLE**

**Purpose.** Attribute each gain to guarding or to steering. This is the step
that separates *remembering* from *typing* — the paper's whole thesis.

**Anchor.** RQ3; Table 4. Predicted: guard-only reproduces untyped's round
savings but leaves redundant attempts untouched; steering-only drives redundant
attempts to zero and lifts proposal-budget success to ~0.95.

```bash
bash scripts/eval_shard.sh --exp E3-guard-only     # --steer off
bash scripts/eval_shard.sh --exp E3-steer-only     # --guard off
```

Scale: ~6,100 calls ≈ **$37**.

## 9. E4 / E5 — the robustness sweeps · **BILLABLE**

**Purpose.** RQ2. The ρ and c sweeps are the paper's pre-registration of how the
guarantees should degrade, and §9 calls type coherence *the* decisive quantity.

**Anchor.** RQ2; Table 3; Findings 4 and 5; Def. 3.1.

Declare the subset once, stratified across the π bands so a sweep is not
confounded with difficulty, and pass the identical list to the driver and to the
freeze.

Write it to a **file**, and pass the file — `--programs-from`,
`--sweep-programs-from` — never a shell variable. Two independent reasons, and
both fail silently. The grid runs across several sessions, and
`freeze_results.py` must receive the identical list days later; its own default
is "the first 30 frozen programs sorted", which is *not* the stratified subset.
And zsh word-splits an unquoted `$(...)` but **not** an unquoted `$VAR`, so
`--programs $SWEEP` arrives as one 24-name string. `scripts/eval_shard.sh` cuts
the file (`data/sweep_programs.txt`) and hands it to both.

```bash
bash scripts/eval_shard.sh --exp E4-k20      # E4 — oracle informativeness (rho proxy)
bash scripts/eval_shard.sh --exp E4-k8
bash scripts/eval_shard.sh --exp E4-k3
bash scripts/eval_shard.sh --exp E5-c90      # E5 — typing coherence
bash scripts/eval_shard.sh --exp E5-c75
bash scripts/eval_shard.sh --exp E5-c50
```

Each preset walks `data/sweep_programs.txt`, which the same script cuts from the
frozen corpus — six per band, spaced evenly so any shard of it is balanced too.

E4's reference level is `--max-examples 100` and E5's is `c = 1.0`; both are the
typed cell E2 already ran, so neither sweep pays for its own baseline —
`run_eval.py` recognises the cell and skips it.

**E4 must be read off `is_truly_correct`, not off `accept`.** Lowering
`max_examples` weakens the oracle, so more wrong patches are accepted and the
apparent repair rate *rises*; read naively, the sweep concludes that a less
informative oracle repairs better. Plot the `data/overfit_checks.jsonl` series.

E4's levels stop at 20 because `src/oracle.py::_sample` returns the whole pool
whenever `max_examples ≥ len(cases)`, and ConDefects pools are small (median 30
cases, 97% at ≤80, max 148). `--max-examples 300` and `100` are the same
experiment at twice the price. The same fact is why the overfitting audit is
near-vacuous in E2, and why E4 is the only place ρ actually moves.

Scale: ~4,300 calls ≈ **$26**.

---

## 10. Analysis · no API calls

Order matters: the freeze needs the strata, `fit_theory` needs the frozen
results, and `build_strata`'s drift audit needs `fit_theory`.

```bash
python3 scripts/consolidate_evals.py                     # -> data/episodes.jsonl
python3 scripts/freeze_results.py --experiment main      # -> data/results_real.json
python3 scripts/analyze.py                               # -> data/analysis.json
python3 scripts/fit_theory.py                            # -> data/theory_fit.json
python3 scripts/build_strata.py --force                  # now with the drift audit
python3 scripts/measure_coherence.py                     # the c proxy off E1
python3 scripts/measure_anchoring.py                     # -> data/anchoring.json
python3 figures/make_figures.py
python3 scripts/check_consistency.py

python3 scripts/freeze_results.py --experiment ablation
python3 scripts/freeze_results.py --experiment oracle_sweep \
        --sweep-programs-from data/sweep_programs.txt
python3 scripts/freeze_results.py --experiment typing_sweep \
        --sweep-programs-from data/sweep_programs.txt
```

`check_consistency.py` rebuilds every frozen file from `data/episodes.jsonl` and
deep-diffs against what is on disk, so the manuscript cannot drift from the
artifact — the property §10 promises.

**Highest-value optional step.** `scripts/label_tool.py` gives two annotators an
independent tag on the same 150-attempt sample, so inter-annotator agreement and
agreement with the automatic θ() can be reported next to the automated proxy:

```bash
python3 scripts/label_tool.py --annotator alice
python3 scripts/label_tool.py --annotator bob
python3 scripts/label_tool.py --compare alice bob
```

§9 identifies real type coherence as the single decisive open quantity, and
`measure_coherence.py` cannot separate ρ from c on its own. This is the only
measurement in the repo that reaches it with human ground truth.

---

## Budget and wall clock

| step | calls | cost |
|---|---|---|
| 3 screen π̂ | ~11,200 | $7 |
| 6 E1 | ~11,500 | $7 |
| 7 E2 | ~10,200 | $7 |
| 8 E3 | ~6,100 | $5 |
| 9 E4+E5 | ~4,300 | $3 |
| **total** | **~43,300** | **~$30** |

Projections are upper bounds: E[rounds] = (1 − (1−π)^B)/π is evaluated at π
rather than at the per-round rate q the memory arms actually achieve, and q ≥ π
whenever steering helps at all. Only E1's figure is exact.

Rate card for `gpt-4o-mini` ($0.15/$0.60 per Mtok): **$0.00059** per no-memory
call, **$0.00073** per memory-arm call. The token profile behind it is measured —
4,778 logged calls, median 620 in / 831 out, ~900 more input tokens on a memory
arm — but those calls were `claude-haiku-4-5`, so the dollar figures are that
profile repriced, not yet re-measured. Re-derive them from `data/calls.jsonl`
after the screen. Output is ~85% of cost, so the memory arms — which add evidence
to the *input* — run ~25% dearer, not 2×.

`llm.spent()` sums the whole of `data/calls.jsonl`, so **prior spend counts
against `BUDGET_USD_CAP`**. Set the cap to the projection plus whatever that file
already carries.

> **Three hazards before the first billable command.**
>
> 1. `data/calls.jsonl` was deleted in the reset, so `llm.spent()` now returns
>    $0.00 and the ledger no longer knows about spend that already happened. The
>    cache still holds those completions, but cache entries record only
>    `{text, model, temperature, nonce}` — no token counts — so the ledger cannot
>    be rebuilt from them. Decide deliberately whether to carry the old spend
>    forward.
> 2. **The plan costs ~$30 of fresh spend at `gpt-4o-mini` rates** — down from
>    ~$240 at `claude-haiku-4-5`. If the cap binds mid-grid, `run_eval.py` raises
>    `BudgetExceeded` and stops cleanly — but that leaves a *partial* arm, and a
>    partially-run condition is not comparable to a complete one. Either raise the
>    cap to cover the whole plan, or trim scope up front (screen a slice of the
>    pool, or drop a sweep level) so every arm that starts also finishes.
> 3. **Nothing replays.** The ~6,900 cached completions are `claude-haiku-4-5`
>    responses and the model id is part of `src.llm`'s cache key, so every call
>    in the table above is a fresh one. That is the intended behaviour, not a
>    fault — but it also means the frozen corpus is stale in a way money cannot
>    fix: `data/tasks.json` stratifies tasks by π̂ measured on Haiku, and π is a
>    property of the model. **Step 3 has to run again and the corpus has to be
>    re-frozen before E1**, or the "Hard" band the primary comparison rests on is
>    not the Hard band.

Wall clock binds harder than money: ~10.7 s/call, most of it sandbox execution
rather than API latency, and neither `run_eval.py` nor `measure_pi.py`
parallelises. Budget ~100 hours for the full grid, run it under `tmux`, and watch
it with `scripts/watch_eval.sh`.

Debug the pipeline against the local backend first — it is free, so a broken
flag costs nothing to discover. `scripts/screen_shard.sh` is the worked example:
it pins `qwen2.5-coder:7b`, verifies the served context window, and runs one
shard of the π screen. Note that a local run is not a cheap substitute for the
grid above — `qwen2.5-coder:7b` at ~16 tok/s would need well over a month of
wall clock for it — and that **π is a property of the model**, so a corpus
stratified on a local π̂ is not stratified for `gpt-4o-mini`.

**Run the billable steps one at a time.** They all append to
`data/episodes.jsonl` and all read `llm.spent()` from `data/calls.jsonl`, so
concurrent runs race. `run_eval.py` is resumable and every model call is cached
on disk, so an interrupted run costs nothing to restart.

---

## Fidelity caveats — where the implementation is not the model

These are not bugs; they are the places where a real LLM forced a departure from
the formal model, and each one weakens a specific claim. They belong in threats
to validity, not in a footnote.

**The untyped arm shows the proposer nothing — corrected 2026-08-20.** Section
5 defines the untyped baseline as *"a flat counterexample log that guards by
re-running all stored counterexamples but **cannot steer the proposer**"*, and
Algorithm 1 draws `p_t ~ G(·|E)` with `E` empty for an agent that has no types —
the same unconditional distribution no memory draws from. Section 6 reports the
consequence directly: *"untyped collapses to 0.68, indistinguishable from no
memory"*, and Table 4 puts Guard-only at the same 0.68. Both only hold if
neither arm's proposals are conditioned on memory.

`src/proposer.py::_evidence_block` used to read "cannot steer" as "gets no
exclusion instruction" and put the full transcript — every refuted patch's
source plus its counterexample — into the untyped arm's prompt; the same reading
kept that evidence under `--steer off`. It now shows nothing in either case, so
`no_memory`, `untyped` and `guard-only` build byte-identical prompts and differ
only in which guard runs.

Two consequences worth planning around. Those three arms share `src.llm`'s cache
entries under the same draw nonce, so **the untyped and guard-only arms cost no
model calls at all** once E1 has run — only oracle time. And their budgeted
success must come out *exactly* equal to no-memory's, because a guard can only
block a candidate that provably fails a stored counterexample and a correct
patch fails none: any gap is a guard-soundness bug
(`src/memory.py::_still_refutes` also blocks on a sandbox timeout), not a
result.

What the old arm measured is a real question — transcript memory as actually
used by conversational repair agents — and on the first 30 tasks it answered
loudly: the proposer re-emitted near-copies of the patch it had just been shown,
the guard blocked 16–19 of its 20 rounds, and budgeted success fell *below* no
memory (0.50 vs 0.63). That belongs in the paper as its own condition, not as
this baseline. Adding it means a fourth mode, not a relabelling.

**Steering is a prompt instruction, not Eq. (3).** The paper renormalises the
proposer's support onto the not-yet-eliminated types, so Theorem 4.2(i)
non-repetition holds *by construction* — an eliminated class cannot be drawn.
`src/proposer.py::_exclusion_block` instead writes English:
*"Do not propose a patch that falls into any of these already-eliminated failure
classes."* Whether the model obeys is an empirical question, so on this
implementation **non-repetition is a measured outcome, not a theorem**. Report
the observed redundant-attempt count as a test of the instruction's strength;
do not present exactly-zero as guaranteed.

**Typing noise is injected on half the type.** `src/memory.py::store` mistypes
only the `location` half of the (location, property) pair; the property half is
always correct. Worse, the mistype needs another location to swap to
(`other = [loc for loc in self._seen_locations if loc != location]`), so **an
episode's first store can never be mistyped**. The realised noise rate is
therefore strictly below the nominal 1−c, and E5's c axis should be labelled as
a lower bound on the damage, not as Def. 3.1's c.

**ρ is a sample-size proxy.** On a real oracle you cannot know a failure class in
advance, so E4 varies `--max-examples` (how much of the test pool is sampled)
rather than the paper's ρ (whether a counterexample generalises to its class).
These are related but not the same quantity, and E4 has three levels against the
paper's four.

**Accept is not repair.** The loop accepts when no *sampled* case refutes;
`is_truly_correct` over the whole pool runs only under `--check-overfit`. The gap
is widest at `--max-examples 3`, so read that level off the overfit log.

## Known gaps against the paper's §10

Recorded here rather than silently carried:

- **`scripts/gen_synthetic.py` does not exist.** It produced every number in the
  paper. This repo replaced the synthetic corpus with the real-benchmark chain
  above, so the paper's own four-command reproduce sequence cannot be run here.
- **`data/results.json` is never produced** — the frozen artifact is
  `data/results_real.json`. §10's re-derivability guarantee names the former.
- **`data/schema.md` does not exist**, though §10 promises it documents the task
  and result formats.
- `requirements.txt` pins `openai` and `python-dotenv`; §10 states the
  artifact depends *solely* on numpy, scipy and matplotlib. That claim describes
  the synthetic artifact and does not survive the move to a real LLM proposer.
