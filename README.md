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
# 1. candidate pool - execution-only gates plus the natural-mutant gate. No model calls.
python3 scripts/validate_oracle.py --select none --corpus-size 360 \
        --min-siblings 1 --data-dir data/pool --jobs 6

# 2. screen the pool on measured pi. Stage A places everything at k=20; stage B
#    deepens only the LOW end to k=40, where the bands are narrow. Stage B
#    replays A's 20 draws from cache, so it costs 20 new calls per task.
python3 scripts/measure_pi.py --calls-per-program 20 --out data/screen_a.json \
        --programs $(python3 -c "import json;print(' '.join(t['name'] for t in json.load(open('data/pool/tasks.json'))['tasks']))")
python3 scripts/measure_pi.py --calls-per-program 40 --out data/screen_b.json \
        --programs $(python3 -c "import json;d=json.load(open('data/screen_a.json'))['per_program'];print(' '.join(k for k,v in d.items() if v['successes']<=4))")

# 3. select the reported corpus by measured pi -> data/tasks.json, data/screening.json
python3 scripts/select_corpus.py --screen data/screen_a.json data/screen_b.json
python3 scripts/build_strata.py                # absolute pi bands -> data/strata.json
python3 scripts/measure_pool_strength.py       # planted mutants, measured not gated

# 4. the grid. Run these one at a time - they all append to data/episodes.jsonl
#    and all read llm.spent() from data/calls.jsonl, so concurrent runs race.
python3 scripts/run_eval.py --modes no_memory --force-full-budget   # E1
python3 scripts/run_eval.py --modes untyped typed --check-overfit   # E2

