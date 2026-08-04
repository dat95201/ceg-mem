# Project status — CEGMem

**Last updated:** 2026-08-04 · **Branch:** `feat/condefects` · **Model under test:** `claude-haiku-4-5`

This file tracks where the replication package stands against the proposal. It is
written to be read both by a human reviewer and by an LLM agent picking the work
up cold. Every number below is quoted from a generated artifact in `data/`, not
from memory; the artifact is named next to each claim so it can be re-checked.

---

## 1. Summary

The benchmark adapter, the counterexample oracle, the mutation-based oracle
validation, and the corpus freeze are **done and passing**. A 60-task corpus is
frozen, and π̂ has been measured on all 60.

Two things block moving to the main experiments:

1. **The benchmark's test data is only 39% downloaded.** The corpus is frozen
   against a salvaged partial test tree, so it is usable for development but is
   **not the corpus the paper can report**.
2. **The measured π̂ distribution is bimodal and mostly outside the range the
   proposal designs for.** Only 8 of 60 tasks fall inside π ∈ [0.08, 0.35];
   13 sit at π̂ ≥ 0.90 and 21 at π̂ ≤ 0.05. This is a design problem, not a bug,
   and it should be resolved before spending the remaining API budget.

Spent so far: **$9.09 of a $20.00 cap** (`data/calls.jsonl`).

---

## 2. Pipeline status

Stages follow the reproduce order in `README.md`.

| # | Stage | Script | Status | Artifact |
|---|---|---|---|---|
| 0 | Fetch benchmark | `scripts/fetch_condefects.py` | ⚠️ partial | `external/ConDefects/` |
| 0b | Salvage truncated `Test.zip` | `scripts/salvage_test_zip.py` | ✅ done | `external/ConDefects/Test_partial/` |
| 1 | Oracle validation + corpus freeze | `scripts/validate_oracle.py` | ✅ passing | `data/tasks.json`, `data/oracle_validation.json` |
| 2 | π̂ pilot | `scripts/measure_pi.py` | ✅ done | `data/pi_pilot.json` |
| 3 | Stratify by measured π̂ | `scripts/build_strata.py` | ⬜ not run | `data/strata.json` |
| 4 | E1 — no-memory arm | `run_eval.py --modes no_memory --force-full-budget` | ⬜ not run | `data/episodes.jsonl` |
| 5 | E2 — memory arms | `run_eval.py --modes untyped typed --check-overfit` | ⬜ not run | `data/episodes.jsonl` |
| 6 | E3–E5 — ablations & sweeps | `run_eval.py` (guard/steer, `--max-examples`, `--typing-noise-c`) | ⬜ not run | — |
| 7 | Freeze results | `scripts/freeze_results.py` | ⬜ not run | `data/results_real.json` |
| 8 | Analysis, theory fit, figures | `analyze.py`, `fit_theory.py`, `make_figures.py` | ⬜ not run | `data/analysis.json`, `figures/` |
| 9 | Consistency check | `scripts/check_consistency.py` | ⬜ not run | — |

---

## 3. Benchmark state — read this before trusting any frozen corpus

ConDefects (Python subset) ships **2,864 faulty programs across 985 coding
tasks**, contests dated 2021-10-02 → 2024-06-30. The code was cloned fine. The
contest test data is a separate ~16 GB `Test.zip` hosted on OneDrive/Baidu.

**The downloaded `Test.zip` is truncated.** It has no end-of-central-directory
record, so `zipfile` rejects it outright. Walking its local file headers
recovers 34,442 complete entries and then stops mid-entry:

| | coding tasks | date range |
|---|---|---|
| in `Code/` (Python) | 985 | 2021-10-02 → 2024-06-30 |
| recoverable from the archive | **374** | 2021-10-02 → **2023-01-28** |
| missing | 611, including **all `agc` and all `arc`** | 2023-02 → 2024-06 |

