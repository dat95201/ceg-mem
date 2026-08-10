# Project status — CEGMem

**Last updated:** 2026-08-10 · **Branch:** `feat/condefects` · **Model under test:** `claude-haiku-4-5`

This file tracks where the replication package stands against the paper
(`paper/draft.pdf`), which is the authority on what the experiments must
measure. Every number below is quoted from a generated artifact in `data/` or
from a section of the paper, and the source is named next to the claim so it can
be re-checked.

---

## 1. Summary

The apparatus is complete and validated. What is *not* settled is the corpus,
and the paper says so in its own abstract: *"apparatus complete, corpus
calibration unresolved"*.

Two blockers from the previous revision are now closed, and one that was not
previously recognised is now the main open item.

**Closed.**

- **Benchmark test data.** `Test.zip` is fully unpacked. `external/ConDefects/Test`
  holds 199 contest directories, the corpus spans 2021-10-02 → 2024-06-30
  including all `agc` and `arc`, and all 120 tasks of the current freeze resolve
  their test cases. §VI-B of the paper is out of date.
- **Response truncation (§VI-D-a).** `_extract_code` no longer returns raw prose
  when a reply carries no closed fence; it raises `TruncatedResponse`, and
  `src/loop.py` logs the round as spent-but-inconclusive and leaves memory
  untouched — the same treatment an unusable oracle already got. This had to be
  fixed before E1, because a harness defect that acquires a failure type
  pollutes exactly the mechanism under study.

**Open — the corpus is calibrated against the wrong end of the π range.**

§VI-A is the paper's central problem: at budget B, π alone fixes both
Pr[accept within B] and E[rounds | accept], and two regimes contribute nothing to
the primary metric. The previous revision reacted by replacing the easy/medium/
hard quotas with an AtCoder rating floor of 1600, aiming at the proposal's Hard
band. §V-G of the paper measures what that floor buys: **median π̂ = 0.038.**

That is the wrong side. Table II of the paper puts π = 0.05 in the *too hard*
regime; the primary metric (§VII-a) is averaged over accepted episodes only, and
at B = 12, Pr[accept] = 0.46 there. The proxy-easy and proxy-medium terciles the
floor discarded came in at median π̂ = **0.300** and **0.275** — inside the
informative window. **The floor kept the band that answers nothing and threw
away the band that answers the question.**

The fix is the paper's own §VI-A-b course 2 combined with course 3: re-select on
measured π̂ into absolute bands, and keep the out-of-band tasks as pre-declared
negative controls. `scripts/select_corpus.py` implements it under the three
conditions §VI-A-c attaches.

Spent so far: **$24.52** (`data/calls.jsonl`, 4,778 calls).

---

## 2. Pipeline status

Stages follow the reproduce order in `README.md`. Everything below the screen is
pending a re-freeze, so no experiment cell has been run against the corpus the
paper will report.

| # | Stage | Script | Status | Artifact |
|---|---|---|---|---|
| 0 | Fetch benchmark | `fetch_condefects.py` | ✅ complete | `external/ConDefects/Test` |
| 1 | Candidate pool: usability + natural-mutant gate | `validate_oracle.py --select none` | ⬜ to re-run | `data/pool/tasks.json` |
| 2a | π screen, stage A (k=8) | `measure_pi.py --out data/screen_a.json` | ⬜ not run | `data/screen_a.json` |
| 2b | π screen, stage B (k=20 on survivors) | `measure_pi.py --out data/screen_b.json` | ⬜ not run | `data/screen_b.json` |
| 3 | Select corpus by measured π̂ | `select_corpus.py` | ⬜ not run | `data/tasks.json`, `data/screening.json` |
| 4 | Stratify into absolute π bands | `build_strata.py` | ⬜ not run | `data/strata.json` |
| 5 | Pool strength (measured, not gated) | `measure_pool_strength.py` | ⚠️ stale corpus | `data/pool_strength.json` |
| 6 | E1 — no-memory arm | `run_eval.py --budget 20 --modes no_memory --force-full-budget` | ⬜ not run | `data/episodes.jsonl` |
| 7 | E2 — memory arms | `run_eval.py --budget 20 --modes untyped typed --check-overfit` | ⬜ not run | `data/episodes.jsonl` |
| 8 | E3–E5 — ablations & sweeps | `run_eval.py` (guard/steer, `--max-examples`, `--typing-noise-c`) | ⬜ not run | — |
| 9 | Freeze results | `freeze_results.py` ×4 | ⬜ not run | `data/results_*.json` |
| 10 | Analysis, theory fit, figures | `analyze.py`, `fit_theory.py`, `make_figures.py` | ⬜ not run | `data/analysis.json`, `figures/` |
| 11 | Consistency check | `check_consistency.py` | ⬜ not run | — |

