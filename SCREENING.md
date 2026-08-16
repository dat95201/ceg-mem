# Screening — measuring π̂ in shards across several machines

Operational runbook for step E0b of [PLAN.md](PLAN.md): draw N i.i.d. no-memory
proposals per candidate and count how many the oracle accepts. Why π̂ has to be
measured at all, and why selecting on it is legitimate, is in
[SELECTION.md](SELECTION.md) — this file is only how to run it.

The screen is ~5,300 model calls at ~15–20 s each, so it is cut into index
ranges over `data/candidates.json` and run on several machines.

| | |
|---|---|
| `scripts/screen_shard.sh` | runs one index range on one machine |
| `scripts/consolidate_screens.py` | merges the shards and audits the join |

---

## 1. The protocol contract

Every machine must agree on the values below. They are not preferences: each one
changes what π̂ *is*, and a shard measured under a different one is a different
instrument, not a noisier reading of the same one.

```
MODEL=qwen2.5-coder:7b   TEMPERATURE=1.0        SEED=20260717
CONTEXT_LENGTH=32768     MAX_EXAMPLES=100       SANDBOX_TIMEOUT_SEC=30.0
```

They split into two halves, and the difference decides what a mistake costs.

**In `src.llm`'s cache key** — `model`, `temperature`, and `seed` (via the draw
nonce). Changing one makes every draw already bought unreachable: the new run
asks for keys nothing has stored. Expensive, but self-announcing — you watch it
re-buy.

**Not in the cache key, and this is the dangerous half.** `max_examples` re-judges
the very same completions against a weaker oracle. `sandbox_timeout_sec` turns a
slow correct patch into a wrong one. The served context window decides whether
the prompt arrived whole or had its head cropped. All three change π̂ for free
and leave no trace in the cache — so all three are recorded in every shard
report, and `consolidate_screens.py` refuses to merge across a disagreement.

### The context window, specifically

Ollama picks it from available VRAM — `4k/32k/256k`, per `ollama serve --help` —
and **truncates** an over-long prompt instead of refusing it. The
OpenAI-compatible endpoint has no field to raise it, and the window reaches
neither the cache key nor `RoundRecord.model`. So the same model id on two
machines can be two different instruments with nothing to say so. On this
project's own hardware the ambient desktop server serves this model at **4096**.

`screen_shard.sh` therefore pins `OLLAMA_CONTEXT_LENGTH`, loads the model, asks
`/api/ps` what is *actually* being served, and refuses to spend on a mismatch.
That check — not who started the server — is what makes a shard trustworthy.

### Incremental by construction

Draws are nonced `pi-pilot|<task>|seed<S>|call<i>` for `i` in `0..K-1`, so
re-running a shard at a larger `--calls` replays every earlier draw from cache
and buys only the difference: 10 → 50 costs 40 new calls per task, not 50.
Nothing else in the key moves — `max_tokens` is `budget_for_source(faulty
source)`, and the prompt is that source plus the two worked examples
`Task.spec_note` picks, both fixed by the checkout.

This is why `--calls 10` is a sensible first pass rather than a commitment.

---

## 2. Verifying a machine before giving it real work

Do this once per machine. Roughly five minutes. Candidate #2 is used because it
is a single short task with 15 test cases.

**Plan only, spends nothing:**

```bash
bash scripts/screen_shard.sh --from 2 --to 2 --calls 10 --dry-run
```

Check the `protocol` line against §1, and open `data/shards/shard_002_002.txt`:
its `candidate_order_sha256` must be byte-identical on every machine. A
different digest means that machine is on another commit of
`data/candidates.json`, so its indices mean something else.

**Then for real (~3–4 min):**

```bash
bash scripts/screen_shard.sh --from 2 --to 2 --calls 10
```

Four lines have to appear:

```
starting ollama on 127.0.0.1:11435 with OLLAMA_CONTEXT_LENGTH=32768
qwen2.5-coder:7b already present - nothing to download
loading qwen2.5-coder:7b and verifying the served window
  ok: {"backend": "ollama", "context_length": 32768, "model_digest": "dae161e27b0e90dd", ...}
```

`context_length` must read 32768 and `model_digest` must match the other
machines — the same tag can point at a different blob after a re-pull.

**Check what came out:**

```bash
wc -l data/calls_screen_002_002.jsonl        # -> 10, this task had no cache
lsof -i :11435 | wc -l                       # -> 0, the server is down
ollama ps                                    # -> qwen2.5-coder:7b is not listed
python3 -c "
import json; d = json.load(open('data/screen_002_002.json'))
print(d['model'], d['seed'], d['max_examples'], d['sandbox_timeout_sec'])
print(d['runtime'])"
```

**Prove the incremental replay — the check that matters most:**

```bash
bash scripts/screen_shard.sh --from 2 --to 2 --calls 14
wc -l data/calls_screen_002_002.jsonl        # -> 14, NOT 24
```

The ledger records only real calls; a cache hit is never logged. If it reads 24,
something in the cache key moved between the two runs — stop and diff the
`protocol` lines rather than screening 527 tasks twice.

**Then clear the rehearsal:**

```bash
rm -f data/screen_002_002.json* data/calls_screen_002_002.jsonl data/screen_merged.json
rm -rf data/shards logs
```

The 14 cached draws stay, and the real shard replays them for free.

---

## 3. Running the screen

Shards are contiguous index ranges, 1-based and inclusive, over
`data/candidates.json`. **They must not overlap.** Any contiguous range is
already balanced on `K_proxy` — the order is the seeded stratified round-robin
from `select_candidates.py` — so a shard is a smaller screen, not a skewed one.

```bash
bash scripts/screen_shard.sh --from   1 --to 132     # machine 1
bash scripts/screen_shard.sh --from 133 --to 264     # machine 2
bash scripts/screen_shard.sh --from 265 --to 396     # machine 3
bash scripts/screen_shard.sh --from 397 --to 527     # machine 4
```

Each writes four files:

```
data/screen_<from>_<to>.json         the pi_hat report
data/shards/shard_<from>_<to>.txt    the program list, with the pool digest
data/calls_screen_<from>_<to>.jsonl  that shard's call ledger
logs/screen_<from>_<to>.log          the trace
```

A shard of 132 tasks at `--calls 10` takes about **7 hours**, so run it under
`tmux` or `nohup`. It is resumable: `measure_pi.py` rewrites the report after
every task, and re-running the identical command replays from cache.

> **Never hand-trim the shard list to skip what is already done.** The report
> holds only what that run walked, so a trimmed re-run silently drops the rest.
> Re-run the whole range; the finished part is nearly free.

Chaining shards on one machine, without reloading 6.4 GB of weights each time:

```bash
bash scripts/screen_shard.sh --from 1  --to 66  --no-stop-model --keep-serving
bash scripts/screen_shard.sh --from 67 --to 132                 # tears down
```

By default the model is unloaded and any server this script started is stopped.
A server that was already listening on the port is reused and left running — but
the weights are still unloaded, because a shard that borrows a machine should
not walk away leaving 6.4 GB pinned on it.

---

## 4. Merging

Collect `data/screen_*.json` and `data/calls_screen_*.jsonl` onto one machine,
then:

```bash
python3 scripts/consolidate_screens.py
```

Writes `data/screen_merged.json` (one record per task, deepest screen wins —
never averaged, since a deeper screen replays the shallower one's draws and
adding them would count those twice) and `data/calls_screen.jsonl` (the ledgers
concatenated, deduplicated on `cache_key`, idempotent).

`cache/` does **not** need syncing. It is content-addressed, so copying it
between machines is conflict-free, but shards are disjoint so their caches are
too — and the experiment proper uses a different nonce prefix (`proposal|…`), so
the screen's cache is useless to it. Sync it only if you intend to deepen a
shard on a machine that did not run it.

