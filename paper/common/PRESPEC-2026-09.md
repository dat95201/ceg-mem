# Pre-specification register — CEGMem, September 2026 cycle

This file is the register the paper's §Threats paragraph points at when it says
*pre-specified* rather than *pre-registered*. It records, for every experiment
in this cycle, the criterion that was fixed for it and the commit that fixed
it. There is no third-party registry entry and this document does not pretend
to be one: its only claim to force is that git holds a timestamp for each row,
and that the timestamps can be checked against the timestamps on the result
files.

**What this document is not.** It is not a claim that every line in it predates
every result. §0 states, row by row, which criteria were fixed before their
result existed and which were not. A register that blurred that distinction
would be worth less than no register at all.

---

## 0. Chronology, stated exactly

| Falsifier | Criterion fixed in | Commit timestamp (UTC) | Result artifact | Result timestamp | Criterion predates result? |
|---|---|---|---|---|---|
| **F4** (P1-1 baselines) | `scripts/simulate_policies.py` docstring, "PRE-DECLARED CRITERION" | `fa40e83`, 2026-09-06 09:13:40 | `data/official-2026-09-01/policies.json` | 2026-09-07 | **Yes** |
| **F5** (informed vs. random skipping) | this file, §3 | — see git log for this file — | not yet run | — | **Yes** (not run) |
| **F6** (guard soundness below $k=100$) | this file, §3 | — see git log for this file — | not yet run | — | **Yes** (not run) |
| **F7** (steering null at a second scale) | this file, §3 | — see git log for this file — | `data/gpto4mini-2026-09-06/results_real.json` (1,782 episodes, 0 missing cells) | 2026-09-09 | **Yes** — criteria predate the run; the *proposer* was substituted (§6) |
| **D1** (class-exclusivity demotion) | this file, §4 | — see git log for this file — | not yet run | — | **Yes** (not run) |

**One row needs a caveat and gets it here.** This document was created on
2026-09-07, *after* F4's result landed. F4's criterion itself was committed the
day before, in the script that computes it, and that commit is checkable — but
the narrowing text this register points to (§5, `NARROWING-P1-1.md`) was written
after the outcome was known, not before. The plan called for writing it first;
that did not happen. The paper says so rather than implying otherwise.

F1–F3 were fixed before the reported run and are already reported in
`tables/integrity.tex`; they are restated in §2 for completeness only.

---

## 1. What "pre-specified" means in this project

Metrics, arms, falsifiers and the banding rule are fixed in a design document,
in the frozen corpus and protocol artifacts, or in the script that computes
them, before the run they govern. The commit that fixes them is the timestamp.
Three things follow, and all three are limitations:

1. A commit is checkable by anyone with the repository, but it is not
   third-party custody. An author with commit rights can rewrite history.
   Nothing here defends against that; it defends against the weaker and far
   commoner failure of choosing a threshold after seeing where the data fell.
2. "Fixed before the run" is not "fixed before any pilot". Where a pilot
   informed a threshold, the row says so.
3. Only the criteria listed here are confirmatory. Every other comparison in
   the paper is exploratory and is labelled as such in the text.

---

## 2. F1–F3 — fixed before the reported run, already reported

Restated from `tables/integrity.tex`. No changes.

| ID | Falsifier | Outcome |
|---|---|---|
| **F1** | A guarded arm's repair rate equals its unguarded twin's | Held: $0.704$ vs. $0.707$; $295/297$ episodes outcome-identical |
| **F2** | Redundancy *present* is invariant across unconditioned arms | Held: $4.48$ / $4.50$ (no-mem, untyped) |
| **F3** | A random partition must **not** reproduce typing's advantage | Held on the responding column ($9/0$, $p = 0.004$); indistinguishable elsewhere |

---

## 3. F4–F7 — this cycle

### F4 · A classical prioritizer matches or beats the guard — **FIRED**

> **Criterion (committed `fa40e83`, 2026-09-06 09:13:40 UTC).** `cegmem-guard`
> reaches its first refutation in no more case executions than the better of
> `qi13` / `venugopal20` on at least 60 of the 99 tasks, with a one-sided
> task-level Wilcoxon $p < 0.05$.

**Failure rule, as committed:** *"If that fails, the finding is that a classical
prioritizer matches the guard on this corpus, the novelty claim narrows to the
key and the evidence rather than the saving, and Table I moves CEGMem into the
same cell as Qi'13. Either outcome is reportable; neither is a reason to leave
the comparison out."*