---

## 3. What changed in this revision, and why

### 3.1 Selection now measures π instead of proxying it

Stage 1 (`validate_oracle.py`) freezes a **candidate pool**, not the corpus, and
it samples across every rating (`--select none`) rather than above a floor. That
matters for feasibility as much as for calibration: the floor-1600 pool holds 188
coding tasks with a usable sibling, the unfiltered one **646**.

Stage 2 measures π̂ on that pool and fills absolute quotas. The bands are the
proposal's own and are shared by `select_corpus.py` and `build_strata.py`:

| stratum | π̂ | quota | role |
|---|---|---|---|
| `dead` | [0.00, 0.02) | 20 | below the analysis range; B binds hardest, memory longest |
| `hard` | [0.02, 0.08) | 30 | primary — largest predicted effect |
| `medium` | [0.08, 0.18) | 20 | primary |
| `easy` | [0.18, 0.35] | 30 | primary |
| `too_easy` | (0.35, 1.00] | 15 | control — predicted null |

Supply drives the unequal quotas one way: §V-F's π̂ distribution is bimodal — 25
of 60 above 0.35, 16 below 0.02, leaving 4 Easy / 4 Medium / 11 Hard — so
`medium` is the trough between the modes and a quota of 30 there would need ~450
screened candidates on its own. Screen in rating-stratified batches (§V-G puts
proxy-easy at median π̂ = 0.300 and proxy-hard at 0.038) and let
`select_corpus.py`'s shortfall report drive a top-up; the walk is deterministic
over the pool order, so a top-up leaves already-selected tasks in place.

The quotas *ask* for 80 primary tasks (30 + 20 + 30) against the **12 of 60**
§VI-A found in the pilot corpus — §VI-A's verdict on that corpus was
*"underpowered for its own primary metric, and this is knowable before spending
anything."* Whether 80 is what the screen delivers is not knowable until it has
run: `select_corpus.py` prints the realised counts and flags any band that came
up short, and a short band is a signal to screen more candidates in the matching
rating range, not to lower the target. No number downstream of N — corpus size,
primary-comparison size, grid cost — is settled before that point, and none is
quoted here as if it were.

### The low-π strata are not controls — §VI-A's framing is too narrow

`paper/main_proposal.txt` (now on disk) makes this unambiguous, and it inverts
the reasoning that produced the retired rating floor *and* the reasoning I would
otherwise have used to justify keeping `hard`/`dead` as mere negative controls.

The proposal's simulation puts the **largest** predicted effect at the low-π end:
Vargha–Delaney A₁₂ = 0.83 / 0.96 / **1.00** for Easy / Medium / Hard; oracle
calls 23.07 → 6.50 on Hard against 2.6× overall; 16.62 redundant attempts on
Hard against 7.25 overall. Proposition 4.5 states its guard-cost gap *"grows with
task difficulty — smaller π accumulates more refuted types before a repair"*, and
Corollary 4.4's budgeted-success advantage holds *"whenever B binds"*.

§VI-A's claim that a task at π < 0.1 "contributes no datum" is true of exactly
one metric — oracle calls to repair, conditioned on accepting — and false of the
other four. **Three of the proposal's four theoretical results are most visible
precisely where that metric goes undefined.** Only `too_easy` is a genuine
control: there the first proposal is accepted, nothing is stored, and the three
conditions coincide by construction.

### The metric set was incomplete

The proposal's §5 lists six per-episode metrics. `scripts/analyze.py` computed
**two**. The other four were recoverable from episode summaries already being
written, at no additional API cost, and three are now added:

