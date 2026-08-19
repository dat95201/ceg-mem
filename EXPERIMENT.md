# Experiment — running E1–E5 on the frozen corpus

Operational runbook for steps 6–9 of [PLAN.md](PLAN.md): the no-memory arm, the
two memory arms, the guard/steer ablation, and the ρ and c sweeps. PLAN.md says
*which paper claim each step serves and why it exists*; this file is only how to
run it. [SCREENING.md](SCREENING.md) is the sibling runbook for E0b, and
[CORPUS.md](CORPUS.md) for the pool and the freeze.

This checkout runs **entirely on a local proposer**. `.env` is already the local
profile — no per-command prefixes, no key.

| | |
|---|---|
| `.env` | the client half of the protocol; already set to `qwen2.5-coder:7b` on `127.0.0.1:11435` |
| `scripts/serve_local.sh` | brings the model up with the context window pinned **and verified** |
| `scripts/run_eval.py` | the one driver; every one of E1–E5 is this grid with different flags |
| `scripts/watch_eval.sh` | progress, rate, ETA off a driver log |
| `scripts/summarize.py` | per-arm means straight from an episode log, no freeze needed |

---

## 1. The protocol contract

```
MODEL=qwen2.5-coder:7b   TEMPERATURE=1.0        CONTEXT_LENGTH=32768
MAX_EXAMPLES=100         SANDBOX_TIMEOUT_SEC=30.0   GRANULARITY=fine
BUDGET=20                SEEDS=1..5 (E1, E2) · 1..3 (E3, E4, E5)
```

The first line is the screen's own protocol, unchanged. It has to be: the corpus
is stratified on π̂ measured under exactly these values, and **π is a property of
the model** — a corpus banded under `qwen2.5-coder:7b` is not banded for anything
else. `data/tasks.json` records the model it was frozen under; if that ever
disagrees with `MODEL`, the bands the primary comparison rests on mean nothing.

The values split the same way they do in [SCREENING.md](SCREENING.md) §1, and the
difference decides what a mistake costs.

**In `src.llm`'s cache key** — `model`, `temperature`, the prompt, and the draw
nonce. Change one and every completion already bought becomes unreachable:
expensive, but self-announcing, since you watch it re-buy.

**Not in the cache key — the dangerous half.** `max_examples` re-judges the very
same completions against a weaker oracle. `sandbox_timeout_sec` turns a slow
correct patch into a wrong one. The served context window decides whether the
prompt arrived whole or had its head cropped. All three move the numbers for free
and leave no trace. The first two are recorded in every `RoundRecord` and are
part of `run_eval.py`'s cell key; the third is what `serve_local.sh` exists to
assert.

### Nonces, and why the arms are paired

A proposal draw is nonced `<task>|seed<S>|r<round>` — deliberately *not* on mode
or the ablation flags (`src/loop.py::proposal_nonce`). Every arm's round 1 has an
empty history and therefore a byte-identical prompt, so all three conditions
share that one completion: the arms are paired on common random numbers instead
of buying the same answer three times. The moment the evidence and exclusion
blocks appear the prompts diverge, and the prompt is itself in the cache key, so
nothing is shared that should not be.

Two consequences worth planning around. A cell that dies halfway costs nothing to
restart — every earlier round replays. And the trial run of §3.1 is not throwaway
spend: its draws are the same nonces E1 and E2 will ask for.

---

## 2. What the corpus is, and what it is missing

`data/tasks.json`, frozen, 85 tasks:

| stratum | π̂ band | quota | taken | role |
|---|---|---|---|---|
| `dead` | [0.00, 0.02) | 20 | **20** | control — B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | 30 | **0** | primary — largest predicted effect |
| `medium` | [0.08, 0.18) | 20 | **20** | primary |
| `easy` | [0.18, 0.35] | 30 | **30** | primary |
| `too_easy` | (0.35, 1.00] | 15 | **15** | control — predicted null, all arms coincide |

