# Corpus selection — design

Anchored on `paper/main_proposal.txt` §5 ("Task suite and stratification") and
the theory it stratifies for. This document states the *design*; the numbers that
instantiate it are produced by steps 2–4 of [PLAN.md](PLAN.md) and live in
`data/screening.json` once they exist.

## Why π has to drive selection

§5 stratifies the task suite by difficulty into Easy / Medium / Hard, and on this
loop difficulty is not a matter of taste — it is π, the one-shot probability that
the proposer emits a correct patch.

Under the no-memory arm every round is an independent Bernoulli draw, so **π
alone fixes both** Pr[accept within B] and E[rounds | accept]. That is the
content of Theorem 4.3(a): expected oracle calls are 1/π with no memory and 1+D
with either memory, and D is a function of the type mixture. Every quantity the
paper predicts is a function of π.

Two regimes therefore contribute nothing to the primary metric:

- **π above the top band.** The first proposal is accepted in all three
  conditions. No counterexample is produced, so nothing is ever stored, and the
  cell cannot distinguish typed from no-memory — not because the hypothesis is
  false but because the memory was never written to.
- **π below the bottom band.** The episode exhausts B without accepting, and the
  primary metric (oracle calls before an accepted patch) is averaged over
  accepted episodes only, so the task supplies no datum to it.

### Why the contest rating cannot substitute

An earlier revision refused to let a measured quantity name the corpus — a
measured quantity is not reproducible from a seed — and substituted the AtCoder
rating shipped in `difficulty.txt`. The substitution does not work, for a reason
that is structural rather than empirical: **contest rating measures how hard a
problem is to solve from scratch; the task here is to repair an almost-correct
program.** The two come apart, and the pilot that measured the correlation found
it far too weak to place a task inside a band 0.10 wide.

Worse, the proxy pointed the wrong way. A high rating floor selects tasks whose
π sits *below* the informative window, where the primary metric is conditioned on
an accept that mostly does not happen — and discards the mid-rating tasks that
land inside it. The floor keeps the band that answers nothing and throws away the
band that answers the question.

The rating proxy is retired. `validate_oracle.py --select none` samples across
every rating; the `hard` and `terciles` modes remain only for re-deriving old
draws and are not used by [PLAN.md](PLAN.md).

## Two stages, two questions

| stage | script | question | cost |
|---|---|---|---|
| 1 | `validate_oracle.py` | is this fault **usable**? test data, working reference, real refutation, an oracle that catches mutants it has not seen | CPU only |
| 2 | `measure_pi.py` → `select_corpus.py` | is a usable fault **informative**? | API calls |

Stage 1 freezes a **candidate pool**, not the corpus. Only stage 2 can answer the
second question, and only by measuring π.

## The bands

Absolute, from the paper, not terciles of whatever was screened. Held in one
table shared by `select_corpus.py` and `build_strata.py`.

| stratum | π̂ | quota | role |
|---|---|---|---|
| `dead` | [0.00, 0.02) | 20 | control — B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | 30 | primary — largest predicted effect |
| `medium` | [0.08, 0.18) | 20 | primary |
| `easy` | [0.18, 0.35] | 30 | primary |
| `too_easy` | (0.35, 1.00] | 15 | control — predicted null; nothing is ever stored |

Each band takes `min(quota, available)`, so the corpus is ≤115 tasks and its
final size is not knowable until the screen has run.

### Why the quotas are unequal

**Supply.** The π̂ distribution over real ConDefects faults is bimodal — a mass of
tasks the model repairs almost immediately and a mass it essentially never
repairs — and `medium` is the trough between the two modes. A quota of 30 there
would demand several hundred screened candidates on its own.

**Demand, and this is what a naive reading of §5 gets backwards.** The paper's
own simulation puts the **largest** predicted effect at the *low*-π end:
Vargha–Delaney A₁₂ of 0.83 / 0.96 / **1.00** for Easy / Medium / Hard, oracle
calls 23.07 → 6.50 on Hard against 2.6× overall. Proposition 4.5 states its
guard-cost gap *"grows with task difficulty — smaller π accumulates more refuted
types before a repair"*, and Corollary 4.4's budgeted-success advantage holds
*"whenever B binds"*, which is the low-π regime by definition.