| metric | result | defined on | status |
|---|---|---|---|
| `oracle_calls_to_accept` | Thm 4.3(a) | accepted episodes | was present |
| `redundant_attempts` | Thm 4.3(b) | every episode | was present |
| `success_at_b` | **Cor. 4.4** | every episode | **added** |
| `guard_evaluations` | **Prop. 4.5** | every episode | **added** (was recorded, never compared) |
| `proposals` | model-call budget | every episode | **added** |
| anchoring rate | **RQ2** | typed episodes | **still missing** — see §4 |

`success_at_b` is the important one. It is the co-primary that keeps the low-π
strata measurable when the round count cannot be defined because nothing
accepted, and its absence is what made §VI-A's "12 of 60" look like a corpus
problem rather than a metric problem. `analyze.py` also now stratifies over all
five bands and applies Benjamini–Hochberg across the three that carry a
prediction, rather than across whatever three strata happened to exist.

### 3.2 `build_strata.py` uses absolute bands, not terciles

It previously split the corpus into terciles of the observed π̂ and recorded the
paper's ranges as decoration. On the bimodal distribution §V-F measured, terciles
put π̂ = 0.00 and π̂ = 0.05 in different strata and π̂ = 0.30 and π̂ = 1.00 in the
same one, and they rename the bands whenever the corpus changes — which makes
"the effect is predicted in the middle band" untestable.

### 3.3 Two π̂'s, kept apart (§VI-A-c)

- **selecting π̂** (`screen_pi_hat`, stage 2) fixes the stratum. Measured on the
  no-memory arm before any treatment, so it is dose-range choice, not outcome
  selection — the distinction §VI-A-c draws explicitly.
- **reported π̂** (E1, via `data/theory_fit.json`) is what results tables print.
  A different sample under a different cache nonce, so conditioning on the first
  cannot inflate the second.

`build_strata.py` writes the migration matrix between the two. Its size is the
regression-to-the-mean term §VI-A-c warns about, so it is reported rather than
hidden. `data/screening.json` carries every candidate screened and why it was or
was not taken — the full excluded distribution the same section requires.

### 3.4 B = 20, not 12

The one deliberate deviation from §IX-B, forced by §VI-A-a's arithmetic. At
B = 12 the informative window starts near π = 0.13 — above the floor of the
proposal's own Medium band, and clear of Hard entirely:

| π | Pr[accept] at B=12 | at B=20 |
|---|---|---|
| 0.02 | 0.215 | 0.332 |
| 0.05 | 0.460 | 0.642 |
| 0.08 | 0.632 | **0.811** |
| 0.10 | 0.718 | 0.878 |
| 0.20 | 0.931 | 0.988 |

B = 20 brings the window down to π = 0.08 and makes [0.08, 0.35] — the interval
§VI-A itself reports counts for — usable. Cost is 67% more calls per
non-accepting episode, paid mostly on the two control strata.

### 3.5 Oracle validation: natural mutants, and pool strength as a measurement

The paper's §V-C describes **planted** mutants as the gate (201 planted, 180
caught, 21 equivalent, 0 missed). The implementation has since replaced the gate
with **natural** mutants — other people's wrong submissions to the same coding
task — and demoted planted mutants to a post-freeze *measurement*
(`measure_pool_strength.py`). Both changes are improvements and both need §V-C
rewritten before submission:

- A natural mutant has a real mistake's detectability: median 4 sampled cases to
  refute against 1 for a planted one, and **none of 240 turned out equivalent**
  where 21 of 201 planted ones did.
- Planted mutants remain the only way to ask whether the pool can separate a
  *small perturbation* of a correct program from the correct program — the
  population the repair loop actually judges. Measuring that after the freeze,
  and reporting rather than removing weak tasks, is what keeps it from being
  selection on a post-freeze property.

Its result stands and is not affected by the re-selection: over 348 planted
mutants, **0 missed** and **104 (29.9%) equivalent**. Read 29.9% as an upper
bound on pool weakness. It must be re-run once the corpus is re-frozen.

---

