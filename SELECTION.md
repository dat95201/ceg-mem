# Selecting the 120-fault hard corpus

Written after the full `Test.zip` landed (2026-08-06). This document fixes the
selection rule, states the evidence behind every parameter in it, and records
what the rule cannot do — so that `data/tasks.json` can be *checked* against a
declared design rather than trusted.

Read `STATUS.md` §3 and §5–6 first for the state this replaces.

---

## 0. What the full archive changes

`external/ConDefects/Test.zip`, 22,279,934,402 bytes, read end-to-end from its
central directory:

```
108,028 entries · 67,327,401,354 bytes uncompressed (66.9% ratio)
199 contests: 135 abc · 52 arc · 12 agc
1,422 contest/problem directories carrying an in/ tree
contest dates 2021-10-02 → 2024-06-30
```

The central directory is present and readable, which is the exact failure the
salvaged download had. Against the 985 Python coding tasks in `Code/`:

| | coding tasks with test data | Python faults | hard pool (rating ≥1600) |
|---|---|---|---|
| `Test_partial/` (salvaged) | 374 | 931 | **123 coding tasks** |
| `Test.zip` (full) | **980** | **2,844** | **336 coding tasks** |

This closes **P2** (corpus frozen on 39% of the benchmark) and it is what makes
a 120-fault freeze possible at all: the walk in `validate_oracle.py` claims a
coding task the moment one of its faults enters stage 2, so the pool must hold
`POOL_HEADROOM × 120 = 180` coding tasks before the run may start. The salvaged
tree held 123 and would have been refused. The full tree holds 336 at floor
1600, and 188 once §3a's natural-mutant requirement is applied.

**Unpack target is `Test/`, not `Test_partial/`.** `.env` sets no
`CONDEFECTS_TEST_DIR`, so `src.adapter.TEST_DIR` resolves to
`external/ConDefects/Test`. Unpacking anywhere else silently freezes the corpus
against the wrong tree, and `data/tasks.json` would record it — which is the
one thing that check is there to prevent.

Disk: 67.3 GB needed, 208 GB free. `Test_partial/` (19 GB) is reclaimable once
`Test/` verifies, and is worth deleting rather than keeping, because a stale
partial tree parked next to a good one is exactly the confusion
`salvage_test_zip.py` was written to avoid.

---

## 1. The decision

**120 faults, all from one band: hard. No easy/medium quotas.**

The earlier design balanced 40 easy / 40 medium / 40 hard. The pilot retired
it. Median π̂ by proxy stratum came out **0.300 easy · 0.275 medium · 0.038
hard**: two thirds of that corpus sat where the proposer succeeds on the first
try, no counterexample is ever produced, memory is never written, and the cell
cannot separate `no_memory` from `typed` — the comparison the paper exists to
make. The budget now buys 120 discriminating cells instead of 40 of them plus
80 that answer themselves.

This is a deliberate narrowing of external validity and must be stated as one:
the paper will report a hard-stratum result, not an across-difficulty one. The
synthetic study already reports per-stratum numbers (Table 2), so the real
study lines up against its Hard column and nothing else.

---

## 2. The selection rule

Everything the seed touches is a pure function evaluated **before any program
runs**. In order:

1. **Enumerate.** `adapter.discover()` sorts faults by name, so the input to
   the shuffle never depends on filesystem iteration order.
2. **Require test data.** Drop faults whose coding task ships no `in/` tree. A
   fault with no test data can never clear stage 1. *(2,864 → 2,844)*
3. **Apply the hard floor.** Drop faults whose coding task is rated below
   `--hard-floor` in `difficulty.txt`. A task with no rating is dropped, not
   assumed hard. *(2,844 → 793 faults over 336 coding tasks)*
4. **Shuffle** with `random.Random(20260717)` — the single source of randomness.
5. **Require a natural mutant.** Drop faults whose coding task supplies no
   *other* wrong submission, because stage 2 validates the oracle against
   exactly those siblings and a fault without one cannot be validated at all.
   *(336 → 188 coding tasks)*
