# CEGMem — counterexample-guided repair with a typed memory of refuted attempts

Replication package for a **real-benchmark** instantiation of the CEGMem study.

`paper/main_proposal.txt` is the authority on what must be measured. Its own
evaluation is synthetic by design (§5: *"no model is prompted and no test suite
is executed"*) — that is what makes soundness and non-repetition checkable
against ground truth the authors set. §9 names the next step: instantiate the
type function and the counterexample oracle on a real benchmark, and measure how
coherent real failure types actually are. This repository is that step.

**No results are currently held.** `data/` contains source only. See
[RUNBOOK.md](RUNBOOK.md) for the ordered run plan — which command serves which
experiment, which paper claim it anchors to, and what it costs.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # API key, model, BUDGET_USD_CAP
python3 scripts/fetch_condefects.py       # clone the benchmark, check the layout
bash scripts/pipeline.sh                  # what has run, and what runs next
```

`scripts/pipeline.sh` is the entry point for the whole study. It runs one stage
at a time, refuses to start a stage whose input artifact is missing, and reads
the parameters that link two stages off the artifact rather than letting them be
retyped. The stages, in order:

```
benchmark  ->  candidates  ->  gate  ->  screen  ->  corpus  ->  eval  ->  analyse
```

The first real decision is **`candidates`**: `select_candidates.py` decides which
faults the study is ever allowed to see, and its output order is the seeded
stratified traversal every later index range is cut from. It spends nothing and
runs once — [DESIGN.md](DESIGN.md) §2 argues each gate, and why filtering on
them is dose-range selection rather than outcome selection.

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
so. `screen_shard.sh` therefore pins `OLLAMA_CONTEXT_LENGTH`, reads back what
`/api/ps` actually serves, and refuses to spend on a mismatch.

[RUNBOOK.md](RUNBOOK.md) is the runbook: the protocol every machine has to
agree on, how to verify a new one before giving it real work, and how the shards
merge.

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
| **`scripts/pipeline.sh`** | **one entry point: stage status, the order guard, and the wiring between stages** |
| `scripts/fetch_condefects.py` | clones the benchmark and verifies the layout |
| `scripts/select_candidates.py` | Stage 0 — which faults the study may ever see, and in what order |
| `scripts/oracle_gate.sh` → `validate_oracle.py` | E0 — usability + natural-mutant gate; freezes the candidate pool |
| `scripts/refreeze_pool.py` | re-freezes a finished gate at the cohort size the candidate list could seat |
| `scripts/screen_shard.sh` → `measure_pi.py` | E0b — one shard of the π screen: N i.i.d. no-memory draws per candidate |
| `scripts/consolidate_screens.py` | merges the screen shards and audits the join |
| `scripts/select_corpus.py` | fills the π bands; freezes the corpus and the screening audit |
| `scripts/build_strata.py` | absolute π bands, and the selection-vs-reported drift audit |
| `scripts/measure_pool_strength.py` | planted mutants on the frozen corpus: how blind is the pool |
| `scripts/eval_shard.sh` → `run_eval.py` | E1–E5 — one shard of one experiment, over `(task × condition × seed)` |
| `scripts/serve_local.sh` | the local proposer on its own: start / verify / unload / stop |
| `scripts/consolidate_evals.py` | merges the experiment shards and audits the join |
| `scripts/summarize.py` `watch_eval.sh` | per-arm means mid-run; live progress and ETA |
| `scripts/freeze_results.py` `analyze.py` `fit_theory.py` | the analysis chain |
| `scripts/measure_coherence.py` `measure_anchoring.py` `label_tool.py` | RQ2: coherence and the anchoring failure mode |
| `scripts/check_consistency.py` | asserts every reported number matches the frozen data |
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

## Where to go next

| | |
|---|---|
| [RUNBOOK.md](RUNBOOK.md) | **how to run the whole study** — every stage in order, the protocol every machine shares, sharding and merge audits, and what to do when a step refuses |
| [DESIGN.md](DESIGN.md) | **why each stage exists** — which paper claim it anchors, why the benchmark is filtered and on what, the pre-registered deviations and commitments, and where the implementation is not the model |
| [STATUS.md](STATUS.md) | what this checkout currently holds, and the four things a run has to decide before it starts |

## Data availability

See §10 of the paper. Gaps between §10 and this repository are recorded at the
end of [DESIGN.md](DESIGN.md) rather than papered over.