## 4. Known bugs and limitations

| # | Issue | Status |
|---|---|---|
| B1 | `max_tokens=2048` truncated long replies; `_extract_code` then returned raw prose as a patch, which acquired a spurious failure type and entered typed memory (paper §VI-D-a). | **fixed** — `budget_for_source` sizes the budget per program; `_extract_code` raises `TruncatedResponse`; `src/loop.py` logs the round via the new `proposal_error` field and leaves memory untouched |
| B2 | `data/pi_pilot.json` recorded `"model": null` whenever the id came from `.env`, so the artifact did not state which model produced it (§VI-D-b). | **fixed** — `measure_pi.py` and `run_eval.py` both resolve the model before the first call and record it |
| B3 | `run_eval.py::_cell_key` omitted `model` and `granularity`, so a second model's sweep would skip every cell as "already complete" against the first model's rows — silently, since the driver prints a skip line either way. | **fixed** — both are in the key |
| B4 | 3 of 510 salvaged test directories are internally inconsistent (`in/` without matching `out/`). A property of the archive, not of the salvage. | mitigated — rejected by the stage-1 guard |
| B5 | `data/tasks.json`, `data/pool_strength.json`, `data/oracle_validation.json` and `data/episodes.jsonl` all describe the retired rating-floor corpus. | **stale** — re-freeze before use |
| B6 | **Anchoring rate is not measured anywhere.** RQ2 of the proposal asks "what new failure mode (anchoring) does typed steering introduce?", and E5 is the experiment that answers RQ2. Without it E5 answers half its question. | **open** — needed before E5 is written up |
| — | `TEST_DIR` fell back to `.` when `CONDEFECTS_TEST_DIR` was unset (`pathlib.Path("")` is `PosixPath(".")`, which is truthy). | fixed |
| — | `_check_reference` returned "reference OK" when *every* case lacked an expected output. | fixed |
| — | `measure_pi.py` counted `oracle_error` as a failed draw, silently producing π̂ = 0.000 across the board. | fixed — now aborts |
| — | `run_eval.py` had no test-data guard; same failure mode at ~20× the cost. | fixed — now aborts |

### B6 in detail — where anchoring can and cannot come from

It cannot come from the guard. `TypedMemory.guard` looks up the type-matched
bucket and then re-runs its stored counterexample through
`memory.py::_still_refutes`, blocking only when that counterexample *still*
refutes the new candidate. A blocked candidate therefore provably fails, at any
value of `c`. Mis-typing changes *which* bucket is consulted — and so changes the
guard's cost, which is Proposition 4.5's subject — but it cannot make the guard
reject a correct patch.

Anchoring in this harness is a **generation-side** effect of the exclusion block
(`proposer.py::_exclusion_block`): when mis-typing files a refutation under the
location the correct patch would edit, the proposer is instructed to avoid the
class that contains the answer. Measuring it means typing the reference patch
(`src/typer.py` against `correctVersion.py`) and asking whether that location
entered `eliminated_locations()` while the episode was still running and the
episode then failed.

That is a **post-hoc, CPU-only audit** over `data/episodes.jsonl` — no model
calls — so it can be written after E5 has run and does not block any command.
It does need writing before E5 is reported.

### Not a bug, but a constraint on E4

`src/oracle.py::_sample` returns the whole pool when `max_examples ≥ len(cases)`,
and across the benchmark the pools are small: median 30 cases, 97.0% at ≤ 80,
98.9% at ≤ 100, maximum 148 — so `--max-examples 300` never differs from the full
pool anywhere in ConDefects. `--max-examples 300` and `100` are
the same experiment on 96% of the corpus. E4's levels are 20 / 8 / 3 with 100 as
the reference cell E2 already paid for. The same fact is why §VI-C calls the
overfitting audit near-vacuous: the sampled oracle already *is* the full oracle
almost everywhere.

And E4 must be read off `is_truly_correct`, not `accept` — a weaker oracle
accepts more wrong patches, so the naive series says a less informative oracle
repairs better.

---

## 5. Budget and wall clock

