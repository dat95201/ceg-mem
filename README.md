# CEGMem: counterexample-guided repair with a typed memory of refuted attempts

Replication package. All results are regenerated from a single seed; no number
in the paper is written by hand.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your API key and model prices
python3 scripts/fetch_condefects.py    # clones the benchmark, checks the layout
```

`fetch_condefects.py` can only clone the *code*. ConDefects ships its contest
test data as a separate `Test.zip` (OneDrive or Baidu Drive — the script prints
both links); drop that archive into `external/ConDefects/` and re-run the
script to unpack and verify it. Without `Test/` there are no inputs, so there
is no oracle.

## Reproduce

```bash
python3 scripts/validate_oracle.py                        # mutation gate; freezes data/tasks.json
python3 scripts/measure_pi.py                             # -> data/pi_pilot.json
python3 scripts/build_strata.py                           # freezes data/strata.json

python3 scripts/run_eval.py --modes no_memory --force-full-budget   # E1
python3 scripts/run_eval.py --modes untyped typed --check-overfit   # E2

python3 scripts/freeze_results.py --experiment main       # -> data/results_real.json
python3 scripts/analyze.py                                # -> data/analysis.json
python3 scripts/fit_theory.py                             # -> data/theory_fit.json
python3 figures/make_figures.py
python3 scripts/check_consistency.py   # asserts every reported number matches
```

E1 *is* the main grid's no-memory arm. It runs to the full budget so that
`pi_hat` and `q_hat` are estimated without early-stopping bias; the summary
step then truncates its rounds back at the first accept, so the arm stays
comparable to the memory arms it is tabulated against. Do not run `no_memory`
a second time without `--force-full-budget` — that is a different cell, and
`scripts/analyze.py` will refuse to pool the two.

`run_eval.py` is resumable and caches every model call on disk, so an
interrupted run costs nothing to restart. Both properties come from the same
place: every episode's id, typing-noise RNG, and per-round cache nonce are
derived from its experiment cell, so a re-run reproduces the episode exactly
and rewrites its own rows rather than appending a second copy.

## Layout

| Path | Contents |
|---|---|
| `src/adapter.py` | loads a ConDefects fault and its contest test pool |
| `data/mutants.py` | mutation operators, one per fault type of section 3.5 |
| `src/sandbox.py` | runs a candidate program on one input under a timeout |
| `src/oracle.py` | counterexample oracle over the shipped test pool |
| `src/typer.py` | failure-type function, two granularities |
| `src/memory.py` | no-memory / untyped / typed stores |
| `src/loop.py` | the repair loop (Algorithm 1) |
| `src/llm.py` | model client with on-disk cache and a cost meter |
| `scripts/` | experiment driver and consistency checker |
| `paper/` | LaTeX source (IEEEtran) |
| `data/`, `cache/`, `external/` | generated or vendored; not tracked |

## Benchmark

We evaluate on the Python subset of [ConDefects](https://github.com/appmlk/ConDefects):
real faults from AtCoder submissions, each paired with the same author's
accepted version, the annotated fault lines, and the contest's own test data.
Two properties make it the right fit here.

A counterexample oracle is possible at all. Every test input arrives with the
output AtCoder accepted, so a refutation is a concrete input on which the
candidate's stdout differs from the expected output — not the pass/fail bit a
benchmark exposing only an opaque test suite would give, which cannot support
the class-level refutation this work studies. The reference implementation is
visible to the oracle only and is never placed in a model prompt; the sampled
oracle used inside the loop is separated from the full-pool audit that decides
whether an accepted patch was merely plausible (`is_truly_correct`).

The faults postdate the benchmarks LLM training corpora were built from. That
is ConDefects' reason for existing, and it is a *relative* guarantee, not an
absolute one: the corpus covers October 2021 – June 2024, which sits inside the
training window of any recent model. `scripts/validate_oracle.py --since/--until`
selects a time slice, and the honest position for a model with a later cutoff is
either to report contamination as a threat to validity or to re-mine AtCoder
past that cutoff using ConDefects' own collection protocol.

One further consequence of using real submissions: a program carries no problem
statement, so its intended output format is underdetermined by the source
alone. Each prompt therefore includes two worked input/output examples from the
task's test data (`src.adapter.Task.spec_note`), identically in all three memory
conditions.

## Oracle validation

`scripts/validate_oracle.py` runs two stages before it will freeze anything.
The first asks whether a fault is *usable* — test data present, reference
passes its own cases, faulty version actually refuted, nothing times out. The
second asks whether the oracle *catches bugs it has not seen*: three mutants
are planted in the reference, one per fault type of the proposal's section 3.5
(`off_by_one`, `wrong_comparison`, `missing_guard`), and the same sampling
oracle the repair loop calls is asked to refute each.

A mutant the sample accepts gets a second opinion from the whole shipped pool,
which separates two very different failures. If the pool refutes it, the
sample was too small — that is a real property of the oracle at this
`max_examples`, and it is reported. If the pool does not refute it either, no
oracle could have caught it: it is an equivalent mutant, an artefact of
mutation testing rather than evidence about the oracle, and the generator
moves to the fault type's next candidate site.

A program passes at ≥2 of 3 mutants caught; the corpus freezes at ≥75% of the
cohort passing — the proposal's 30 of 40, held as a fraction, so 90 of 120 on
the current corpus. The gate is measured on the cohort and the corpus
is then topped up past it, because a pass rate computed over a set already
filtered on passing would be vacuous. `data/oracle_validation.json` carries
every mutant's diff, site and verdict.

## Selecting the corpus

`validate_oracle.py --select hard` (the default) freezes 120 faults drawn from
the hard band and nothing else. The pilot is what retired the earlier
easy/medium/hard quotas: median π̂ came out 0.300 easy, 0.275 medium, 0.038
hard, so two thirds of that corpus sat where the proposer usually succeeds on
the first try and no memory condition can separate from another. The budget now
buys 120 of the discriminating cells rather than 40 of them plus 80 that mostly
answer themselves.

The band is an absolute AtCoder rating floor — `--hard-floor`, default 1600 —
not a tercile of whatever is on disk. π is *measured* (`scripts/measure_pi.py`
spends a model call per sample) so it cannot drive selection, and the rating
ConDefects ships in `difficulty.txt` stands in for it: the pilot puts the band
above 1600 at π̂ ≈ 0.04, inside the paper's Hard range of 0.02–0.08. Terciles
moved with the test tree — 1577 on the salvaged partial tree, 1639 on the full
one — so the same seed named a different corpus depending on how much of the
download had survived, which is not a property a frozen corpus may have. 1600 is
AtCoder's own blue boundary, fixed outside this project. The substitution is
recorded as `strata_selection.proxy`; `scripts/build_strata.py` remains the
authority on the strata the paper reports.

Everything the seed touches is a pure function evaluated before any program
runs: take the faults in adapter order, drop those whose coding task ships no
test data, drop those rated below the floor, shuffle with `random.Random(seed)`,
then walk that order one fault per coding task until 120 have passed.
`data/tasks.json` records the floor, the seed, the pool size and a SHA-256 of
the candidate order, so a re-run can be *checked* against the freeze rather than
trusted. `--jobs` changes how many candidates are in flight, never which are
chosen — though a program near the sandbox's wall-clock limit can time out under
load when it would not have serially, so re-run a publication freeze with
`--jobs 1` if that matters.

The walk claims a coding task the moment one of its faults reaches stage 2, so a
task whose fault fails the mutation gate is spent; the run refuses to start
unless the hard pool holds ~2 coding tasks per corpus slot. The full benchmark
supplies 337 at floor 1600, the salvaged partial tree only 123 — which is why
the whole `Test.zip` has to be unpacked before a 120-task freeze. `--select
terciles` restores the retired quota behaviour, `--select none` samples across
every rating.

`data/mutants.py` writes the *operators*, not the anchors. A ConDefects
program is an anonymous AtCoder submission drawn from a pool of 2,864, and the
corpus is not known until after this stage has run, so a hand-typed table of
substring anchors per named program — what the QuixBugs version of this file
held — has nothing to key on. Each operator locates its own site with `ast`
and then splices the source *text* over that node's byte range, so every byte
outside the edit is identical to what AtCoder accepted and no mutant is ever
caught because of a round-trip through `ast.unparse`. `MUTANT_OVERRIDES` takes
a hand-written edit for a specific program when one is wanted.

## Cost

Every model call records input tokens, output tokens and cost to
`data/calls.jsonl`. The driver aborts when the configured cap is reached.

## Data availability

See the paper's Data Availability section.