**Outcome: NOT MET, and by a wider margin than the failure rule anticipated.**
Over 495 cells / 99 tasks: 1 win, 18 ties, 80 losses; $p = 1.0$. The failure
rule said "matches"; the measurement says a classical prioritizer *beats* the
guard on this axis, and also on total program executions (2 / 5 / 92 tasks).
The narrowing therefore goes one step further than the committed text: see
`NARROWING-P1-1.md` §2, and the deviation note in §6 below.

**Not affected by F4, and measured in the same run:** oracle invocations, where
the guard wins on 99 of 99 tasks ($p < 10^{-17}$) and no prioritizer moves the
metric at all.

### F5 · Informed skipping is not the mechanism — pending

> **Criterion.** With `--oracle-skip-p 0.37` (the typed guard's measured block
> rate) on 33 tasks $\times$ 3 seeds, repair rate must fall by at least 5
> percentage points relative to `no_memory` at the same oracle budget,
> task-level $p < 0.05$.

**Failure rule.** If skipping *at random* does not damage repair, then the
oracle is over-allocated and the guard's saving cannot be attributed to the
skips being informed. §VII gains a paragraph and the word "informed" leaves the
abstract. This is a falsifier of the thesis, not of an implementation detail;
it is run early for that reason.

### F6 · Guard soundness below full oracle depth — pending

> **Criterion.** For each $k \in \{20, 8, 3\}$, reconstruct the check set
> $X_k = \mathrm{Random}(\text{seed} + \text{round})\,.\,\mathrm{sample}(\text{pool}, k)$
> for every guard-blocked round and report a Clopper–Pearson upper bound on the
> rate at which a blocked candidate would have passed all of $X_k$.

**Failure rule.** A violation is the better outcome and is reported as one: the
soundness statement becomes conditional on depth rather than unconditional. The
existing audit at $k = 100$ is uninformative because the draw exhausts the pool
for 97 of 99 faults, and the paper already says so; this replaces an admission
with a bound.

### F7 · The steering null is scale-dependent — in progress

> **Criterion.** On a second proposer, three arms over the primary-band
> universe: (a) the guard's oracle-call reduction is at least $\times 3.0$
> (against $\times 5.17$ at 7B); (b) the task-clustered interval on the
> steer-only repair-rate difference **contains 0**.

**Failure rule.** If (b) fails with steering *positive* at the larger scale,
the abstract, §I and the conclusion narrow to "drop prompt steering for
proposers of this size", which is a title-level change. That is why this item
must start early enough for its answer to be usable; a story-changing result
arriving in the freeze week is not usable.

---

## 4. Demotion rules

Thresholds that do not falsify a claim but relocate one.

| ID | Measurement | Rule |
|---|---|---|
| **D1** | Class-exclusivity: for each counterexample $x$ of class $\tau$, the share of candidates of class $\tau' \neq \tau$ that $x$ does **not** refute | If the exclusivity rate is below $0.5$, Theorems 3 and 4 leave the body entirely and survive as one sentence in §IV. The paper already concedes that $\lambda$ does not satisfy Assumption 2; this puts a number on how badly, and the number is the product whichever way it falls. |

---

## 5. Pre-written failure-branch text

| Branch | Text lives in | Written before its result? |
|---|---|---|
| F4 fired — novelty narrows | `NARROWING-P1-1.md` | **No** — written 2026-09-07, after the result |
| F5 fired — "informed" leaves the abstract | to be written before F5 runs | — |
| F7 fired — scope narrows to small proposers | **not written before the result.** The §3 failure rule ("drop prompt steering for proposers of this size") was applied on 2026-09-09 and the paper text written the same day, after the outcome was known. Same failure as the F4 row. | **No** |
| D1 fires — Thm 3/4 demoted | to be written before D1 runs | — |

The first row is the one the plan got wrong, and the register says so rather
than back-dating it. The remaining three are still ahead of their results and
must stay that way.

---

## 6. Outcome registry

Filled as results land. A row is never edited once written; a correction is a
new row.

