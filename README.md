# CEGMem — counterexample-guided repair with a typed memory of refuted attempts

Replication package for a **real-benchmark** instantiation of the CEGMem study.

`paper/main_proposal.txt` is the authority on what must be measured. Its own
evaluation is synthetic by design (§5: *"no model is prompted and no test suite
is executed"*) — that is what makes soundness and non-repetition checkable
against ground truth the authors set. §9 names the next step: instantiate the
type function and the counterexample oracle on a real benchmark, and measure how
coherent real failure types actually are. This repository is that step.

**No results are currently held.** `data/` contains source only. See
[PLAN.md](PLAN.md) for the ordered run plan — which command serves which
experiment, which paper claim it anchors to, and what it costs.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # API key, model, BUDGET_USD_CAP
python3 scripts/fetch_condefects.py       # clone the benchmark, check the layout
```

The proposer talks OpenAI chat-completions, and the same code path serves a
local [Ollama](https://ollama.com) — it differs only in `LLM_BASE_URL`,
`LLM_API_KEY` and `MODEL`, none of which needs `.env` edited. The π screen runs
against `qwen2.5-coder:7b` locally:

```bash
ollama pull qwen2.5-coder:7b
bash scripts/screen_shard.sh --from 1 --to 132    # one shard of the screen
python3 scripts/consolidate_screens.py            # merge the shards
```

The one thing a local backend must not be left to decide for itself is the
context window. Ollama picks it from available VRAM — `4k/32k/256k`, per `ollama
serve --help` — and **truncates** an over-long prompt instead of refusing it,
which would silently cut the memory arms' evidence, the one thing the experiment
measures. The window reaches neither the cache key nor the report, so the same
model id on two machines can be two different instruments with nothing to say
so. `screen_shard.sh` therefore starts its own server with `OLLAMA_CONTEXT_LENGTH`
pinned, reads back what `/api/ps` actually serves, and refuses to spend on a
mismatch; `LLM_CONTEXT_TOKENS` is the client-side half of the same check.

`fetch_condefects.py` can only clone the *code*. ConDefects ships its contest
test data as a separate `Test.zip` (~6.4 GB, OneDrive or Baidu — the script
prints both links); drop that archive into `external/ConDefects/` and re-run the
script to unpack and verify it. Without `Test/` there are no inputs, so there is
no oracle.

## Layout

| Path | Contents |
|---|---|
| `src/loop.py` | the repair loop — Algorithm 1 of the paper |
| `src/memory.py` | the three conditions: no-memory / untyped / typed stores |
| `src/typer.py` | the failure-type function θ, two granularities, and Def. 3.1's `c` knob |
| `src/oracle.py` | counterexample oracle over the shipped test pool; the ρ-proxy knob |
| `src/proposer.py` | the proposer, and the evidence/exclusion blocks the arms differ on |
| `src/adapter.py` | loads a ConDefects fault and its contest test pool |
| `src/sandbox.py` | runs a candidate program on one input under a timeout |
| `src/llm.py` | model client with an on-disk cache and a cost meter |
| `src/metrics.py` | the per-round evidence log every metric is computed from |
| `data/mutants.py` | planted-mutant operators, one per fault type of §3.5 (source, not data) |
| `scripts/validate_oracle.py` | usability + natural-mutant gate; freezes the candidate pool |
| `scripts/measure_pi.py` | the π screen — N i.i.d. no-memory draws per candidate |
| `scripts/select_corpus.py` | fills the π bands; freezes the corpus and the screening audit |
| `scripts/build_strata.py` | absolute π bands, and the selection-vs-reported drift audit |
| `scripts/measure_pool_strength.py` | planted mutants on the frozen corpus: how blind is the pool |
| `scripts/run_eval.py` | the experiment driver — one grid, `(task × condition × seed)` |
| `scripts/freeze_results.py` `analyze.py` `fit_theory.py` | the analysis chain |
| `scripts/measure_coherence.py` `measure_anchoring.py` `label_tool.py` | RQ2: coherence and the anchoring failure mode |
| `scripts/check_consistency.py` | asserts every reported number matches the frozen data |
| `scripts/watch_eval.sh` | live monitor for a long run |
| `scripts/screen_shard.sh` | one shard of the π screen: pins the backend, verifies it, runs, shuts it down |
| `scripts/consolidate_screens.py` | merges the shards and audits the join |
| `figures/make_figures.py` | the paper's figures |
| `cache/`, `external/` | model-response cache and the vendored benchmark; not tracked |

## Benchmark

We evaluate on the Python subset of
[ConDefects](https://github.com/appmlk/ConDefects): real faults from AtCoder
submissions, each paired with the same author's accepted version, the annotated
fault lines, and the contest's own test data. Two properties make it the right
fit.

**A counterexample oracle is possible at all.** Every test input arrives with the
output AtCoder accepted, so a refutation is a concrete input on which the
candidate's stdout differs from the expected output — not the pass/fail bit a
benchmark exposing only an opaque test suite would give. A bit cannot support the
class-level refutation this work studies. The reference implementation is visible
to the oracle only and never enters a model prompt; the sampled oracle used
inside the loop is kept separate from the full-pool audit
(`src.oracle.is_truly_correct`) that decides whether an accepted patch was merely
plausible.

**The faults postdate the usual benchmarks.** That is ConDefects' reason for
existing, and it is a *relative* guarantee: the corpus covers October 2021 – June
2024, inside the training window of any recent model. `validate_oracle.py
--since/--until` selects a time slice; the honest position for a model with a
later cutoff is to report contamination as a threat to validity, or to re-mine
AtCoder past that cutoff using ConDefects' own protocol.

One consequence of using real submissions: a program carries no problem
statement, so its intended output format is underdetermined by the source alone.
Every prompt therefore includes two worked input/output examples from the task's
test data (`src.adapter.Task.spec_note`), identically in all three conditions.

## Oracle validation

`scripts/validate_oracle.py` runs two stages before it will freeze anything.

The first asks whether a fault is **usable**: test data present, reference passes
its own cases, faulty version actually refuted, nothing times out.

The second asks whether the oracle **catches bugs it has not seen**. Up to three
*natural mutants* are taken from the coding task itself — other people's wrong
submissions to the same problem (`src.adapter.sibling_faults`) — and the same
sampling oracle the repair loop calls is asked to refute each.

Natural, not planted, and the trade is deliberate. A natural mutant is a real
developer's real mistake, with a real mistake's detectability; a planted edit is
easier and less representative. The cost is coverage: a coding task with a single
submission has no sibling to borrow, hence `--min-siblings`.

A mutant the sample accepts gets a second opinion from the whole shipped pool,
which separates two different failures. If the pool refutes it, the sample was
too small — a real property of the oracle at this `max_examples`, and reported.
If the pool does not refute it either, no oracle could have caught it: it is
excluded from the denominator rather than charged against the oracle.

A program passes at ≥2/3 of its scoreable mutants caught — held as a fraction,
not "2 of 3", because a task supplies between one and three siblings and the
criterion must mean the same thing for each. The pool freezes at ≥75% of the
cohort passing. The gate is measured on the cohort and the pool is then topped up
past it, because a pass rate computed over a set already filtered on passing
would be vacuous.

### What that cannot say — `scripts/measure_pool_strength.py`

A natural mutant is a submission AtCoder *rejected*, so the shipped pool refutes
it by construction. Asking the pool about a program it is already known to reject
cannot reveal a case the pool would miss.

The unanswered question is whether the pool can tell a *small perturbation* of
the correct program from the correct program itself — which is the population the
repair loop actually judges, since an LLM patch is a near miss rather than a
stranger's from-scratch reimplementation. `measure_pool_strength.py` answers it
by planting mutants on the **already-frozen** corpus.

It is a measurement, not a gate: it selects nothing, drops nothing, and a task
whose pool turns out to be weak is reported rather than removed — removing it
would be selecting the corpus on a property measured after the freeze.

The verdict it exists to produce is `equivalent`: the mutant is wrong and no test
in the pool distinguishes it. That is a hole in the oracle, and in the loop a
patch landing in one is accepted while being wrong. Report the rate as an **upper
bound** on invisible overfitting, not an estimate of it — some share of any
planted edit changes no behaviour at all, and separating the two needs coverage
data this pipeline does not collect.

## Corpus selection

See [SELECTION.md](SELECTION.md) for why measured π has to drive selection, the
band table, and the discipline that keeps the selecting π̂ independent of the
reported one.

## Running it

See [PLAN.md](PLAN.md).

## Data availability

See §10 of the paper. Gaps between §10 and this repository are recorded at the
end of [PLAN.md](PLAN.md) rather than papered over.