**`hard` is empty, and this is a measurement limit, not an accident.** π̂ lives on
a grid of 1/K and the screen ran at K = 10, where `0/10 = 0.000` is `dead` and
`1/10 = 0.100` is `medium` — no outcome can land in [0.02, 0.08) at all. `hard`
is where the paper predicts its largest effect (A₁₂ = 1.00, oracle calls
23.07 → 6.50), so running as-is means reporting two primary bands, not three.

The fix is [SCREENING.md](SCREENING.md) §5 — deepen the same shards to
`--calls 38`, re-merge, re-run `select_corpus.py --min-calls 38` and
`build_strata.py` — and it costs ~28 extra draws per candidate (~82 h on one
machine) because the first 10 replay from cache. **It has to happen before E1**,
not after: re-freezing the corpus once episodes exist invalidates them, since the
cells were run against a different task list.

Decide that first. Everything below assumes the corpus you intend to report.

### The two pre-registered deviations

From PLAN.md §0, restated so they are not rediscovered mid-run: **B = 20**, not
the paper's 10, because the primary metric is conditioned on accepting and at
B = 10 the [0.02, 0.08) band is barely estimable; and **5 seeds**, not 30, which
leaves the statistical protocol intact (the bootstrap resamples *tasks*) and is
reported as a threat to validity.

---

## 3. The run order

Everything in this section is free — the backend is local, and `.env` prices
calls at zero. What it costs is wall clock: **~150 hours**, §7.

> **One driver at a time.** Every run appends to `data/episodes.jsonl` and reads
> `llm.spent()` from `data/calls.jsonl`; two concurrent drivers race on both.
> Sequential is also the only order in which a partial arm is obvious rather than
> interleaved with a complete one.

### 3.0 Once, before the first run

```bash
cd ~/Study/research/ceg-mem && source .venv/bin/activate
bash scripts/serve_local.sh
```

Three lines have to appear, and the third is the one that matters:

```
starting ollama on 127.0.0.1:11435 with OLLAMA_CONTEXT_LENGTH=32768
loading qwen2.5-coder:7b and verifying the served window
  ok: {"backend": "ollama", "context_length": 32768, "model_digest": "dae161e27b0e90dd", ...}
```

`context_length` must read **32768** and `model_digest` must match the screen's
(`data/screen_merged.json` → `runtime`) — the same tag can point at a different
blob after a re-pull, and that is a different instrument.

Then clear the pilot rows. `data/episodes.jsonl` holds 56 rounds from a
three-task pilot run under the model id `cegmem-qwen2.5-coder-7b`. That id is in
the cell key, so they cannot be mistaken for real cells — but they would still be
read by `summarize.py` and by anything else that walks the log, so move them
aside rather than reasoning about them later:

```bash
mv data/episodes.jsonl data/episodes_pilot_qwen_custom.jsonl.bak
```

### 3.1 Trial run — 3 tasks, 1 seed, ~25 min

Not a smoke test of the model; a test of the *flags*. It exercises all five arms
and both sweep axes on a separate episode log, so a broken flag is discovered in
minutes instead of on day four.

```bash
TRIAL="agc065_c/48625236 abc330_c/54071794 abc339_b/54672032"   # dead / medium / easy

python3 scripts/run_eval.py --modes no_memory untyped typed \
    --programs $TRIAL --seeds 1 --budget 5 --check-overfit \
    --episodes-path data/episodes_trial.jsonl \
    --overfit-path data/overfit_trial.jsonl

python3 scripts/run_eval.py --modes typed --steer off --programs $TRIAL \
    --seeds 1 --budget 5 --episodes-path data/episodes_trial.jsonl
python3 scripts/run_eval.py --modes typed --guard off --programs $TRIAL \
    --seeds 1 --budget 5 --episodes-path data/episodes_trial.jsonl
python3 scripts/run_eval.py --modes typed --max-examples 3 --typing-noise-c 0.5 \
    --programs $TRIAL --seeds 1 --budget 5 --episodes-path data/episodes_trial.jsonl

python3 scripts/summarize.py --episodes-path data/episodes_trial.jsonl
```

