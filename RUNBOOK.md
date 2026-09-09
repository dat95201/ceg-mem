# Runbook — reproducing the study

[README.md](README.md) says what this is and gives the one command.
[DESIGN.md](DESIGN.md) says *why* each stage exists and which paper claim it
anchors. This file is how the pipeline runs: every stage, what it reads and
writes, when it is a hit and when it runs, how to run one stage alone or on
several machines, what the artifacts contain, and what to do when a check fails.

```
0 env  →  1 artifacts  →  2 corpus  →  3 grid  →  4 analyse  →  5 policies  →  6 numbers  →  7 paper
                           [per proposer: local, cloud]           [local]
```

```bash
bash scripts/reproduce.sh                  # everything
bash scripts/reproduce.sh --dry-run        # the plan, nothing started
bash scripts/reproduce.sh --stage <name>   # one stage: env artifacts corpus grid analyse policies numbers paper
bash scripts/reproduce.sh --update-numbers # accept regenerated paper JSONs as the new reference
bash scripts/reproduce.sh --no-paper       # stop after the numbers check
```

Every external step is echoed with a leading `+` before it runs, and a summary
table (stage, scope, status, seconds) closes every run, also a failed one.

---

## 1. The rule

> With the artifacts installed and no change to the defaults, the one command
> rebuilds `paper/main.pdf` with every reported number identical to the
> committed one, starting no model server and running no oracle. With no
> artifacts at all, the same command runs the whole study from scratch, given a
> model endpoint, the benchmark test data and enough time.

Four consequences shape every stage:

1. **Every number traces to a JSON in `paper/common/`** (`numbers`, `figdata`,
   `addenda`, `corpus`, `related`, `review3`, `secondproposer`), each
   regenerated from `data/<run>/` alone by a tool under `paper/common/tools/`.
   Stage 6 regenerates all seven into a temporary directory and compares them
   with the committed ones (provenance paths ignored, `NaN == NaN`, relative
   tolerance 1e-6). A difference fails the run; `--update-numbers` is the only
   way to move the committed files.
2. **Data present ⇒ nothing runs.** The grid decision is made per *cell* from
   the merged `data/<run>/episodes.jsonl`, using `scripts/run_eval.py`'s own
   `cell_key` and completion rule (`_completed_cells`): a cell is done when its
   episode ran to the budget, or accepted and the arm may stop early. Only
   presets with missing cells are executed, and only their missing cells —
   `run_eval.py` resumes from the merged log.
3. **Data missing ⇒ the experiment runs.** Model calls hit `cache/` (`src.llm`
   memoises on model, temperature, max_tokens, nonce and prompt); a draw the
   cache lacks is bought from the real model and cached. The oracle is never
   cached and needs `external/ConDefects/Test/`.
4. **Both proposers run, each in its own run directory**, configured by the
   environment; the defaults reproduce the paper.

Two reproduction regimes follow. (a) **With the artifacts** — the run data and
the response cache — nothing is re-run and every number is bit-identical to the
committed one. (b) **Without them** the study re-runs; the proposer is sampled
at temperature 1.0, so any draw the cache does not hold is a new draw, and the
results are statistically similar to the paper's, not identical. Only data +
cache give identical numbers.

---

## 2. Environment

All variables are read by `scripts/reproduce.sh` from the shell (it is bash and
does not read `.env`). `.env.example` lists them beside the python-side keys.