# 5. the analysis chain. Order matters: freeze needs strata, fit_theory needs
#    the frozen results, and build_strata's drift audit needs fit_theory.
python3 scripts/freeze_results.py --experiment main       # -> data/results_real.json
python3 scripts/analyze.py                                # -> data/analysis.json
python3 scripts/fit_theory.py                             # -> data/theory_fit.json
python3 scripts/build_strata.py --force                   # now with the drift audit
python3 scripts/measure_coherence.py                      # C1/C2 off E1, no model calls
python3 scripts/measure_anchoring.py                      # RQ2 anchoring rate, no model calls
python3 figures/make_figures.py
python3 scripts/check_consistency.py   # asserts every reported number matches
```

`--budget` defaults to 20 and is deliberately *not* part of the cell key, so a
run that passes a different one silently mixes arms rather than creating new
cells. Leave it alone unless every arm changes together.

E3–E5 and their freezes are in **Experiments** below.

E1 *is* the main grid's no-memory arm. It runs to the full budget so that
`pi_hat` and `q_hat` are estimated without early-stopping bias; the summary
step then truncates its rounds back at the first accept, so the arm stays
comparable to the memory arms it is tabulated against. Do not run `no_memory`
a second time without `--force-full-budget` — that is a different cell, and
`scripts/analyze.py` will refuse to pool the two.

Because the no-memory prompt carries no evidence and no exclusion block, it is
byte-identical across all `--budget` rounds of an episode (`src/proposer.py`,
`_evidence_block` returns `""` for that mode). Every round of E1 is therefore
an independent draw of π, and E1 at 5 seeds × 20 rounds measures π̂ on 100
draws per task — against the screen's 20, and against the 40 the pilot used.
That is why the *reported* π̂ comes from E1 and the screen's π̂ is spent on
selection and never printed in a results table.

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
| `scripts/select_hard_tasks.py` | the pure draw: which faults, seeded and checkable |
| `scripts/validate_oracle.py` | usability + natural-mutant gate; freezes the candidate pool |
| `scripts/measure_pi.py` | the π screen: N i.i.d. no-memory draws per candidate |
| `scripts/select_corpus.py` | stage 2: fills the π bands; freezes the corpus + the screening audit |
| `scripts/build_strata.py` | absolute π bands, and the selection-vs-reported drift audit |
| `scripts/measure_anchoring.py` | RQ2: how often steering rules out the class holding the fix |
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

Selection runs in two stages, and they answer different questions. Stage 1
(`validate_oracle.py`) asks whether a fault is *usable* — test data, a working
reference, a real refutation, an oracle that catches mutants it has not seen.
It costs no model calls and it freezes a **candidate pool**, not the corpus.
Stage 2 (`measure_pi.py` → `select_corpus.py`) asks whether a usable fault is
*informative*, and that question can only be answered by measuring π.

### Why π has to drive selection

An earlier revision refused to let it, on the ground that a measured quantity
cannot name a reproducible corpus, and substituted the AtCoder rating shipped in
`difficulty.txt`. The substitution does not work, and the paper says why.

§VI-A of the paper reads informativeness off *absolute* π at budget B. Under the
no-memory arm every round is an independent Bernoulli draw, so π alone fixes both
Pr[accept within B] and E[rounds | accept]. Two regimes contribute nothing:

- **π ≳ 0.5** — the first proposal is accepted in all three conditions. No
  counterexample is produced, so nothing is ever stored, and the cell cannot
  distinguish typed from no-memory. Not because the hypothesis is false but
  because the memory was never written to.
- **π below the band** — the episode exhausts B without accepting, and the
  primary metric (§VII-a: oracle calls before an accepted patch) is averaged over
  accepted episodes only, so the task supplies no datum to it.

The rating proxy cannot separate those. §V-G measured it: Spearman(rating, π̂) =
−0.350 on 60 tasks — monotone in the intended direction, moderately predictive,
and nowhere near sharp enough to place a task in a band 0.10 wide. Contest
difficulty measures how hard a problem is to solve from scratch; the task here is
to repair an almost-correct program.

Worse, the proxy pointed the wrong way. A rating floor of 1600 selects the band
§V-G puts at median π̂ = 0.038 — which is inside the proposal's Hard range
[0.02, 0.08] and therefore looks right, but at B = 12 sits in the regime §VI-A-a
calls *too hard*: Pr[accept] = 0.46 at π = 0.05, and the primary metric is
conditioned on accepting. Meanwhile the proxy-easy and proxy-medium terciles that
floor discarded came in at median π̂ = 0.300 and 0.275 — squarely inside the
informative window. The floor kept the band that answers nothing and threw away
the band that answers the question.

### The bands, and the two π̂'s

`scripts/select_corpus.py` fills absolute quotas. The bands are the proposal's
own, held in one table shared with `scripts/build_strata.py`:

| stratum | π̂ | quota | role |
|---|---|---|---|
| `dead` | [0.00, 0.02) | 20 | below the analysis range; B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | 30 | primary — **largest predicted effect** |
| `medium` | [0.08, 0.18) | 20 | primary |
| `easy` | [0.18, 0.35] | 30 | primary |
| `too_easy` | (0.35, 1.00] | 15 | control — predicted null, nothing is ever stored |

The quotas are unequal for two reasons, one of supply and one of demand.

**Supply.** §V-F's π̂ distribution is bimodal: of 60 tasks, 25 above 0.35 and 16
below 0.02, leaving 4 Easy / 4 Medium / 11 Hard. `medium` is the trough between
the modes, so a quota of 30 there would demand ~450 screened candidates on its
own. Screen in rating-stratified batches — low-rated candidates feed
`easy`/`too_easy` (§V-G puts the proxy-easy tercile at median π̂ = 0.300),
high-rated ones feed `hard`/`dead` (median 0.038) — and let `select_corpus.py`'s
shortfall report drive a top-up. The walk is deterministic over the pool order,
so a top-up leaves every already-selected task exactly where it was.

**Demand — and this is the part that a naive reading of §VI-A gets backwards.**
The proposal's own simulation puts the largest predicted effect at the *low*-π
end: Vargha–Delaney A₁₂ = 0.83 / 0.96 / **1.00** for Easy / Medium / Hard,
oracle calls 23.07 → 6.50 on Hard against 2.6× overall, and 16.62 redundant
attempts on Hard. Proposition 4.5 states its guard-cost gap *"grows with task
difficulty — smaller π accumulates more refuted types before a repair"*, and
Corollary 4.4's budgeted-success advantage holds *"whenever B binds"*, which is
the low-π regime by definition.

§VI-A's "a task at π < 0.1 contributes no datum" is true of exactly one metric —
oracle calls to repair, which is conditioned on accepting. It is false of the
other four. Three of the proposal's four theoretical results are most visible
precisely where that one metric goes undefined, so `hard` gets a full quota and
`dead` a generous one. Only `too_easy` is a pure control: there the first
proposal is accepted, nothing is ever stored, and all three conditions coincide
by construction.

### Metrics

The proposal's §5 lists six per-episode metrics; `scripts/analyze.py` computes
five of them, each per stratum and pooled, with a per-task mean over seeds and a
10⁴-resample bootstrap over tasks, Wilcoxon signed-rank paired on tasks,
Vargha–Delaney A₁₂, and Benjamini–Hochberg across the three primary strata.

| metric | result | defined on |
|---|---|---|
| `oracle_calls_to_accept` | Thm 4.3(a) — primary cost | accepted episodes only |
| `redundant_attempts` | Thm 4.3(b) — exactly zero for typed | every episode |
| `success_at_b` | Cor. 4.4 — budgeted success | every episode |
| `guard_evaluations` | Prop. 4.5 — Θ(m) vs O(1) | every episode |
| `proposals` | the model-call budget | every episode |

The sixth, **anchoring rate** (RQ2: the failure mode typed steering introduces),
comes from `scripts/measure_anchoring.py` — a post-hoc audit over
`data/episodes.jsonl`, no model calls.

It cannot arise at the guard. `TypedMemory.guard` re-runs the matched bucket's
stored counterexample (`memory.py::_still_refutes`) and blocks only when it
*still* refutes, so a blocked candidate provably fails at any `c`. Anchoring here
is a generation-side effect of `_exclusion_block`: if mistyping files a
refutation under the location the fix has to occupy, the proposer is told in
plain words to avoid the answer.

An episode is **anchored** when the reference patch's edit location entered the
exclusion block *and* the episode never accepted. The audit also splits the
round that first excluded it:

| | |
|---|---|
| `by_noise` | stored location ≠ true location — Def. 3.1's mistyping. Should vanish at c = 1. |
| `by_conflation` | stored *is* the true location: a genuinely refuted patch really does edit where the fix goes, and θ's location granularity cannot separate them. **Survives at c = 1**, so it is a property of the real type function rather than of the noise model. |

`excluded but accepted anyway` is reported alongside `anchored`, because a
proposer that repairs in spite of the exclusion is evidence about how strong the
steering channel actually is.

The target location is `edit_location(buggy, reference)`. Checked against
ConDefects' own `faultLocation.txt` on the current corpus: it contains an
annotated fault line on **112 of 118** comparable tasks (94.9%), **117 of 118**
within one line.

Two π̂'s, and keeping them apart is the whole of §VI-A-c:

- **selecting π̂** — `screen_pi_hat` in `data/tasks.json`, from the stage-2
  screen. Fixes the stratum. Measured on the no-memory arm *before any treatment
  is applied*, which makes it dose-range choice rather than outcome selection.
- **reported π̂** — from E1, via `data/theory_fit.json`. What results tables
  print. A different sample with a different cache nonce, so conditioning on the
  first cannot inflate the second.

Tasks migrate between the two. That is regression to the mean and it is expected;
`build_strata.py` writes the migration matrix out rather than hiding it, because
its size *is* the term §VI-A-c warns about. `data/screening.json` records every
candidate screened, its π̂ and why it was or was not taken — the full excluded
distribution that §VI-A-c requires to be reported.

### The budget is B = 20, not 12

The one deliberate deviation from §IX-B, forced by §VI-A-a's own arithmetic:

| π | Pr[accept] at B=12 | at B=20 |
|---|---|---|
| 0.02 | 0.215 | 0.332 |
| 0.05 | 0.460 | 0.642 |
| 0.08 | 0.632 | **0.811** |
| 0.10 | 0.718 | 0.878 |
| 0.20 | 0.931 | 0.988 |

At B = 12 the primary metric does not reach the proposal's Hard band at all, and
only half-reaches Medium — the informative window starts around π = 0.13, above
Medium's floor. B = 20 brings the window down to π = 0.08 and makes
[0.08, 0.35] — the interval §VI-A itself reports counts for — actually usable.
The cost is 67% more calls per non-accepting episode, which is paid mostly on the
two control strata.

### What the seed still fixes

Stage 1 keeps every reproducibility property the rating floor was introduced to
protect; it just applies them to the candidate pool instead of to the corpus.
Everything the seed touches is a pure function evaluated before any program
runs: take the faults in adapter order, drop those whose coding task ships no
test data, shuffle with `random.Random(seed)`, drop those whose coding task
supplies no sibling wrong submission, then walk that order one fault per coding
task until the pool is full. `--select none` is the mode that does this across
every rating; `--select hard` (a floor) and `--select terciles` remain for
re-deriving the retired draws, and neither is used by the pipeline above.

Stage 2 then adds exactly one non-pure input — measured π̂ — and records it in
full (`data/screening.json`), so a re-run can be checked against the freeze
rather than trusted. The corpus is reproducible from *the seed plus the screen*,
and the screen is an artifact, not a hidden state. This is a real weakening of
the earlier "reproducible from the seed alone" property and it is the price of
§VI-A: a corpus that cannot see π cannot be calibrated to π.

The sibling filter runs *after* the shuffle, deliberately. Both orders give a
uniform sample — filtering a uniformly shuffled list leaves the survivors
uniformly ordered — but only this one is stable: adding a constraint removes
the faults that fail it and leaves every other fault where it was. Adding
`--min-siblings 1` this way kept 91 of a previous draw's 120; filtering
before the shuffle kept 18, discarding tasks for no reason but a changed index.
`data/pool/tasks.json` records the seed, the pool size and a SHA-256 of the
candidate order. `--jobs` changes how many candidates are in flight, never which
are chosen — though a program near the sandbox's wall-clock limit can time out
under load when it would not have serially, so re-run a publication freeze with
`--jobs 1` if that matters.

The walk claims a coding task the moment one of its faults reaches stage 2, so
a task whose fault fails the mutation gate is spent, and the run refuses to
start unless the pool holds 1.5 coding tasks per slot (and warns below 2.0).
Requiring a natural mutant is what makes that tight — the harder a contest
problem, the fewer people submit to it and the fewer wrong submissions exist:

| AtCoder rating | coding tasks with test data | …and ≥1 sibling |
|---|---|---|
| < 800 | 412 | 299 |
| 800–1200 | 112 | 81 |
| 1200–1600 | 114 | 78 |
| 1600–2200 | 155 | 97 |
| ≥ 2200 | 181 | 91 |
| **total** | **980** | **646** |

Sampling across every rating rather than above a floor is what makes a 280-fault
candidate pool affordable: the floor-1600 pool holds 188 coding tasks, the
unfiltered one 646. Requiring *three* natural mutants would leave far fewer,
which is why `--min-siblings` is 1 and up to two of a task's three mutant slots
may go unfilled.

## Experiments

One driver, one grid — `(task × condition × seed)` — with different flags. All
five run at `--budget 20`.

| | command | seeds | scope |
|---|---|---|---|
| **E1** no-memory arm | `--modes no_memory --force-full-budget` | 1–5 | whole corpus |
| **E2** memory arms | `--modes untyped typed --check-overfit` | 1–5 | whole corpus |
| **E3** ablation | `--modes typed --guard off` / `--steer off` | 1–3 | whole corpus |
| **E4** oracle sweep | `--modes typed --max-examples 20 \| 8 \| 3 --check-overfit` | 1–3 | 30-task subset |
| **E5** typing sweep | `--modes typed --typing-noise-c 0.9 \| 0.75 \| 0.5` | 1–3 | 30-task subset |

E4's reference level is `--max-examples 100` and E5's is `c = 1.0`; both are the
typed cell E2 already ran, so neither sweep pays for its own baseline —
`run_eval.py` recognises the cell and skips it.

**E4 must be read off `is_truly_correct`, not off `accept`.** Lowering
`max_examples` weakens the oracle, so more wrong patches are accepted and the
apparent repair rate *rises*; reported naively the sweep concludes that a less
informative oracle repairs better. `--check-overfit` re-runs the full pool on
every accept and writes the verdict to `data/overfit_checks.jsonl`, which is the
series to plot.

E4's levels stop at 20. `src/oracle.py::_sample` returns the whole pool whenever
`max_examples ≥ len(cases)`, and across the *benchmark* — not just some corpus —
the test pools are small: median 30 cases, 97.0% at ≤ 80, 98.9% at ≤ 100, and a
maximum of **148**. So `--max-examples 300` never differs from running the whole
pool anywhere in ConDefects, and 100 differs on 1.1% of coding tasks. Those two
levels are the same experiment at twice the price. The same fact is why the
overfitting audit is near-vacuous in E2 (§VI-C): the sampled oracle already *is*
the full oracle almost everywhere, so E4 is the only place ρ actually moves.

The sweep subset must be pre-declared and shared: pass the same list to
`run_eval.py --programs` and to `freeze_results.py --sweep-programs`, and draw it
stratified across the π bands so a sweep is not confounded with difficulty.

## Cost

Every model call records input tokens, output tokens and cost to
`data/calls.jsonl`. The driver aborts when the configured cap is reached.

Measured on 4,778 logged calls at `claude-haiku-4-5` ($1/$5 per Mtok): **mean
$0.00513 per call**, median $0.00472, median 620 in / 831 out tokens. Output
dominates at ~87% of cost, so the memory arms — which add evidence to the
*input* — run only about 15–20% dearer than no-memory, not double.

**The grid's cost is not knowable until the screen has run.** The quotas in
`select_corpus.py` are a ceiling, not a prediction: each band takes
`min(quota, available)`, and §V-F's distribution says `medium` is the trough
between two modes, so it is the band most likely to come up short. A corpus that
under-fills is smaller *and* cheaper, and both numbers land at the same moment.

So the projection lives in `select_corpus.py`, computed from the counts it just
produced rather than written down here:

```
projected grid cost at B=20 (upper bound: E[rounds] evaluated at pi, not q)
  E1  no_memory, 94 x 5 x 20 full budget          9,400 calls  $  48
  E2  untyped+typed, 94 x 5 x 2, early stop       7,697 calls  $  46
  E3  ablations, 94 x 3 x 2                       4,618 calls  $  28
  E4+E5  2 sweeps x 3 levels x 30 x 3             2,520 calls  $  15
  TOTAL                                          24,236 calls  $ 137
