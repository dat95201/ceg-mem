# Design — what each stage is for, and what it is allowed to assume

`paper/main_proposal.txt` is the authority. Its own evaluation is **synthetic**
(§5: *"no model is prompted and no test suite is executed"*), and §9 names the
next step:

> instantiate the type function and counterexample oracle on real repair
> benchmarks and measure how coherent real failure types actually are — the
> single quantity our theory identifies as decisive.

This repository is that instantiation, on the Python subset of ConDefects. Every
stage below names the claim it serves; a stage that serves no claim is not run.
[RUNBOOK.md](RUNBOOK.md) is how to run them.

---

## 1. What the paper fixes, and where this deviates

| | paper | here |
|---|---|---|
| conditions | no memory / untyped / typed (§5) | `--modes no_memory untyped typed` |
| ablations | guard-only, steering-only (§5, RQ3) | `--steer off` / `--guard off` |
| metrics | oracle calls, proposals, redundant attempts, guard evals, success@B, anchoring rate (§5) | `analyze.py` + `measure_anchoring.py` |
| strata | Easy [0.18,0.35] / Medium [0.08,0.18] / Hard [0.02,0.08] (§5) | same, plus `dead` / `too_easy` controls |
| budget | B = 10 (§5) | **B = 20** — deviation, below |
| seeds | 30 proposer seeds (§5) | **5** — deviation, below |
| master seed | 20260717 (§5, §10) | same |
| statistics | per-task mean over seeds → mean over tasks; 10⁴-resample bootstrap over tasks; Wilcoxon signed-rank paired on tasks; Vargha–Delaney A₁₂; Benjamini–Hochberg (§5) | `analyze.py` |

**B = 20, not 10.** The primary metric — oracle calls to accept — is conditioned
on accepting, so a band that rarely accepts supplies no datum to it. With
`Pr[accept] = 1 − (1−π)^B`, at B = 10 the Hard band [0.02, 0.08) — where the
paper predicts its **largest** effect — is barely estimable (0.18 to 0.57 across
the band); at B = 20 the whole [0.08, 0.35] interval the paper tabulates comes
into range. The cost is ~2× the calls on non-accepting episodes, paid mostly on
the control strata.

**5 seeds, not 30.** The statistical protocol is unaffected: the bootstrap
resamples **tasks**, not seeds, so the resampling unit is unchanged and seeds only
sharpen each per-task mean. Report the reduced seed count as a threat to
validity.

---

## 2. Stage 0 — which faults the study may ever see

ConDefects ships thousands of Python faults with test data; this study screens a
few hundred of them. The first question a reviewer asks is whether the rest were
dropped because they were inconvenient.

The answer cannot be "they were too easy or too hard" — difficulty does not
disqualify a task here. What disqualifies it is that **θ is not computable on
it**, or that the loop cannot run at all. Both are properties of the program,
fixed before any agent runs, that no arm of the experiment can change.

### 2.1 What the theorems actually require

Write `D = Σ_τ q_τ/(q_τ + π)`. The formal core gives

| | oracle calls | redundant attempts |
|---|---|---|
| no memory | `1/π` | `R := 1/π − 1 − D` |
| untyped memory | `1 + D` | `R` |
| typed memory (CEGMem) | `1 + D` | `0` |

plus Theorem 4.2's round bound `K+1` and Proposition 4.5's guard-cost separation
`Θ(m)` vs `O(1)`, `m ≤ K`. Two consequences set the whole design.

**(a) The typed-vs-untyped comparison cannot live on oracle calls.** Theorem
4.3(a) gives both memories the same `1 + D`. The thesis — *typing, not merely
remembering* — is visible only in redundant attempts, proposals, success under a
**proposal** budget, and the guard-cost separation. So a task does not need to
*accept* for the primary comparison to be defined on it; it needs to **store
refutations**.

**(b) The primary effect is largest at small `K`, not large.**

```
R = 1/π − 1 − D = (1/π) · Σ_τ q_τ² / (q_τ + π)
```

is *decreasing* in `K`. At `K = 1, π = 0.10` the untyped arm still pays ~8
redundant proposals against typed's 0 — the gap is at its maximum, because
without a class notion the untyped proposer re-emits the one refuted class until
it happens to draw the correct one. An earlier version of this design asserted
the opposite and used it to justify a `K_proxy ≥ 6` gate. That was algebraically
wrong; the gate is gone (§2.4). It is recorded here rather than quietly deleted
because it changed the design.

### 2.2 What killed the first corpus