Measured on 4,778 logged calls: **$0.00513/call** mean, $0.00472 median, 620 in /
831 out median tokens. Output is ~87% of cost, so the memory arms run ~15–20%
dearer than no-memory rather than double.

**The grid's total is not knowable yet, and should not be quoted as if it were.**
The quotas in `select_corpus.py` are a ceiling — each band takes
`min(quota, available)` — and §V-F's distribution makes `medium` the band most
likely to come up short. N, the primary-comparison size, and the cost all become
known at the same moment: when `select_corpus.py` prints its counts. It prints
the cost projection there, from those counts.

Only two lines can be budgeted in advance:

| | calls | est. |
|---|---|---|
| screening — 360-fault pool, 8 draws + 12 on stage-A survivors | ~5,800 | **$30** |
| already spent (`data/calls.jsonl`, 4,778 calls) | — | **$24.52** |

For the rest, what is fixed is the rate card ($0.0051/call no-memory,
$0.0060/call memory arms) and the arithmetic: E1 costs `N × seeds × B`, the
early-stopping arms cost `N × seeds × arms × E[rounds]` with
`E[rounds] = (1 − (1−π)^B)/π` evaluated per band. That last substitution uses π
where the memory arms actually achieve q ≥ π, so every memory-arm figure is an
upper bound and only E1's is exact.

As an order of magnitude: a corpus that fills every quota (115 tasks) projects to
roughly $217; the synthetic under-fill in `select_corpus.py`'s own test (94
tasks, `hard` 20 short) projects to $137. **Both are conditional on the screen.**

`BUDGET_USD_CAP` is currently **150.0** and `llm.spent()` sums the whole of
`data/calls.jsonl`, so the $24.52 already spent counts against it. Raise it after
the screen, from the projection — not before, and not from a number in this file.

Wall clock binds harder than money: the pilot ran at ~10.7 s/call, most of it
sandbox execution rather than API latency, and neither `run_eval.py` nor
`measure_pi.py` parallelises. Budget ~100 hours for the full grid.

---

## 6. Next steps

**No budget:**

1. Re-freeze the candidate pool: `validate_oracle.py --select none --corpus-size 280 --data-dir data/pool`.
2. Tag the tree. `src/llm.py`'s cache key covers the prompt, so any edit to
   `src/proposer.py` after E1 starts invalidates every call already paid for.

**Budget, in order:**

3. Screen (stage A, then stage B on survivors) → `select_corpus.py` → `build_strata.py`.
4. Re-run `measure_pool_strength.py` on the new freeze (CPU only).
5. E1, then E2. Raise `BUDGET_USD_CAP` first.
6. E3, E5, E4 — in that order if the budget runs short. E5 sweeps the theory's
   own coherence parameter c; E4 is the one whose reading is confounded by the
   oracle change and the cheapest to drop.
7. `freeze_results.py` ×4 → `analyze.py` → `fit_theory.py` → `make_figures.py` →
   `check_consistency.py`.

8. Write the anchoring audit (B6) before E5 is reported. CPU only.

**Paper edits this revision forces:**

- §V-B/§V-C: selection is now two-stage and the mutation gate is natural, not
  planted; §V-C's table describes the retired gate.
- §V-D/§V-F: corpus shape and the π̂ table are for the 60-task tercile corpus.
- §VI-A: resolve it — the answer taken here is course 2 + course 3, and the
  `[TODO: decide]` should record that. The section also needs the qualification
  that "contributes no datum" applies to `oracle_calls_to_accept` alone.
- §VI-B: closed, the archive is complete.
- **§VII: the metric list is short by four.** It names oracle calls and redundant
  attempts; the proposal's §5 names six, and success@B (Cor. 4.4) and guard
  evaluations (Prop. 4.5) are the two whose absence distorted the corpus
  argument. The `[TODO: table by condition and stratum]` markers should be filled
  from the expanded `analyze.py`.
- §IX-B: B is 20, not 12 (the proposal says 10; §VI-A-a's own arithmetic rules
  out both), and E4's levels are 20/8/3.
- §IX-B/RQ2: the proposal sweeps c and ρ over 1.0 → 0.5 in six steps. Three
  levels each is what the budget buys; say so rather than implying the full grid.