### What the audit catches

| | what it means |
|---|---|
| protocol disagreement | a shard measured a different quantity — **hard stop**, with the cost of re-running spelled out per §1's two halves |
| `GAPS` | index ranges nobody screened |
| `PARTIAL` | a shard walked fewer tasks than its own list; `measure_pi.py`'s `complete: true` only tracks the budget cap, so a Ctrl-C leaves the last checkpoint claiming completeness |
| `DIVERGENT CHECKOUT` | a program hashes differently between shards. The faulty source and the worked examples *are* the prompt, hence the cache key, so those two shards screened different programs under one name |
| `DISAGREEMENT` | the same task at the same depth gave different counts. Draws are cached under deterministic nonces and the oracle is seeded, so this cannot happen unless the machines are not interchangeable — usually `SANDBOX_TIMEOUT_SEC` firing on the slower one. Fix and re-run both; do not pick a winner |

---

## 5. Deepening

π̂ lives on a grid of `1/K`, and the bands `select_corpus.py` cuts on are
`dead` [0, 0.02) · `hard` [0.02, 0.08) · `medium` [0.08, 0.18) · `easy`
[0.18, 0.35] · `too_easy` (0.35, 1].

At `K = 10` **no outcome at all lands in `hard`** — `0/10 = 0.000` is `dead`,
`1/10 = 0.100` is `medium` — and `hard` is where the paper predicts its largest
effect. `K = 13` puts one outcome there, but at `1/13 = 0.077`, hard against the
upper edge, so a task whose true π is 0.03 reaches it only by drawing exactly
one success. `K = 38` is the first depth with three interior points, which is
what makes the band a measurement rather than a coin flip.

`consolidate_screens.py` prints both numbers and the per-task cost:

```
deepening (a re-run at a larger --calls replays what is already bought and pays
only the difference, so this is cheap to act on)
  K= 13  puts any outcome in `hard` at all                          [+3/task]
  K= 38  puts 3 outcomes inside `hard` - a measurement, not a coin flip [+28/task]
  -> re-run the same shards with --calls 38
```

Deepening is the same command with a larger `--calls`, on the machine that holds
that shard's cache:

```bash
bash scripts/screen_shard.sh --from 1 --to 132 --calls 38
```

---

## 6. When something refuses

**`served context is 4096, not 32768`** — the server is serving a window it
chose itself. If you started it by hand, restart it as the error message shows;
otherwise stop it and let the script start its own. Do not work around this: the
prompt would be silently cropped, worst on the arms carrying the most evidence.

**`something is already listening` / model absent** — a server already on the
port is reused, and the model is pulled only if that server does not have it.
Neither is an error. Note that `ollama list` is itself a client and fails on a
machine with no server running, which is why the check goes through `/api/tags`.

**`shards were not measured under the same protocol`** — read which field
disagrees. If it is `model`, `temperature` or `seed`, the odd shard's draws are
unreachable and re-running buys them again. If it is anything else, the draws
replay and only the verdicts are recomputed.

**Budget cap** — irrelevant locally: `screen_shard.sh` prices the calls at zero.
The ledger still records tokens, `finish_reason` and seconds, which is what
PLAN.md's rate card has to be re-derived from.

---

## 7. Cost

Wall clock, not money — the local backend is free.

| | draws | one machine | four machines |
|---|---|---|---|
| 527 candidates at `K = 10` | 5,270 | ~29 h | ~7 h each |
| deepen all to `K = 38` | +14,756 | ~82 h | ~20 h each |

At ~20 s per call, most of it model generation rather than sandbox execution.
`measure_pi.py` does not parallelise within a shard; the sharding *is* the
parallelism.

> **π is a property of the model.** A corpus stratified on π̂ measured with
> `qwen2.5-coder:7b` is not stratified for anything else. If the reported runs
> are to use a different proposer, this whole screen has to be re-run under it —
> the model id is in the cache key, so nothing replays.