```

What *is* fixed in advance is the rate card and the shape of the arithmetic:

| | |
|---|---|
| no-memory / screening call | $0.0051 |
| memory-arm call | $0.0060 |
| E1 calls | `N × seeds × B` (full budget, no early stop) |
| E2 calls | `N × seeds × 2 × E[rounds]` |
| E[rounds] at one-shot rate π | `(1 − (1−π)^B) / π` |

E[rounds] is evaluated at π rather than at the per-round rate q the memory arms
achieve. Since q ≥ π whenever steering helps at all, every memory-arm figure is
an upper bound; only E1's is exact. Per-band π used for the projection:
`dead` 0.01, `hard` 0.05, `medium` 0.13, `easy` 0.26, `too_easy` 0.60 —
projection only, never selection.

Screening is the one line that *can* be budgeted up front, because the pool size
is chosen rather than discovered: a 360-fault pool at 20 draws each, plus 20 more
on the ~55% that come back at ≤ 4/20, is ~11,200 calls ≈ **$57**.

That depth is not padding. The bands at the low end are narrow and π̂ lives on a
grid of 1/k, so a shallow screen cannot represent them: **at k = 20 the whole
`hard` band [0.02, 0.08) is reachable by exactly one outcome** — 1 success out of
20 — and a genuinely hard task lands in it only about a third of the time.

| true π | band | placed correctly at k=20 | at k=40 |
|---|---|---|---|
| 0.03 | `hard` | 33.6% | **67.3%** |
| 0.05 | `hard` | 37.7% | **73.3%** |
| 0.07 | `hard` | 35.3% | **63.9%** |
| 0.12 | `medium` | 49.8% | 62.4% |
| 0.30 | `easy` | 66.5% | 66.5% |
| 0.60 | `too_easy` | 97.9% | 97.9% |

The top of the range needs no deepening — `easy` is 0.17 wide and `too_easy` is
open-ended — which is why stage B is spent only where the resolution is missing.
A mislabelled `hard` band would be the expensive mistake: it is where the
proposal predicts A₁₂ = 1.00.

Set `BUDGET_USD_CAP` from the projection *plus* whatever `data/calls.jsonl`
already carries — `llm.spent()` sums the whole file, so prior spend counts
against the cap.

Wall clock is the binding constraint more often than money: the pilot ran at
~10.7 s/call (most of it sandbox execution, not the API), and neither
`run_eval.py` nor `measure_pi.py` parallelises. Budget roughly 100 hours for the
full grid and run it under `tmux`/`nohup`.

## Data availability

See the paper's Data Availability section.
