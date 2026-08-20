# Experiment — running E1–E5 in shards

Operational runbook for steps 6–9 of [PLAN.md](PLAN.md): the no-memory arm, the
two memory arms, the guard/steer ablation, and the ρ and c sweeps. PLAN.md says
*which paper claim each step serves and why it exists*; this file is only how to
run it. [SCREENING.md](SCREENING.md) is the sibling runbook for E0b, and
[CORPUS.md](CORPUS.md) for the pool and the freeze.

This checkout runs **entirely on a local proposer**, and `.env` is already that
profile — no per-command prefixes, no key. The grid is tens of thousands of model
calls and days of wall clock, so it is cut into index ranges over the corpus and
run in shards, exactly as the screen was.

| | |
|---|---|
| `scripts/eval_shard.sh` | **one shard of one experiment**: starts the server, verifies it, runs the grid, tears it down |
| `scripts/consolidate_evals.py` | merges the shard logs into `data/episodes.jsonl` and audits the join |
| `scripts/serve_local.sh` | the server on its own — start / verify / unload / stop |
| `scripts/run_eval.py` | the driver underneath; every one of E1–E5 is this grid with different flags |
| `scripts/watch_eval.sh` | progress, rate, ETA off a shard log |
| `scripts/summarize.py` | per-arm means straight from an episode log, no freeze needed |
| `.env` | the client half of the protocol; already `qwen2.5-coder:7b` on `127.0.0.1:11435` |

> **Your shell splits variables differently from the scripts.** zsh — the
> interactive shell here — word-splits an unquoted `$(...)` but **not** an
> unquoted `$VAR`, so `--programs $SWEEP` arrives at argparse as one 24-name
> string and the driver rejects it as a single unknown program. Nothing in this
> file passes a program list through a variable: lists live in files, and
> `--programs-from` / `--sweep-programs-from` read them. That is also why
> `eval_shard.sh` exists rather than a paragraph of copy-paste.

---

## 0. What must already be true

This file starts at E1. Four stages come before it, each with its own runbook,
and E1 rests on all of them. `bash scripts/pipeline.sh` reports which have run;
the block below is the check worth doing by hand before committing days of
compute. None of it costs anything.

| stage | what it earns | runbook | artifact |
|---|---|---|---|
| **Stage 0** — candidates | the fault list every later index range is cut from | [SELECTION.md](SELECTION.md) | `data/candidates.json` |
| **E0** — the oracle gate | Assumption 1: the oracle actually refutes wrong patches | [CORPUS.md](CORPUS.md) | `data/pool/tasks.json`, `data/pool/oracle_validation.json` |
| **E0b** — the π̂ screen | the difficulty axis §5 stratifies on | [SCREENING.md](SCREENING.md) | `data/screen_merged.json` |
| **freeze the corpus** | the task list E1–E5 walk | [CORPUS.md](CORPUS.md) | `data/tasks.json`, `data/screening.json` |

```bash
python3 - <<'PY'
import json
pool   = json.load(open('data/pool/tasks.json'))
screen = json.load(open('data/screen_merged.json'))
corpus = json.load(open('data/tasks.json'))
print(f"E0    pool frozen={pool['frozen']}  {pool['n_cohort_passing']}/{pool['n_cohort']} passed the gate")
print(f"E0b   screen complete={screen['complete']}  model={screen['model']}  "
      f"K={screen['min_calls_per_program']}  pi_hat={screen['pi_hat_pooled']:.3f}")
print(f"corpus frozen={corpus['frozen']}  {corpus['n_selected']} tasks  "
      f"model={corpus['model']}")
assert screen['model'] == corpus['model'], "the corpus was banded under a different model than the screen measured"
PY
```

The assertion at the end is the one that matters, and it is the reason E0b is
listed here rather than assumed: **π is a property of the model**. The bands in
`data/tasks.json` mean something only if E1–E5 run under the same proposer the
screen measured, which is what §1 pins and `eval_shard.sh` enforces.