Five arms must come out with plausible numbers, and `no_memory` must show
`redundant_attempts` > 0 on at least one task — an arm that never repeats itself
is an arm whose memory is on when it should not be.

**Then the check that matters most.** Re-run the first command verbatim:

```bash
python3 scripts/run_eval.py --modes no_memory untyped typed \
    --programs $TRIAL --seeds 1 --budget 5 --check-overfit \
    --episodes-path data/episodes_trial.jsonl \
    --overfit-path data/overfit_trial.jsonl
```

Every line must read `already complete, skipping`, and it must finish in seconds.
Anything else means the resume key does not match the index — the bug PLAN.md §1
records, which re-ran every cell at full price — and 150 hours is the wrong place
to discover it.

Clear the rehearsal (the cached completions stay, and E1 replays them free):

```bash
rm -f data/episodes_trial.jsonl data/overfit_trial.jsonl
```

### 3.2 E0c — the oracle's blind spot · no model calls

The one free measurement still outstanding. Reads the frozen corpus, plants
mutants, drops nothing.

```bash
python3 scripts/measure_pool_strength.py --jobs 6      # -> data/pool_strength.json
```

Report its `equivalent` rate as an **upper bound** on invisible patch
overfitting: some share of any planted edit is semantically inert rather than
undetectable, and separating the two needs coverage data this run does not
collect.

### 3.3 E1 — the no-memory arm · ~54 h

```bash
tmux new -s cegmem
nohup python3 scripts/run_eval.py --modes no_memory --force-full-budget \
      --seeds 1 2 3 4 5 --budget 20 > logs/E1.log 2>&1 &
```

`--force-full-budget` is what makes this an estimator rather than just a
baseline. The no-memory prompt carries no evidence and no exclusion block, so it
is byte-identical across all 20 rounds; every round is an independent draw of π,
and 5 seeds × 20 rounds gives **100 i.i.d. draws per task** against the screen's
10. `summarize.py` truncates the rounds back at the first accept, so the arm
stays comparable to the memory arms it is tabulated against.

> **Never run `no_memory` again without `--force-full-budget`.** That is a
> different cell, and `analyze.py` deliberately refuses to pool the two.

This is the only step whose call count is exact: 85 × 5 × 20 = **8,500**.

### 3.4 E2 — the memory arms · ≤46 h

```bash
nohup python3 scripts/run_eval.py --modes untyped typed --check-overfit \
      --seeds 1 2 3 4 5 --budget 20 > logs/E2.log 2>&1 &
```

`--check-overfit` re-runs the whole test pool on every accept and writes the
verdict to `data/overfit_checks.jsonl` — the audit that separates *repaired* from
*passed the sampled oracle*. It is near-vacuous here on purpose: at
`max_examples = 100` the sampler returns the whole pool for almost every
ConDefects task. E4 is where it earns its keep.

### 3.5 E3 — the mechanism ablation · ≤28 h

The step that separates *remembering* from *typing*.

```bash
nohup python3 scripts/run_eval.py --modes typed --steer off \
      --seeds 1 2 3 --budget 20 > logs/E3_guard_only.log 2>&1     # guard-only
nohup python3 scripts/run_eval.py --modes typed --guard off \
      --seeds 1 2 3 --budget 20 > logs/E3_steer_only.log 2>&1     # steering-only
```

Predicted (Table 4): guard-only reproduces untyped's round savings but leaves
redundant attempts untouched; steering-only drives redundant attempts toward zero
and lifts budgeted success. Note that on this implementation steering is an
English instruction, not Eq. (3)'s renormalised support — so **non-repetition is
a measured outcome, not a theorem**, and exactly-zero is not guaranteed.