Zip entries are stored alphabetically, so what is missing is a *suffix*, not a
random sample: every contest after `abc287`, plus `agc*` and `arc*` which sort
after `abc*`. By task-directory count the archive is **~39% complete**, not the
~99.96% that the byte offset of the truncation suggests.

`scripts/salvage_test_zip.py` extracts the recoverable prefix into
`external/ConDefects/Test_partial/` (19 GB, 931 programs / 374 coding tasks).
It is deliberately **not** placed in `Test/`, because `fetch_condefects.py`
skips unpacking when `Test/` exists and a salvage parked there would silently
survive the real download.

**Consequence for the paper.** The proposal prefers ConDefects specifically for
contamination control via a late-dated slice. Freezing on data that stops at
2023-01-28 removes exactly that slice. The current corpus is therefore
**provisional**: good enough to develop and validate the pipeline, not good
enough to report. `data/tasks.json` records `"test_dir":
"external/ConDefects/Test_partial"` so a provisional freeze can never be
mistaken for a final one.

**Action required (human, out of band):** re-download `Test.zip` with a client
that resumes and verify before use:

```bash
python3 -c "import zipfile; z=zipfile.ZipFile('Test.zip'); print(len(z.namelist()),'entries')"
```

`BadZipFile` means it is truncated again. `file(1)` is not a sufficient check —
it reported the truncated archive as a valid zip.

---

## 4. What was built

The proposal's week-2 deliverables, and what each turned into on ConDefects.

### 4.1 Input-generation strategies → not applicable

The plan called for per-task input strategies. ConDefects **ships its inputs**,
each paired with the output AtCoder accepted, so there is nothing to generate.
The equivalent artifact is the shipped-pool loader (`src/adapter.py`) plus the
per-round sampling policy (`src/oracle.py::_sample`). This deliverable is
complete by deletion and should be written up as such rather than counted as
built.

### 4.2 Oracle — `src/oracle.py`

Already ConDefects-shaped before this work. A counterexample is a test-case
**name**, not an input, so it can be logged and replayed without putting
hundreds of kilobytes of contest input into a prompt or a metrics row.

### 4.3 Mutants — `data/mutants.py` (rewritten)

The previous version was a hand-typed table of 120 substring anchors keyed to
QuixBugs algorithm names (`bitcount`, `gcd`, …). That does not transfer: a
ConDefects program is an anonymous AtCoder submission drawn from a pool of
2,864, and the corpus is not known until *after* the mutation stage has run.

So the hand-written thing is now the **operator**, not the anchor — three
families, one per fault type of proposal §3.5:

| Fault type | Operator family |
|---|---|
| `off_by_one` (τ₁) | `range()` bounds, slice bounds, accumulator steps, index constants |
| `wrong_comparison` (τ₂) | strict↔non-strict swaps, equality inversion, membership/identity inversion; handles chained comparisons |
| `missing_guard` (τ₃) | neutralise an `if`/`while`/ternary test, preferring early-exit guards |

Each operator locates its site with `ast` but applies the edit by splicing the
source **text** over that node's byte range. Nothing is unparsed, so every byte
outside the edit is identical to what AtCoder accepted and no mutant can be
"caught" because of an `ast.unparse` round-trip. `MUTANT_OVERRIDES` is the
escape hatch for hand-writing a specific mutant; it is currently empty.

### 4.4 Oracle validation + corpus selection — `scripts/validate_oracle.py`

The ConDefects migration had **deleted** the mutation gate entirely, leaving no
implementation of the proposal's ≥2/3 and ≥30/40 thresholds. Restored, as three
stages:

- **Stage 1 — usable?** Test data present; reference passes its own cases; the
  faulty version is actually refuted; nothing times out.