E0 needs no model at all — it is CPU-only — so if you ever need to re-run it (a
new `Test/` unpack, a different `--min-siblings`), it does not go through this
file's machinery. The gate is what earns the paper's Assumption 1: the paper
takes oracle soundness as given, and on a real benchmark it has to be
demonstrated before anything is spent through it.

One no-model step belongs to this file rather than to CORPUS.md, because it
measures the frozen corpus rather than selecting it: the oracle's blind spot,
§4.2.

---

## 1. The protocol contract

```
MODEL=qwen2.5-coder:7b   TEMPERATURE=1.0        CONTEXT_LENGTH=32768
MAX_EXAMPLES=100         SANDBOX_TIMEOUT_SEC=30.0   GRANULARITY=fine
BUDGET=20                SEEDS=1..5 (E1, E2) · 1..3 (E3, E4, E5)
```

`eval_shard.sh` pins every one of these and exports them over `.env`, so a shard
run on a machine whose `.env` has drifted still measures the same thing. The
first line is the screen's own protocol, unchanged, and it has to be: the corpus
is stratified on π̂ measured under exactly these values, and **π is a property of
the model** — a corpus banded under `qwen2.5-coder:7b` is not banded for anything
else. `data/tasks.json` records the model it was frozen under.

The values split the same way they do in [SCREENING.md](SCREENING.md) §1, and the
difference decides what a mistake costs.

**In `src.llm`'s cache key** — `model`, `temperature`, the prompt, and the draw
nonce. Change one and every completion already bought becomes unreachable:
expensive, but self-announcing, since you watch it re-buy.

**Not in the cache key — the dangerous half.** `max_examples` re-judges the very
same completions against a weaker oracle. `sandbox_timeout_sec` turns a slow
correct patch into a wrong one. The served context window decides whether the
prompt arrived whole or had its head cropped. All three move the numbers for free
and leave no trace. The first two are in `run_eval.py`'s cell key and in every
`RoundRecord`; the third is what the `/api/ps` assertion exists to catch.

### Nonces, and why the arms are paired

A proposal draw is nonced `<task>|seed<S>|r<round>` — deliberately *not* on mode
or the ablation flags (`src/loop.py::proposal_nonce`). Every arm's round 1 has an
empty history and therefore a byte-identical prompt, so all three conditions
share that one completion: the arms are paired on common random numbers instead
of buying the same answer three times. The moment the evidence and exclusion
blocks appear the prompts diverge, and the prompt is itself in the cache key, so
nothing is shared that should not be.

### What each arm puts in the prompt

Memory reaches the proposer only through typing. Three of the five arms
therefore build a byte-identical prompt, and differ only in which guard runs:

| arm | prompt | guard |
|---|---|---|
| `no_memory` | unconditioned | none |
| `untyped` | **unconditioned** | flat scan of every stored counterexample |
| `E3-guard-only` | **unconditioned** | type-indexed bucket |
| `E3-steer-only` | typed classes + exclusion | none |
| `typed` | typed classes + exclusion | type-indexed bucket |

That is the paper's own factorisation (§3.3, §5's baseline definitions,
Table 4), and it buys two things. The three unconditioned arms share cache
entries under the same draw nonce, so **`untyped` and `guard-only` cost no model
calls once E1 has run**. And their `success@B` must equal `no_memory`'s exactly —
a guard only blocks candidates that provably fail a stored counterexample, and a
correct patch fails none. A gap is a guard-soundness bug, not a finding; check
`src/memory.py::_still_refutes`, which also blocks on a sandbox timeout.

**The oracle is not cached at all.** Re-running a finished cell replays its model
calls for free but re-executes every candidate against the test pool, and that
sandbox time is most of the wall clock here. This is why `eval_shard.sh` passes
`--resume-from data/episodes.jsonl` by default: a cell any earlier shard finished
is skipped rather than re-walked.

---

## 2. What the corpus is, and the one thing to check before E1

`select_corpus.py` fills five bands on π̂ and writes `data/tasks.json`:

| stratum | π̂ band | role |
|---|---|---|
| `dead` | [0.00, 0.02) | control — B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | primary — largest predicted effect |
| `medium` | [0.08, 0.18) | primary |
| `easy` | [0.18, 0.35] | primary |
| `too_easy` | (0.35, 1.00] | control — predicted null, all arms coincide |

Each band takes `min(quota, available)`, so the corpus is as large as the screen
lets it be and no larger. The two control bands are kept, not discarded: an
effect that appears only where the mechanism has room to operate is stronger
evidence than a uniform one.

**Check the band counts before spending anything on E1.** π̂ lives on a grid of
1/K, where K is the depth the screen reached, so a band can only be filled if
some multiple of 1/K falls inside it. At K = 10, for instance, `0/10 = 0.000` is
`dead` and `1/10 = 0.100` is `medium` — nothing can land in [0.02, 0.08) at all,
and the `hard` band, where the paper predicts its largest effect (A₁₂ = 1.00),
comes out empty with nothing to say so.

```bash
python3 -c "
import json, collections
d = json.load(open('data/tasks.json'))
print(d['n_selected'], 'tasks;', dict(collections.Counter(t['stratum'] for t in d['tasks'])))
print('screen depth K =', d['selection']['min_calls'])"
```

An empty primary band is fixed by deepening the screen, not by proceeding:
[SCREENING.md](SCREENING.md) §5 computes the depth that puts three interior
points in `hard` and prints the per-task cost, and re-running a shard at a larger
`--calls` replays every draw already bought. **It has to happen before E1**, not
after: re-freezing the corpus once episodes exist invalidates them, and
`eval_shard.sh` refuses to cut a shard from a corpus whose digest no longer
matches the one its index order was built from.

Decide that first. Everything below assumes the corpus you intend to report.

### The two pre-registered deviations

From PLAN.md §0, restated so they are not rediscovered mid-run: **B = 20**, not
the paper's 10, because the primary metric is conditioned on accepting and at
B = 10 the [0.02, 0.08) band is barely estimable; and **5 seeds**, not 30, which
leaves the statistical protocol intact (the bootstrap resamples *tasks*) and is
reported as a threat to validity.

---

## 3. Sharding

A shard is a contiguous index range over that experiment's **universe** —
`data/eval_order.txt` (the whole frozen corpus) for E1/E2/E3,
`data/sweep_programs.txt` (six tasks per band) for E4/E5. Both files are written
by `eval_shard.sh` on first use, deterministically, with the corpus digest that
produced them in the header.

**The order is not `data/tasks.json`'s order.** That file is grouped by stratum —
twenty `dead`, then `medium`, then `easy`, then `too_easy` — so index ranges cut
from it would hand one machine every `dead` task (twenty rounds in every cell, by
construction) and another every `too_easy` one (usually one round). The shards
would differ ~10× in wall clock, and a run that finished three shards of four
would hold a *stratum-biased* grid rather than a smaller one. So each band is
spaced evenly over the whole order first, which leaves every prefix and every
suffix proportional. To see it for the corpus you actually froze:

```bash
python3 -c "
import json, collections
strat = {t['name']: t['stratum'] for t in json.load(open('data/tasks.json'))['tasks']}
order = [l.strip() for l in open('data/eval_order.txt') if l.strip() and not l.startswith('#')]
n = len(order)
for lo, hi in ((0, n//3), (n//3, 2*n//3), (2*n//3, n)):
    print(f'positions {lo+1:>3}-{hi:<3}', dict(collections.Counter(strat[t] for t in order[lo:hi])))"
```

Rules, the same three the screen has:

- **Shards must not overlap.** They may be re-cut between experiments, but two
  shards covering the same index range on two machines buy the same work twice.
- **Never hand-trim a shard to "skip what is done".** `--resume-from` does that
  correctly and for free; a trimmed range is how a cell goes missing from an
  otherwise complete arm.
- **One shard at a time per machine.** Two concurrent shards race on the response
  cache and on `data/episodes.jsonl`.

Each shard writes five files, all tagged with the experiment and the range:

```
data/eval_shards/<exp>_<from>_<to>.txt        the exact program list, with digests
data/eval_shards/<exp>_<from>_<to>.meta.json  the protocol, and the runtime as verified
data/episodes_eval_<exp>_<from>_<to>.jsonl    this shard's rounds
data/overfit_eval_<exp>_<from>_<to>.jsonl     this shard's overfit verdicts
data/calls_eval_<exp>_<from>_<to>.jsonl       this shard's call ledger
logs/eval_<exp>_<from>_<to>.log               the trace
```

The `.meta.json` is what makes a shard auditable rather than merely present. A
`RoundRecord` carries `model`, `granularity` and `max_examples`, but **not** the
served context window, the model digest, the temperature or the sandbox timeout —
which is exactly the half of §1 that moves the numbers while leaving no trace in
the response cache. `eval_shard.sh` writes them at the moment it verifies them,
and `consolidate_evals.py` refuses a merge across a disagreement. The screen has
the same contract, in `consolidate_screens.py`.

---

## 4. The run order

Everything here is free — the backend is local, and `.env` prices calls at zero.
What it costs is wall clock — days of it. §8 is how to project it for the
corpus you froze.

### 4.0 Once, before the first shard

```bash
cd ~/Study/research/ceg-mem && source .venv/bin/activate
```

`data/episodes.jsonl` must not already exist. It is the *merged* log
`consolidate_evals.py` writes, and every shard consults it for cells that are
already finished — so rows left over from an earlier run, a rehearsal or a
superseded corpus would be read as work already done. If one is there, move it
aside deliberately; `consolidate_evals.py` refuses to overwrite a log holding
rounds no shard accounts for, which is the same check after the fact.

Then plan a shard without starting anything. This writes the order files and the
shard list, and prints the protocol it would run under:

```bash
bash scripts/eval_shard.sh --exp E1 --from 1 --to 30 --dry-run
```

### 4.1 Verify the machine — trial, ~25 min

Not a smoke test of the model; a test of the *flags* and of the server. Three
tasks, one seed, B=5, over all three arms — on a log named so it can never be
merged into reported data, and the one preset that ignores the merged history: a
rehearsal that skips cells because another machine already ran them rehearses
nothing.

```bash
bash scripts/eval_shard.sh --exp trial
```

Four lines have to appear, and the fourth is the one that matters:

```
starting ollama on 127.0.0.1:11435 with OLLAMA_CONTEXT_LENGTH=32768
qwen2.5-coder:7b already present - nothing to download
loading qwen2.5-coder:7b and verifying the served window
  ok: {"backend": "ollama", "context_length": 32768, "model_digest": "dae161e27b0e90dd", ...}
```

`context_length` must read **32768** and `model_digest` must match the screen's
(`data/screen_merged.json` → `runtime`) — the same tag can point at a different
blob after a re-pull, and that is a different instrument.

Then check what came out:

```bash
python3 scripts/summarize.py --episodes-path data/episodes_trial.jsonl
ollama ps                                    # -> nothing loaded; the shard tore it down
lsof -i :11435 | wc -l                       # -> 0, the server is down
```

Three arms must come out with plausible numbers, and `no_memory` must show
`redundant_attempts` > 0 on at least one task — an arm that never repeats itself
is an arm whose memory is on when it should not be.

The trial covers the three conditions but not the ablation or sweep flags. Those
are one task each, and worth the ten minutes before a 28-hour shard depends on
them:

```bash
bash scripts/eval_shard.sh --exp E3-guard-only --from 1 --to 1 --seeds 1 --budget 5
bash scripts/eval_shard.sh --exp E3-steer-only --from 1 --to 1 --seeds 1 --budget 5
bash scripts/eval_shard.sh --exp E4-k3         --from 1 --to 1 --seeds 1 --budget 5
bash scripts/eval_shard.sh --exp E5-c50        --from 1 --to 1 --seeds 1 --budget 5
```