"A task at low π contributes no datum" is true of exactly **one** metric — oracle
calls to repair, which is conditioned on accepting. It is false of redundant
attempts, guard evaluations, proposals, and success@B, all of which are defined
on every episode. Three of the paper's four theoretical results are most visible
precisely where that one metric goes undefined. So `hard` gets a full quota and
`dead` a generous one.

Only `too_easy` is a pure control: there the first proposal is accepted, nothing
is ever stored, and all three conditions coincide by construction. Because
selection is banded on measured π̂, that saturated class is identified **at
selection time** — no post-hoc saturation screen is required, and none is kept in
the repo.

## The two π̂'s

Keeping them apart is the whole of the selection discipline.

| | source | cache nonce | role |
|---|---|---|---|
| **selecting π̂** | stage-2 screen | `pi-pilot\|…` | fixes the stratum; spent, never printed in a results table |
| **reported π̂** | E1 | `proposal\|…` | what results tables and `fit_theory.py` print |

Two independent samples. Conditioning on the first cannot inflate the second, so
regression to the mean cannot manufacture an effect.

Selection is measured on the **no-memory arm, before any treatment is applied**.
That is what makes it dose-range choice rather than outcome selection: it is a
property of the task, not of anything the study does to it.

Tasks migrate between the two π̂'s. That migration *is* the risk, so
`build_strata.py` writes the migration matrix out rather than hiding it, and
`data/screening.json` records every candidate screened, its π̂, and why it was or
was not taken — the full excluded distribution.

## Screen depth

π̂ lives on a grid of 1/k, so a shallow screen cannot represent a narrow band.
**At k = 20 the entire `hard` band [0.02, 0.08) is reachable by exactly one
outcome** — one success in twenty — and a genuinely hard task lands there only
about a third of the time. Doubling to k = 40 roughly doubles that.

The top of the range needs no deepening: `easy` is 0.17 wide and `too_easy` is
open-ended, so both are placed reliably at k = 20. Hence the two-stage screen —
everything at k = 20, then only the low end at k = 40, replaying the first
stage's draws from cache. A mislabelled `hard` band would be the expensive
mistake: it is where the paper predicts A₁₂ = 1.00.

## The budget is B = 20, not 10

The one deliberate deviation from §5, forced by §5's own arithmetic.
Pr[accept] = 1 − (1−π)^B:

| π | B = 10 | B = 20 |
|---|---|---|
| 0.02 | 0.183 | 0.332 |
| 0.05 | 0.401 | 0.642 |
| 0.08 | 0.566 | **0.811** |
| 0.20 | 0.893 | 0.988 |

At B = 10 the primary metric does not reach the Hard band at all and only
half-reaches Medium. B = 20 makes [0.08, 0.35] — the interval the paper itself
tabulates — actually usable. The cost is ~2× the calls on non-accepting episodes,
paid mostly on the two control strata.

`--budget` is deliberately **not** part of the experiment cell key, so a run that
passes a different one silently mixes arms rather than creating new cells. Leave
it alone unless every arm changes together.

## What the seed still fixes

Stage 1 keeps every reproducibility property the rating floor was introduced to
protect; it just applies them to the candidate pool instead of to the corpus.
Everything the seed touches is a pure function evaluated before any program runs:
take the faults in adapter order, drop those whose coding task ships no test
data, shuffle with `random.Random(seed)`, drop those whose coding task supplies
no sibling wrong submission, then walk that order one fault per coding task until
the pool is full.

The sibling filter runs *after* the shuffle, deliberately. Both orders give a
uniform sample — filtering a uniformly shuffled list leaves the survivors
uniformly ordered — but only this one is **stable**: adding a constraint removes
the faults that fail it and leaves every other fault where it was. Filtering
before the shuffle discards tasks for no reason but a changed index.

`data/pool/tasks.json` records the seed, the pool size and a SHA-256 of the
candidate order. `--jobs` changes how many candidates are in flight, never which
are chosen — though a program near the sandbox wall-clock limit can time out
under load when it would not have serially, so re-run a publication freeze with
`--jobs 1`.

Stage 2 then adds exactly one non-pure input — measured π̂ — and records it in
full, so a re-run can be **checked** against the freeze rather than trusted. The
corpus is reproducible from *the seed plus the screen*, and the screen is an
artifact, not hidden state. This is a real weakening of "reproducible from the
seed alone", and it is the price of stratifying on π: a corpus that cannot see π
cannot be calibrated to π.