Selected on AtCoder difficulty, it came out pooled `π̂ = 0.369` and bimodal, with
`Spearman(difficulty, π̂) = −0.350`. Two causes:

1. **Bimodal π.** Almost nothing sat where refutations get stored. Tasks at
   `π ≥ 0.5` accept on the first proposal in all three arms, so no memory is ever
   written and the arms cannot differ — not because the hypothesis is false but
   because nothing was stored.
2. **Program size, via θ's computability.** That corpus was drawn overwhelmingly
   from AtCoder **A** problems of a few lines. `src/typer.py` marks the edit
   location `wholesale` when a candidate's diff spans more than `_MAX_HUNKS`
   hunks or `_MAX_HUNK_FRACTION` of lines; on a six-line program *every* rewrite
   trips that, so θ's location axis is a constant and the type function
   degenerates regardless of `K`.

Cause 2 is what the filter is really for, and it is a statement about θ being
**computable**, not about `K` being **large**. That distinction is the difference
between a defensible gate and a decorated one.

### 2.3 The filter

Applied in this order; a fault is attributed to the **first** gate it fails, so
the funnel names the deepest reason.

| gate | test | what it protects |
|---|---|---|
| **G1** executable | test data present; `correctVersion` answers its own cases; `faultyVersion` is actually refuted; neither times out | the loop can run at all |
| **G3** size | `15 ≤ lines ≤ 120`, `stmts ≥ 12` | §2.2(2): θ's location axis is recoverable; the ceiling is prompt budget |
| **G4** oracle strength | `≥ 15` shipped test cases | Def. 3.1's ρ — too few cases and the sampling oracle stops being informative about the class |
| **G2** validation coverage | all three fault families admit a site | `validate_oracle.py` plants one mutant per family; a branch-free program would be scored on a property of the program, not of the oracle |
| **G5** recency | a date floor | pool size, declared as such (§2.4) |
| **G6** dedup | one fault per coding task, **seeded uniform choice** | near-duplicates correlate cells the paired analysis assumes independent |

Every gate is a pure function of `faultyVersion.py`, the shipped test-directory
listing and `date.txt`. **No gate reads `correctVersion.py`.**

**There is no gate on `K`.** By §2.1(b) a floor would select against the primary
effect. `K_proxy` — the count of distinct `(source line, fault family)` pairs the
faulty source admits — is computed, used to spread the traversal order over a
range of `K`, and reported as a covariate. It gates nothing.

**G6 is a coin flip, not an argmax.** "Keep the fault with the largest `K_proxy`"
is wrong twice: it selects on the same statistic the traversal order stratifies
on, and it selects *upward* on `K` — toward smaller `R`, and toward `K > B−1`
where Theorem 4.2's bound is unreachable within budget.

**Traversal order.** Stratify on `K_proxy` terciles, shuffle inside each tercile
with the single seeded RNG, then round-robin. Every prefix is balanced on `K`, so
an interrupted screen yields a smaller pool rather than a skewed one. Contest
family is deliberately *not* balanced: it is a nuisance variable, not a design
factor, and interleaving on it would push the smallest bucket into every prefix.

### 2.4 What the filter does not do

**`K_proxy` is largely program length, so it gates nothing.**
`Spearman(K_proxy, lines) ≈ +0.89`. A `K_proxy` gate would have been a size gate
that G3 already performs, dressed in theory. What replaces it is stronger: `R(K)`
in §2.1(b) is a **falsifiable quantitative prediction** — the typed-vs-untyped
redundancy gap should *decrease* in `K` along a curve the theory specifies — and
the screen yields `K̂` per task at no extra cost, so the analysis is a regression
of the observed gap on `K̂` with the predicted slope as the null to beat.

**Selecting on `repeat_rate` would be circular.** `repeat_rate` *is* the untyped
arm's disadvantage, the quantity the primary result reports. `repeat_rate` and
`R̂` are moderators, not filters, and the prediction is a dose-response across the
whole surviving pool *including its low end*. The `K̂` upper bound is gone for the
same reason in reverse: it selected against Theorem 4.2's own bound instead of
testing it.

**The date threshold is pool-size engineering** and must be described in those
words, not as a contamination argument. Contamination is a *weaker* threat here
than for a paper reporting pass rates: it raises π roughly uniformly across all
three arms, while every claim is a within-task, between-arm comparison. Its cost
is instrumental — pushing tasks out of the band where refutations get stored —
not a threat to validity.