Guard-only must log `n_guarded` > 0 and steer-only exactly `n_guarded` = 0 — if
those two look alike, the ablation flags did not take. These four *do* write
mergeable logs, at B=5: the real shard later rewrites rounds 1–5 of the same
episodes and carries on to 20, so they cost nothing. If you never run the real
shard, `consolidate_evals.py` reports them as `TRUNCATED` rather than averaging
them in.

**Then the check that matters most.** Run the identical command again:

```bash
bash scripts/eval_shard.sh --exp trial
```

Every cell must print `already complete, skipping`, and it must finish in
seconds. Anything else means the resume key does not match the index — the bug
PLAN.md §1 records, which re-ran every cell at full price — and a multi-day grid
is the
wrong place to discover it.

Clear the rehearsal (the cached completions stay, and E1 replays them free):

```bash
rm -f data/episodes_trial.jsonl data/overfit_trial.jsonl data/calls_trial.jsonl
```

### 4.2 The oracle's blind spot (PLAN.md §5) · no model calls

The one free measurement still outstanding. Reads the frozen corpus, plants
mutants, drops nothing.

```bash
python3 scripts/measure_pool_strength.py --jobs 6      # -> data/pool_strength.json
```

Report its `equivalent` rate as an **upper bound** on invisible patch
overfitting: some share of any planted edit is semantically inert rather than
undetectable, and separating the two needs coverage data this run does not
collect.

### 4.3 The grid

Run the experiments **in order**, and each experiment's shards in any order —
across machines, or one after another on this one. Under `tmux`, or with
`nohup ... &`.

```bash
tmux new -s cegmem
```

```bash
# E1 - the no-memory arm, and the estimator for pi_hat/q_hat        ~54 h
bash scripts/eval_shard.sh --exp E1 --from  1 --to 30
bash scripts/eval_shard.sh --exp E1 --from 31 --to 60
bash scripts/eval_shard.sh --exp E1 --from 61 --to 85

# E2 - the memory arms                                             <=46 h
bash scripts/eval_shard.sh --exp E2 --from  1 --to 30
bash scripts/eval_shard.sh --exp E2 --from 31 --to 60
bash scripts/eval_shard.sh --exp E2 --from 61 --to 85

# E3 - the mechanism ablation                                      <=28 h
bash scripts/eval_shard.sh --exp E3-guard-only
bash scripts/eval_shard.sh --exp E3-steer-only

# E4 / E5 - the robustness sweeps, over the 24-task subset         <=23 h
bash scripts/eval_shard.sh --exp E4-k20
bash scripts/eval_shard.sh --exp E4-k8
bash scripts/eval_shard.sh --exp E4-k3
bash scripts/eval_shard.sh --exp E5-c90
bash scripts/eval_shard.sh --exp E5-c75
bash scripts/eval_shard.sh --exp E5-c50
```

Omit `--from/--to` and the shard is the whole universe — which is the right call
on one machine for the shorter experiments. Chain shards without reloading 4.7 GB
of weights each time:

```bash
bash scripts/eval_shard.sh --exp E1 --from  1 --to 30 --no-stop-model --keep-serving
bash scripts/eval_shard.sh --exp E1 --from 31 --to 60                 # tears down
```

What each preset actually runs, and why it is a preset rather than a flag you
retype per shard — a flag mistyped on shard 3 of 4 lands in a *different cell
key*, and analysis reports it as missing days later:

| `--exp` | `run_eval.py` flags | universe | seeds |
|---|---|---|---|
| `trial` | `--modes no_memory untyped typed --check-overfit`, B=5 | 3 tasks | 1 |
| `E1` | `--modes no_memory --force-full-budget` | corpus (85) | 1–5 |
| `E2` | `--modes untyped typed --check-overfit` | corpus | 1–5 |
| `E3-guard-only` | `--modes typed --steer off` | corpus | 1–3 |
| `E3-steer-only` | `--modes typed --guard off` | corpus | 1–3 |
| `E4-k20` `E4-k8` `E4-k3` | `--modes typed --max-examples K --check-overfit` | sweep (24) | 1–3 |
| `E5-c90` `E5-c75` `E5-c50` | `--modes typed --typing-noise-c C` | sweep | 1–3 |