6. **Walk** that order, at most one fault per coding task, running stage 1 →
   stage 2 on each, until 120 have passed.

Step 5 sits **after** the shuffle and step 3 before it, and the split is not
arbitrary. The rating floor defines *which population the corpus is a sample
of*, so changing it should redraw — and does. The sibling requirement is an
eligibility constraint: it says whether a given fault can serve as an
experimental unit at all. Applying eligibility after the shuffle makes it
additive — the faults that fail it drop out and every other fault keeps its
position. Adding `--min-siblings 1` this way kept **91 of the previous draw's
120**; filtering before the shuffle kept 18, throwing away tasks for no reason
but a changed index, including three of the five whose π̂ had already been paid
for.

Then the two gates, both from the proposal, neither inferred from the data:

- **Per program:** passes if the sampling oracle catches ≥2/3 of the task's
  *scoreable natural mutants* — up to three other wrong submissions to the same
  coding task. Held as a fraction, not "2 of 3", because a task supplies
  between one and three siblings and the criterion has to mean the same thing
  for each: with one mutant it must be caught, with two both must be.
- **Per corpus:** freezes only if ≥ 30/40 of the *cohort* — the first 120
  stage-2 candidates — pass, i.e. **≥90 of 120**. The corpus is then topped up
  past the cohort until it holds 120 *passing* faults. Cohort ≠ corpus on
  purpose: a pass rate computed over a set already filtered on passing is
  vacuous.

`data/tasks.json` records the floor, the seed, the pool size and a SHA-256 of
the shuffled candidate order, so a re-run is checked, not trusted.

### Parameters, and why these values

| Parameter | Value | Basis |
|---|---|---|
| `--select` | `hard` | §1 |
| `--hard-floor` | **1600** | see below |
| `--min-siblings` | **1** | §3a — three would leave 68 coding tasks |
| `--mutants-per-task` | 3 | caps a task with eleven submissions |
| `--hard-ceiling` | none | see §4 |
| `--corpus-size` | 120 | proposal |
| `--seed` | 20260717 | the project's single master seed |
| `MUTANT_CATCH_FRACTION` | 2/3 | proposal's "2 of 3", held as a fraction |
| `CORPUS_PASS_FRACTION` | 30/40 → 90/120 | proposal, held as a fraction |
| `POOL_HEADROOM` | **1.5** | 2.0 would refuse every run once the sibling requirement halves the pool; warns below 2.0 |
| `--jobs` | 1 for the publication freeze | see §6 |

**Why the floor is absolute and why it is 1600.** Terciles of "whatever is on
disk" moved with the test tree — the cut landed at 1577 on the salvaged tree
and 1639 on the full one — so the same seed named a different corpus depending
on how much of a download had survived. A frozen corpus may not have that
property. 1600 is AtCoder's own blue boundary, fixed outside this project, and
the pilot puts the band above it at median π̂ = 0.038, inside the paper's Hard
range of 0.02–0.08.

**Why not raise the floor further.** The pool shrinks faster than the band
sharpens, and `POOL_HEADROOM` binds:

| floor | coding tasks, no sibling rule | with ≥1 sibling (§3a) | headroom vs. 120 |
|---|---|---|---|
| 1600 | 336 | **188** | 1.57× |
| 1800 | 279 | 152 | 1.27× — **refused** |
| 2000 | 235 | 127 | 1.06× — **refused** |

Raising the floor is no longer available: the sibling requirement already
halves the pool, and 1800 falls below `POOL_HEADROOM`. If the realized π̂ from
E1 comes out above the band, the fallback is now to drop `--min-siblings` back
to 0 and accept planted mutants again, or to accept a smaller corpus — not to
raise the floor. This is a real cost of §3a and is recorded as one.

---

## 3. What actually predicts π — and what does not

The paper's Easy/Medium/Hard are bands of π, and π is *measured*: one model
call per draw. It therefore cannot drive selection, because π̂ is only knowable
for faults already in the corpus and paying for it on candidates that go on to
fail the mutation gate burns budget on programs nobody keeps. Selection has to
run on a free a-priori proxy, so the question is which free feature to use.

