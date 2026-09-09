# CEGMem — counterexample-guided repair with a typed memory of refuted attempts

Replication package for the CEGMem study on real faults: a repair loop in which
every refuted candidate patch is stored under a *failure type*, a guard blocks
candidates that repeat a stored refutation before the oracle is paid, and the
prompt is steered away from failure classes already seen. The study measures
this on the Python subset of [ConDefects](https://github.com/appmlk/ConDefects)
(99 faults, five difficulty bands) with two proposers: `qwen2.5-coder:7b` served
locally by Ollama (the reported grid, 15 experiment presets) and `gpt-4o-mini`
(three presets, the second-proposer comparison). The paper is
`paper/common/main.tex`; its reference build is `paper/main.pdf`.

Everything in the paper traces to one JSON in `paper/common/` (`numbers.json`,
`figdata.json`, `addenda.json`, `corpus.json`, `related.json`, `review3.json`,
`secondproposer.json`), and every one of those is regenerated from the run
directories by a tool in this repository. `scripts/reproduce.sh` regenerates
them and fails if they differ from the committed ones.

## The one command

```bash
git clone --branch promote-main <REPO_URL> ceg-mem && cd ceg-mem
ARTIFACTS=<path/to/artifacts.zip | URL | directory> bash scripts/reproduce.sh
```

With the artifacts installed and no change to the defaults this rebuilds
`paper/main.pdf` with every reported number identical to the committed one. No
model server is started, no oracle runs: the shipped merged logs already hold
every experiment cell, so the pipeline only re-derives the analysis (seconds),
regenerates and checks the paper's JSONs, and builds the PDF. Elapsed time is a
few minutes.

```bash
bash scripts/reproduce.sh --dry-run        # the plan: per preset, cells expected / complete / missing
bash scripts/reproduce.sh --stage numbers  # one stage only
bash scripts/reproduce.sh --no-paper       # stop after the numbers check
```

The same command **without artifacts** runs the whole study from scratch —
candidate selection, the oracle gate, the corpus freeze, the grid for both
proposers, the analysis, the paper — given the benchmark test data, a model
endpoint per proposer and time (days of GPU time for the local grid, tens of
dollars for the cloud one). What runs is decided per stage and, for the grid,
per *cell*: `scripts/grid_status.py` compares the cells a preset would walk with
the cells the merged log already completes, and only missing cells are run.
Model calls hit `cache/` first. [RUNBOOK.md](RUNBOOK.md) describes every stage.

**Two reproduction regimes.** (a) With the artifacts — the run data *and* the
response cache — the reproduction is deterministic: nothing is re-run, and every
number comes out bit-identical. (b) Without them the study re-runs, and because
the proposer is sampled at temperature 1.0, draws the cache does not hold are
new draws: the results are statistically similar to the paper's, not identical.
Only data + cache give identical numbers.

**Known incompleteness.** The shipped local run holds 169 of the 216
`E9-freeguard` cells (two shards, `001_004` and `005_008`, stopped early; two
more cells are truncated). The paper reports the free-guarded result on the
cells present and as exploratory. The 47 missing cells are declared in
`data/official-2026-09-01/grid_coverage.json` and are **not** run from the
artifacts. `FILL_DECLARED_GAPS=1` (or a run from scratch) completes the grid —
`tables/freeguard.tex` and `addenda.free_guarded` then move, and the numbers
check fails until `--update-numbers` is used deliberately.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # numpy scipy matplotlib pytest openai python-dotenv
python3 scripts/fetch_condefects.py        # clones the ConDefects CODE (125 MB), reports Test/
```

`scripts/reproduce.sh` does both on its own (stage `env`) if they have not been
done. Two more inputs are needed only when something has to *run*:

* **The benchmark test data.** ConDefects ships its contest inputs as a separate
  `Test.zip` (about 6.4 GB; `fetch_condefects.py` prints the download links).
  Drop it into `external/ConDefects/` and run `fetch_condefects.py` again to
  unpack it. Without `Test/` there is no oracle, so no missing cell and no
  Tier-B artifact can be recomputed — a run from the shipped artifacts does not
  need it.
* **A proposer.** For the local grid: [Ollama](https://ollama.com) on `PATH`
  with `ollama pull qwen2.5-coder:7b` (a GPU with 8 GB+ of memory; `scripts/
  install_ollama_colab.sh` does the install on a Colab runtime). `eval_shard.sh`
  starts its own server on `PORT` (default 11435) with the context window
  pinned and verified, and stops it afterwards. For the cloud grid: `LLM_API_KEY`
  (OpenAI-compatible; `LLM_BASE_URL` to point elsewhere) plus the price card and
  the spending cap, all required before a paid cell is run.

TeX (`latexmk`, `pdflatex`, IEEEtran, acmart with the libertine/newtx/inconsolata
fonts) is needed for the paper stage only; without it the stage is skipped with
a warning and everything up to the numbers check still runs.

## The two proposers

Each proposer has its own run directory, model id, context window and backend,
all set through the environment and all defaulting to the values the paper was
produced with:

| | local | cloud |
|---|---|---|
| run directory | `LOCAL_RUN_DIR=official-2026-09-01` | `CLOUD_RUN_DIR=gpto4mini-2026-09-06` |
| model | `LOCAL_MODEL=qwen2.5-coder:7b` | `CLOUD_MODEL=gpt-4o-mini` |
| context window | `LOCAL_CONTEXT_LENGTH=32768` | `CLOUD_CONTEXT_LENGTH=200000` |
| backend | `LOCAL_BACKEND=ollama` (`PORT=11435`) | `CLOUD_BACKEND=cloud` (`LLM_API_KEY`, `LLM_BASE_URL`, `PRICE_IN_PER_MTOK=0.15`, `PRICE_OUT_PER_MTOK=0.60`, `BUDGET_USD_CAP=25`) |
| presets | E1 E2 E3-guard-only E3-steer-only E4-k20/k8/k3 E5-c90/c75/c50/c25/c00 E5-random E8-corpus E9-freeguard | E1 E2 E3-steer-only |

`PROPOSERS="local cloud"` selects the blocks. The second proposer inherits the
local run's frozen corpus and banding (the same 99 tasks); the preset table
both proposers run from is `scripts/presets.py`. `CLOUD_CONTEXT_LENGTH` is
200000 rather than gpt-4o-mini's 128k window because the shipped cloud shards
were collected with that value and `consolidate_evals.py` refuses to pool a
shard whose recorded `context_length` differs (the value only makes `src.llm`
refuse an over-long prompt; no prompt in this study comes near either figure).
The shipped cloud metas record the o4-mini rate card (1.10/4.40) although the
run is gpt-4o-mini; the defaults are gpt-4o-mini's 0.15/0.60 — prices are
ledger metadata, not merge-blocking. `KNOWN_DIVERGENT_TASKS="abc285_e/48880084"`
names the fault whose candidate patches sit at the sandbox-timeout edge, so E1
and the guarded arms disagree on success@B for two of its seeds;
`check_consistency.py --allow-divergent` reports it without failing, and the
paper excludes it in a sensitivity analysis.

**Sharding.** `SHARDS=1` by default. A larger value splits each preset's
universe into contiguous index ranges that are run one after the other, or by
hand on several machines (`scripts/eval_shard.sh --from/--to`, `scripts/
fleet.sh`). Sharding never changes which cells are hit — hits are decided from
the merged log — but it does change the shard *file names* under
`data/<run>/`; `scripts/consolidate_evals.py` merges them regardless.

## Where things land

```
data/<run>/            episodes.jsonl (the merged log), overfit_checks.jsonl, calls.jsonl,
                       the corpus (tasks.json, pool/, candidates.json), the universe lists,
                       eval_shards/*.meta.json, the Tier-B and Tier-C analysis artifacts
logs/<run>/            shard traces
cache/                 model-response cache (cross-run; what makes a re-run free)
paper/common/*.json    the regenerated provenance JSONs
paper/main.pdf         the paper (copied from paper/common/main.pdf)
```

Expected runtimes from the shipped artifacts: install and grid check under a
minute, analysis about 2 min for both proposers, policies 5 s, numbers 25 s,
paper 15 s. From scratch: the local grid is on the order of 150 GPU-hours on a
T4 across the 15 presets, the oracle gate and the Tier-B artifacts add tens of
CPU-hours, and the cloud grid is under $25 at the default rate card.

## Layout

| Path | Contents |
|---|---|
| `src/loop.py` | the repair loop — Algorithm 1 of the paper |
| `src/memory.py` | the three conditions: no-memory / untyped / typed stores |
| `src/typer.py` | the failure-type function θ, two granularities, the `c` knob |
| `src/oracle.py` | counterexample oracle over the shipped test pool |
| `src/proposer.py` | the proposer and the evidence/exclusion prompt blocks |
| `src/adapter.py` | loads a ConDefects fault and its contest test pool |
| `src/sandbox.py` | runs a candidate program on one input under a timeout |
| `src/llm.py` | model client with an on-disk cache and a cost meter |
| `src/metrics.py` | the per-round evidence log every metric is computed from |
| `src/paths.py`, `scripts/run_dir_paths.sh` | `RUN_DIR` → `data/<run>/`, `logs/<run>/` |
| **`scripts/reproduce.sh`** | **the one command: stages 0–7, hit/skip per stage and per cell** |
| `scripts/presets.py` | the experiment preset table (one source for `eval_shard.sh` and `grid_status.py`) |
| `scripts/grid_status.py` | expected / complete / missing cells of a preset on a run's merged log |
| `scripts/universes.py` | the deterministic, stratum-interleaved universe lists |
| `scripts/fetch_artifacts.py`, `scripts/build_artifacts.py` | install the shipped artifacts / pack them from raw run directories |
| `scripts/pipeline.sh` | stage runner for the corpus stages (candidates, gate, corpus) and the grid |
| `scripts/select_candidates.py`, `oracle_gate.sh` → `validate_oracle.py`, `select_corpus.py` | Stage 0, E0, the corpus freeze |
| `scripts/eval_shard.sh` → `run_eval.py` | one shard of one preset; `consolidate_evals.py` merges the shards |
| `scripts/serve_local.sh`, `fleet.sh`, `watch_eval.sh`, `summarize.py` | the local server; parallel shards; progress |
| `scripts/freeze_results.py`, `fit_theory.py`, `build_strata.py`, `analyze.py`, `measure_*.py` | the analysis chain |
| `scripts/build_verdict_matrix.py`, `simulate_policies.py`, `verify_policies.py` | the validation-policy comparison |
| `scripts/check_consistency.py` | every frozen number matches the log it came from |
| `paper/common/` | the paper body, the provenance JSONs, `tools/` (the extractors), `Makefile` |
| `notebooks/reproduce_colab.ipynb` | the same one command on Colab |
| `DESIGN.md` | why each stage exists and which paper claim it anchors |
| `data/mutants.py` | planted-mutant operators (source, not data) |

## Benchmark

We evaluate on the Python subset of ConDefects: real faults from AtCoder
submissions, each paired with the same author's accepted version and the
contest's own test data. Every test input arrives with the output AtCoder
accepted, so a refutation is a concrete input on which the candidate's output
differs — the class-level evidence this work studies, not a pass/fail bit. The
reference implementation is visible to the oracle only and never enters a
prompt. Every prompt includes two worked input/output examples from the task's
test data, identically in all conditions, because a submission carries no
problem statement.

## Data availability

The shipped artifacts (`artifacts.zip`: the two run directories with merged
logs, corpus, universe lists, shard protocol records and the oracle-heavy
Tier-B artifacts, plus shard traces; about 7 MB compressed, 130 MB unpacked)
are built by `scripts/build_artifacts.py` and carry a `MANIFEST.json` with the
sha256 of every file. The model-response cache is placed under `cache/` by
hand. See `paper/common/sections/11b-availability.tex` for the archive location
and [RUNBOOK.md](RUNBOOK.md) for the layout.
