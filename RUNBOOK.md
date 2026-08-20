# Runbook — running the whole study

Every stage, in order, from a fresh clone to the figures. [DESIGN.md](DESIGN.md)
says *why* each stage exists and which paper claim it anchors; this file is only
how to run it. [STATUS.md](STATUS.md) says what this checkout currently holds.

```
benchmark  ->  candidates  ->  gate  ->  screen  ->  corpus  ->  eval  ->  analyse
                                          ^^^^^^                 ^^^^
                                        sharded, local model
```

`scripts/pipeline.sh` is the entry point. It runs one stage at a time, refuses to
start a stage whose input artifact is missing, and reads the parameters that link
two stages off the artifact instead of letting them be retyped.

```bash
bash scripts/pipeline.sh              # where you are, and what runs next
bash scripts/pipeline.sh <stage>      # run one stage
bash scripts/pipeline.sh --dry-run <stage>
```

---

## 0. Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
bash scripts/pipeline.sh benchmark          # clone ConDefects, verify the layout
```

`fetch_condefects.py` can only clone the *code*. The contest test data ships
separately as `Test.zip` (several GB, OneDrive or Baidu — the script prints both
links); drop it into `external/ConDefects/` and re-run. Without `Test/` there are
no inputs, so there is no oracle and nothing below runs.

**The proposer.** `src/llm.py` speaks OpenAI chat-completions, and the same code
path serves a local [Ollama](https://ollama.com) — it differs only in
`LLM_BASE_URL`, `LLM_API_KEY` and `MODEL`. For a local run:

```bash
ollama pull qwen2.5-coder:7b
bash scripts/serve_local.sh                 # start (or adopt) and VERIFY
```

---

## 1. The protocol contract

Every stage that calls a model shares these. They are not preferences: each one
changes what the measured quantities *are*, and a shard measured under a
different one is a different instrument, not a noisier reading of the same one.

```
MODEL=qwen2.5-coder:7b   TEMPERATURE=1.0        CONTEXT_LENGTH=32768
MAX_EXAMPLES=100         SANDBOX_TIMEOUT_SEC=30.0   GRANULARITY=fine
SEED=20260717            BUDGET B=20            SEEDS=1..5 (E1,E2) · 1..3 (E3-E5)
```

`screen_shard.sh` and `eval_shard.sh` pin all of them and export them over
`.env`, so a machine whose `.env` has drifted still measures the same thing.

The values split in two, and the difference decides what a mistake costs.

**In `src.llm`'s cache key** — `model`, `temperature`, the prompt, `max_tokens`
and the draw nonce. Change one and every completion already bought becomes
unreachable: expensive, but self-announcing, since you watch it re-buy.

**Not in the cache key — the dangerous half.** `max_examples` re-judges the very
same completions against a weaker oracle. `sandbox_timeout_sec` turns a slow
correct patch into a wrong one. The served context window decides whether the
prompt arrived whole or had its head cropped. All three move the numbers for free
and leave no trace in the cache, so all three are recorded in every shard's
report and the merge refuses to join across a disagreement.

### The context window, specifically

Ollama picks it from available VRAM — `4k/32k/256k`, per `ollama serve --help` —
and **truncates** an over-long prompt instead of refusing it. The
OpenAI-compatible endpoint has no field to raise it, and the window reaches
neither the cache key nor any logged row. So the same model id on two machines
can be two different instruments with nothing to say so, and the arms carrying
the most evidence are the ones silent truncation lands on hardest.

Both shard scripts therefore pin `OLLAMA_CONTEXT_LENGTH`, load the model, ask
`/api/ps` what is *actually* being served, and refuse to spend on a mismatch.
That check — not who started the server — is what makes a shard trustworthy.

---

## 2. Stage 0 — the candidate list · no model calls

```bash
bash scripts/pipeline.sh candidates          # -> data/candidates.json
```

This decides which faults the study is ever allowed to see, and its output order
is the seeded stratified traversal that every later index range is cut from. It
is run **once** and then treated as frozen: re-running it with different flags
renumbers every shard.

Every gate is a pure function of `faultyVersion.py`, the shipped test-directory
listing and `date.txt`. No gate reads `correctVersion.py`. [DESIGN.md](DESIGN.md)
§2 argues each one, and why filtering on them is dose-range selection rather than
outcome selection — that is the question a reviewer asks first.

`data/candidates.json` carries every fault examined and the first gate it failed,
so the funnel is reconstructible from the artifact alone.

---

## 3. E0 — the oracle gate · no model calls

Earns Assumption 1: the paper takes oracle soundness as given, and on a real
benchmark it has to be demonstrated before anything is spent through it.

```bash
bash scripts/pipeline.sh gate                # ~2 h at --jobs 6
bash scripts/pipeline.sh gate -- --jobs 1    # publication freeze, slower
```

Writes `data/pool/tasks.json` (the frozen **candidate pool** — not the corpus)
and `data/pool/oracle_validation.json`. Up to three *natural* mutants per fault —
other wrong submissions to the same coding task — are judged by the same sampling
oracle the repair loop calls. A fault passes at ≥ 2/3 of its scoreable mutants
caught; the pool freezes only if enough of the cohort passes.

**Natural mutants, not planted, and the trade is deliberate.** A natural mutant
is a real developer's real mistake, with a real mistake's detectability; a
planted edit is easier and less representative. The cost is coverage — a coding
task with a single submission has no sibling to borrow, hence `--min-siblings`.

A mutant the *sample* accepts gets a second opinion from the whole shipped pool,
which separates two different failures. If the pool refutes it, the sample was
too small — a real property of the oracle at this `max_examples`, and reported as
such. If the pool does not refute it either, no oracle could have caught it, so
it is excluded from the denominator rather than charged against the oracle.

The pass bar is held as a **fraction**, not "2 of 3": a task supplies between one
and three siblings, and the criterion has to mean the same thing for each. The
gate is measured on a cohort and the pool is then topped up past it, because a
pass rate computed over a set already filtered on passing would be vacuous.

Run a publication freeze with `--jobs 1`: a timeout is a verdict, not a retry, so
a program near the sandbox wall-clock limit can fail under parallel load when it
would not have serially.

This stage needs no model and does not depend on the screen, so it can run on any
spare machine — including while screening is going.

**If the gate refuses because the cohort could not be seated**, that is not a
quality failure: the candidate list ran out of faults with a sibling wrong
submission before reaching the requested size. `scripts/refreeze_pool.py`
re-freezes the finished report at the size the data actually supported, without
lowering the pass bar.

---

## 4. E0b — the π̂ screen · sharded, local model

Difficulty on this loop *is* π: under the no-memory arm every round is an
independent Bernoulli draw, so π alone fixes both `Pr[accept within B]` and
`E[rounds | accept]`. Nothing cheaper predicts it — AtCoder rating correlates at
only ρ = −0.35, which cannot place a task in a band 0.10 wide.

Shards are contiguous index ranges over `data/candidates.json`, 1-based and
inclusive. **They must not overlap.** Any contiguous range is already balanced —
the order is the seeded stratified round-robin from Stage 0 — so a shard is a
smaller screen, not a skewed one.

```bash
bash scripts/pipeline.sh screen --from   1 --to 132     # machine 1
bash scripts/pipeline.sh screen --from 133 --to 264     # machine 2
...
bash scripts/pipeline.sh screen --merge                 # -> data/screen_merged.json
```

Each shard writes its π̂ report, its program list (with the pool digest), its own
call ledger and its trace under `logs/`. Run it under `tmux` or `nohup`: it is
resumable, and re-running the identical command replays from cache.

> **Never hand-trim a shard list to skip what is already done.** The report holds
> only what that run walked, so a trimmed re-run silently drops the rest. Re-run
> the whole range; the finished part is nearly free.

### Verifying a machine before giving it real work

Once per machine, about five minutes. Candidate #2 is a single short task.

```bash
bash scripts/screen_shard.sh --from 2 --to 2 --calls 10 --dry-run   # plan only
bash scripts/screen_shard.sh --from 2 --to 2 --calls 10             # for real
```

`context_length` must read the protocol value and `model_digest` must match the
other machines — the same tag can point at a different blob after a re-pull. Then
prove the incremental replay, which is the check that matters most:

```bash
bash scripts/screen_shard.sh --from 2 --to 2 --calls 14
wc -l data/calls_screen_002_002.jsonl        # -> 14, NOT 24
```

The ledger records only real calls; a cache hit is never logged. If it reads 24,
something in the cache key moved between the two runs — stop and diff the
`protocol` lines rather than screening the whole pool twice.

### Depth, and why it decides which bands can be filled

π̂ lives on a grid of `1/K`. A band can only be filled if some multiple of `1/K`
falls inside it, so the depth chosen here decides what the corpus can contain. At
`K = 10`, for instance, `0/10 = 0.000` is `dead` and `1/10 = 0.100` is `medium` —
**nothing can land in `hard` = [0.02, 0.08) at all**, and that is the band where
the paper predicts its largest effect.

`consolidate_screens.py` prints the depth that puts three interior points in
`hard` and the per-task cost of getting there. Deepening is the same command with
a larger `--calls`, on the machine holding that shard's cache: draws are nonced
`pi-pilot|<task>|seed<S>|call<i>`, so a re-run at larger K replays `0..K-1` and
buys only the difference.

### What the merge audit catches

| | what it means |
|---|---|
| protocol disagreement | a shard measured a different quantity — **hard stop**, with the cost of re-running spelled out |
| `GAPS` | index ranges nobody screened |
| `PARTIAL` | a shard walked fewer tasks than its own list; `complete: true` only tracks the budget cap, so a Ctrl-C leaves the last checkpoint claiming completeness |
| `DIVERGENT CHECKOUT` | a program hashes differently between shards — those two shards screened different programs under one name |
| `DISAGREEMENT` | the same task at the same depth gave different counts. Draws are cached under deterministic nonces and the oracle is seeded, so this cannot happen unless the machines are not interchangeable — usually `SANDBOX_TIMEOUT_SEC` firing on the slower one. Fix and re-run both; do not pick a winner |

`cache/` does **not** need syncing between machines: it is content-addressed, and
shards are disjoint so their caches are too.

---

## 5. Freeze the corpus · no model calls

```bash
bash scripts/pipeline.sh corpus
# -> data/tasks.json      the frozen corpus
#    data/screening.json  every candidate screened, its pi_hat, its band, and why
#    data/strata.json     absolute bands + the selection-vs-reported drift audit
```

The pipeline reads `--min-calls` off the merged screen report rather than letting
it be retyped. **This is the trap the stage exists around**: ask for a depth the
screen never reached and every task below it is dropped; ask for less and a band
whose edges enclose no multiple of `1/K` comes out empty with nothing to say so.

The five bands, and what each is for:

| stratum | π̂ band | role |
|---|---|---|
| `dead` | [0.00, 0.02) | control — B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | primary — largest predicted effect |
| `medium` | [0.08, 0.18) | primary |
| `easy` | [0.18, 0.35] | primary |
| `too_easy` | (0.35, 1.00] | control — predicted null, all arms coincide |

Each band takes `min(quota, available)`. The two control bands are kept, not
discarded: an effect that appears only where the mechanism has room to operate is
stronger evidence than a uniform one. `too_easy` in particular is saturated by
construction — the first proposal is accepted, nothing is ever stored, and all
three conditions coincide.

**Check the band counts before spending anything on the grid:**

```bash
python3 -c "
import json, collections
d = json.load(open('data/tasks.json'))
print(d['n_selected'], 'tasks;', dict(collections.Counter(t['stratum'] for t in d['tasks'])))
print('screen depth K =', d['selection']['min_calls'])"
```

An empty primary band is fixed by deepening the screen (§4), not by proceeding.
**It has to happen before the grid**, not after: re-freezing the corpus once
episodes exist invalidates them, and `eval_shard.sh` refuses to cut a shard from
a corpus whose digest no longer matches the one its index order was built from.

**Keep the two π̂ apart — this is the whole discipline.** The screen's draws carry
cache nonce `pi-pilot|…` and are spent here; the π̂ that gets *reported* comes
from E1, whose draws carry a different nonce. Two independent samples, so
conditioning on one cannot inflate the other. Selection happens on the no-memory
arm before any treatment, which makes it dose-range choice, not outcome
selection. Migration between the two π̂ is regression to the mean, and
`build_strata.py` writes the migration matrix out rather than hiding it, because
its size *is* the risk.

---

## 6. The oracle's blind spot · no model calls

```bash
bash scripts/pipeline.sh pool-strength       # -> data/pool_strength.json
```

Measurement, not a gate: it reads the frozen corpus and drops nothing. The
natural-mutant gate of §3 cannot answer this question — a natural mutant is a
submission the judge rejected, so the pool refutes it by construction. This
plants synthetic mutants instead and reports how many the shipped pool cannot
distinguish at all.

Report the `equivalent` rate as an **upper bound** on invisible patch
overfitting: some share of any planted edit is semantically inert rather than
undetectable, and separating the two needs coverage data this run does not
collect. It also prints the catch rate as a function of sampled cases, which is
what says whether the E4 sweep levels sit on the informative part of the curve.

---

## 7. E1–E5 — the grid · sharded, local model

### The arms

Memory reaches the proposer only through typing, so three of the five arms build
a byte-identical prompt and differ only in which guard runs:

| `--exp` | prompt | guard | seeds |
|---|---|---|---|
| `E1` | unconditioned | none | 1–5 |
| `E2` (`untyped`) | **unconditioned** | flat scan of every stored counterexample | 1–5 |
| `E2` (`typed`) | typed classes + exclusion | type-indexed bucket | 1–5 |
| `E3-guard-only` | **unconditioned** | type-indexed bucket | 1–3 |
| `E3-steer-only` | typed classes + exclusion | none | 1–3 |
| `E4-k20` `E4-k8` `E4-k3` | typed, at `--max-examples K` | type-indexed | 1–3 |
| `E5-c90` `E5-c75` `E5-c50` | typed, at `--typing-noise-c C` | type-indexed | 1–3 |

That is the paper's own factorisation, and it buys two things. The unconditioned
arms share cache entries under the same draw nonce, so **`untyped` and
`guard-only` cost no model calls once E1 has run**. And their `success@B` must
equal E1's exactly — a guard only blocks candidates that provably fail a stored
counterexample, and a correct patch fails none. A gap is a guard-soundness bug,
not a finding.

E1 runs with `--force-full-budget`, which is what makes it an estimator rather
than just a baseline: the no-memory prompt is byte-identical across all rounds,
so every round is an independent draw of π. Never run `no_memory` without it —
that is a different cell, and `analyze.py` refuses to pool the two.

### Nonces, and why the arms are paired

A proposal draw is nonced `<task>|seed<S>|r<round>` — deliberately not on mode or
the ablation flags. Every arm's round 1 has an empty history and therefore an
identical prompt, so all conditions share that one completion: the arms are
paired on common random numbers instead of buying the same answer three times.

**The oracle is not cached at all.** Re-running a finished cell replays its model
calls for free but re-executes every candidate against the test pool, and that
sandbox time is most of the wall clock. This is why `eval_shard.sh` passes
`--resume-from data/episodes.jsonl` by default: a cell any earlier shard finished
is skipped rather than re-walked.

### Verify the machine — the trial

Not a smoke test of the model; a test of the *flags* and of the server. Three
tasks, one seed, B=5, on a log that can never be merged into reported data, and
the one preset that ignores the merged history.

```bash
bash scripts/pipeline.sh eval --exp trial
python3 scripts/summarize.py --episodes-path data/episodes_trial.jsonl
```

Then **the check that matters most** — run the identical command again. Every
cell must print `already complete, skipping` and it must finish in seconds.
Anything else means the resume key does not match the index, and a multi-day grid
is the wrong place to discover that.

The ablation and sweep flags are not covered by the trial. One task each:

```bash
bash scripts/pipeline.sh eval --exp E3-guard-only --from 1 --to 1 --seeds 1 --budget 5
bash scripts/pipeline.sh eval --exp E3-steer-only --from 1 --to 1 --seeds 1 --budget 5
bash scripts/pipeline.sh eval --exp E4-k3         --from 1 --to 1 --seeds 1 --budget 5
bash scripts/pipeline.sh eval --exp E5-c50        --from 1 --to 1 --seeds 1 --budget 5
```

Guard-only must log `n_guarded > 0` and steer-only exactly `n_guarded = 0`; if
those two look alike, the ablation flags did not take. Clear the rehearsal
afterwards (`rm -f data/episodes_trial.jsonl data/overfit_trial.jsonl
data/calls_trial.jsonl`); the cached completions stay and E1 replays them free.

### Sharding

A shard is a contiguous index range over that experiment's **universe** —
`data/eval_order.txt` (the whole corpus) for E1/E2/E3, `data/sweep_programs.txt`
(six tasks per band) for E4/E5. Both are written by `eval_shard.sh` on first use,
deterministically, with the corpus digest that produced them in the header.

The order is **not** `data/tasks.json`'s, which is grouped by stratum. Index
ranges cut from that would hand one machine every `dead` task — full budget in
every cell, by construction — and another every `too_easy` one, so shards would
differ ~10× in wall clock and a run that finished three shards of four would hold
a *stratum-biased* grid rather than a smaller one. Each band is spaced evenly
over the whole order instead, which leaves every prefix and every suffix
proportional.

Same three rules as the screen: **shards must not overlap**, never hand-trim one
to skip finished work (`--resume-from` does that correctly and for free), and run
**one shard at a time per machine** — two race on the cache and on the log.

```bash
bash scripts/pipeline.sh eval --exp E1 --from 1 --to 30
bash scripts/pipeline.sh eval --exp E2                       # omit the range = all
bash scripts/pipeline.sh eval --exp E3-guard-only
...
bash scripts/pipeline.sh eval --merge                        # -> data/episodes.jsonl
```

Chain shards without reloading the weights each time:

```bash
bash scripts/eval_shard.sh --exp E1 --from  1 --to 30 --no-stop-model --keep-serving
bash scripts/eval_shard.sh --exp E1 --from 31 --to 60                 # tears down
```

**E4 must be read off `is_truly_correct`, not off `accept`.** Lowering
`max_examples` weakens the oracle, so more wrong patches are accepted and the
apparent repair rate *rises*; read naively, the sweep concludes that a less
informative oracle repairs better. Plot the `data/overfit_checks.jsonl` series.
Neither sweep pays for its own baseline: E4's reference level is
`--max-examples 100` and E5's is `c = 1.0`, both the typed cell E2 already ran.

### What the merge audit catches

| | what it means |
|---|---|
| protocol disagreement | a shard ran under a different `model` or `granularity`. Both are in the cell key, so those rows would never pool — they are a second grid sharing one file. **Hard stop** |
| runtime disagreement | two shards' `.meta.json` differ on the served context window, the model digest, the temperature or the sandbox timeout. None of these reaches the cache, so those shards re-judged each other's draws against a different instrument. **Hard stop** |
| no protocol record | a shard log with no `.meta.json` — produced by a hand-run `run_eval.py`, so what it ran under is unknown. Reported, not fatal |
| `GAPS` | (task, seed) cells missing from an arm, as index runs over that arm's universe |
| `TRUNCATED` | episodes that neither accepted nor reached the budget. The rows are real and every round-averaged estimator will average them, as a task that took fewer rounds than it did |
| `DISAGREEMENT` | the same (episode, round) collected twice with different results — see the screen's row of the same name |
| `FOREIGN` | a task not in the universe at all — a shard cut from a different corpus |

### Watching and resuming

```bash
bash scripts/watch_eval.sh logs/eval_E1_001_030.log   # rate, cells done, ETA
python3 scripts/summarize.py --by-task                # arm means so far
```

A run is resumable at round granularity: one `RoundRecord` is appended per round
as it goes, and the episode id is deterministic, so a cell that died halfway
rewrites its own rounds. Re-run the identical command; the finished part replays
from cache in seconds. After a reboot, just re-run the shard — it brings the
server back up and re-verifies the window first.

---

## 8. Analysis · no model calls

```bash
bash scripts/pipeline.sh analyse
```

which is, in the order that matters — the freeze needs the strata, `fit_theory`
needs the frozen results, and `build_strata`'s drift audit needs `fit_theory`:

```bash
python3 scripts/freeze_results.py --experiment main      # -> data/results_real.json
python3 scripts/analyze.py                               # -> data/analysis.json
python3 scripts/fit_theory.py                            # -> data/theory_fit.json
python3 scripts/build_strata.py --force                  # now with the drift audit
python3 scripts/measure_coherence.py
python3 scripts/measure_anchoring.py
python3 figures/make_figures.py
python3 scripts/check_consistency.py
```

Then the sub-grids, each against the list it actually ran over:

```bash
python3 scripts/freeze_results.py --experiment ablation
python3 scripts/freeze_results.py --experiment oracle_sweep \
        --sweep-programs-from data/sweep_programs.txt