Eight candidates, scored against the pilot's measured π̂ (n=60 programs × 40
i.i.d. draws, `data/pilot_v0/pi_pilot.json` × `data/pilot_v0/oracle_validation.json`
— retired from the pipeline when the corpus was re-drawn, kept because the
hard-only corpus that replaced it cannot re-derive a correlation that spans the
rating range; see `data/pilot_v0/README.md`):

| feature | Spearman ρ with π̂ | verdict |
|---|---|---|
| `examples_tried` (cases sampled before the fault is refuted) | −0.352 | confounded with rating — see below |
| **AtCoder rating** (`difficulty.txt`) | **−0.350** | **the only usable proxy** |
| `n_test_cases` | −0.240 | weak, and a property of the contest, not the fault |
| mutant capacity (AST sites for the 3 fault types) | −0.185 | weak |
| program LOC | −0.108 | no |
| changed lines, faulty→correct | +0.062 | no |
| char-level diff size, faulty→correct | +0.032 | no |
| diff hunks | +0.029 | no |
| number of fault lines | 0.000 | **degenerate: all 60 are single-line faults** |

**The fault-level features are dead.** The intuitive story — a bigger, more
structural fix is harder to one-shot — does not survive contact with the data:
every diff-shape feature is at |ρ| < 0.07. ConDefects Python faults are
single-line by construction, so there is no multi-hunk tail to exploit either.
Repair difficulty here is not a function of the edit's size.

**`examples_tried` looked promising and is not.** Pooled, a fault refuted by
the very first case the oracle samples has median π̂ = 0.625 (n=13) — a strong
signal. But it is confounded with rating: easy contest problems have gross
bugs. *Within* the rating ≥1600 band it collapses. Screening on
`examples_tried > 1` there drops 3 of 20 programs, of which one (π̂ = 0.075) is
a good hard cell, and it misses the worst offenders entirely — the two tasks at
π̂ = 0.975 and 0.900 have `examples_tried` of 3 and 22. It is not worth the
extra rule.

### The consequence: the hard band is bimodal, and no free rule fixes it

At rating ≥1600 the pilot (n=20) gives median π̂ = 0.038 — on target — around a
distribution that is not:

```
π̂ = 0.00        8 tasks   verification censors at B in every arm
π̂ ∈ [0.02,0.08] 5 tasks   the paper's Hard band
π̂ ∈ (0.08,0.4)  2 tasks
π̂ ≥ 0.40        5 tasks   ratings 2135, 2159, 2250, 2323, 2531
```

The five saturated cells are *high*-rated. Every free feature tested above
fails to separate them. **A fault in a hard problem is not a hard fault**: an
AtCoder rating measures the difficulty of writing the program, while π measures
the difficulty of repairing one that is already 95% written. The benchmark
gives us a proxy for the first and the experiment needs the second.

### Why we still do not screen on measured π

Two reasons, in order of weight:

1. **It would make the corpus model-dependent and un-checkable.** The freeze's
   whole reproducibility argument is that selection is a pure function
   evaluated before any program runs, so a re-run can be verified against a
   digest. Screening on π̂ puts a nondeterministic LLM inside the selection
   rule: re-running it does not reproduce the same 120, "hard" stops being a
   property of the benchmark and becomes a property of `claude-haiku-4-5`, and
   the corpus cannot be reused by anyone running a different model.
2. **It would cost the budget we do not have** (§6): a screen deep enough to
   resolve π ≈ 0.05 needs tens of draws per candidate, on candidates that may
   then fail the mutation gate.

The bimodality is therefore handled **after** the freeze, in analysis, not
before it in selection. See §4.

---

## 3a. Why the oracle is validated against natural mutants

Stage 2 asks whether the oracle catches bugs it has not seen. It used to answer
that with **planted** mutants: three edits spliced into the reference by AST,
one per fault type of the proposal's §3.5. The problem with planting is that we
choose how to break the program, and the breaks one reaches for fail loudly.
Measured on the pilot corpus:

