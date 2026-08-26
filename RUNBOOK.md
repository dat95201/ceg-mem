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
| `E5-c90` `E5-c75` `E5-c50` `E5-c25` `E5-c00` | typed, at `--typing-noise-c C` | type-indexed | 1–3 |
| `E6-transcript` | **whole refuted transcript** | flat scan | 1–5 |
| `E5-random` | typed, classes assigned **at random** | type-indexed | 1–3 |
| `E8-audit` | as `untyped` / `typed` | + oracle on guarded rounds | 1–3 |

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

**`E6-transcript` is the one arm that costs model calls.** Its prompt carries
every refuted patch, so no round past the first shares a cache key with E1. It
is the ChatRepair condition — the thing reflective agents actually do — and it
exists because `untyped` shows the proposer nothing, which a reviewer will read
as a straw man unless the transcript arm is reported next to it. `--transcript-window K`
caps how many past attempts are shown (`0` = all); the window is in the cell
key, because truncating is a design decision, not an implementation detail.

**`E8-audit` buys one number and spends oracle time for it.** With
`--audit-guarded` the loop runs the oracle on rounds the guard blocked — for
the record only; the verdict never reaches memory and never ends the episode.
Without it, every θ-based redundancy count is *censored exactly where an arm
guards*, so an arm that guards often looks less redundant for procedural
reasons. Run it on the sweep subset only: over the full grid it would spend the
oracle time E2 exists to show can be saved. `measure_redundancy.py` prints a
warning when it is reading censored data.

**`E5-random` is the c axis's null, and `c = 0.00` is not it.** `TypedMemory.store`
noises only the *location* half of a type, and the first store of an episode has
no other location to move to, so the bottom of the sweep is a **lower bound on
the damage** rather than random assignment. `--typing-random` files every stored
refutation under a location drawn uniformly from those seen so far, its own
included: memory still partitions the evidence and the guard is still O(1), but
the partition carries no information about failure type. Without this arm,
"typing helps because the classes are right" is not separable from "any
partition of the evidence helps" — which is the first thing a reviewer asks of a
typed index. 90 cells on the sweep subset.