python3 scripts/freeze_results.py --experiment typing_sweep \
        --sweep-programs-from data/sweep_programs.txt
```

`--sweep-programs-from` rather than `--sweep-programs`: the flag's own default is
"the first 30 frozen programs, sorted", which is **not** the stratified subset
E4/E5 ran over, and a shell that does not split the variable turns the list into
one name. Both mistakes freeze the wrong tasks silently.

`check_consistency.py` rebuilds every frozen file from `data/episodes.jsonl` and
deep-diffs it against what is on disk, so a reported number cannot drift from the
artifact that produced it. Run it last, and again before submission.

**Optional and high-value.** `scripts/label_tool.py` gives two annotators an
independent tag on the same sample, so inter-annotator agreement and agreement
with the automatic θ can be reported next to the automated proxy. §9 of the paper
identifies real type coherence as the single decisive open quantity, and
`measure_coherence.py` cannot separate ρ from c on its own.

```bash
python3 scripts/label_tool.py --annotator alice
python3 scripts/label_tool.py --annotator bob
python3 scripts/label_tool.py --compare alice bob
```

---

## 9. When something refuses

**`served context is N, not 32768`** — the server picked the window itself. Stop
whatever is on the port and let the shard script start its own. Do not work
around it: the prompt would be silently cropped, worst on the arms carrying the
most evidence.

**`data/eval_order.txt was cut from a different data/tasks.json`** — the corpus
was re-frozen after shards had started. Every shard index now means a different
task. Move the old order files and episode logs aside deliberately, or restore
the corpus they belong to; there is no merge that makes the two halves one grid.

**Every cell re-runs instead of skipping** — something in the cell key moved. The
key is `(task, mode, seed, guard, steer, max_examples, typing_noise_c,
force_full_budget, model, granularity)`. In practice it is `model`: a run that
bypasses the shard script and picks up a different id writes rows no later run
will ever match. Check the `model` field in the episode log first.

**Two arms look complete but ran under an older prompt** — the cell key does not
cover the version of `build_prompt`. Change what a mode puts in the prompt and
the affected cells stay "complete" to `--resume-from`, silently. There is no
automatic detector: after any change to `src/proposer.py`, delete the affected
rows from the shard logs (match on `mode`, `guard_on`, `steer_on`) before
re-running, or re-run into a fresh `--episodes-path`.

**`unknown program(s): ['a b c']`** — the shell did not split the list. zsh
word-splits an unquoted `$(...)` but not an unquoted `$VAR`. Use
`--programs-from` with a file, which is what the shard scripts do.

**`BudgetExceeded`** — local calls are priced at zero, so this cannot fire on a
healthy run. The cap is left low on purpose as a tripwire: if it fires, something
has repointed the client at a paid endpoint mid-run. Find out what, rather than
raising the cap.

**`context_overflow` in a round's `proposal_error`** — a prompt exceeded
`LLM_CONTEXT_TOKENS`, and the client-side guard turned a would-be silent
truncation into a refusal. `src/loop.py` records the round as
spent-but-inconclusive and carries on; uncaught it would abort the shard, and
because the prompt is deterministic every re-run would abort at the identical
round. Count them and report the count — it is a threat-to-validity number for
the memory arms specifically. Do not raise the window to make them go away.

**`freeze_results.py` refuses a log holding two models** — π is a property of the
model, so two models in one freeze is two experiments reported as one. Move the
foreign rows aside (match on `model`) and re-run.

**`analyze.py` refuses to pool two `no_memory` arms** — one was run without
`--force-full-budget`. They are different cells, and averaging them would mix an
estimator with a baseline.

**`data/tasks.json is not frozen` / missing** — the corpus freeze did not
complete. §5, not this section.

---

## 10. Cost

Wall clock, not money, when the backend is local — `.env` prices those calls at
zero and the ledger still records tokens, `finish_reason` and seconds. Nothing
parallelises within a shard; the sharding *is* the parallelism.

Two projections, both computed rather than assumed:

```bash
# the grid, for the corpus you actually froze - select_corpus.py prints this at
# freeze time, evaluated at its --budget / --seeds-main / --seeds-abl / --sweep-size
python3 scripts/select_corpus.py --help | grep -A1 "cost projection"

# the per-call rate on this machine, from your own screen
python3 -c "
import json, statistics
sec = [json.loads(l)['sec'] for l in open('data/calls_screen.jsonl')]
print(f'{len(sec)} calls: median {statistics.median(sec):.1f}s, mean {statistics.mean(sec):.1f}s')"
```

Only E1's call count is exact — `--force-full-budget` runs every round of every
episode, so it is `tasks × seeds × B`. The rest are upper bounds: they stop at
the first accept, and the per-task expectation `E[rounds] = (1 − (1−π)^B)/π` is
evaluated at the screen's π̂, whereas the memory arms achieve a per-round rate
`q ≥ π` whenever steering helps at all. If the mechanism works, those steps
finish early — itself a weak signal worth noticing in the logs.

Where the hours actually go: `dead`-band tasks burn the full budget in every
cell, by construction, and dominate the total out of proportion to their count.
They are kept because a control band where B binds hardest and memory grows
longest is where the guard's predicted advantage should be most visible — not
despite the cost, but for it.
