# Corpus selection

How the tasks the experiments run on are chosen, and why choosing them at all is
legitimate. Everything here is Stage 0: pure CPU, no model calls, reproducible
from `scripts/select_candidates.py --seed 20260717`.

Status: the filter is implemented and has been run (`data/candidates.json`,
527 candidates). No screening draws have been spent yet. §7 states a precondition
that must be measured before they are.

---

## 1. The question a reviewer asks first

ConDefects ships 2,785 Python faults with test data. This study screens 527 of
them and freezes roughly 55. Any reviewer will ask whether the other 98% were
dropped because they were inconvenient.

The answer cannot be "they were too easy or too hard". Difficulty does not
disqualify a task here. What disqualifies it is that **θ is not computable on
it** — the failure type the entire paper is built on cannot be recovered — or
that the loop cannot run at all. Both are properties of the program, fixed
before any agent runs, that no arm of the experiment can change.

### 1.1 What the theorems actually require

Write `D = Σ_τ q_τ/(q_τ + π)`. The formal core gives

| | oracle calls | redundant attempts |
|---|---|---|
| no memory | `1/π` | `R := 1/π − 1 − D` |
| untyped memory | `1 + D` | `R` |
| typed memory (CEGMem) | `1 + D` | `0` |

plus Theorem 4.2's round bound `K+1` and Proposition 4.5's guard-cost separation
`Θ(m)` vs `O(1)`, `m ≤ K`.

Two consequences set the whole design.

**(a) The typed-vs-untyped comparison cannot live on oracle calls.** Theorem
4.3(a) gives both memories the same `1 + D`; the synthetic table shows 4.58 vs
4.59. The thesis — *typing, not merely remembering* — is visible only in
redundant attempts, proposals, success under a **proposal** budget, and the
guard-cost separation. So a task does not need to *accept* for the primary
comparison to be defined on it. It needs to **store refutations**.

**(b) The primary effect is largest at small `K`, not large.** `R` has the
closed form

```
R = 1/π − 1 − D = (1/π) · Σ_τ q_τ² / (q_τ + π)
```

which is *decreasing* in `K`. Under the proposal's own generative model
(`q ~ Dirichlet(0.7)·(1−π)`), simulated over 20 000 draws:

| K | π=0.05 | π=0.10 | π=0.20 | π=0.35 |
|---|---|---|---|---|
| 1 | 18.05 | 8.10 | 3.20 | 1.21 |
| 4 | 16.28 | 6.77 | 2.38 | 0.78 |
| 8 | 14.64 | 5.71 | 1.84 | 0.55 |
| 16 | 12.39 | 4.45 | 1.29 | 0.36 |
| 40 | 8.82 | 2.78 | 0.70 | 0.17 |

At `K = 1, π = 0.10` the untyped arm still pays **8.10** redundant proposals
against typed's 0 — the gap is at its maximum, because without a class notion the
untyped proposer re-emits the one refuted class until it happens to draw the
correct one.

An earlier version of this design asserted the opposite — that `K → 1` makes the
two memories coincide — and used it to justify a `K_proxy ≥ 6` gate. That was
algebraically wrong, and the gate has been removed (§6.1). It is recorded here
rather than quietly deleted because it changed the design.

### 1.2 What actually killed the previous corpus

The first 60-task corpus was selected on AtCoder difficulty. `π̂` came out pooled
0.369, bimodal: 16 of 60 tasks at exactly 0.00, 13 at ≥ 0.90.
`Spearman(difficulty, π̂) = −0.350`.

Two causes, and by §1.1(b) neither is "K collapsed":

1. **Bimodal `π`.** Almost nothing sat where refutations get stored. Tasks at
   `π ≥ 0.5` accept on the first proposal in all three arms, so no memory is ever
   written and the arms cannot differ — not because the hypothesis is false but
   because nothing was stored.
2. **Program size, via θ's computability.** That corpus was drawn overwhelmingly
   from AtCoder **A** problems, median **6 lines**. `src/typer.py` marks the edit
   location `wholesale` when a candidate's diff against the faulty source spans
   more than `_MAX_HUNKS = 3` hunks or `_MAX_HUNK_FRACTION = 0.4` of lines. On a
   six-line program *every* rewrite trips that, so θ's location axis is the
   constant `wholesale` and the type function degenerates regardless of `K`.

Cause 2 is what the filter is really for, and it is a statement about θ being
**computable**, not about `K` being **large**. That distinction is the difference
between a defensible gate and a decorated one.