- **Stage 1.5 — can it host the taxonomy?** Some accepted AtCoder submissions
  are branch-free (no `if`, no comparison, just a comprehension and a `print`).
  There is no site in them for τ₂ or τ₃. Scoring such a program "1/3 caught"
  would charge the oracle for a property of the program, so it is excluded.
  10 of 102 candidates were dropped here.
- **Stage 2 — does the oracle catch planted bugs?** Three mutants per program,
  judged by the same sampling oracle the repair loop uses. A mutant the sample
  accepts gets a second opinion from the whole pool, which splits the failure:

  - `missed` — the pool refutes it but the sample did not draw the separating
    case. A real limit of the oracle at this `max_examples`.
  - `equivalent` — the pool does not refute it either. No oracle could catch
    it; this is an equivalent mutant, an artefact of mutation testing rather
    than evidence about the oracle. The generator retries on the next candidate
    site instead of counting it against the program.

  That distinction is what makes the stage a measurement of the oracle rather
  than a mutation-adequacy score.

**Thresholds** come from the proposal and are not inferred from data: a program
passes at ≥2 of 3 caught; the corpus freezes at ≥30/40 of the cohort, held as
a fraction so a 60-task corpus is gated at the same strictness (≥45/60).

The gate is measured on a cohort and the corpus is then topped up past it,
because a pass rate computed over a set already filtered on passing would be
vacuous.

### 4.5 Stratified selection — the paper's three levels

The proposal's Easy/Medium/Hard are bands of π, and π is **measured**
(`measure_pi.py` spends a model call per draw), so it cannot drive selection —
paying for it on candidates that go on to fail the mutation gate would burn
budget on programs nobody keeps.

Selection therefore balances on a free a-priori proxy: the AtCoder difficulty
rating in `difficulty.txt`, cut into terciles of the candidate pool's own
distribution. Harder contest problem ⇒ lower π, so the rating runs opposite to
π: lowest tercile is `easy`.

**Reproducibility.** Everything the seed touches is a pure function evaluated
before any program runs:

1. filter to faults whose coding task ships test data (2,864 → 931);
2. cut that pool's ratings into terciles (cuts: **[419, 1577]**) — no randomness;
3. `random.Random(seed).shuffle` — the single source of randomness;
4. interleave the three strata round-robin, so all three fill at the same rate.

Verified: same seed → identical candidate order; different seed → different
order; stratum assignment independent of seed. `--jobs` changes how many
candidates are in flight, never which are chosen.

---

## 5. Results

### 5.1 Corpus freeze — PASSING

`data/tasks.json`, `data/oracle_validation.json`, seed `20260717`,
`max_examples=80`, 97 min wall clock at `--jobs 6`.

```
examined 102 candidates → 83 usable, 10 excluded as branch-free
gate     57/60 cohort caught ≥2/3 mutants (threshold ≥45/60) → MET
strata   easy 20 / medium 20 / hard 20
mutants  180 caught · 21 equivalent · 0 missed
```

**`0 missed` is the headline result.** Across 201 planted mutants, the oracle at
`max_examples=80` never once failed to catch a mutant that the full pool could
catch. The 21 `equivalent` verdicts are mutants no test in the pool
distinguishes — undetectable in principle, and excluded from the score rather
than charged to the oracle. All three fault types are caught at comparable
rates (61 / 56 / 63), so no fault class is invisible to the oracle.

### 5.2 π̂ pilot — done, but the distribution is a problem

`data/pi_pilot.json`: 60 tasks × 40 i.i.d. draws = **2,400 calls**, mode
`no_memory` with empty history.

```
pooled π̂ = 0.369
min 0.00 · q33 0.05 · median 0.16 · q67 0.50 · max 1.00
```

Against the proposal's reference bands:

| π̂ range | tasks |
|---|---|
| > 0.35 (above the Easy band) | **25** — of which 13 at ≥0.90, 6 at exactly 1.00 |
| 0.18–0.35 (Easy) | 4 |
| 0.08–0.18 (Medium) | 4 |
| 0.02–0.08 (Hard) | 11 |
| < 0.02 | **16** |