Three of those deserve their reason stated once:

**E1's `--force-full-budget`** is what makes it an estimator rather than just a
baseline. The no-memory prompt carries no evidence and no exclusion block, so it
is byte-identical across all 20 rounds; every round is an independent draw of π,
and 5 seeds × 20 rounds gives **100 i.i.d. draws per task** against the screen's
10. `summarize.py` truncates the rounds back at the first accept, so the arm
stays comparable to the memory arms it is tabulated against. Never run
`no_memory` without it — that is a different cell, and `analyze.py` deliberately
refuses to pool the two.

**E3's names are the ablation, not the flag.** *Guard-only* means steering is
off (`--steer off`), *steering-only* means the guard is off (`--guard off`).
Predicted (Table 4): guard-only reproduces untyped's round savings but leaves
redundant attempts untouched; steering-only drives redundant attempts toward zero
and lifts budgeted success. On this implementation steering is an English
instruction rather than Eq. (3)'s renormalised support, so **non-repetition is a
measured outcome, not a theorem** — exactly zero is not guaranteed.

**E4 must be read off `is_truly_correct`, not off `accept`.** Lowering
`max_examples` weakens the oracle, so more wrong patches are accepted and the
apparent repair rate *rises*; read naively, the sweep concludes that a less
informative oracle repairs better. Plot the `data/overfit_checks.jsonl` series.
Neither sweep pays for its own baseline: E4's reference level is
`--max-examples 100` and E5's is `c = 1.0`, both the typed cell E2 already ran,
and the driver recognises the cell and skips it.

### 4.4 Merge the shards

After each experiment, or once at the end:

```bash
python3 scripts/consolidate_evals.py --dry-run     # audit only, writes nothing
python3 scripts/consolidate_evals.py               # -> data/episodes.jsonl
```

It concatenates every `data/episodes_eval_*.jsonl`, collapses on
`(episode_id, round_index)` — `src.metrics.load_rounds`'s own rule, so a cell
that died halfway and was re-run rewrites its rounds rather than appending a
second truncated episode — and merges the ledgers and overfit logs alongside.
`data/episodes_trial.jsonl` is deliberately not matched by that glob.

What the audit catches:

| | what it means |
|---|---|
| protocol disagreement | a shard ran under a different `model` or `granularity`. Both are in the cell key, so those rows would never pool with the rest — they are a second grid sharing one file. **Hard stop** |
| runtime disagreement | two shards' `.meta.json` differ on the served context window, the model digest, the temperature or the sandbox timeout. None of these reaches the cache, so the shards replayed each other's draws and re-judged them against a different instrument. **Hard stop** |
| no protocol record | a shard log with no `.meta.json` — produced by a hand-run `run_eval.py` rather than by `eval_shard.sh`, so what it ran under is unknown. Reported, not fatal |
| `GAPS` | (task, seed) cells missing from an arm, printed as index runs against the universe the shard was cut from — a shard that was killed, or never run |
| `TRUNCATED` | episodes that neither accepted nor reached the budget. The rows are real and every round-averaged estimator will happily average them, as a task that took fewer rounds than it did |
| `DISAGREEMENT` | the same (episode, round) collected twice with different results. Draws are cached under deterministic nonces and the loop is seeded, so this cannot happen unless the machines are not interchangeable — usually `SANDBOX_TIMEOUT_SEC` firing on the slower one. Fix and re-run both; do not pick a winner |
| `FOREIGN` | a task not in the universe at all — a shard cut from a different corpus |

`cache/` does **not** need syncing between machines. It is content-addressed, so
copying it is conflict-free, but shards are disjoint so their caches are too.
What *is* worth copying between machines is the merged `data/episodes.jsonl`,
because `--resume-from` reads it and every cell it names is a cell nobody has to
re-execute.

---

## 5. Watching a run, and resuming one

