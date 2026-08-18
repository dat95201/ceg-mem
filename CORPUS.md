# Corpus — turning a merged screen into the frozen task list

Operational runbook for steps E0 and 4 of [PLAN.md](PLAN.md): earn the oracle
assumption, then pick the tasks every experiment runs on. Why selecting on
measured π̂ is legitimate rather than cherry-picking is argued in
[SELECTION.md](SELECTION.md); how the screen that measures π̂ is run is in
[SCREENING.md](SCREENING.md). This file is only how to get from one to the other.

> **SELECTION.md §5 describes a different scheme** — tiers named
> mechanism/acceptance/saturation, keyed on `wholesale_rate`. Nothing implements
> it. What runs is the five absolute π bands below, and they are what
> `select_corpus.py` and `build_strata.py` agree on. One of the two has to give
> before the paper is written.

---

## 1. Three commands, in this order

```bash
# E0 - the oracle gate. CPU only, no model calls. Independent of the screen,
# so it can run at any time, on any machine, in parallel with screening.
python3 scripts/validate_oracle.py \
    --programs $(python3 -c "import json;print(' '.join(c['name'] for c in json.load(open('data/candidates.json'))['candidates']))") \
    --corpus-size 360 --min-siblings 1 --data-dir data/pool --jobs 6
#   -> data/pool/tasks.json          the frozen CANDIDATE POOL (not the corpus)
#      data/pool/oracle_validation.json

# 4a - fill the pi bands from that pool
python3 scripts/select_corpus.py --pool data/pool/tasks.json \
        --screen data/screen_merged.json --min-calls 38
#   -> data/tasks.json               the frozen CORPUS
#      data/screening.json           every candidate and what became of it

# 4b - stratify
python3 scripts/build_strata.py
#   -> data/strata.json
```

Order matters: `select_corpus.py` refuses a pool that is not frozen, and
`build_strata.py` refuses a corpus that is not frozen. Re-run `build_strata.py
--force` after E1 and `fit_theory.py` to add the drift audit.

---

## 2. E0 — the oracle gate

The paper takes oracle soundness as given (Assumption 1). On a real benchmark it
has to be demonstrated before anything is spent through it.

**Stage 1 — is this fault usable?** Test data present; the reference passes the
cases it is run on; the faulty version is actually refuted; neither times out.

**Stage 2 — does the oracle catch bugs it has not seen?** Up to three *natural*
mutants per task — other people's wrong submissions to the same problem — judged
by the same sampling oracle the repair loop calls. A mutant the sample accepts
gets a second opinion from the whole pool, which separates `missed` (the sample
was too small — a real limit, reported) from `equivalent` (the pool cannot refute
it either — no oracle could, so it leaves the denominator rather than being
charged against the oracle).

| threshold | value |
|---|---|
| a program passes | ≥ 2/3 of its scoreable natural mutants caught |
| the pool freezes | ≥ 30 of every 40 in the cohort passing |
| reference must answer | 20 cases |
| mutants judged per task | 3 |

`--corpus-size` is both the cohort size for the gate and how many *passing*
programs to freeze. The two sets are deliberately different: the gate is measured
on the first `--corpus-size` candidates so the pass rate stays unbiased, and the
pool is then topped up past that cohort. A pass rate computed over a set already
filtered on passing would be vacuous.

**360, not 115.** The band quotas below total 115, but the pool has to be far
larger than the corpus it feeds: the π̂ distribution is bimodal, so the middle
bands are thin, and the gate itself drops candidates. A pool sized to the quotas
cannot fill them. The ceiling is the candidate list — 527 — and the risk of
asking for too many is the opposite one: freezing requires the cohort to *reach*
`--corpus-size`, so a value above the number of stage-1 survivors leaves
`"frozen": false` and `select_corpus.py` refuses it.

**`--programs`, not `--select`.** Passing the pool explicitly keeps
`validate_oracle.py` walking the seeded `K_proxy`-stratified order that
`candidates.json` defines — the same order the screen was cut into shards from,
and the order `select_corpus.py` walks when filling bands. Left to itself the
script builds its own shuffle over every supported program, which would validate
faults the screen never measured and order the survivors differently. With
`--programs` given, `--select` is ignored.

Run the publication freeze with `--jobs 1`. A timeout is a verdict here, not a
retry, so a program near the sandbox wall-clock limit can fail under parallel
load when it would not have serially. Serial is ~6–12 h; `--jobs 6` is ~2 h.

`--programs` passes the pool explicitly and preserves its order. Without it
`validate_oracle.py` builds its own candidate order, which is not the
`K_proxy`-stratified one the screen was cut from.

---

## 3. The bands

Absolute, from the paper — not terciles of whatever happens to be on disk.
`select_corpus.py` and `build_strata.py` hold the same table.

| stratum | π̂ | quota | role |
|---|---|---|---|
| `dead` | [0.00, 0.02) | 20 | control — B binds hardest, memory grows longest |
| `hard` | [0.02, 0.08) | 30 | **primary** — largest predicted effect |
| `medium` | [0.08, 0.18) | 20 | primary |
| `easy` | [0.18, 0.35] | 30 | primary |
| `too_easy` | (0.35, 1.00] | 15 | control — predicted null; nothing is ever stored |

The quotas are unequal on purpose, for two reasons.