### 1.3 Dose-range selection, not outcome selection

Selecting on a property of the task and of the no-memory arm, fixed before any
treatment, is ordinary design of experiments — the sense in which a dose-response
study chooses resolvable doses. Selecting on the outcome is not. Four
commitments enforce the line, formalised in §9:

1. Every gate is a pure function of `faultyVersion.py`, the shipped test
   directory listing, and `date.txt`. **No gate reads `correctVersion.py`.**
2. Nothing is selected on a quantity that is also a reported outcome (§6.2).
3. Statistics used to select and statistics reported come from independent
   draws — for every screened quantity, not only `π̂`.
4. An unfiltered arm is run and reported whatever it shows (§6.4).

---

## 2. The filter

Applied in this order; a fault is attributed to the **first** gate it fails, and
the order runs most-fundamental first so the funnel names the deepest reason.

| gate | test | what it protects |
|---|---|---|
| **G1** executable | test data present; `correctVersion` answers its own cases; `faultyVersion` is actually refuted; neither times out | the loop can run at all |
| **G3** size | `15 ≤ lines ≤ 120`, `stmts ≥ 12` | §1.2(2): θ's location axis is recoverable at all; ceiling is prompt budget |
| **G4** oracle strength | `≥ 15` shipped test cases | Definition 3.1's ρ — too few cases and the sampling oracle stops being informative about the class |
| **G2** validation coverage | all three fault families admit a site | `validate_oracle.py` plants one mutant per family; a branch-free program would be scored on a property of the program, not of the oracle |
| **G5** recency | `date ≥ 2022-04-01` | pool size, declared as such (§6.3) |
| **G6** dedup | one fault per coding task, **seeded uniform choice** | several submissions fail the same coding task; near-duplicates correlate cells the paired analysis assumes independent |

**There is no gate on `K`.** By §1.1(b) a floor would select against the primary
effect, and empirically a floor of 6 removes only ~6 coding tasks that G3 does
not already remove. `K_proxy` — the count of distinct `(source line, fault
family)` pairs `faultyVersion.py` admits under `data/mutants.py`'s three
families — is computed, used to spread the traversal order over a range of `K`,
and reported as a covariate. It gates nothing.

**G6 is a coin flip, not an argmax.** "Keep the fault with the largest `K_proxy`"
was the first version and is wrong twice: it selects on the same statistic the
traversal order stratifies on, and it selects *upward* on `K` — toward smaller
`R` by §1.1(b), and toward `K > B−1` where Theorem 4.2's bound is unreachable
within budget.

---

## 3. What comes out

```
faults with test data           2785   (943 coding tasks)
  dropped at G3 (lines)         1036
  dropped at G3 (stmts)           39
  dropped at G4 (cases)          140
  dropped at G2 (families)       111
  dropped at G5 (date)           234
  passed all gates              1225
  dropped at G6 (dedup)          698
CANDIDATES                       527
UNFILTERED CONTROL ARM            40   (G1 only, uniform — §6.4)
```

Size is the dominant filter, which is the honest reading: the archive is mostly
short programs on which θ cannot be localised.

| | n | K_proxy q25/med/q75 | lines | test cases |
|---|---|---|---|---|
| pool | 527 | 11 / 18 / 30 | 23 / 34 / 56 | 25 / 35 / 49 |
| — Block 1 (`≥ 2023-01-01`) | 340 | 11 / 18 / 30 | 22 / 33 / 56 | 26 / 38 / 51 |
| — Block 2 (reserve) | 187 | 11 / 17 / 28 | 23 / 36 / 57 | 24 / 32 / 43 |
| control arm (unfiltered) | 40 | 7 / 20 / 30 | 16 / 31 / 52 | 23 / 35 / 48 |

Families abc 392 / arc 120 / agc 15. Problem index a 49, b 87, c 87, d 100,
e 76, f 65, g 63. Dates 2022-04-02 to 2024-06-30.

**Two blocks with a pre-declared priority.** Block 1 is screened first; Block 2
is reserve, drawn only if Block 1 fails to fill the corpus. Block membership is
recorded per frozen task and reported with a sensitivity analysis. The blocks
have near-identical marginals, so opening the reserve does not shift the corpus
— but it weakens recency, which is why it is a declared contingency and not a
default.

---

## 4. Traversal order