| variable | default | meaning |
|---|---|---|
| `ARTIFACTS` | *(none)* | URL, Google Drive share link, `artifacts.zip` path or unpacked directory; stage 1 |
| `ARTIFACTS_SHA256` | *(none)* | expected digest of the archive |
| `ARTIFACTS_FORCE` | `0` | `1` = overwrite existing files on install |
| `PROPOSERS` | `local cloud` | which proposer blocks run |
| `LOCAL_RUN_DIR` | `official-2026-09-01` | `data/<run>/`, `logs/<run>/` of the local proposer |
| `LOCAL_MODEL` | `qwen2.5-coder:7b` | model id; in every cell key |
| `LOCAL_CONTEXT_LENGTH` | `32768` | served window, pinned and verified by `eval_shard.sh` |
| `LOCAL_BACKEND` | `ollama` | `ollama` starts and verifies a local server; `cloud` uses an endpoint |
| `PORT` | `11435` | the local server's port (not the desktop app's) |
| `CLOUD_RUN_DIR` | `gpto4mini-2026-09-06` | run directory of the second proposer |
| `CLOUD_MODEL` | `gpt-4o-mini` | |
| `CLOUD_CONTEXT_LENGTH` | `200000` | client-side window (`src.llm` refuses an over-long prompt before paying). 200000 rather than gpt-4o-mini's 128k because the shipped cloud shards were collected with that value and `consolidate_evals.py` refuses to pool a shard whose recorded `context_length` differs; no prompt in this study comes near either figure |
| `CLOUD_BACKEND` | `cloud` | |
| `LLM_API_KEY`, `LLM_BASE_URL` | *(none)* | required only when a cloud cell must run (`LLM_BASE_URL` empty = api.openai.com) |
| `PRICE_IN_PER_MTOK`, `PRICE_OUT_PER_MTOK`, `BUDGET_USD_CAP` | `0.15`, `0.60`, `25` | the cloud rate card (gpt-4o-mini's) and per-process spending cap; passed to the cloud shards only. The shipped cloud metas record the o4-mini card (1.10/4.40) although the run is gpt-4o-mini: prices are ledger metadata, not merge-blocking |
| `SHARDS` | `1` | contiguous ranges each preset's universe is split into (run sequentially) |
| `RECOMPUTE_HEAVY` | `0` | `1` = recompute Tier-B artifacts even when shipped (needs `Test/`, hours) |
| `FILL_DECLARED_GAPS` | `0` | `1` = also run cells a run's `grid_coverage.json` declares missing (§4.3) |
| `KNOWN_DIVERGENT_TASKS` | `abc285_e/48880084` | passed to `check_consistency.py --allow-divergent`: this fault's candidate patches sit at the sandbox-timeout edge, so E1 and the guarded arms disagree on success@B for two of its seeds — reported and excluded in the paper's sensitivity analysis, not a guard bug; a divergence on any other task still fails the check |
| `PAPER_TARGETS` | `main` | `main`, or `main saner fse` for the venue builds too |
| `CONDEFECTS_ROOT`, `CONDEFECTS_TEST_DIR` | `external/ConDefects`, `<root>/Test` | the benchmark checkout and its test data |

---

## 3. The stages

### Stage 0 — `env`

Reads nothing that a run produces. Creates `.venv` and installs
`requirements.txt` if the imports fail; clones the ConDefects *code*
(`scripts/fetch_condefects.py`, 125 MB — `fit_theory`, `measure_anchoring` and
`measure_patch_quality` read the source files) if `external/ConDefects/Code` is
absent; reports whether `Test/` and TeX (`latexmk`, `pdflatex`) are present.
Neither is required for a run from the shipped artifacts; `Test/` is required to
run any missing cell or to recompute a Tier-B artifact, and TeX for stage 7
(skipped with a warning otherwise).

### Stage 1 — `artifacts` (only if `ARTIFACTS` is set)

`python3 scripts/fetch_artifacts.py --source $ARTIFACTS [--sha256 ..] [--force]`:
download (https, or a Google Drive share link — the "cannot scan for viruses"
confirmation is followed automatically), verify the digest if given, unpack into
`artifacts/`, check every file against `artifacts/MANIFEST.json`, then install:

```
artifacts/cache/**        →  ./cache/            (the model-response cache)
artifacts/data/<run>/**   →  ./data/<run>/
artifacts/logs/<run>/**   →  ./logs/<run>/
```

A file that already exists is never overwritten (`ARTIFACTS_FORCE=1` overrides).
Idempotent: a second run installs nothing and says so. `--verify [--installed]`
re-checks the hashes later. Layout: §7.

### Stage 2 — `corpus` (per proposer)

Reads `data/<run>/tasks.json`, `pool/tasks.json`, `pool/oracle_validation.json`,
`candidates.json`. All four present ⇒ **hit**. Otherwise, for the local run:

```bash
RUN_DIR=<run> bash scripts/pipeline.sh candidates   # Stage 0 of the study: the candidate list
RUN_DIR=<run> bash scripts/pipeline.sh gate         # E0: the oracle gate, freezes the pool (hours)
RUN_DIR=<run> bash scripts/pipeline.sh corpus       # freezes the corpus
```

(each skipped when its artifact exists; needs `Test/`). For the cloud run the
corpus is **inherited**: the second proposer runs on the local run's frozen
corpus and banding — the same 99 tasks, the same pool, the same candidate list —
so the four files are copied from `data/$LOCAL_RUN_DIR/`. A corpus built from
scratch is a *new* corpus (a different draw of faults); the paper's corpus ships
in the artifacts.

### Stage 3 — `grid` (per proposer)

For every preset of the proposer (`scripts/presets.py`: `LOCAL_PRESETS`,
`CLOUD_PRESETS`):

```bash
python3 scripts/grid_status.py --run-dir <run> --all-presets local|cloud --model <model> --shards $SHARDS --json
```

prints expected / complete / missing cells per preset. Expected cells are
universe × modes × seeds with the preset's flags folded into the cell key;
complete cells come from the merged `episodes.jsonl` under `run_eval.py`'s rule.
The universe lists (`eval_order.txt`, `sweep_programs.txt`, `trial_programs.txt`)
are generated from `tasks.json` exactly as `eval_shard.sh` generates them when
absent (`scripts/universes.py`), `live_programs.txt` through
`scripts/build_live_universe.py`; a list already on disk is digest-checked, never
rewritten.

* missing = 0 ⇒ **hit**: nothing is started, no server.
* missing > 0 ⇒ for each of the `SHARDS` ranges that still holds a missing cell:

  ```bash
  RUN_DIR=<run> CONTEXT_LENGTH=<ctx> [PORT=..] bash scripts/eval_shard.sh --exp <P> --backend <ollama|cloud> --model <M> --from a --to b
  RUN_DIR=<run> python3 scripts/consolidate_evals.py --episodes data/<run>/episodes.jsonl data/<run>/episodes_eval_*.jsonl
  ```

  The merged file is listed first so nothing already in it is lost; if no shard
  log was produced the merge is skipped. Then the status is recomputed and must
  show nothing missing. Running a cell needs `Test/`, plus `ollama` on `PATH`
  (local) or `LLM_API_KEY` (cloud); the pipeline checks both before starting
  anything.

`grid_status.py` exits 0 when nothing is missing, 3 when cells are missing, and
can be run by hand at any time (`-v` lists the cells).

### Stage 4 — `analyse` (per proposer)

The order that works — the freeze needs the strata, `fit_theory` needs the
frozen results, `build_strata` needs `fit_theory`:

```bash
RUN_DIR=<run> python3 scripts/freeze_results.py --experiment main --force   # first pass, no strata yet
RUN_DIR=<run> python3 scripts/fit_theory.py                                  # theory_fit.json (E1's pi_hat)
RUN_DIR=<run> python3 scripts/build_strata.py --force                        # reported banding from E1
RUN_DIR=<run> python3 scripts/freeze_results.py --experiment main --force   # stamps the stratum
RUN_DIR=<run> python3 scripts/analyze.py
RUN_DIR=<run> python3 scripts/fit_theory.py
RUN_DIR=<run> python3 scripts/measure_anchoring.py
RUN_DIR=<run> python3 scripts/measure_redundancy.py
RUN_DIR=<run> python3 scripts/measure_patch_quality.py
RUN_DIR=<run> python3 scripts/measure_coherence.py       # Tier B: hit if coherence_report.json exists
RUN_DIR=<run> python3 scripts/measure_pool_strength.py   # Tier B, local only: hit if pool_strength.json exists
RUN_DIR=<run> python3 scripts/check_consistency.py --allow-divergent $KNOWN_DIVERGENT_TASKS   # must pass
```

Three tiers of artifact under `data/<run>/`:

| tier | files | cost | policy |
|---|---|---|---|
| A | `episodes.jsonl`, `overfit_checks.jsonl`, `calls.jsonl`, `eval_shards/*.meta.json` | model calls, GPU-days | shipped; hit per cell (stage 3) |
| B | `pool/oracle_validation.json`, `pool_strength.json`, `coherence_report.json`, `verdicts.jsonl`, `verdicts_cases.json` | oracle only, hours of sandbox time | shipped; hit if present unless `RECOMPUTE_HEAVY=1` |
| C | `results_real.json`, `theory_fit.json`, `strata.json`, `analysis.json`, `anchoring.json`, `redundancy.json`, `patch_quality.json`, `failure_taxonomy.json`, `policies.json`, `policies_cells.json` | seconds | never shipped; always recomputed |

`check_consistency.py` rebuilds every frozen file from `episodes.jsonl` and
deep-diffs it against what is on disk, so a reported number cannot drift from
the artifact that produced it. It must pass; the stage fails otherwise.

### Stage 5 — `policies` (local only)

```bash
RUN_DIR=<run> python3 scripts/build_verdict_matrix.py   # Tier B: hit if verdicts.jsonl + verdicts_cases.json exist
RUN_DIR=<run> python3 scripts/simulate_policies.py      # Tier C: policies.json, policies_cells.json
python3 scripts/verify_policies.py data/<run>            # must pass
```

The matrix replays every logged no-memory candidate against every usable case
of its task (CPU-hours; needs `Test/`); the simulation and the verifier run in
seconds and use the standard library only.

### Stage 6 — `numbers`

```bash
make -C paper/common regen NUMBERS_OUT=<tmp> RUN=data/$LOCAL_RUN_DIR CLOUD_RUN=data/$CLOUD_RUN_DIR
python3 paper/common/tools/check_numbers.py --regenerated <tmp> --committed paper/common [--update]
```

`regen` runs `extract_numbers → fix_numbers → figdata → review_addenda →
corpus_addenda → related_addenda → review3_addenda` on the local run and
`second_proposer.py` on both runs. Identical ⇒ nothing under `paper/common` is
written; the committed files stay the reference byte for byte, so a
reproduction leaves git clean. Different ⇒ the run fails and the temporary
directory is kept for inspection; with `--update-numbers` the regenerated files
become the new reference. Identical means equal within `rel_tol 1e-6`
(provenance paths ignored, `NaN == NaN`): a different platform or Python version
may move a bootstrap endpoint in its last bit, and the tools use `math.fsum` and
tie tolerances in permutation tests so that no *reported* digit does. By hand:
`make -C paper/common check-numbers` (the same comparison) and `make -C
paper/common numbers` (regenerate in place).

### Stage 7 — `paper`

```bash
python3 paper/common/tools/make_figures.py paper/common    # figures/*.pdf from numbers.json + figdata.json
make -C paper/common                                        # main.pdf
cp paper/common/main.pdf paper/main.pdf
make -C paper/saner2027 ; make -C paper/fse2027             # PAPER_TARGETS="main saner fse"
```

Skipped with a loud warning when TeX is absent. `paper/main.pdf` is the
reference build and stays tracked; every other PDF under `paper/` is a build
product.

---

## 4. The grid in detail

### 4.1 The presets

`scripts/presets.py` is the single table (`python3 scripts/presets.py --list`).
`eval_shard.sh` reads it (`--shell`) and `grid_status.py` reads it, so a shard and
the status tool cannot disagree about what an experiment *is*.

| `--exp` | modes | flags | universe | seeds |
|---|---|---|---|---|
| `E1` | no_memory | `--force-full-budget` | corpus (99) | 1–5 |
| `E2` | untyped typed | `--check-overfit` | corpus | 1–5 |
| `E3-guard-only` | typed | `--steer off` | corpus | 1–3 |
| `E3-steer-only` | typed | `--guard off` | corpus | 1–3 |
| `E4-k20` `E4-k8` `E4-k3` | typed | `--max-examples K --check-overfit` | sweep (30) | 1–3 |
| `E5-c90` `E5-c75` `E5-c50` `E5-c25` `E5-c00` | typed | `--typing-noise-c C` | sweep | 1–3 |
| `E5-random` | typed | `--typing-random` | sweep | 1–3 |
| `E8-corpus` | untyped typed | `--audit-guarded` | corpus | 1–3 |
| `E9-freeguard` | no_memory untyped typed | `--free-guarded-rounds --free-guard-draw-cap 3 --check-overfit` | live (24) | 1–3 |
| `trial` | all three | `--check-overfit`, B=5 | trial (3) | 1 |

`E8-audit`, `E10-chat`, `E11-selftest`, `E11b-selftest-guard` and `E12-randskip`
exist in the table but are not part of the reported grid. The cloud proposer runs
`E1 E2 E3-steer-only`; its E2 was collected *without* `--check-regression`, and
the preset must not gain that flag. Budget is 20 everywhere but the trial.

### 4.2 Cell identity and completion

A cell is `run_eval.py::cell_key(task, mode, seed, guard_on, steer_on,
max_examples, typing_noise_c, force_full_budget, model, granularity,
audit_guarded, reasoning_effort, typing_random, free_guarded_rounds, history,
selftest, oracle_skip_p)`. `model` is in the key, so the two proposers never
pool; the *universe* is deliberately not — the same task/mode/seed is the same
cell whichever list named it. A cell is complete when its rounds reach the
budget, or one accepted and the arm may stop early (`force_full_budget` off).
`--check-overfit` and `--check-regression` are post-episode audits and not in the
key, so they can be switched on without invalidating finished cells.

### 4.3 Declared gaps — the shipped E9-freeguard grid

`scripts/build_artifacts.py` refuses to pack a run whose presets are not
complete — unless a preset is named with `--allow-partial`, in which case the
missing cells are written to `data/<run>/grid_coverage.json`. `grid_status.py`
reports those cells as *declared* and does not count them (exit 0), so a
reproduction from the artifacts rebuilds the paper at the coverage the paper
reports instead of extending the grid underneath it.

The shipped local run has **169 of the 216 `E9-freeguard` cells**: the shards
`E9-freeguard_001_004` and `005_008` stopped early (45 cells never ran, on six of
the 24 live tasks) and two more cells are truncated (stopped before accepting or
reaching the budget). The paper reports the free-guarded result on the cells
present and as exploratory (`tables/freeguard.tex`: 60/57/54 cells per arm). With
the artifacts the 47 cells are declared in
`data/official-2026-09-01/grid_coverage.json` and are **not** run.
`FILL_DECLARED_GAPS=1` (`grid_status.py --strict`), or a run from scratch,
completes the grid; `tables/freeguard.tex` and `addenda.free_guarded` then move,
and the numbers check fails until `--update-numbers` is used deliberately.

### 4.4 Running a shard yourself

```bash
RUN_DIR=official-2026-09-01 bash scripts/eval_shard.sh --exp E1 --dry-run          # the plan, the shard list
RUN_DIR=official-2026-09-01 bash scripts/eval_shard.sh --exp E1 --from 1 --to 30    # one shard
RUN_DIR=official-2026-09-01 bash scripts/eval_shard.sh --exp E2                     # the whole universe
RUN_DIR=official-2026-09-01 python3 scripts/consolidate_evals.py                    # merge the shards
```

`eval_shard.sh` writes, for positions FROM..TO of the preset's universe:
`data/<run>/eval_shards/<tag>.txt` (the program list with digests),
`<tag>.meta.json` (the protocol record), `episodes_eval_<tag>.jsonl`,
`overfit_eval_<tag>.jsonl`, `calls_eval_<tag>.jsonl`, and `logs/<run>/eval_<tag>.log`.
The model id is folded into the tag whenever it is not `qwen2.5-coder:7b`
(`E2_gpt-4o-mini_001_040`), so two proposers never append to one file. The cloud
path needs `LLM_API_KEY`, `PRICE_IN_PER_MTOK`, `PRICE_OUT_PER_MTOK`,
`BUDGET_USD_CAP` and a `CONTEXT_LENGTH` other than the local default — each
refused up front because none is recoverable afterwards. `BUDGET_USD_CAP` is per
process: N parallel shards need total/N each.

**Shards must not overlap, and a shard list is never hand-trimmed** to skip
finished work: `--resume-from data/<run>/episodes.jsonl` (the default) skips a
cell any earlier shard finished, correctly and for free. Run the identical
command again after an interruption; the finished part replays from cache in
seconds.

### 4.5 Several machines: `SHARDS` and `fleet.sh`

`SHARDS=N bash scripts/reproduce.sh --stage grid` runs the N ranges of every
incomplete preset one after the other on one machine. On several machines, run
`eval_shard.sh` with disjoint `--from/--to` ranges on each, copy the
`episodes_eval_*.jsonl`, `overfit_eval_*.jsonl`, `calls_eval_*.jsonl` and
`eval_shards/*.meta.json` files into one `data/<run>/`, and consolidate there.
`cache/` needs no syncing between machines: it is content-addressed and disjoint
shards have disjoint caches (only the draws E1 shares with the unconditioned arms
are bought twice).

```bash
bash scripts/fleet.sh eval --exp E1 --shards 6     # launches six shards in the background
bash scripts/fleet.sh status ; bash scripts/fleet.sh wait
```

`fleet.sh` cuts a range into N contiguous shards, forces `--no-stop-model` so the
first shard to finish does not unload the weights under the other five, divides
`BUDGET_USD_CAP` by N on the cloud path, and does the single unload at the end.
Model calls queue at the server (`OLLAMA_NUM_PARALLEL=1`); what overlaps is the
oracle's sandbox work, so four to six shards is the useful range on a Colab T4.

### 4.6 What the merge audit catches

`consolidate_evals.py` merges last-write-wins per `(episode_id, round_index)`
and refuses to merge when the instrument differs:

| | what it means |
|---|---|
| protocol disagreement | a shard ran under a different `model` or `granularity` — both are in the cell key, so those rows would never pool. **Hard stop** |
| runtime disagreement | the shards' `.meta.json` differ on the served context window, model digest, temperature, sandbox timeout, backend or reasoning effort. None reaches the cache, so those shards re-judged each other's draws against a different instrument. **Hard stop** |
| no protocol record | a shard log without `.meta.json` — a hand-run `run_eval.py`. Reported, not fatal |
| `GAPS` / `TRUNCATED` | (task, seed) cells missing from an arm; episodes that neither accepted nor reached the budget |
| `DISAGREEMENT` | the same (episode, round) collected twice with different results — usually `SANDBOX_TIMEOUT_SEC` firing on a slower machine. Fix and re-run both; do not pick a winner |
| `FOREIGN` | a task not in the universe — a shard cut from a different corpus |

---

## 5. The protocol contract

Every stage that calls a model shares these values. They are not preferences:
each changes what the measured quantities *are*, and a shard measured under a
different one is a different instrument, not a noisier reading of the same one.

```
MODEL=qwen2.5-coder:7b   TEMPERATURE=1.0      CONTEXT_LENGTH=32768   GRANULARITY=fine
MAX_EXAMPLES=100         SANDBOX_TIMEOUT_SEC=30.0   BUDGET B=20   SEEDS 1..5 (E1,E2) · 1..3 (others)
```

`eval_shard.sh` pins all of them and exports them over `.env`, so a machine
whose `.env` has drifted still measures the same thing. They split in two:

**In `src.llm`'s cache key** — `model`, `temperature`, the prompt, `max_tokens`
and the draw nonce (`reasoning_effort` when set). Change one and every
completion already bought becomes unreachable: expensive, but self-announcing.

**Not in the cache key — the dangerous half.** `max_examples` re-judges the same
completions against a weaker oracle; `sandbox_timeout_sec` turns a slow correct
patch into a wrong one; the served context window decides whether the prompt
arrived whole. All three move the numbers for free and leave no trace in the
cache, so all three are recorded in every shard's `.meta.json` and the merge
refuses to join across a disagreement.

**The context window.** Ollama picks it from available VRAM and *truncates* an
over-long prompt instead of refusing it; the OpenAI-compatible endpoint has no
field to raise it. `serve_local.sh` therefore pins `OLLAMA_CONTEXT_LENGTH`, loads
the model, asks `/api/ps` what is actually served and refuses to spend on a
mismatch; `LLM_CONTEXT_TOKENS` is the client-side half, turning a prompt that
would not fit into a recorded `context_overflow` rather than a truncated answer.
On the cloud path the window check is replaced by that client-side refusal,
which is why `CONTEXT_LENGTH` must be the hosted model's real window.

**Nonces, and why the arms are paired.** A proposal draw is nonced
`<task>|seed<S>|r<round>` — not on mode or the ablation flags. Every arm's round 1
has an empty history and an identical prompt, so all conditions share that
completion: the arms are paired on common random numbers. The unconditioned arms
(`no_memory`, `untyped`, `guard-only`) share every draw, so once E1 has run they
cost no model calls, and their `success@B` must equal E1's — a gap is a
guard-soundness bug, not a finding. E1 runs with `--force-full-budget`, which is
what makes it an estimator of π rather than a baseline. `--free-guarded-rounds`
(E9) is in the cell key — success@B under it is a curve of a different B — and
the reason a guarded round *charged* to the budget cannot change an outcome at
all is recorded in `docs/DIAGNOSIS.md`.

**The oracle is not cached.** Re-running a finished cell replays its model calls
free but re-executes every candidate against the test pool, which is most of the
wall clock. That is what the cell-level resume is for.

---

## 6. Adding a proposer

1. Pick a run directory and a model id: `CLOUD_RUN_DIR=<run> CLOUD_MODEL=<id>`
   (or a third block by hand with `eval_shard.sh --model <id>`). `model` is in
   the cell key and in the shard tag, so nothing pools with the existing runs.
2. Inherit the corpus (stage 2 does this for the cloud block) — the bands then
   describe the *local* model's difficulty and the write-up must say so. A
   proposer with its own banding needs its own π screen
   (`scripts/screen_shard.sh --backend cloud --model <id> --out ...`,
   `scripts/consolidate_screens.py`) and its own `select_corpus.py` run.
   `scripts/build_second_proposer_universe.py` draws the `hardend` universe (the
   corpus minus the easy bands) and `scripts/second_proposer_gate.py` is the
   declared gate between E1 and the arms that cost money.
3. Set the rate card and cap (`PRICE_*`, `BUDGET_USD_CAP`) and the context
   window the run will be collected under — it is recorded in every shard's
   meta and must stay the same for the life of the run; for o-series models add
   `--reasoning-effort` (in the cache key, the cell key and the meta record).
4. Run the grid: `PROPOSERS=cloud bash scripts/reproduce.sh --stage grid`, or
   `eval_shard.sh` by hand, then consolidate. `grid_status.py --model <id>`
   reports coverage.
5. Add the presets the new proposer runs to `scripts/presets.py`
   (`PROPOSER_PRESETS`), teach `paper/common/tools/second_proposer.py` about the
   run, and let stage 6 tell you what changed.

---

## 7. Artifacts

```
artifacts/
  MANIFEST.json                     sha256, bytes, tier, producer stage of every file; git commit; date
  README.md                         layout and the coverage table at build time
  data/official-2026-09-01/         local run: corpus (tasks.json, pool/, candidates.json, screening.json),
                                    universe lists, Tier A (episodes.jsonl, overfit_checks.jsonl, calls.jsonl,
                                    eval_shards/*.meta.json), Tier B (pool_strength.json, coherence_report.json,
                                    verdicts.jsonl, verdicts_cases.json), grid_coverage.json (declared gaps)
  data/gpto4mini-2026-09-06/        cloud run: corpus, universe lists, Tier A, coherence_report.json
  logs/<run>/*.log                  shard traces
  cache/                            model-response cache - only with --cache (placed by hand otherwise)
```

Not shipped: per-shard episode/call/overfit logs (their content is in the merged
files), shard program lists (regenerated), and every Tier-C artifact.

```bash
python3 scripts/build_artifacts.py \
    --local-run /path/data/official-2026-09-01 --logs-local /path/logs/official-2026-09-01 \
    --cloud-run /path/data/gpto4mini-2026-09-06 --logs-cloud /path/logs/gpto4mini-2026-09-06 \
    [--cache /path/cache] --out artifacts/ --zip [--allow-partial E9-freeguard]
```

validates coverage first (every local preset on the local run, every cloud
preset on the cloud run, with `grid_status.py`'s logic), copies the tiers above,
writes `MANIFEST.json` and `README.md`, and with `--zip` writes `artifacts.zip`
beside the directory (about 7 MB for 130 MB of JSONL; its single top-level
directory is always `artifacts/`, whatever `--out` was named). A run may carry universe
lists it drew by hand (`demo_programs.txt`, `hardend_programs.txt`); they travel
too. The raw run directories may hold the shard logs of many overlapping shards
in several universes (the cloud run was collected in three) — none of that
matters here: coverage is read from the merged log.

`python3 scripts/fetch_artifacts.py --verify [--installed]` checks the hashes.

---

## 8. Colab

`notebooks/reproduce_colab.ipynb`: mount Drive, clone `promote-main`, `pip
install`, `fetch_condefects.py` plus `Test.zip` from a Drive path, an optional
Ollama install (`scripts/install_ollama_colab.sh`, GPU runtime) or an API key
cell, `ARTIFACTS` from a Drive path or URL, `reproduce.sh --stage artifacts`
then `--dry-run` (every preset should read `hit`), the one command, and a copy
of `paper/main.pdf` back to Drive. `cache/`, `data/` and `logs/` are symlinks
into Drive so a multi-session run survives. The same hit/skip rules apply; a
preset marked `RUN` needs the test data and a proposer before the run can finish.

---

## 9. When a check fails

**Stage 3 stops: cells are missing and `Test/` is absent.** The shipped log does
not cover the preset (or `FILL_DECLARED_GAPS=1` asked for the declared gaps).
Install the artifacts (`ARTIFACTS=...`), or unpack `Test.zip` and provide a
proposer, then re-run — only the missing cells run.

**Stage 3 stops: cells still missing after the run.** The shard did not reach
them — read `logs/<run>/eval_<tag>.log`. A `BUDGET CAP REACHED` or `MODEL SERVER
UNREACHABLE` line means a clean stop; re-running the identical command resumes.
A cell that hits the E9 draw cap without spending its budget stays incomplete by
construction and belongs in `grid_coverage.json`.

**`consolidate_evals.py` refuses to merge.** A new shard's `.meta.json` disagrees
with the shipped ones on `model`, `temperature`, `context_length`,
`sandbox_timeout_sec`, `granularity`, `backend` or `reasoning_effort` (the rate
card is recorded but not merge-blocking). Run the top-up under the protocol the
run was collected with — the shipped metas say what it was; for the cloud run
that is `CLOUD_CONTEXT_LENGTH=200000`, the default — rather than pooling two
instruments.

**`check_consistency.py` fails.** A Tier-C file on disk was not produced by the
code that is now checking it (a stale copy, or a metric definition changed).
Delete the Tier-C files of the run and re-run `--stage analyse`; if it still
fails, the code and the frozen artifact disagree and the paper cannot be trusted
until one is fixed. A `guard_soundness` divergence on a task other than
`KNOWN_DIVERGENT_TASKS` is a guard bug, not a timeout effect — do not add the
task to the list to make the check pass.

**`verify_policies.py` fails.** The policy table in the paper disagrees with the
replay over the shipped matrix; the message names the number. Same rule.

**Stage 6 fails: regenerated numbers differ.** The temporary directory is kept;
diff it against `paper/common/`. If the change is intended (a preset was
completed, a metric was corrected), re-run with `--update-numbers`, rebuild the
paper, and commit the JSONs together with the text that cites them. If it is not
intended, the regenerating tool or the run directory has changed — find which.

**`served context is N, not 32768`** — the server on `PORT` picked the window
itself. Stop it and let the shard script start its own.

**`eval_order.txt was cut from a different tasks.json`** — the corpus was
re-frozen after shards had started; every index now means a different task.
Move the old lists and episode logs aside deliberately, or restore the corpus
they belong to.

**Every cell re-runs instead of skipping** — something in the cell key moved,
in practice `model`: a run that bypassed `eval_shard.sh` picked up a different
id. Check the `model` field of the episode log.

**`BudgetExceeded` on the local path** — local calls are priced at zero, so the
cap (a tripwire at $1) can only fire if the client was repointed at a paid
endpoint. Find out what did it.

**`context_overflow` in a round's `proposal_error`** — a prompt exceeded
`LLM_CONTEXT_TOKENS` and the client-side guard refused it; the round is recorded
as spent-but-inconclusive. Count them and report the count; do not raise the
window to make them go away.

---

## 10. Tests

```bash
pytest -q                         # everything under tests/
python3 tests/test_presets.py     # presets.py agrees with eval_shard.sh --dry-run for every preset
python3 tests/test_grid_status.py # a synthetic log with one incomplete and one absent cell
python3 tests/test_reproduce_plan.py  # reproduce.sh --dry-run parses and prints the summary
```

None of the tests needs a model, the benchmark or an API key.