```bash
bash scripts/watch_eval.sh logs/eval_E1_001_030.log   # rate, cells done, ETA
python3 scripts/summarize.py --by-task                # arm means so far
tail -f logs/eval_E1_001_030.log
```

A run is resumable at round granularity: `src.loop.run_episode` appends one
`RoundRecord` per round as it goes, and the episode id is deterministic, so a
cell that died halfway rewrites its own rounds. Re-run the identical
`eval_shard.sh` command; the finished part replays from cache in seconds.

If the machine is rebooted mid-grid, just re-run the shard command — it brings
the server back up and re-verifies the window before doing anything else.

The server, by hand, when you want to chain several things against one warm copy:

```bash
bash scripts/serve_local.sh            # start (or adopt) and verify
bash scripts/serve_local.sh --check    # verify only
bash scripts/serve_local.sh --unload   # free the 4.7 GB, leave the server up
bash scripts/serve_local.sh --stop     # unload, and stop the server on the port
```

---

## 6. Analysis · no model calls

Order matters: the freeze needs the strata, `fit_theory` needs the frozen
results, and `build_strata`'s drift audit needs `fit_theory`.

```bash
python3 scripts/consolidate_evals.py                     # -> data/episodes.jsonl
python3 scripts/freeze_results.py --experiment main      # -> data/results_real.json
python3 scripts/analyze.py                               # -> data/analysis.json
python3 scripts/fit_theory.py                            # -> data/theory_fit.json
python3 scripts/build_strata.py --force                  # now with the drift audit
python3 scripts/measure_coherence.py                     # the c proxy, off E1
python3 scripts/measure_anchoring.py                     # -> data/anchoring.json
python3 figures/make_figures.py
python3 scripts/check_consistency.py

python3 scripts/freeze_results.py --experiment ablation
python3 scripts/freeze_results.py --experiment oracle_sweep \
        --sweep-programs-from data/sweep_programs.txt
python3 scripts/freeze_results.py --experiment typing_sweep \
        --sweep-programs-from data/sweep_programs.txt
```

`--sweep-programs-from` rather than `--sweep-programs`: the flag's own default is
"the first 30 frozen programs, sorted", which is **not** the stratified subset
E4/E5 ran over, and a shell that does not split the variable turns 24 names into
one. Both mistakes freeze the wrong tasks silently. The file is the same one
`eval_shard.sh` cut the sweep shards from.

`check_consistency.py` rebuilds every frozen file from `data/episodes.jsonl` and
deep-diffs it against what is on disk, so a reported number cannot drift from the
artifact that produced it. Run it last, and again before submission.

Optional and high-value, per PLAN.md §10: `scripts/label_tool.py` is the only
measurement in this repo that reaches real type coherence with human ground
truth, which §9 of the paper calls the single decisive open quantity.

```bash
python3 scripts/label_tool.py --annotator alice
python3 scripts/label_tool.py --annotator bob
python3 scripts/label_tool.py --compare alice bob
```

---

## 7. When something refuses

**`served context is 4096, not 32768`** — the server picked the window itself.
Stop whatever is on port 11435 and let the shard start its own. Do not work
around it: the prompt would be silently cropped, worst on the memory arms, which
is exactly the comparison being measured.

**`data/eval_order.txt was cut from a different data/tasks.json`** — the corpus
was re-frozen after shards had started. Every shard index now means a different
task, and episodes already collected were run against the old list. Move the old
order files and episode logs aside deliberately, or restore the corpus they
belong to; there is no merge that makes the two halves one grid.

**Every cell re-runs instead of skipping** — something in the cell key moved. The
key is `(task, mode, seed, guard, steer, max_examples, typing_noise_c,
force_full_budget, model, granularity)`. In practice it is `model`: a run that
bypasses `eval_shard.sh` and picks up a different id — `cegmem-qwen2.5-coder-7b`,
`gpt-4o-mini` — writes rows no later run will ever match. Check the `model` field
in the episode log before assuming the resume logic is broken.

**`unknown program(s): ['a b c']`** — the shell did not split the list. zsh
splits an unquoted `$(...)` but not an unquoted `$VAR`. Use `--programs-from`
with a file, which is what `eval_shard.sh` does.