### 3.6 E4 / E5 — the robustness sweeps · ≤23 h

Declare the subset **once, to a file**. The sweeps run across several sessions
and `freeze_results.py --sweep-programs` must receive the identical list days
later; its default is "the first 30 frozen programs sorted", which is not this
list, and a mismatch silently freezes the wrong tasks.

```bash
python3 - <<'PY' > data/sweep_programs.txt
import json, collections
by = collections.defaultdict(list)
for t in json.load(open('data/tasks.json'))['tasks']:
    by[t['stratum']].append(t['name'])
print('\n'.join(n for s in ('dead','hard','medium','easy','too_easy')
                  for n in sorted(by[s])[:6]))
PY

SWEEP=$(cat data/sweep_programs.txt)          # 24 tasks — 6 per band, `hard` empty
```

```bash
for k in 20 8 3; do            # E4 — oracle informativeness (the ρ proxy)
  python3 scripts/run_eval.py --modes typed --max-examples $k --check-overfit \
      --seeds 1 2 3 --budget 20 --programs $SWEEP >> logs/E4.log 2>&1
done

for c in 0.9 0.75 0.5; do      # E5 — typing coherence
  python3 scripts/run_eval.py --modes typed --typing-noise-c $c \
      --seeds 1 2 3 --budget 20 --programs $SWEEP >> logs/E5.log 2>&1
done
```

Neither sweep pays for its own baseline: E4's reference level is
`--max-examples 100` and E5's is `c = 1.0`, both the typed cell E2 already ran,
and the driver recognises the cell and skips it.

**E4 must be read off `is_truly_correct`, not off `accept`.** Lowering
`max_examples` weakens the oracle, so more wrong patches are accepted and the
apparent repair rate *rises*; read naively, the sweep concludes that a less
informative oracle repairs better. Plot the `data/overfit_checks.jsonl` series.
The levels stop at 20 because `src/oracle.py::_sample` returns the whole pool
whenever `max_examples ≥ len(cases)`, and ConDefects pools are small — `100` and
`300` are the same experiment at twice the price.

---

## 4. Watching a run, and resuming one

```bash
bash scripts/watch_eval.sh logs/E1.log      # rate, cells done, ETA, last 3 lines
python3 scripts/summarize.py --by-task      # arm means so far, off data/episodes.jsonl
tail -f logs/E1.log
```

A run is resumable at round granularity: `src.loop.run_episode` appends one
`RoundRecord` per round as it goes, and the episode id is deterministic, so a
cell that died halfway **rewrites its own rounds** instead of appending a second
truncated episode. Re-run the identical command; the finished part replays from
cache in seconds.

> **Never hand-trim the program list to "skip what is done".** The driver's own
> resume does that correctly and for free. A trimmed re-run is how a cell goes
> missing from an otherwise complete arm.

If the machine has to be rebooted, `serve_local.sh` again first — the driver will
otherwise fail every call against a dead endpoint and burn through
`LLM_MAX_RETRIES` on each one.

To hand the machine back:

```bash
bash scripts/serve_local.sh --stop      # unload 6.4 GB of weights, stop our server
```

---

## 5. Analysis · no model calls

Order matters: the freeze needs the strata, `fit_theory` needs the frozen
results, and `build_strata`'s drift audit needs `fit_theory`.

```bash
python3 scripts/freeze_results.py --experiment main      # -> data/results_real.json
python3 scripts/analyze.py                               # -> data/analysis.json
python3 scripts/fit_theory.py                            # -> data/theory_fit.json
python3 scripts/build_strata.py --force                  # now with the drift audit
python3 scripts/measure_coherence.py                     # the c proxy, off E1
python3 scripts/measure_anchoring.py                     # -> data/anchoring.json
python3 figures/make_figures.py
python3 scripts/check_consistency.py

python3 scripts/freeze_results.py --experiment ablation
python3 scripts/freeze_results.py --experiment oracle_sweep --sweep-programs $SWEEP
python3 scripts/freeze_results.py --experiment typing_sweep --sweep-programs $SWEEP
```