**An unfiltered control arm is emitted and reported whatever it shows.** Every
gate below G1 is defended above as dose-range selection, which establishes that
the mechanism **exists** where it has room to operate. It says nothing about how
often a practitioner meets such a task. So `select_candidates.py` also emits an
arm drawn uniformly over G1-only survivors: no size gate, no date gate, no
screening. Without it the headline reads *"on 2% of the archive, selected so the
mechanism is visible, the mechanism is visible."* With it, the filtered result is
an effect size and this one is its base rate.

**What θ requires of a deployment.** θ's property axis is an exception class or a
divergence shape obtained by token-diffing the candidate's stdout against a
reference implementation's. That vocabulary exists because the artifact is a
single-file program printing a short token stream with a whole-program reference
oracle. Where the only observable is "assertion X in test Y failed", the property
axis degenerates toward the identity of the failing test. No task selection
touches this; the framing must be *"typed memory helps when the oracle exposes a
structured failure signature"*, not a claim about program repair in general.

### 2.5 The precondition that can invalidate the design

`src/proposer.py` asks the model for the **whole corrected program**, and
`src/typer.py::edit_location` recovers θ's location by diffing that against the
faulty source — falling back to `wholesale` above the hunk thresholds. If
`wholesale` dominates at scale, then:

- `TypedMemory`'s location index has one bucket, so its guard scans nearly the
  whole history and **Proposition 4.5's separation collapses**;
- realised `K` equals the size of the property alphabet, a task-independent
  constant, so no selection on program structure can move it;
- the exclusion block degenerates into generic prompt advice rather than typed
  steering.

**Measure `wholesale_rate` before committing to the full grid**, and report
`guard_evaluations` for typed vs untyped **stratified by wholesale/non-wholesale
rounds** whatever it shows. If the rate is near 1.0, no task filter helps and one
of these must change first — fixing it afterwards would invalidate every episode
run before the fix:

1. the proposer emits a unified diff or minimal edit rather than a whole file;
2. θ localises from the counterexample's execution trace rather than the patch
   diff — more generic, and it would blunt §2.4's last point too;
3. the primary comparison moves to coarse granularity, in which case every
   `K_proxy` claim is deleted, since coarse makes it definitionally irrelevant.

---

## 3. What each stage anchors

| stage | purpose | paper claim |
|---|---|---|
| **Stage 0** candidates | admit only faults on which θ is computable and the loop can run | §2 above; the funnel is reported |
| **E0** oracle gate | demonstrate the oracle actually refutes wrong patches | Assumption 1 (§4); oracle definition Eq. (1) (§3.1) |
| **E0b** π̂ screen | the difficulty axis the strata are cut on | §5 task suite and stratification; Thm 4.3(a) |
| **corpus freeze** | fill the π bands; declare the strata before any treatment | §5 stratification; Prop 4.5's guard gap growing with difficulty |
| **blind spot** | bound how often a wrong patch can be *accepted* | Thm 4.1 — its guarantee is exactly "acceptance implies the patch passes every check the oracle can run" |
| **E1** no-memory arm | the trivial baseline, and the estimator for π̂/q̂ every closed form is evaluated at | §5 baselines; Thm 4.3(a) (1/π); RQ3 |
| **E2** memory arms | the central comparison | RQ1; Thm 4.3(a) rounds, 4.3(b) redundant attempts, Cor 4.4 budgeted success, Prop 4.5 guard evaluations |
| **E3** ablation | attribute each gain to guarding or to steering — *remembering* vs *typing* | RQ3; Table 4 |
| **E4/E5** sweeps | how the guarantees degrade as the oracle informs less and typing gets noisier | RQ2; Table 3; Findings 4 and 5; Def. 3.1 |

---

## 4. Fidelity caveats — where the implementation is not the model

These are not bugs; they are the places where a real LLM forced a departure from
the formal model, and each one weakens a specific claim. They belong in threats
to validity, not in a footnote.

**The untyped arm shows the proposer nothing.** §5 defines the untyped baseline
as *"a flat counterexample log that guards by re-running all stored
counterexamples but **cannot steer the proposer**"*, and Algorithm 1 draws
`p_t ~ G(·|E)` with `E` empty for an agent that has no types — the same
unconditional distribution no memory draws from. §6 reports the consequence
directly (*"untyped collapses to 0.68, indistinguishable from no memory"*), and
Table 4 puts Guard-only at the same value. Both hold only if neither arm's
proposals are conditioned on memory, so `_evidence_block` returns nothing for
`untyped`, and `--steer off` suppresses the evidence block as well as the
exclusion block.

A transcript-in-the-prompt condition is a legitimate and interesting question
about real LLM agents — it is what §2 of the paper says reflective agents
actually do — but it is a **fourth mode**, not a relabelling of this baseline.