Stratify on `K_proxy` terciles (cuts 13 / 26), shuffle inside each tercile with
the single seeded RNG, then round-robin over `(block, tercile)`. Every prefix is
balanced on `K` — 20/20/20 at n = 60, 40/40/40 at n = 120 — so an interrupted
screen yields a smaller corpus rather than a skewed one, and, per §6.1, the
screened pool spans enough of `K` to *estimate* `R(K)` rather than assume it.

`K_proxy` is the only stratification variable. Contest family is deliberately not
balanced: it is a nuisance variable, not a design factor, and interleaving on it
would push the 15-task `agc` bucket into every prefix. Shuffling leaves it
proportional (23% arc in the pool, 22–28% across prefixes), and no gate or
ordering rule references it.

---

## 5. Screening (Stages 1–2)

Stage 0 spends nothing. Screening does, at ~2.7 s per call locally.

**Stage 1 — 8 draws per task, `no_memory`, no feedback.** Drop a task if ≥ 4/8
succeed. Deliberately one-sided: it removes only the regime where the first
proposal is accepted, nothing is stored, and the arms cannot differ. It does not
remove low `π̂`.

| true π | P(dropped at ≥ 4/8) |
|---|---|
| 0.00 | 0.000 |
| 0.15 | 0.021 |
| 0.25 | 0.114 |
| 0.35 | 0.294 |
| 0.50 | 0.637 |
| 0.70 | 0.942 |

**Stage 2 — survivors only.** Draw until ~25 *failed* draws accumulate (cap 40).
The stopping rule is on failures, not draws, because `π̂` needs successes while
`K̂` and `q̂` need failures, and low-`π` tasks — the ones that matter — supply
failures fastest. Type every failed draw with `src/typer.py`; record `π̂`, `K̂`,
`q̂`, `R̂`, `repeat_rate`, `wholesale_rate`, `n_distinct_locations`.

**Tiers of the frozen corpus**, defined on `π̂` and `wholesale_rate` only:

| tier | criterion | what it funds |
|---|---|---|
| mechanism (~30) | `π̂ ≤ 0.35`, `wholesale_rate ≤ 0.25` | primary: typed vs untyped on redundant attempts, proposals, success@B under a proposal budget |
| acceptance (~15) | `π̂ ∈ [0.10, 0.35]` | `1/π` vs `1+D` — memory vs no memory only; needs episodes that accept |
| saturation (~10) | `π̂ ≈ 0` | Theorem 4.2(i) non-repetition as distinct-types-proposed ÷ proposals, which needs no acceptance |

`K̂`, `repeat_rate` and `R̂` are **not** tier criteria — see §6.1 and §6.2. The
`wholesale_rate` threshold is instrument validity, and it must be fixed by the
§7 pilot *before* the full screen; a task dropped for high `wholesale_rate`
after the fact would be post-treatment selection.

---

## 6. What this filter does not do

Each item below was raised by an adversarial reviewer panel and survived
verification against the code and the data.

### 6.1 `K_proxy` is largely program length — so it gates nothing

`Spearman(K_proxy, lines) = +0.891` over all 2,785 examined faults, `+0.782`
within the pool. A `K_proxy` gate would therefore have been a size gate that G3
already performs, dressed in theory. It has been removed; `K_proxy` survives only
as a traversal-order stratifier and a reported covariate.

What replaces it is stronger. `R(K)` in §1.1(b) is a **falsifiable quantitative
prediction**: the typed-vs-untyped redundancy gap should *decrease* in `K` along
a curve the theory specifies. Stage 2 yields `K̂` for every screened task at no
extra cost, so the pre-registered analysis is a regression of the observed gap on
`K̂` across the full range, with the predicted slope as the null to beat. A
confirmed slope of predicted magnitude is far stronger evidence than a filtered
mean, and it converts the largest confound into evidence.

Also pre-declared: report `Spearman(K_proxy, K̂)` and its partial correlation
controlling for LOC. If `K_proxy` fails to predict `K̂`, say so — the project has
done this once already, scoring AtCoder difficulty against measured `π̂` (−0.350)
and reporting it as a methodological finding rather than burying it.

### 6.2 Selecting on `repeat_rate` would have been circular — removed

An earlier version defined the mechanism tier as "`π̂ ≤ 0.3`, `K̂ ∈ [4,10]`, high
`repeat_rate`". That is outcome selection. `repeat_rate` *is* the untyped arm's
disadvantage, the quantity the primary result reports; choosing tasks where a
pilot already showed it large and then reporting it large is circular, with
regression to the mean uncontrolled for exactly the headline statistic.