Only **8 of 60** tasks land inside [0.08, 0.35]. The distribution is bimodal:
the model either solves the task almost every time or almost never.

### 5.3 Does the difficulty proxy predict π? — partly

Measured on the full corpus (n=60, `data/pi_pilot.json` × `data/tasks.json`):

```
Spearman(AtCoder difficulty, π̂) = −0.350
median π̂ by proxy stratum:  easy 0.300 · medium 0.275 · hard 0.038
```

The proxy is **moderately** predictive and monotone in the intended direction.

> **Correction on the record.** An earlier calibration on 12 tasks reported
> ρ = −0.063 and the conclusion "the proxy does not work". That estimate was
> noise from a tiny, extremes-weighted sample. The n=60 figure supersedes it.
> `data/pi_pilot_calibration12.json` retains the 12-task run.

---

## 6. Open problems, in priority order

### P1 — π̂ distribution does not match the design (blocking)

13 tasks at π̂ ≥ 0.90 accept in round 1 in **all three** memory conditions: no
counterexample is produced, memory is never written, and the cell cannot
discriminate `no_memory` from `typed`. At the other end, 16 tasks at π̂ = 0.00
may never accept in any arm. Roughly half the corpus is weakly informative for
the exact comparison the paper makes.

**This is now cheap to fix.** π̂ is already measured for all 60 tasks, so
re-selecting on measured π̂ instead of the difficulty proxy costs **zero
additional API calls**. The open question is whether 60 tasks in a usable π band
can be found within the 374 coding tasks the partial test tree covers, or
whether it needs the full archive first.

### P2 — corpus is frozen on 39% of the benchmark (blocking for publication)

See §3. Requires the human to re-download. Everything else can proceed
meanwhile; re-freezing is CPU-only and costs no budget.

### P3 — remaining budget is tight

$10.91 left. Estimated at the `no_memory` per-call rate ($3.89/1000 calls):

| | est. |
|---|---|
| E1 — 60 tasks × 12 rounds | ~$2.80 |
| E2 — 2 arms × 60 × 12, worst case | ~$5.60 |
| E3–E5 ablations and sweeps | not estimated |

E2's prompts carry memory evidence and are longer than E1's, so these are
**floors, not ceilings**. Raise `BUDGET_USD_CAP` in `.env` before starting, or
`run_eval.py` will stop mid-sweep when the cap is hit. (It stops cleanly and is
resumable, so this is a delay rather than a loss.)

---

## 7. Known bugs and limitations

| # | Issue | Status |
|---|---|---|
| B1 | `max_tokens=2048` truncates long responses. When the closing code fence is lost, `_extract_code` falls through to returning the **raw prose including the fence line**, producing a patch that always fails — and that gets assigned a spurious failure type in the typed-memory experiment. Observed in 1 of 12 sampled responses. | **open — fix before E1/E2** |
| B2 | `data/pi_pilot.json` records `"model": null` when `--model` is not passed, so the artifact does not state which model produced it. | open, minor |
| B3 | 3 of 510 salvaged test directories are internally inconsistent (`in/` without matching `out/`). Verified to be a property of the archive itself, not of the salvage. Two are now rejected by the stage-1 guard below; the third fails stage 1 for an unrelated reason. | mitigated |
| — | `TEST_DIR` fell back to `.` when `CONDEFECTS_TEST_DIR` was unset, because `pathlib.Path("")` is `PosixPath(".")`, which is truthy. | **fixed** |
| — | `_check_reference` returned "reference OK" when *every* case lacked an expected output, asserting criterion 2 with no evidence. Such a task would put the reference in the loop as the only authority, with nothing cross-checking it against what AtCoder accepted. | **fixed** |
| — | `measure_pi.py` counted `oracle_error` (oracle could not run) as a failed draw, indistinguishable from "the patch is wrong". This silently produced π̂ = 0.000 across the board and billed $0.41 for it. | **fixed** — now aborts |
| — | `run_eval.py` had no test-data guard at all. Same failure mode as above but ~20× the cost, since every round of every episode would have been inconclusive. | **fixed** — now aborts |