That fourth mode now exists: `transcript` (`src/memory.py`, preset
`E6-transcript`). It subclasses `UntypedMemory` and changes exactly one thing —
`src/proposer.py` shows the model every refuted patch and its counterexample —
so any gap between the two arms is attributable to the steering channel alone.
It is the ChatRepair condition and it is reported **beside** `untyped`, never in
place of it: `untyped` is what the theory is about, `transcript` is what a
reviewer will otherwise say the theory ignores. `freeze_results.py` treats it as
its own sub-grid (`--experiment transcript`) rather than a fourth cell of the
main grid, for that reason.

Two things about it that must reach the write-up. It is the **only** arm that
cannot share E1's cached draws, so its cost is real rather than free. And
`--transcript-window K` is a declared design decision, in the cell key: an
unbounded transcript is the one arm that can hit the context ceiling, and where
it does, `src/llm.py` raises `ContextOverflow` rather than letting the backend
crop — so the run reports the overflow instead of silently measuring a cropped
prompt. Count `proposal_error="context_overflow"` per arm before reporting
anything about this condition.

**Steering is a prompt instruction, not Eq. (3).** The paper renormalises the
proposer's support onto the not-yet-eliminated types, so Theorem 4.2(i)
non-repetition holds *by construction* — an eliminated class cannot be drawn.
`_exclusion_block` instead writes English. Whether the model obeys is an
empirical question, so on this implementation **non-repetition is a measured
outcome, not a theorem**. Report the observed redundant-attempt count as a test
of the instruction's strength; do not present exactly-zero as guaranteed.

**Typing noise is injected on half the type.** `TypedMemory.store` mistypes only
the `location` half of the (location, property) pair, and the mistype needs
another location to swap to — so an episode's first store can never be mistyped.
The realised noise rate is therefore strictly below the nominal `1−c`, and E5's
axis is a lower bound on the damage, not Def. 3.1's `c`.

**ρ is a sample-size proxy.** On a real oracle you cannot know a failure class in
advance, so E4 varies how much of the test pool is sampled rather than the
paper's ρ (whether a counterexample generalises to its class). Related, not the
same quantity, and E4 has three levels against the paper's four.

**Accept is not repair.** The loop accepts when no *sampled* case refutes;
`is_truly_correct` over the whole pool runs only under `--check-overfit`. The gap
is widest at the smallest `--max-examples`, so read that level off the overfit
log.

---

## 5. Pre-registered commitments

1. **Criteria fixed in advance.** The band edges, the quotas, B and the seed
   counts are fixed before any screening draw is spent, and are not revised after
   the screen is read.
2. **Full distribution reported.** `data/candidates.json` carries every fault
   examined and the first gate it failed; `data/screening.json` carries every
   candidate screened and why it was or was not taken. The funnel appears in the
   paper body with survival fractions.
3. **Independent draws for every selected statistic.** Values used to *select*
   come from screening draws (nonce `pi-pilot|…`); every value *reported* comes
   from the experiment's own draws. Regression to the mean applies to all of
   them, not only π̂, and `build_strata.py` writes the migration matrix out.
4. **Nothing is selected on a reported outcome** (§2.4).
5. **`R(K)` is estimated, not assumed** (§2.4), with `Spearman(K_proxy, K̂)` and
   its LOC-partial reported whatever they are.
6. **The unfiltered arm is reported whatever it shows** (§2.4).
7. **An estimand with a scale.** The typed arm is expected at exactly 0, so the
   paired difference vector degenerates: a rank test reduces to "untyped repeats
   sometimes", `A₁₂` saturates at 1.00 for any nonzero untyped count and carries
   no magnitude, and a bootstrap CI on the typed arm is `[0, 0]`. The primary
   estimand is a **paired rate ratio** (typed redundant attempts per proposal ÷
   untyped) with a paired cluster bootstrap over tasks, reported with its CI.
   `A₁₂ = 1.00` against a zero arm is stated as degeneracy, not as a replication.
8. **Multiplicity** corrected across the full family of reported tests, not
   across strata alone.

---

## 6. Open items

Known, and recorded rather than carried silently.