| | cases the oracle sampled before refuting | refuted by the first case |
|---|---|---|
| the submission's own **real** fault | median 4 | 27% |
| a **planted** mutant | median 1 | **61%** |

The oracle was sitting an exam we had written to be easy, and "0 missed across
201 planted mutants" — the old headline — was a pass mark on that exam.

A **natural mutant** is another person's wrong submission to the same coding
task, already on disk (`src.adapter.sibling_faults`). It is a real developer's
real mistake, with a real mistake's detectability, and measuring all 192
available across the corpus confirms it is the harder test:

- **median 3 sampled cases to refute**, against 1 for a planted mutant;
- **192/192 are refutable by their task's shipped pool** — zero equivalent
  mutants, against 21 of 201 planted ones that no test could catch and that had
  to be discarded as wasted work;
- no AST site is needed, so the old stage 1.5 — which excluded branch-free
  programs because tau_2 and tau_3 had nowhere to go — is gone entirely, and
  with it `data/mutants.py`.

**What it costs: coverage.** The harder a contest problem, the fewer people
submit to it, so the fewer wrong submissions exist. At rating ≥2400 only 10% of
coding tasks have four or more submissions. Requiring one sibling per fault is
what fits the hard band; requiring three does not:

| requirement | natural mutants/task | coding tasks at floor 1600 |
|---|---|---|
| ≥2 submissions | 1 | **188** ✓ |
| ≥3 submissions | 2 | 109 |
| ≥4 submissions | 3 | 68 — short of the corpus itself |

So `--min-siblings 1`, `--mutants-per-task 3`: every task is validated against
at least one real bug, tasks that can supply more do, and none supplies more
than three so that a task with eleven submissions does not outweigh one with
two. The realized corpus carries **254 natural mutants over 120 tasks** — 55 tasks
with 3, 24 with 2, 41 with 1.

---

## 4. Handling the degenerate cells: pre-registered, post-hoc

Declare this before E1 runs, so it is a design decision and not a result.

π̂ for the frozen 120 costs **zero extra model calls**: E1 is the main grid's
`no_memory` arm run with `--force-full-budget`, which is B i.i.d. draws per
task — exactly `measure_pi.py`'s protocol. Do not run `measure_pi.py` on the
frozen corpus; take π̂ from E1 and let `build_strata.py` assign strata from the
measurement, as it already does. The floor stays recorded as
`strata_selection.proxy` and the measured strata stay the authority.

Three cell classes, defined on measured π̂ and B, fixed now:

| class | definition | what it can still evidence |
|---|---|---|
| **saturated** | every arm accepts in round 1 | nothing — no counterexample, memory never written |
| **live** | some arm accepts within B, some arm does not | rounds, redundancy, success@B — all three claims |
| **censored** | no arm accepts within B | redundancy only (rounds/success are censored at B in every arm) |

Note that **censored is payload, not waste**, and this is worth being explicit
about because the instinct is the opposite. A task at π ≈ 0 running to the full
budget is where untyped memory accumulates the most redundant attempts while
typed memory holds at exactly zero — the paper's headline separation
(Theorem 4.3b, Finding 2) is strongest precisely there. It is also where
Corollary 4.4 can bite hardest: steering renormalizes the proposer onto the
not-yet-eliminated support, so typed memory's per-round success rate *rises*
above π as classes are retired, and a cell where typed repairs and the other
two arms never do is the single most discriminating observation in the study.
Only the **saturated** class is dead weight.

