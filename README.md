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
python3 scripts/select_hard_tasks.py           # the pure draw; -> data/hard_120.json
python3 scripts/validate_oracle.py             # gate on natural mutants; freezes data/tasks.json
python3 scripts/measure_pool_strength.py       # planted mutants, measured not gated; -> data/pool_strength.json

python3 scripts/run_eval.py --modes no_memory --force-full-budget   # E1 - also the pi_hat/q_hat corpus
python3 scripts/build_strata.py                # freezes data/strata.json from E1's measured pi_hat

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
| `src/sandbox.py` | runs a candidate program on one input under a timeout |
| `src/oracle.py` | counterexample oracle over the shipped test pool |
| `src/typer.py` | failure-type function, two granularities |
| `data/mutants.py` | planted-mutant operators, one per fault type of section 3.5 |
| `src/memory.py` | no-memory / untyped / typed stores |
| `src/loop.py` | the repair loop (Algorithm 1) |
| `src/llm.py` | model client with on-disk cache and a cost meter |
| `scripts/select_hard_tasks.py` | the pure draw: which 120 faults, seeded and checkable |
| `scripts/validate_oracle.py` | usability + natural-mutant gate; freezes the corpus |
| `scripts/measure_pool_strength.py` | planted mutants on the frozen corpus: how blind is the pool |
| `scripts/` | experiment driver and consistency checker |
| `paper/` | LaTeX source (IEEEtran) |
| `cache/`, `external/` | model-response cache and the vendored benchmark; not tracked |
| `data/` | frozen artefacts *are* tracked (the corpus, the gate's audit, the pool-strength report); `cache/` is not |

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
second asks whether the oracle *catches bugs it has not seen*: up to three
**natural mutants** are taken from the coding task itself — other people's
wrong submissions to the same problem (`src.adapter.sibling_faults`) — and the
same sampling oracle the repair loop calls is asked to refute each.

Natural, not planted. This stage used to write its own mutants by planting an
edit in the reference, one per fault type of the proposal's section 3.5. A
natural mutant is a real developer's real mistake with a real mistake's
detectability — median 4 sampled cases to refute against 1 for a planted one,
refuted by the very first sampled case 22% of the time against 61% — and none
of the 240 across the corpus turns out to be equivalent, where 21 of 201
planted ones were. The cost is coverage: a coding task with a single submission
has no sibling to borrow, so the draw requires at least one (`--min-siblings`).

A mutant the sample accepts gets a second opinion from the whole shipped pool,
which separates two very different failures. If the pool refutes it, the
sample was too small — that is a real property of the oracle at this
`max_examples`, and it is reported. If the pool does not refute it either, no
oracle could have caught it: it is excluded from the denominator rather than
charged against the oracle.

A program passes at ≥2/3 of its scoreable mutants caught — held as a fraction,
not "2 of 3", because a coding task supplies between one and three siblings and
the criterion has to mean the same thing for each. The corpus freezes at ≥75%
of the cohort passing — the proposal's 30 of 40, held as a fraction, so 90 of
120 on the current corpus. The gate is measured on the cohort and the corpus
is then topped up past it, because a pass rate computed over a set already
filtered on passing would be vacuous. `data/oracle_validation.json` carries
every mutant's diff, site and verdict.

The corpus clears this gate outright: **240 of 240 natural mutants caught, none
missed, none equivalent.**

### What that result cannot say — `scripts/measure_pool_strength.py`

A natural mutant is a submission AtCoder *rejected*, so the shipped pool refutes
it by construction. Asking the pool about a program it is already known to
reject cannot reveal a case the pool would miss — and with 109 of the 120 tasks
shipping 80 test cases or fewer, `--max-examples 80` runs the *whole* pool
rather than a sample. On 91% of the corpus, 240/240 is close to a tautology.

The unanswered question is whether the pool can tell a *small perturbation* of
the correct program from the correct program itself — which is the population
the repair loop actually judges, since an LLM patch is a near miss rather than
a stranger's from-scratch reimplementation. `measure_pool_strength.py` answers
it by planting mutants on the **already-frozen** corpus. It is a measurement,
not a gate: it selects nothing, drops nothing, and a task whose pool turns out
to be weak is reported rather than removed — removing it would be selecting the
corpus on a property measured after the freeze.

Over 348 planted mutants on all 120 tasks (`data/pool_strength.json`):

| verdict | | |
|---|---|---|
| caught | 244 | 70.1% |
| **missed** | **0** | the sampled oracle is exactly as strong as the full pool |
| **equivalent** | **104** | **29.9% — no test in the pool distinguishes it** |

`missed = 0` settles the oracle: 80 sampled cases lose nothing against running
every case, and the catch rate saturates well before that — 98.8% at k=40,
89.8% at k=10. The natural mutants need more of the pool than the planted ones
do at every k (95.8% vs 98.8% at 40, 75.8% vs 89.8% at 10), which is the same
selection effect that makes a shipped fault subtler than a fresh mutation: the
author already ran the sample cases before submitting.

`equivalent = 29.9%` is the finding. It is a property of the contest's test
suite, not of this implementation, and it is concentrated rather than uniform —
53 of 120 tasks have none, while 8 have every planted mutant invisible. A
`wrong_comparison` edit is the worst case at 40% invisible, against 24-25% for
`off_by_one` and `missing_guard`; catching a boundary swap needs a test that
lands exactly on the boundary. Task size does not predict it: `arc173_e` ships
144 cases and still hides all three.

**What it means for the results.** In the loop, a patch that lands in one of
those holes is accepted while being wrong, so π̂ is an *upper bound* on the true
repair rate and every result is stated with respect to this oracle. The
comparison between the three memory conditions is unaffected — all three call
the same oracle, so a permissive oracle is permissive for all of them equally.
Read 29.9% as an upper bound on pool weakness rather than a measurement of it:
some share of it is a planted edit that changes no behaviour at all (a
comparison on a branch never reached), and separating the two needs coverage
data this run does not collect.

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
drop those whose coding task supplies no sibling wrong submission, then walk
that order one fault per coding task until 120 have passed.

The sibling filter runs *after* the shuffle, deliberately. Both orders give a
uniform sample — filtering a uniformly shuffled list leaves the survivors
uniformly ordered — but only this one is stable: adding a constraint removes
the faults that fail it and leaves every other fault where it was. Adding
`--min-siblings 1` this way kept 91 of the previous draw's 120; filtering
before the shuffle kept 18, discarding tasks for no reason but a changed index.
The rating floor stays before the shuffle because it is not an eligibility
constraint — it defines which population the corpus is a sample of, so changing
it should redraw, and does.
`data/tasks.json` records the floor, the seed, the pool size and a SHA-256 of
the candidate order, so a re-run can be *checked* against the freeze rather than
trusted. `--jobs` changes how many candidates are in flight, never which are
chosen — though a program near the sandbox's wall-clock limit can time out under
load when it would not have serially, so re-run a publication freeze with
`--jobs 1` if that matters.

The walk claims a coding task the moment one of its faults reaches stage 2, so
a task whose fault fails the mutation gate is spent, and the run refuses to
start unless the pool holds 1.5 coding tasks per corpus slot (and warns below
2.0). Requiring a natural mutant is what makes that tight: the floor-1600 pool
falls from 336 coding tasks to **188**, because the harder a contest problem
the fewer people submit to it and the fewer wrong submissions exist. Requiring
*three* natural mutants would leave 68 — short of the corpus itself — which is
why `--min-siblings` is 1 and up to two of a task's three mutant slots may go
unfilled. `--select terciles` restores the retired quota behaviour, `--select
none` samples across every rating.

## Cost

Every model call records input tokens, output tokens and cost to
`data/calls.jsonl`. The driver aborts when the configured cap is reached.

## Data availability

See the paper's Data Availability section.