**`--check-regression` turns `accept` into a verdict** (metric #22). ConDefects
ships one stdin→stdout program per fault, so there is no pass-to-pass suite to
borrow — but the shipped pool splits into one anyway, measured rather than
declared. Run the *faulty* version over the pool and the cases separate into
**F2P**, the ones it fails, which is the fault's footprint and the only half the
loop ever looks at; and **P2P**, the ones it already passes, which nothing in the
loop checks at any point. That asymmetry is the whole reason to measure it: a
patch that repairs the fault and breaks three P2P cases is indistinguishable
from a clean repair until someone runs the other half.

```bash
bash scripts/pipeline.sh eval --exp E2 --check-regression                # whole pool
bash scripts/pipeline.sh eval --exp E2 --check-regression --regression-cap 60
```

Three properties worth knowing before you spend the time:

* **It is not in the cell key.** Like `--check-overfit` it is a post-episode
  audit, not part of what the cell *is*, so switching it on does not invalidate
  a single finished cell and it composes with any `--exp`.
* **The split is paid once per program**, not once per accept: it belongs to the
  faulty version, so `src/oracle.py` memoises it and every mode and seed that
  accepts on that program is graded against the same denominator. A per-patch
  split would quietly grade two patches on two different partitions.
* **An uncapped run subsumes `--check-overfit`.** "Failed no scoreable case" is
  exactly `is_truly_correct`'s question, so the two audits share one pass
  instead of paying for two. Under a `--regression-cap` it answers the weaker
  "failed none of the sampled cases", so the real check still runs and the log
  records which basis was used in `truly_correct_basis`. The cap itself rides
  along in every row — a capped audit is a different measurement and the number
  alone would not say so.

### Switching the proposer — `--backend`

Nothing about the model lives in code. Both shard scripts take
`--backend ollama|cloud` (default `ollama`) plus `--model`, and the cloud path
skips the server lifecycle entirely.

```bash
# local, unchanged — this is what the reported grid runs under
bash scripts/eval_shard.sh --exp E2

# gpt-4o-mini
LLM_API_KEY=sk-... PRICE_IN_PER_MTOK=0.15 PRICE_OUT_PER_MTOK=0.60 \
BUDGET_USD_CAP=25 CONTEXT_LENGTH=128000 \
bash scripts/eval_shard.sh --exp E2 --backend cloud --model gpt-4o-mini

# o4-mini — a reasoning model. src/llm.py switches to max_completion_tokens,
# drops temperature, and records reasoning tokens separately; src/proposer.py
# raises the output ceiling so reasoning does not eat the answer.
LLM_API_KEY=sk-... PRICE_IN_PER_MTOK=1.10 PRICE_OUT_PER_MTOK=4.40 \
BUDGET_USD_CAP=60 CONTEXT_LENGTH=200000 \
bash scripts/eval_shard.sh --exp E2 --backend cloud --model o4-mini \
     --reasoning-effort medium
```

Four things the cloud path refuses to start without, each because it is
unrecoverable afterwards: `LLM_API_KEY`, both prices, and `BUDGET_USD_CAP`. An
unpriced ledger cannot be re-priced later — the token counts survive but which
rate applied does not — and a cap left at the local tripwire (`1`) stops a paid
grid three calls in. It also refuses `CONTEXT_LENGTH=32768`, the local default,
because `src/llm.py` uses it to reject an over-long prompt *before* paying for
it, and at the local window the transcript arm would refuse prompts that fit.

**`BUDGET_USD_CAP` is per process.** `llm.spent()` sums only the shard's own
ledger and every shard has its own, so N parallel shards need `total/N` each.

**A different `--model` writes different filenames.** Both scripts fold the
model id into the shard tag whenever it is not `qwen2.5-coder:7b`, so a cloud
E2 shard lands in `episodes_eval_E2_gpt-4o-mini_001_040.jsonl` rather than on
top of the local one. `model`, `backend` and `reasoning_effort` are all in the
cell key and in the shard's `.meta.json`, so `consolidate_evals.py` hard-stops
rather than pooling two proposers, and `freeze_results.py` refuses a log
holding two models.

**π is a property of the model.** A second proposer needs its own screen before
its bands mean anything — `screen_shard.sh --backend cloud --model ... --out
data/screen_<model>_<range>.json`, which is why `--out` is mandatory there.
Running a second proposer on the *existing* bands is still informative as a
robustness check, but the band labels then describe the local model's difficulty,
not the cloud model's, and the write-up has to say so.

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
bash scripts/pipeline.sh eval --exp E6-transcript --from 1 --to 1 --seeds 1 --budget 5
bash scripts/pipeline.sh eval --exp E8-audit      --from 1 --to 1 --seeds 1 --budget 5
```

Guard-only must log `n_guarded > 0` and steer-only exactly `n_guarded = 0`; if
those two look alike, the ablation flags did not take.

`E6-transcript` must show `tokens_in` rising with `round_index` — that is the
whole point of the arm, and a flat profile means the transcript is not reaching
the prompt. `E8-audit` must show guarded rounds carrying a non-null `fine_type`;
without the flag they carry `null`, and if they still do, the audit did not run.

```bash
python3 -c "
import json
rows=[json.loads(l) for l in open('data/episodes_trial.jsonl')]
for r in rows:
    if r['mode']=='transcript': print(r['round_index'], r['prompt_tokens'])
"
``` Clear the rehearsal
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

Same rules as the screen: **shards must not overlap**, and never hand-trim one
to skip finished work (`--resume-from` does that correctly and for free).

Two shards on one machine used to be forbidden because they raced on the response
cache. They no longer do — `src/llm.py` writes a cache entry to a temporary file
and `os.replace`s it into place, and every shard already gets its own ledger,
episode log and shard list. What is left is contention, not corruption: see
`fleet.sh` below for what that buys and what it does not.

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

### Running shards side by side — `fleet.sh`

```bash
bash scripts/fleet.sh eval --exp E1 --shards 6          # launches, returns at once
bash scripts/fleet.sh status                            # where all six are
bash scripts/fleet.sh tail 3                            # follow one
bash scripts/fleet.sh wait                              # block until drained
```

`fleet.sh` cuts a range into N contiguous non-overlapping shards, launches each
in the background, and keeps one manifest and one log per shard under
`logs/fleet/<run>/`. It exists because typing six `nohup` lines into a notebook
cell gets three things wrong that nothing downstream notices for days — see the
header of the script for all four, but two are worth repeating here.

**It forces `--no-stop-model` on every shard, and that is the point.** Both shard
scripts unload the weights on exit by default *and on purpose*, including when
they are only borrowing a server someone else started. So the first of six shards
to finish runs `ollama stop` on the model the other five are still calling, and
they each eat a cold reload. `--keep-serving` does **not** prevent this: it keeps
the server process, not the resident model. `fleet.sh wait` does the single
unload at the end.

**On `--backend cloud` it divides `BUDGET_USD_CAP` by the shard count.**
`src.llm.spent()` reads only its own process's ledger and each shard has one, so
six shards each honouring a $25 cap is a real ceiling of $150. Export the total
and let `fleet.sh` divide it; the arithmetic is printed at launch.

**What parallelism actually buys.** Ollama runs with `OLLAMA_NUM_PARALLEL=1`, so
model calls queue at the server — shards do not get parallel *generation*. What
overlaps is the oracle, which runs each candidate patch in a sandbox subprocess:
CPU work, off the GPU's critical path. So the useful shard count is bounded by
cores, and four to six is the honest range on a Colab T4. A fleet of twelve
mostly buys context switches.

**A drained fleet is not a covered grid.** Every shard exiting 0 says every
process finished, nothing more. Coverage is still the merge audit below.

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
python3 scripts/measure_redundancy.py
python3 scripts/measure_patch_quality.py
python3 figures/make_figures.py
python3 scripts/check_consistency.py
```

Then the sub-grids, each against the list it actually ran over:

```bash
python3 scripts/freeze_results.py --experiment ablation
python3 scripts/freeze_results.py --experiment transcript
python3 scripts/freeze_results.py --experiment oracle_sweep \
        --sweep-programs-from data/sweep_programs.txt
python3 scripts/freeze_results.py --experiment typing_sweep \
        --sweep-programs-from data/sweep_programs.txt
```

### What the two new post-hoc scripts produce

Both are free — they read the round log and the sources already on disk, and
make no model calls.

`measure_redundancy.py` → `data/redundancy.json`

| | |
|---|---|
| **Duplicate-Patch Rate** | the honest redundancy proxy, and the only one that is **not censored on guarded rounds** — `src/loop.py` logs the patch of a round it blocked, so DPR is comparable across arms without an `E8-audit` run. AST-normalised (docstrings dropped); identifiers are *not* renamed, because two patches differing only in a variable name are two patches |
| FSRR / NCDR / elimination yield / EPR | the θ-based family. Theorem 4.2(i) is the claim NCDR = 1.0 for typed. **All four are censored wherever an arm guards** — read them against the `type_metrics_censored` column the script prints, or read `E8-audit` instead |
| class-revisit survival | how long before a type reappears, as a curve, right-censored at B |
| `success@b`, both budgets | as a **curve** over b = 1..B, not one point. Proposal budget counts every round; oracle budget counts only rounds that reached the oracle. Theorem 4.3(a) puts typed and untyped level on the second one, so a gap there is a finding about the guard, not about typing |
| AUC-Budget | one number per curve, so no single B is cherry-picked |
| **pass@k** | the repeated-sampling baseline, straight out of E1 — *Large Language Monkeys* is the strongest objection to this whole line of work, and E1 under `--force-full-budget` already **is** that experiment. Both the empirical curve and the `1-(1-π̂)^k` it implies. Plot it under the typed arm's `success@b`: if they coincide, the contribution is not there |

`measure_patch_quality.py` → `data/patch_quality.json`

| | |
|---|---|
| patch verbosity | accepted patch's edit size against the author's own fix (LOC and hunks). Plausibly a win by construction — steering cuts the refine-again loop that inflates patches |
| regression rate (#22) | share of the **P2P** cases an accepted patch broke — behaviour that already worked before the repair. Needs `--check-regression`. Pooled over cases rather than averaged over episodes, so a program with 200 already-passing cases does not get one vote alongside a program with three. `None` means the audit never ran, which the script keeps distinct from a measured zero; a fault whose footprint is the whole pool has no P2P case and so cannot regress |
| correct/plausible ratio | **a guardrail, not a win to hope for.** Steering pushes away from seen failure classes and nothing guarantees it pushes toward the *correct* one rather than the plausible-but-wrong one. Needs `--check-overfit`; the script reports how many episodes the audit could actually discriminate on and returns `None` rather than a reassuring `1.00` when that is zero |

### Measuring `c` for real — run this deliberately

```bash
python3 scripts/measure_typing_coherence.py --limit-tasks 20   # pilot first
python3 scripts/measure_typing_coherence.py                    # then the rest
```

Not part of `pipeline.sh analyse`: it re-executes every logged patch against the
full test pool, which is hours of sandbox time rather than seconds.

§9 of the paper names real type coherence as the single decisive open quantity,
and `measure_coherence.py` says outright that it cannot separate ρ from c. This
script can, because ConDefects supplies a ground truth that costs nothing but
CPU: the **behavioral signature** σ(p) = the set of pool cases p fails. Two
patches with the same σ are indistinguishable to a complete oracle, so σ induces
the finest partition any test-based notion of "same failure" could justify —
the role ground-truth root cause plays in Igor and Semantic Crash Bucketing.

Scoring θ against σ gives the two directions that literature named:

- **homogeneity** — reported as `c_hat`. Low means one θ bucket mixes patches
  that fail different tests: *under-counting*, and the direction that makes an
  elimination unsound.
- **completeness** — low means one real class is split across θ buckets:
  *over-counting*, which wastes elimination but is not unsound.
- V-measure and adjusted Rand index alongside.

`c_hat` is an operationalisation of Def. 3.1's `c`, not `c` itself — a partition
statistic against a per-attribution probability — and the write-up must say so.
What it does replace is a Dirichlet assumption, with a measured floor.

Both caps (`--max-patches-per-task`, `--max-cases-per-task`) are written into
the report along with how much was dropped. A smaller case cap makes σ coarser,
which can only *raise* homogeneity, so `c_hat` is an upper bound at the cap it
ran under. Report the cap with the number.

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
foreign rows aside (match on `model`) and re-run. If they came from a cloud
shard they are already in their own file: the model id is folded into the shard
tag, so `episodes_eval_E2_gpt-4o-mini_*.jsonl` never merged with the local one
in the first place — you consolidated both on purpose.

**`--backend cloud needs PRICE_IN_PER_MTOK set`** — deliberate, and not worth
working around by exporting a zero. A ledger written at price 0 records real
token counts under a rate that never applied, and nothing afterwards can tell
you which card to re-price it with. Set the real numbers; `.env.example` lists
both cards.

**`REASONING_EFFORT=... but <model> is not an o-series model`** — the effort
would enter the cache key and the cell key while changing nothing about the
call, forking every cell into two protocols that produce identical results.
Unset it, or pass an o-series id.

**Before the first paid o-series run, probe the parameter shape.** One call,
about $0.001. The o-series' handling of an explicitly-passed `temperature` has
varied across snapshots, and the failure mode is a whole shard of rejected
calls hours in.

```bash
LLM_API_KEY=sk-... .venv/bin/python - <<'PY'
import openai, os
c = openai.OpenAI(api_key=os.environ["LLM_API_KEY"])
for kw in ({"max_tokens": 64, "temperature": 1.0},
           {"max_completion_tokens": 64, "temperature": 1.0},
           {"max_completion_tokens": 64}):
    try:
        r = c.chat.completions.create(model="o4-mini",
                                      messages=[{"role": "user", "content": "say ok"}], **kw)
        d = r.usage.completion_tokens_details
        print("OK ", kw, "->", r.usage.completion_tokens, "out,",
              getattr(d, "reasoning_tokens", None), "reasoning")
    except Exception as e:
        print("ERR", kw, "->", type(e).__name__, str(e)[:120])
PY
```

`src/llm.py` sends the third shape. If the second also works, nothing needs
changing; if the third fails, that is the one to fix.

**A cloud run's `usd` looks too low for o4-mini** — check `reasoning_out` in the
ledger. Reasoning tokens are *inside* `completion_tokens`, so `usd` is right;
what is misleading is comparing the visible output length against the bill.
`src/llm.py` breaks the two out precisely so the token metrics can separate
"what the model wrote" from "what the model thought".

**`proposal_error="truncated_response"` on the longest tasks, cloud o-series** —
reasoning was spent out of the same budget as the answer.
`src/proposer.budget_for_source` already raises the ceiling for o-series ids;
if it still happens, raise `_MAX_BUDGET_REASONING`. Note this re-keys the cache
for the affected programs, which is acceptable on a first run of that model and
expensive afterwards.

**`measure_redundancy.py` warns that every arm is >5% censored** — expected
whenever guards fire and `--audit-guarded` did not run. FSRR, NCDR, elimination
yield and EPR are not comparable across arms in that state. Duplicate-Patch Rate
is unaffected and can be read as it stands; run `E8-audit` on the sweep subset
for a comparable θ-based version.

**The transcript arm's `success@B` is *below* no-memory** — that is a plausible
real result, not necessarily a bug: `src/proposer.py`'s docstring records it
happening at 0.50 vs 0.63 on the first 30 tasks. Before reporting it, rule out
truncation — count `proposal_error="context_overflow"` per arm, and confirm
`LLM_CONTEXT_TOKENS` matches what the backend actually serves. A transcript arm
measured through a cropped prompt is measuring the crop.

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