Both new guards run before the first billable call and report what is visible:

```
1/1 programs have no test cases under external/ConDefects/Test (e.g. abc230_a/44828138).
Set CONDEFECTS_TEST_DIR or unpack Test.zip
```

---

## 8. Next steps

**Immediate (no budget):**

1. **Decide P1.** Analyse whether re-selecting on measured π̂ yields 60 tasks in
   a usable band from the current pool. All inputs are already on disk.
2. **Fix B1** — raise `max_tokens`, and make `_extract_code` fail loudly on a
   truncated response instead of returning prose as a patch. Doing this after
   E1/E2 would mean re-running them.

**Then (budget):**

3. `scripts/build_strata.py` → `data/strata.json` (free; reads the pilot).
4. E1, then E2. Raise `BUDGET_USD_CAP` first.
5. E3–E5 ablations, budget permitting.
6. `freeze_results.py` → `analyze.py` → `fit_theory.py` → `make_figures.py` →
   `check_consistency.py`.

**Out of band:** re-download `Test.zip`, then re-freeze the corpus (CPU-only)
and re-run from step 3. Any run performed against `Test_partial` is provisional.

---

## 9. Reproducing the current state

`CONDEFECTS_TEST_DIR` must be set on every command until the full `Test.zip` is
in place. Forgetting it is what caused the π̂ = 0 incident; both entry points now
refuse to run rather than bill for it.

```bash
export CONDEFECTS_TEST_DIR=external/ConDefects/Test_partial

python3 scripts/salvage_test_zip.py                 # only if Test_partial/ is absent
python3 scripts/fetch_condefects.py --check-only     # reports what the adapter sees
python3 scripts/validate_oracle.py --corpus-size 60 --until 2023-01-28 --jobs 6
python3 scripts/measure_pi.py --calls-per-program 40
```

The freeze takes ~97 min at `--jobs 6` and costs nothing. `--jobs` affects wall
time only; selection is a function of `--seed` and the test tree. Re-run a
publication freeze with `--jobs 1`, since a program near the sandbox wall-clock
limit can time out under parallel load when it would not have serially, and a
timeout is a verdict here.

---

## 10. Artifact map (for agents)

| Path | Contents | In git |
|---|---|---|
| `data/tasks.json` | the frozen 60-task corpus, with `stratum`, `test_dir`, `strata_selection.cuts` | tracked |
| `data/oracle_validation.json` | per-fault detail; every mutant's site, diff, verdict, counterexample | tracked |
| `data/pi_pilot.json` | π̂ per task, 40 draws each | untracked — should be committed |
| `data/pi_pilot_calibration12.json` | the superseded 12-task calibration, kept as evidence for §5.3 | untracked — should be committed |
| `data/calls.jsonl` | append-only cost/token ledger; `src.llm.spent()` reads it to enforce the cap | untracked — **should be gitignored**, since committing it would make a fresh clone inherit this machine's spend against its own cap |
| `cache/` | on-disk response cache, keyed by (model, temperature, max_tokens, nonce, prompt) | ignored (`cache/*`) |
| `external/` | vendored benchmark, 25 GB | ignored (`external/`) |

Uncommitted source changes at the time of writing: `src/adapter.py`,
`scripts/measure_pi.py`, `scripts/run_eval.py` — the three fixes marked
**fixed** in §7.

A mutant is **not** stored as source. It is a pure function of the program's
`correctVersion.py`, so it regenerates identically:
`candidate_mutants(name, correct_source, fault_type)[attempts - 1]`, where
`attempts` is recorded per mutant in `oracle_validation.json` alongside the
`diff` to verify against.
