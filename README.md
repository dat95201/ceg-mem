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

A program passes at ≥2 of 3 mutants caught; the corpus freezes at ≥30 of the
40-program cohort passing. The gate is measured on the cohort and the corpus
is then topped up past it, because a pass rate computed over a set already
filtered on passing would be vacuous. `data/oracle_validation.json` carries
every mutant's diff, site and verdict.

## Selecting the corpus

`validate_oracle.py --stratify difficulty` (the default) balances the corpus
across the paper's three levels with an equal quota each, instead of taking one
unstratified sample that might land 45 easy programs and 3 hard ones.

The levels are bands of π, and π is *measured* — `scripts/measure_pi.py` spends
a model call per sample — so it is not knowable at selection time, and paying
for it on candidates that go on to fail the mutation gate would burn budget on
programs nobody keeps. Selection therefore balances on the AtCoder difficulty
rating ConDefects ships in `difficulty.txt`, cut into terciles of the candidate
pool's own distribution. A harder contest problem is harder to repair in one
shot, so the rating runs opposite to π: lowest tercile is `easy`, highest is
`hard`. This is a proxy, recorded as `strata_selection.proxy`;
`scripts/build_strata.py` remains the authority on the strata the paper
reports, and the agreement between the two is worth reporting in its own right.

Everything the seed touches is a pure function evaluated before any program
runs: filter to faults whose coding task ships test data, cut that pool's
ratings into terciles, shuffle with `random.Random(seed)`, interleave the three
strata round-robin. Same seed and same test tree give the same candidate order.
`--jobs` changes how many candidates are in flight, never which are chosen —
though a program near the sandbox's wall-clock limit can time out under load
when it would not have serially, so re-run a publication freeze with `--jobs 1`
if that matters.

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