`repeat_rate` and `R̂` are now **moderators, not filters**. The pre-registered
prediction is a dose-response across the whole surviving pool *including its low
end*, never conditioned on its upper tail. The `K̂ ≤ 10` bound is gone for the
same reason in reverse: it selected against Theorem 4.2's own bound instead of
testing it. Large-`K̂` tasks are kept and the round bound is reported as a
function of `K̂`.

### 6.3 The date threshold is pool-size engineering

`date ≥ 2022-04-01` is the one constant not derived from theory. It is the latest
cut still yielding ~500 candidates:

| cut | coding tasks |
|---|---|
| none | 615 |
| 2022-04-01 | 527 |
| 2022-07-01 | 460 |
| 2023-01-01 | 340 |
| 2023-06-01 | 240 |

The paper must say this in these words, not present 2022-04-01 as a
contamination argument. Two mitigations are already in the design: Block 1
(`≥ 2023-01-01`) is screened first, so tasks actually used carry the strongest
recency the pool affords, and any descent into Block 2 is visible per task.

The deeper point: contamination is a *weaker* threat here than for a paper
reporting pass rates. It raises `π` roughly uniformly across all three arms,
while every claim is a within-task, between-arm comparison. Its cost is
instrumental — pushing tasks out of the band where refutations get stored — not
a threat to validity. That argument is cheaper and more honest than overclaiming
the cut.

### 6.4 The unfiltered control arm

Every gate below G1 is defended in §1.3 as dose-range selection. That defence
establishes the mechanism **exists** where it has room to operate. It says
nothing about how often a practitioner meets such a task.

So `select_candidates.py` also emits a 40-task arm drawn uniformly at random over
G1-only survivors: no size gate, no date gate, no screening, no tiering, one
fault per coding task. Its span is wider by construction (lines 3–777, `K_proxy`
1–311). The typed-vs-untyped comparison runs on it and is reported whatever it
shows — including null, including underpowered.

Without this arm the headline reads *"on 2% of the archive, selected so the
mechanism is visible, the mechanism is visible."* With it, the filtered result is
an effect size and this one is its base rate.

### 6.5 What θ requires of a deployment

θ's property axis is an exception class or a divergence shape
(`off_by_small_high`, `missing_elements`, `reordered`, …) obtained by
token-diffing the candidate's stdout against a reference implementation's stdout.
That vocabulary exists because the artifact is a single-file program printing a
short token stream with a whole-program reference oracle. Where the only
observable is "assertion X in test Y failed", the property axis degenerates
toward the identity of the failing test.

No task selection touches this. The threats section must state what θ requires —
a reference-comparable output channel, or a suite whose failures are individually
nameable — and the framing must be *"typed memory helps when the oracle exposes a
structured failure signature"*, not a claim about program repair in general.

---

## 7. Precondition: measure the wholesale collapse before screening

The one finding that can invalidate the design independently of task selection,
and it is cheap to resolve.

`src/proposer.py:217` instructs the model to *"Return the corrected source for
the whole program"*. `src/typer.py:edit_location` then recovers θ's location by
diffing that whole program against the faulty source, falling back to `wholesale`
above 3 hunks or 40% of lines.

In the only typed episodes in the repository (`data/episodes_smoke.jsonl`),
**all 12 rounds have location `wholesale`** — 3 distinct fine types across 2
tasks. If that holds at scale:

- `TypedMemory`'s location index has one bucket, so its guard scans the whole
  history and **Proposition 4.5's `Θ(m)` vs `O(1)` separation is exactly zero**;
- realised `K` equals the size of the property alphabet, a task-independent
  constant, so no selection on program structure can move it;
- the exclusion block degenerates into generic prompt advice rather than typed
  steering.

That smoke run is confounded: both tasks are AtCoder **A** problems of ~6 lines,
where any rewrite is `wholesale` by definition — exactly what G3 removes. So the
finding is real but not yet decisive.

**Gate zero, before Stage 1 spends anything.** Draw ~20 no-memory samples on each
of ~10 pool candidates (35-line programs, not 6-line ones) and report
`wholesale_rate` and `n_distinct_locations`. ~200 calls, ~10 minutes locally.
Three outcomes, and the response to each is fixed now:

- **low** → proceed; set the mechanism tier's `wholesale_rate` threshold from
  this pilot and freeze it.
- **middling** → same, with the threshold binding, and report the excluded
  distribution.