**`BudgetExceeded`** — local calls are priced at zero, so this cannot fire on a
healthy run. The cap is left low on purpose as a tripwire: if it fires, something
has repointed the client at a paid endpoint mid-run. Find out what, rather than
raising the cap.

**`context_overflow` in a round's `proposal_error`** — a prompt exceeded
`LLM_CONTEXT_TOKENS`. That is the client-side guard doing its job: it turns a
would-be silent truncation into a refusal. Expect it, if at all, on a `dead`-band
task deep into a memory arm, where twenty rounds of accumulated evidence are in
the prompt — so the arms that hit it are the ones being measured.

`src/loop.py` records such a round as spent-but-inconclusive and carries on, the
same treatment a truncated response gets. It has to: uncaught, the exception
would abort the whole shard mid-grid, and because the prompt is deterministic
every re-run would abort at the identical round, so that shard could never get
past that task. Count them and report the count — it is a threat-to-validity
number for the memory arms specifically. Do **not** raise the window to make them
go away: that would make those tasks a different instrument from the rest.

```bash
python3 -c "
import json,collections
c=collections.Counter()
for l in open('data/episodes.jsonl'):
    r=json.loads(l)
    if r.get('proposal_error'): c[(r['mode'], r['proposal_error'])]+=1
print(c or 'none')"
```

**`data/tasks.json is not frozen` / `missing`** — the corpus freeze did not
complete. [CORPUS.md](CORPUS.md), not this file.

**Two arms look complete but were run under an older prompt** — the experiment
cell key covers the flags, the model and the granularity, but not the version of
`build_prompt`. Change what a mode puts in the prompt and the affected cells stay
"complete" to `--resume-from`, silently. There is no automatic detector: after
any change to `src/proposer.py`, delete the affected rows from the shard logs
(match on `mode`, `guard_on`, `steer_on`) before re-running, or re-run into a
fresh `--episodes-path`.

**`freeze_results.py` refuses a log holding two models** — `model` is in the
driver's cell key but not in the freeze's, so without that check episodes from
another proposer would silently satisfy expected cells and the frozen artifact
would never say which model produced its numbers. π is a property of the model:
two models in one freeze is two experiments reported as one. Move the foreign
rows aside (match on the `model` field) and re-run.

**`analyze.py` refuses to pool two `no_memory` arms** — one was run without
`--force-full-budget`. They are different cells, and averaging them would mix an
estimator with a baseline. Delete the wrong one's rows (match on `episode_id`)
and re-run.

---

## 8. Cost

Wall clock, not money — the backend is local and `.env` prices calls at zero.
Nothing parallelises within a shard; the sharding *is* the parallelism.

`select_corpus.py` prints the projection for the corpus it just froze (its
`--budget`, `--seeds-main`, `--seeds-abl` and `--sweep-size` flags are what that
projection is evaluated at), and the per-call rate to multiply it by is the one
your own screen measured:

```bash
python3 -c "
import json, statistics
sec = [json.loads(l)['sec'] for l in open('data/calls_screen.jsonl')]
print(f'{len(sec)} calls: median {statistics.median(sec):.1f}s, mean {statistics.mean(sec):.1f}s')"
```

Only E1's call count is exact — `--force-full-budget` runs every round of every
episode by construction, so it is `tasks × seeds × B`. The rest are upper bounds:
they stop at the first accept, and the per-task expectation
`E[rounds] = (1 − (1−π)^B)/π` is evaluated at the screen's π̂, whereas the memory
arms achieve a per-round rate `q ≥ π` whenever steering helps at all. If the
paper's mechanism works, those steps finish early — itself a weak signal worth
noticing in the logs.

Where the hours actually go: `dead`-band tasks burn the full budget in every
cell, by construction, and dominate the total out of proportion to their count.
They are kept because a control band where B binds hardest and memory grows
longest is where the guard's predicted advantage should be most visible — not
despite the cost, but for it.