| Date | Falsifier | Outcome | Consequence taken |
|---|---|---|---|
| 2026-09-07 | **F4** | **Fired.** 1 / 18 / 80 tasks, $p = 1.0$ | Novelty claim narrowed (`NARROWING-P1-1.md`); Table I's CEGMem row gains a distinct oracle-call cell; the §Threats sentence conceding the baseline was never run is removed and replaced with the measurement |
| 2026-09-07 | — | **Deviation from the F4 failure rule.** The committed text predicted parity ("matches the guard"); the result is a loss on the execution axis. The narrowing is therefore stronger than the pre-written branch specified. | Recorded here rather than silently absorbed. The stronger narrowing is the one that ships. |
| 2026-09-09 | — | **Deviation from the F7 protocol: the proposer changed.** §3 named a 14B model of the same family (plan WI-05). Availability substituted `gpt-4o-mini` (hosted, `T=1.0`, same nonces, same corpus). The two criteria were not changed; the model they were applied to was. | Recorded. §Threats states it. The criteria are evaluated on the universe §3 named: the 55 tasks the **frozen** (7B-screen) banding places in `hard`/`medium`/`easy`. |
| 2026-09-09 | **F7 (a)** | **Held.** Guard oracle-call reduction on the 55 pre-specified tasks: ×4.93 (untyped), ×4.82 (typed); criterion ≥ ×3.0. All 99 tasks: ×5.76 / ×5.48. Per task 59/0 and 61/0. | Guard-transfer claim promoted from "predicted, not tested" (§Threats) to measured (§VII-F). |
| 2026-09-09 | **F7 (b)** | **Fired.** `steer-only` − `no_memory` repair on the 55 pre-specified tasks: +6.7 pp, task-clustered 95% CI [+0.6, +12.7] pp, **excludes 0**; 13 tasks up / 2 down, p = 0.007. All 99 tasks: +9.1 pp, [+4.0, +14.5], 21/3, p = 0.0003. Same arm, same 55 tasks, 7B: +1.2 pp, [−4.9, +7.3] — the null the paper reports. | Failure rule applied: abstract, §I, §VIII, §IX, §X narrowed from "the guard alone" to "the guard unconditionally; steering where the proposer acts on it". Title unchanged (it names the index, not the recommendation). |
| 2026-09-09 | — | **Exploratory, not pre-specified, reported as such:** `typed` beats `untyped` on repair at B=20 under `gpt-4o-mini` (0.743 vs 0.679; 46/14 cells p<10⁻⁴; 16/8 tasks p=0.15; CI [+1.8, +11.7] pp) and on paid redundancy (13/3 tasks, p=0.021). Neither was a criterion. | Stated in §VII-F with the cell/task split; not carried to the abstract. |
| 2026-09-09 | — | **Correction to an interim reading.** An interim analysis on the first 64 of 99 tasks, banded post hoc from the second proposer's own π̂ (15 primary tasks), read F7 (b) as *held*. That banding is not the one §3 names, and the sample was incomplete. The register records the pre-specified reading only. | Interim reading withdrawn; not in the paper. |
| 2026-09-09 | — | **Exploratory, not pre-specified, reported as such: the main comparison restricted to the 55 primary-band tasks** (frozen 7B banding; the F7 universe) for both proposers, supplement `tab:primaryband`. 7B, typed − untyped: oracle calls 28/12 tasks, p = 0.017, CI [−0.24, −0.04]; program executions CI [−6.9, −0.9]; repair −4.4 pp, [−10.9, +2.2]. gpt-4o-mini: repair +5.1 pp, [+0.0, +10.9]; prompt-token overhead +9 % (CI includes 0); every band's typed − untyped repair difference ≥ 0, task-level interaction p = 0.58 (7B: p = 0.03, two-signed). Of 40 band-level task tests per proposer (5 bands × 8 metrics), 6 (7B) and 0 (gpt-4o-mini) reach p < 0.05, unadjusted; 2 expected by chance. The guard's fold on this universe is ×3.7–4.9, smaller than on all 99 tasks, because the *dead* band is excluded. | One sentence each in §VII-B and §VII-F; nothing in the abstract. The universe was fixed by F7 before the run; the restriction was not chosen after seeing which band favoured which arm, and no band-level result is reported as a finding. |

---

## 7. Provenance of the F4 result

| Field | Value |
|---|---|
| Episodes | `runs/2026-09-01/episodes.jsonl` (frozen) |
| Verdict matrix | `data/official-2026-09-01/verdicts.jsonl` — 5,564 rows, all complete, 99 tasks |
| Matrix acceptance gate | `build_verdict_matrix.py --verify`: coverage $1.0000$ (gate $0.99$), reproduction $0.9993$ (gate $0.95$) — **PASS** |
| Reproduction mismatches | 6 of 8,312 rounds, all on `abc285_e/48880084`, the timeout-edge fault §VII-G already names |
| Universe | `no_memory`, depth $k = 100$, main grid: 495 cells / 99 tasks / 5 seeds |
| Cells skipped | 0 |
| Model calls | 0 — every candidate replayed is byte-identical to one the frozen log holds |