`check_consistency.py` rebuilds every frozen file from `data/episodes.jsonl` and
deep-diffs it against what is on disk, so a reported number cannot drift from the
artifact that produced it. Run it last, and run it again before submission.

Optional and high-value, per PLAN.md §10: `scripts/label_tool.py` is the only
measurement in this repo that reaches real type coherence with human ground
truth, which §9 of the paper calls the single decisive open quantity.

```bash
python3 scripts/label_tool.py --annotator alice
python3 scripts/label_tool.py --annotator bob
python3 scripts/label_tool.py --compare alice bob
```

---

## 6. When something refuses

**`served context is 4096, not 32768`** — the server picked the window itself.
Stop whatever is on port 11435 and let `serve_local.sh` start its own. Do not
work around it: the prompt would be silently cropped, worst on the memory arms,
which is exactly the comparison being measured.

**Every cell re-runs instead of skipping** — something in the cell key moved.
The key is `(task, mode, seed, guard, steer, max_examples, typing_noise_c,
force_full_budget, model, granularity)`. In practice it is `model`: a run that
forgets `.env` and picks up a different id — `cegmem-qwen2.5-coder-7b`,
`gpt-4o-mini` — writes rows that no later run will ever match. Check
`data/episodes.jsonl`'s `model` field before assuming the resume logic is broken.

**`BudgetExceeded`** — `.env` prices local calls at zero, so this cannot fire on
a healthy run. It is left low on purpose as a tripwire: if it fires, something
has repointed the client at a paid endpoint mid-run. Stop and find out what,
rather than raising the cap.

**`ContextOverflow`** — a prompt exceeded `LLM_CONTEXT_TOKENS`. This is the
client-side guard doing its job: it turns a would-be silent truncation into a
refusal. Expect it, if at all, on a `dead`-band task deep into a memory arm,
where 20 rounds of accumulated evidence are in the prompt. Record it; do not
raise the window, which would make that task a different instrument from the
other 84.

**`data/tasks.json is not frozen` / `missing`** — the corpus freeze did not
complete. [CORPUS.md](CORPUS.md), not this file.

**`analyze.py` refuses to pool two `no_memory` arms** — one of them was run
without `--force-full-budget`. They are different cells and averaging them would
mix an estimator with a baseline. Delete the wrong one's rows from
`data/episodes.jsonl` (match on `episode_id`) and re-run.

---

## 7. Cost

Wall clock, not money. At the screen's measured rate on this machine — **median
16.5 s, mean 22.8 s per call**, most of it model generation rather than sandbox
execution. Nothing here parallelises within a run.

| step | calls | hours |
|---|---|---|
| 3.1 trial | ~90 | ~0.5 |
| 3.2 pool strength | 0 model calls | ~2 |
| 3.3 E1 | 8,500 (exact) | ~54 |
| 3.4 E2 | ≤7,250 | ≤46 |
| 3.5 E3 | ≤4,350 | ≤28 |
| 3.6 E4 + E5 | ≤3,670 | ≤23 |
| **total** | **≤23,800** | **≤150 h ≈ 6.3 days** |

Only E1's figure is exact — it runs every round of every episode by
construction. The rest are upper bounds: they stop at the first accept, and the
per-task expectation `E[rounds] = (1 − (1−π)^20)/π` is evaluated at the screen's
π̂, whereas the memory arms achieve a per-round rate `q ≥ π` whenever steering
helps at all. If the paper's mechanism works, these steps finish early — which is
itself a weak signal worth noticing in the logs.

Where the hours actually go: the 20 `dead` tasks burn the full 20 rounds in every
cell and account for a third of the grid on their own. They are kept because a
control band where B binds hardest and memory grows longest is where the guard's
predicted advantage should be most visible — not despite the cost, but for it.