**Supply.** The π̂ distribution is bimodal. `medium` is the trough between the
two modes, so a quota of 30 there would demand roughly 450 screened candidates on
its own.

**Demand.** The paper's own simulation puts the largest predicted effect at the
low-π end — A₁₂ of 0.83 / 0.96 / 1.00 for Easy / Medium / Hard, oracle calls
23.07 → 6.50 on Hard. Proposition 4.5's guard-cost gap is stated to *"grow with
task difficulty"*, and Corollary 4.4's budgeted-success advantage holds only
*"whenever B binds"* — which is exactly the low-π regime. So `hard` gets a full
quota and `dead` is generous.

The two control bands are kept, not discarded: an effect that appears only where
the mechanism has room to operate is stronger evidence than a uniform one.
`too_easy` in particular is the saturated class — the first proposal is accepted,
no counterexample is produced, and all three conditions coincide by construction.
Because selection is banded on π̂, that class is known *at selection time*; no
post-hoc saturation screen is needed.

### Changing the size

```bash
--quota BAND=N        # repeatable
--min-calls N         # a candidate screened with fewer draws is not placed
```

Each band takes `min(quota, available)`, walking the pool in its frozen order,
first-come. A band that under-fills prints as `SHORT` and makes the corpus
smaller and the grid cheaper — it is not an error. There is no seed here: the
order was fixed back in Stage 0.

---

## 4. `--min-calls` is the trap

Default is 20, and it does two different jobs that are easy to conflate.

**It filters partial screens.** A shard cut short by the budget cap has fewer
draws for its last tasks; `--min-calls` keeps those out of the corpus.

**It has to match the depth the bands need.** π̂ lives on a grid of `1/K`, so
`K` decides which bands a task can reach *at all*:

| K | reachable |
|---|---|
| 10 | `hard` is **unreachable** — `0/10` is `dead`, `1/10` is `medium` |
| 13 | one outcome in `hard`, at 0.077, hard against the upper edge |
| 38 | three interior points in `hard` — a measurement, not a coin flip |

So a corpus frozen off a K=10 screen has an empty `hard` band, which is where the
paper predicts its largest effect. `consolidate_screens.py` prints the depth
audit and the K that would resolve it.

Two failure modes follow directly:

- `--min-calls` **above** the screen depth → every candidate is `under_screened`
  and the corpus comes out empty.
- `--min-calls` **at** the screen depth but the depth is too shallow → the corpus
  fills, and `hard` is silently 0.

Neither raises; both are visible in the printed band table and in
`data/screening.json`.

---

## 5. Reading `data/screening.json`

Every candidate in the pool and what became of it — the full distribution
[SELECTION.md §9(2)](SELECTION.md) commits to reporting, including everything
excluded.

| disposition | meaning |
|---|---|
| `selected` | placed in its band |
| `band_full` | its band's quota was already met by earlier candidates |
| `under_screened` | fewer than `--min-calls` draws |
| `not_screened` | the screen never covered it |

`not_screened` is where a task the screen could not measure lands — an
interactive problem, say, on which no counterexample oracle exists. It is
recorded rather than deleted, so the funnel stays reconcilable.

---

## 6. Before you freeze

```bash
python3 scripts/consolidate_screens.py       # coverage, depth, conflicts
```

- **coverage** — no `GAPS` beyond exclusions you can name.
- **depth** — every task at the K the bands need; no `BLIND TO:` line.
- **conflicts** — `DISAGREEMENT` and `DIVERGENT CHECKOUT` both empty.
- **model** — the screen's model id is the one the experiments will use. π is a
  property of the model; a corpus stratified on one model's π̂ is not stratified
  for another.
- **pool** — `data/pool/tasks.json` carries `"frozen": true`. If it does not, the
  gate did not pass and `select_corpus.py` will say so.

---

## 7. The discipline this step exists to protect

Selection happens on the **no-memory arm, before any treatment**, which makes it
dose-range choice and not outcome selection. Two things keep it that way, and
both are mechanical rather than promised:

**Two independent π̂.** The screen's draws carry cache nonce `pi-pilot|…` and are
spent here; the π̂ that results tables print comes from E1, whose draws carry
`proposal|…`. Two independent samples, so conditioning on one cannot inflate the
other.

**The migration matrix is published.** Tasks move between the two π̂ — that is
regression to the mean, it is expected, and `build_strata.py` writes it out
rather than hiding it, because its size *is* the risk. Re-run with `--force`
after E1 and `fit_theory.py` to fill it in.

---

## 8. When something refuses

**`data/pool/tasks.json is not frozen`** — the cohort did not clear the gate
(fewer than 30 of every 40 passing), or fewer candidates were validated than
`--corpus-size`. Read `data/pool/oracle_validation.json`: `corpus_gate_ok`,
`n_cohort`, `n_cohort_passing`.

**`data/candidates.json is not frozen`** — you pointed `--pool` at the Stage-0
candidate list instead of the validated pool. They are different artifacts:
`candidates.json` is what gets validated, `data/pool/tasks.json` is what survived.

**`SHORT: {...}`** — a band under-filled. Screen more candidates and re-run; the
walk is deterministic, so already-selected tasks keep their places.

**`is already frozen - pass --force`** — the corpus on disk is frozen. Overwriting
it is a real decision: anything already run against it was run against a
different task list.