Reporting: headline numbers over all 120; the class breakdown reported
alongside; per-class numbers as a pre-registered secondary analysis. Nothing is
dropped from the headline. This is the same treatment the synthetic study
already gives budget exhaustion in its failure taxonomy (Table 6: "not a memory
defect").

**Expected yield**, extrapolating the pilot's rating ≥1600 distribution to 120:
roughly 30 live · 48 censored · 42 saturated. If the realized split is worse
than that — say saturated > 50 — the documented response is to re-freeze at
`--hard-floor 1800` (§2), *not* to filter the existing freeze, which would be
selection on the outcome.

---

## 5. Contamination control: quantified, and not viable at n=120

The reason for preferring ConDefects was a late-dated slice postdating the
training corpora. With the full tree that slice exists again — but it cannot
carry this corpus:

| slice | hard coding tasks (floor 1600) | headroom vs. 120 |
|---|---|---|
| all, 2021-10 → 2024-06 | 336 | 2.80× |
| since 2023-02-01 | 159 | 1.32× |
| since 2023-07-01 | 109 | 0.91× |
| since 2024-01-01 | 59 | 0.49× |

Every date-restricted slice falls below the 2.0 headroom the walk requires, and
the two most recent fall below 120 coding tasks outright. `--since/--until`
stays available for a smaller supplementary cohort, but **the 120-fault corpus
is drawn from the full date range** and contamination is reported as a threat
to validity, not designed away. The honest statement: ConDefects' window is
2021-10 → 2024-06, which sits inside the training window of any recent model,
so the guarantee is relative (these faults postdate Defects4J/QuixBugs) and not
absolute.

---

## 6. Execution

### 6.1 The draw — done, frozen in `data/hard_120.json`

`scripts/select_hard_tasks.py` runs steps 1–5 of §2 and nothing else: no
program executed, no model called, seconds of wall clock. It exists separately
from `validate_oracle.py` because the draw is worth having, checking and
reading before committing hours of sandbox time to the gates.

```bash
python3 scripts/select_hard_tasks.py            # writes data/hard_120.json
python3 scripts/select_hard_tasks.py --check    # re-derives; exit 1 on drift
python3 scripts/select_hard_tasks.py --print    # names, one per line
```

Frozen result:

```
test data   external/ConDefects/Test.zip (central directory)
population  793 faults (floor 1600)
eligible    188 coding tasks with >=1 natural mutant (1.57x the corpus)
selected    120 faults, one per coding task
mutants     254 natural (cap 3/task): 55 tasks with 3, 24 with 2, 41 with 1
rating      min 1601 · median 2201 · max 3271
dates       2021-10-02 → 2024-06-30
digest      7c5cc606d5f6629956625de2e238b3cfef5ffc93505a5cc50781679f20762d63
reserve     68 further coding tasks, in walk order
```

Two properties make the file usable as a freeze rather than a snapshot:

- **Availability is read from the archive, not from the unpacked tree.** The
  central directory lists the complete benchmark whether or not `Test/` exists
  yet, so the draw is the same before and after unpacking — closing the hole
  that let a salvaged partial tree name a different corpus.
- **It is the same order `validate_oracle.py` walks.** Both build
  adapter order → test-data filter → `hard_pool` → `random.Random(seed).shuffle`
  → sibling filter, and the two agree on the digest byte for byte (a smoke run
  of the gate reports `order 7c5cc606d5f6`, the head of the freeze's own). So the gates do not re-draw;
  they walk this list, and a fault that fails a gate is replaced by the head of
  `reserve`, deterministically.

`reserve` is what makes that replacement honest: without it, "the fault that
failed was swapped out" is an unauditable claim.

### 6.2 The gates — still to run

```bash
# 0. verify the archive before trusting it — file(1) is not sufficient
python3 -c "import zipfile; z=zipfile.ZipFile('external/ConDefects/Test.zip'); \
            print(len(z.namelist()), 'entries')"        # expect 108028

# 1. unpack to Test/ (NOT Test_partial/), ~67 GB
python3 scripts/fetch_condefects.py

# 2. sanity-check the tree the freeze will run against
python3 -c "from src.adapter import TEST_DIR, TASKS; print(TEST_DIR, len(TASKS))"
#    expect external/ConDefects/Test and 2864

# 3. freeze — CPU only, no model calls, hours at --jobs 1
python3 scripts/validate_oracle.py --select hard --hard-floor 1600 \
        --min-siblings 1 --corpus-size 120 --seed 20260717 --jobs 1

# 4. reclaim the salvaged tree once (3) has written data/tasks.json
rm -rf external/ConDefects/Test_partial
```

Checks that must hold before the freeze is used:

- `python3 scripts/select_hard_tasks.py --check` still passes — the draw of
  §6.1 has not moved under the gates.
- `data/tasks.json` → `"test_dir": "external/ConDefects/Test"`, `"frozen": true`,
  `n_selected == 120`, `n_cohort_passing >= 90`.
- `data/tasks.json`'s candidate-order digest equals `data/hard_120.json`'s
  `candidate_order_sha256`. They are the same draw; if they differ, one of the
  two filters changed and the reserve list no longer describes the swaps.
- `data/oracle_validation.json` → the natural-mutant catch rate, and
  `n_missed` reported as a property of the oracle at this `max_examples`
  rather than hidden. Expect it to be worse than the planted mutants' 0/201:
  that is the point of §3a, not a regression.
- All 120 `task_id`s distinct (one fault per coding task).
- The recorded candidate-order SHA-256 reproduces on a second dry run.

**Run the publication freeze at `--jobs 1`.** `--jobs` never changes *which*
candidates are chosen, but a program near the sandbox wall-clock limit can time
out under load when it would not have serially, and a timeout is a stage-1
rejection — so parallelism can perturb the corpus even though it cannot
perturb the order. Use `--jobs 6` while developing, `--jobs 1` for the freeze.

---

## 7. Budget: the binding constraint, and a decision required

`data/calls.jsonl`: **2,388 calls, $9.09 spent** against `BUDGET_USD_CAP=20.0`
— **$10.91 remaining**. All of it went to the 60-task pilot, on
`claude-haiku-4-5` at a mean $0.00381/call (574 in / 647 out tokens).

A 120-task hard corpus is the scientifically right choice and the most
expensive one, for the same reason: low π means episodes run to the full budget
instead of accepting early. At B = 10, with counterexample snippets capped at
240 chars (`proposer._MAX_SNIPPET_CHARS`, so memory evidence is bounded):

| | calls | est. cost |
|---|---|---|
| E1 `no_memory`, `--force-full-budget` (120 × 10, always full) | 1,200 | ~$4.6 |
| E2 `untyped` + `typed` (2 × 120 × ≤10, longer prompts) | ≤2,400 | ~$11.5 |
| **main experiment** | **≤3,600** | **~$16** |
| RQ3 ablations: guard-only + steering-only | ≤2,400 | ~$11.5 |
| **everything** | | **~$28** |

**$10.91 does not cover the main experiment.** This needs a decision before
E1 starts; the levers, best first:

1. **Raise `BUDGET_USD_CAP` to ~$35.** Real money, the user's call. Buys the
   full program with margin for a re-run.
2. **Cut B from 10 to 8.** Saves ~20% (~$3.2 on the main grid). It also raises
   the censored fraction, which §4 argues is not a loss — but it weakens
   success@B, which is a headline number, so this trades a claim for money.
3. **Run the RQ3 ablations on a 40-task subset** of the 120 rather than all of
   them. Ablations attribute mechanism; they do not need the full corpus. Saves
   ~$7.7.
4. Not a lever: prompt caching. Output tokens are ~85% of the cost here.

**Order the runs so an early stop degrades gracefully.** `run_eval.py` is
resumable and caches every call, so ordering is free. Run E1 on all 120 first
(~$4.6) — it yields π̂ for every task and therefore the §4 cell classes — then
run E2 with the **live and censored cells first and the saturated cells last**.
If the cap binds mid-sweep, what is lost is the cells that could not have
discriminated anyway. This changes the order evidence arrives in, never the
target population, and it is declared here in advance.

---

## 8. What would invalidate this freeze

- `Test/` not being the full tree — check `test_dir` in `data/tasks.json`.
- The corpus gate failing (<90/120). That is a statement about the oracle, not
  about selection; do not lower the threshold to make it pass.
- Realized saturated fraction ≫ the pilot's ~35%: re-freeze at floor 1800, do
  not filter the freeze.
- Any change to `difficulty.txt`, the seed, the floor, or `Test/` after the
  freeze. The recorded SHA-256 of the candidate order is what detects this.