- **near 1.0** → **stop.** No task filter helps. One of these must change first,
  and fixing it afterwards would invalidate every episode run before the fix:
  (a) the proposer emits a unified diff or minimal edit rather than a whole file;
  (b) θ localises from the counterexample's execution trace rather than the patch
  diff — more generic, and it would blunt §6.5 too;
  (c) the primary comparison moves to coarse granularity, in which case every
  `K_proxy` claim is deleted, since coarse makes it definitionally irrelevant.

Whatever happens, report `guard_evaluations` for typed vs untyped **stratified by
wholesale/non-wholesale rounds**.

---

## 8. Cost

| stage | calls | wall clock (local, 2.7 s/call) |
|---|---|---|
| Stage 0 selection | 0 | ~4 min CPU |
| **Gate zero (§7)** | ~200 | ~10 min |
| Stage 1, 527 × 8 | 4 216 | ~3.2 h |
| Stage 2, ~350 survivors × ~32 | ~11 200 | ~8.4 h |

Screen the first 60 in traversal order and inspect `K̂` and `wholesale_rate`
before committing to the rest. §4 guarantees that prefix is balanced, so it is a
valid pilot rather than a throwaway.

---

## 9. Pre-registered commitments

Written down before any screening draw is spent.

1. **Criteria fixed in advance.** The tier criteria of §5, the Stage-1 drop rule,
   and the ~55-task target are fixed now and not revised after the screen is read.
   The `wholesale_rate` threshold is fixed by the §7 pilot, before the full screen.
2. **Full distribution reported.** `data/candidates.json` carries every fault
   examined and the first gate it failed; the screening artifact will carry every
   candidate screened and why it was or was not taken. The funnel
   2 785 → 527 → screened → ~55 appears in the paper body with survival fractions.
3. **Independent draws for every selected statistic.** `π̂`, `K̂`, `q̂`,
   `repeat_rate`, `wholesale_rate` used to *select* come from screening draws
   (nonce `screen|…`); every value *reported* comes from the experiment's own
   draws (nonce `proposal|…`). The first draft asserted this for `π̂` alone;
   regression to the mean applies to all of them.
4. **Nothing is selected on a reported outcome** (§6.2).
5. **`R(K)` is estimated, not assumed** (§6.1), with `Spearman(K_proxy, K̂)` and
   its LOC-partial reported whatever they are.
6. **The unfiltered arm is reported whatever it shows** (§6.4).
7. **An estimand with a scale.** The typed arm is expected at exactly 0, so the
   paired difference vector degenerates: a rank test reduces to "untyped repeats
   sometimes", `A₁₂` saturates at 1.00 for any nonzero untyped count and carries
   no magnitude, and a bootstrap CI on the typed arm is `[0, 0]`. The primary
   estimand is a **paired rate ratio** (typed redundant attempts per proposal ÷
   untyped) with a paired cluster bootstrap over tasks, reported with its CI.
   `A₁₂ = 1.00` against a zero arm is stated as degeneracy, not as a replication
   of the synthetic `A₁₂ = 1.00`.
8. **Multiplicity** corrected across the full family of reported tests, not
   across strata alone.

---

## 10. Open items this document does not close

- **`scripts/analyze.py:99`** calls `scipy_stats.wilcoxon` with the default
  `zero_method='wilcox'`, which drops tied pairs, while `n` and `n_tasks` report
  the untrimmed count. With a degenerate typed arm most pairs may be tied, so the
  printed `n` can materially overstate the `n` actually used. Fix before any
  analysis is reported.
- **`scripts/fit_theory.py`** compares observed no-memory oracle calls against
  `1/π̂` where `π̂` is estimated from the same episodes — an in-sample fit. The
  `π̂` used for the prediction must come from screening draws, not from the
  episodes being predicted.
- **Seeds within a task are not independent.** Task means are the unit for the
  paired tests; seeds buy precision within a task, not degrees of freedom.
- **The overfitting audit stays near-vacuous.** Pool test cases run to a median
  of 35 while the loop's oracle draws up to 100 per round, so the sampled oracle
  is already the full oracle on most tasks. Selection cannot fix this; the E4
  sweep trades directly against the informative band.
- **A single 7B proposer.** A weak proposer plausibly *helps* the phenomenon
  (lower `π`, more repetitive failures, more skewed `q`) — a legitimate choice
  that must be declared, and ideally checked against a stronger model on a subset
  so the effect is not an artefact of proposer weakness.