- ~~**`redundant_attempts` is not one definition.**~~ **Fixed.** The problem was
  real: `summarize_episode` counted every guarded round as redundant, which
  holds for the typed guard (it looks up a bucket by location) but not for the
  untyped one, which replays stored counterexamples — and a candidate with a
  brand-new failure type routinely fails an old failing input. The column
  measured type repeats for one arm and guard firings for the other while
  Theorem 4.3(b) assigned both the same `R`.

  Two things now exist instead, and the paper should report both:

  `blocked_known_counterexample` — rounds blocked because they **provably
  reproduced a stored counterexample**, labelled by that counterexample's type.
  `GuardResult.blocked_by` carries the attempt that fired, and an `Attempt`
  holds `theta_both`'s verdict regardless of which memory stored it, so this
  means the same thing in every arm and needs no extra run.

  `type_repeats` — rounds whose own θ repeats an earlier one. Honest, but
  **censored exactly where an arm guards**, since a guarded round never reaches
  the oracle and so carries no type. `--audit-guarded` (`E8-audit`) pays the
  oracle on guarded rounds for the record only — the verdict never reaches
  memory and never ends the episode — which un-censors it at the cost of
  sandbox time and no model calls. `scripts/measure_redundancy.py` prints the
  censoring rate per arm and refuses to let it pass unremarked.

  `redundant_attempts` is kept under its own name so nothing that already reads
  it silently changes meaning, but it is not the column to compare arms on.
- ~~**`analyze.py` calls `scipy.stats.wilcoxon` with the default
  `zero_method='wilcox'`**~~ **Fixed.** It now reports `n_effective` — the count
  of pairs that actually differ, which is the test's real sample size — beside
  the untrimmed `n`. `n_effective` is the one that belongs in the paper. The
  pre-registered `paired_rate_ratio` (§7) is also computed now, for the same
  underlying reason: against an arm expected at exactly 0, the rank tests
  degenerate and only a ratio of totals carries magnitude.
- **`fit_theory.py` compares observed no-memory oracle calls against `1/π̂` where
  π̂ is estimated from the same episodes** — an in-sample fit. The π̂ used for the
  prediction must come from screening draws.
- **Seeds within a task are not independent.** Task means are the unit for the
  paired tests; seeds buy precision within a task, not degrees of freedom.
- **The overfitting audit stays near-vacuous** wherever the sampled oracle is
  already the full oracle. Selection cannot fix this; the E4 sweep trades
  directly against the informative band.
- **A single small proposer.** A weak proposer plausibly *helps* the phenomenon
  (lower π, more repetitive failures, more skewed `q`) — a legitimate choice that
  must be declared, and ideally checked against a stronger model on a subset so
  the effect is not an artefact of proposer weakness.

  The machinery for that check now exists: `--backend cloud --model <id>` on
  both shard scripts, with `model`, `backend` and `reasoning_effort` in the cell
  key, in the shard `.meta.json` and in the merge audit, and the model id folded
  into the shard filename so two proposers cannot land in one log. What has
  *not* been decided is whether to run it, and the choice is not free in either
  direction:

  π is a property of the model, so a second proposer's bands are not this
  corpus's bands. On the local model the screen measured 113 of 207 tasks in
  `dead` and 40 in `hard` — and those are the two bands where the predicted
  effect is largest (§V-F's own A₁₂ of 1.00 on Hard; Prop. 4.5's guard-cost gap
  "grows with task difficulty"). A stronger proposer drains both. So a
  second-proposer arm on the *existing* bands is a robustness check whose band
  labels describe the local model's difficulty and must be reported as such; a
  second-proposer arm on its *own* screen is a second experiment. Neither is a
  drop-in replacement for the first, and the wrong one to pick is the one that
  quietly re-labels the corpus.

- **Reasoning models are supported but untested here.** `src/llm.py` routes
  o-series ids to `max_completion_tokens`, drops `temperature`, and records
  `reasoning_out` separately; `src/proposer.py` raises the output ceiling so
  reasoning is not spent out of the answer's budget. None of that has been
  exercised against a live o-series endpoint. Before a paid run, probe the three
  parameter shapes (RUNBOOK.md §9 has the one-liner) — the o-series' handling of
  an explicitly-passed `temperature=1.0` has varied across snapshots, and the
  failure mode is a whole shard of rejected calls.

### Gaps against the paper's §10

- **`scripts/gen_synthetic.py` does not exist.** It produced every number in the
  paper. This repo replaced the synthetic corpus with the real-benchmark chain,
  so the paper's own four-command reproduce sequence cannot be run here.
- **`data/results.json` is never produced** — the frozen artifact is
  `data/results_real.json`. §10's re-derivability guarantee names the former.
- **`data/schema.md` does not exist**, though §10 promises it documents the task
  and result formats.
- `requirements.txt` pins `openai` and `python-dotenv`; §10 states the artifact
  depends *solely* on numpy, scipy and matplotlib. That claim describes the
  synthetic artifact and does not survive the move to a real LLM proposer.
