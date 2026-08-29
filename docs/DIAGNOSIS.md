# Why the pipeline fails its own claims

Branch: `fixed-pipeline` (from `feat-result-01`, 58e3302)
Evidence: `data/episodes.jsonl`, 35,755 rounds, run E1-E8 minus E6.
Every number below is recomputed from the frozen log, not from `analysis.json`.

---

## Summary

| # | Finding | Severity |
|---|---|---|
| 1 | The guard is **outcome-neutral by construction**. `untyped` is byte-identical to `no_memory` at every budget. Cor. 4.4 cannot show an effect — not "did not", *cannot*. | fatal, design |
| 2 | "Oracle calls" is the wrong cost unit. It omits the guard's own sandbox runs. In program executions the whole system buys **1.19x**, not 2.5x. | fatal, metric |
| 3 | Thm 4.3(a) is false *as stated* because the typed guard searches a strict subset of untyped's history — it misses **30% of all rounds** untyped blocks. | theorem is wrong |
| 4 | Under the honest cost unit, `typed` and `untyped` cost **1.00x** each other. Thm 4.3(a)'s *conclusion* holds; only its stated unit was wrong. | recoverable |
| 5 | E5 (c-sweep) is underpowered: n=90/cell, non-monotone, random null sits mid-range. Not a null result — an underpowered experiment. | reviewer-fatal |
| 6 | The guard is nondeterministic: 3 rounds blocked by `typed` that `untyped` missed, which set inclusion forbids. Sandbox timeouts leaking into guard decisions. | reproducibility |

---

## 1. The guard cannot change outcomes

`success@B`, paired on all 530 (task, seed) cells:

| arm | B=1 | B=2 | B=3 | B=5 | B=8 | B=12 | B=20 |
|---|---|---|---|---|---|---|---|
| `no_memory` | 20.9 | 30.9 | 36.2 | 46.4 | 54.0 | 60.4 | 67.0 |
| `untyped`   | 20.9 | 30.9 | 36.2 | 46.4 | 54.0 | 60.4 | 67.0 |
| `guard_only`| 20.8 | 29.9 | 34.9 | 46.5 | 54.7 | 59.7 | 67.0 |
| `typed`     | 20.9 | 32.8 | 39.8 | 47.9 | 56.0 | 62.8 | 67.9 |
| `steer_only`| 20.8 | 32.7 | 39.0 | 47.2 | 56.6 | 65.7 | 69.2 |

`untyped` reproduces `no_memory` to the last decimal at all seven budgets. That is
not a coincidence and not noise. The mechanism is in `src/loop.py:335`:

```python
continue  # oracle call avoided; still consumes one round of budget
```

The guard blocks only candidates a *stored counterexample already refutes* — i.e.
candidates that were going to fail anyway. Under common random numbers the
proposal sequence is fixed by (task, seed, round), so the round at which the first
**accepted** patch appears is invariant to the guard. Blocking a doomed candidate
and then burning the round on the block changes nothing.

Consequence: **Cor. 4.4 was never testable in this design.** Reporting it as
"no effect" concedes a null that the experiment could not have refuted.

The two arms that do move (`typed` +3.6pp, `steer_only` +2.8pp at B=3) both have
**steering on**. Every outcome effect in the entire grid comes from the prompt
exclusion block, none from the guard.

### The fix

A guarded round must not consume an attempt. Then a guard that blocks 82% of
proposals converts a 20-round budget into ~20 *real* attempts instead of ~1.9,
and Cor. 4.4 becomes a live hypothesis with a large predicted effect. This is
one branch in `run_episode`, gated behind a flag so both accountings can be
reported.

---

## 2. "Oracle calls" hides the guard's own cost

`_still_refutes` (`src/memory.py:53`) calls `run_program`. A guard check is a
sandbox execution, exactly like an oracle example. Counting oracle calls while
not counting guard evaluations charges one arm for work the other also does.

Cost to first success, successful episodes, mean:

| arm | model calls | oracle calls | **sandbox runs** | wall s | prompt tok |
|---|---|---|---|---|---|
| `no_memory` | 4.96 | 4.96 | **55.6** | 62.8 | 2446 |
| `untyped`   | 4.96 | 1.98 | **46.6** | 59.4 | 2446 |
| `typed`     | 4.54 | 3.10 | **47.6** | 53.2 | 3997 |
| `guard_only`| 5.02 | 3.37 | **49.2** | 59.3 | 2583 |
| `steer_only`| 4.60 | 4.60 | **54.2** | 59.9 | 4344 |

Speed-up over `no_memory`:

| arm | oracle calls | sandbox runs | wall clock |
|---|---|---|---|
| `untyped` | 2.50x | **1.19x** | 1.06x |
| `typed`   | 1.60x | **1.17x** | 1.18x |

The abstract's 2.6x is a 1.19x. Model calls are **identical** (4.96 vs 4.96):
the loop needs the same number of rounds either way; memory only relabels which
of them are charged to the oracle.

---

## 3. Thm 4.3(a) is false as stated — and the reason is structural

`UntypedMemory.guard` scans the whole history. `TypedMemory.guard` scans
`self._by_location[guess]` — a **subset**. So `typed blocked => untyped blocked`,
never the converse. The theorem's premise (both guards decide the same predicate)
is contradicted by the implementation, by design.

Measured on 3,170 rounds where `untyped` and `guard_only` drew the **identical
patch text** (CRN verified at 100%, `steer_on=False` in both, so the prompt never
diverges):

| | rounds | share |
|---|---|---|
| blocked by `untyped` | 2,587 | 81.6% |
| blocked by `typed`   | 1,641 | 51.8% |
| blocked by both      | 1,638 | — |
| **untyped blocks, typed misses** | **949** | **29.9%** |
| typed blocks, untyped misses | 3 | 0.09% — impossible, see §5 |

The typed guard catches 63% of untyped's blocks. The missing 37% are the
**cross-bucket refutations**: a candidate refuted by a counterexample filed under
a different edit location. That is precisely what the O(1) index trades away, and
the paper never priced it.

Guard evaluations per round: `typed` 0.60, `untyped` 1.04. Prop. 4.5 is
"supported" only in the sense that scanning less costs less. It is not an
indexing win; it is a smaller search.

### But the conclusion survives

Paired on 321 cells where both arms succeeded:

| unit | untyped | typed | ratio |
|---|---|---|---|
| oracle calls | 1.92 | 2.83 | 1.48x |
| **sandbox runs** | **45.07** | **45.10** | **1.00x** |
| wall clock (s) | 52.89 | 49.92 | 0.94x |
| prompt tokens | 2043 | 3453 | 1.69x |

`typed` pays more oracle calls and exactly enough fewer guard evaluations to
cancel. **Thm 4.3(a) holds in program executions to two decimal places.** The
theorem was right; the reported unit was wrong. This is the single recoverable
result in the run and it should become the paper's spine.

---

## 4. E5 is underpowered, not null

| c | eps | succ% | sandbox runs |
|---|---|---|---|
| 0.00 | 90 | 71.1 | 65.2 |
| 0.25 | 90 | 66.7 | 59.7 |
| 0.50 | 90 | 70.0 | 59.6 |
| 0.75 | 90 | 72.2 | 55.9 |
| 0.90 | 90 | 72.2 | 54.7 |
| 1.00 | 530 | 67.9 | 60.2 |
| **random null** | 90 | 67.8 | 58.8 |

c=1.0 is worse than c=0.9; the random null lands mid-sweep. With 90 episodes per
cell this is noise. Paige, Cabot & Ernst (EMSE 22, 2017) state the bar directly:
*"A negative result due primarily to misaligned expectations or due to lack of
statistical power (small samples) is not a negative result, rather a poorly
designed experiment."* A reviewer will apply that sentence to this table.

Deeper problem: E5 varies c by *injecting* noise, which measures degradation
under a hypothetical c, never the real one. `scripts/measure_typing_coherence.py`
measures the real c against a behavioral-signature ground truth. It has never
been run (~34 h serial, see §7).

---

## 5. The guard is nondeterministic

3 of 3,170 rounds show `typed` blocking where `untyped` did not. Set inclusion
makes that impossible, so the guard is not a function of its inputs.
`_still_refutes` returns `True` on `cand.timed_out`, and the sandbox timeout is
wall-clock (`SANDBOX_TIMEOUT_SEC=10`). Under six parallel shards a borderline
program crosses 10 s sometimes and not others.

0.09% is small, but it means the artifact does not reproduce bit-for-bit and the
guard-soundness falsifier's "PASSES on all 530 cells" is a probabilistic
statement. Both need saying out loud, or fixing with a deterministic budget
(instruction count / `sys.setrecursionlimit`-style step cap) instead of wall time.

---

## 6. Overfitting 0.476 is not the problem it looks like

Long & Rinard (ICSE 2016) establish the base rate: repair search spaces hold
"hundreds up to a thousand times more plausible patches than correct patches",
falling to "tens of times" for suites as dense as PHP's ~8,500 tests. Ahmed et
al. (FSE Companion 2026) measure 21.8-35.9% on SWE-bench with modern models, and
find refinement loops *raise* it by ~3pp. 0.476 on AtCoder-strength suites is
reportable data. It needs the oracle-strength delta stated (how much does the
held-out set add over the visible pool), not an apology.

---

## 6b. The corrected numbers, from the same data

Re-frozen and re-analysed with no new runs: `scripts/freeze_results.py --experiment
all --allow-partial` then `scripts/analyze.py`, both now carrying
`sandbox_runs_to_accept`. Output committed as `data/analysis.json`.
Per-task means, paired, with the project's own bootstrap CIs and Vargha-Delaney A12.

**`typed` vs `untyped` - the same comparison under the two cost units:**

| unit | band | RR | 95% CI | A12 | p |
|---|---|---|---|---|---|
| oracle calls | overall | 0.621 | [0.566, 0.684] | 0.284 | 1.0e-11 |
| oracle calls | hard | 0.509 | [0.426, 0.621] | 0.133 | 4.0e-05 |
| **sandbox runs** | **overall** | **0.983** | **[0.944, 1.020]** | **0.500** | **0.66** |
| **sandbox runs** | hard | 0.918 | [0.839, 1.000] | 0.461 | 0.16 |
| **sandbox runs** | medium | 1.043 | [1.003, 1.087] | 0.527 | 0.10 |
| **sandbox runs** | easy | 0.981 | [0.925, 1.030] | 0.505 | 0.74 |

A12 = 0.500 to three decimals, CI [0.944, 1.020]. Same episodes, same pairing,
opposite verdict - the only thing that changed is whether the guard's own sandbox
executions are counted. Thm 4.3(a) is **supported** in the honest unit, and tightly
enough to pass a TOST at a +-10% margin (which is how it should be reported: an
equivalence claim needs an equivalence test, not a non-significant NHST).

**Memory vs no memory, sandbox runs:**

| band | RR | 95% CI | A12 | p |
|---|---|---|---|---|
| hard | 1.526 | [1.266, 1.779] | 0.624 | 0.0009 |
| medium | 1.222 | [1.033, 1.530] | 0.565 | 0.059 |
| easy | 1.024 | [0.982, 1.081] | 0.483 | 0.093 |
| overall | 1.234 | [1.120, 1.364] | 0.539 | 0.009 |

1.23x, not 2.6x - and it is concentrated exactly where a search-space argument
predicts it should be: 1.53x on `hard`, indistinguishable from 1.0 on `easy`.
That gradient is a better result than the flat 2.6x ever was, because it has a
mechanism. It is the sentence to build the evaluation section around.

`success_at_b` from the same run, independently reproducing SS1 through the
project's own code: `no_memory` and `untyped` agree in every band -
0.06 / 0.54 / 0.89 / 0.9905 / 1.0.

---

## 6c. Thm 4.3(b) was a broken metric, not a failed claim

Three defects in `summarize_episode`, all fixed, none of them the
`force_full_budget` truncation - that one is correct: `effective` clips the
no-memory arm at its first accept and both arms come out to 9.92 rounds.

1. **Censoring.** A guarded round carries no `fine_type`, so it was skipped both
   as a possible repeat and as a seed. 81.6% of the flat arm's rounds are
   guarded, so the arm that guards most was measured least.
2. **Summing.** `redundant_attempts = n_guarded + guard_miss` adds a repeat the
   guard CAUGHT to one it MISSED. Opposite signs. And `n_guarded` is 0 for
   no_memory by construction, so its total was a different quantity.
3. **Arm-dependent reference set.** `eliminated_before` seeded only from unguarded
   rounds, so an arm that guards more had fewer types on record and therefore
   fewer *detectable* repeats. Byte-identical draw sequences scored 4.82
   (no_memory) against 3.08 (untyped) for that reason alone.

The censoring is repairable for free. `proposal_nonce` keys a draw on
(task, seed, round) and omits the mode, so every unconditioned arm draws the
byte-identical patch at the same round index - and the no-memory arm already paid
the oracle to type it. Measured: **4,269 of 4,269** guarded untyped rounds and
**1,641 of 1,641** guarded guard-only rounds recover their true type this way.
`--audit-guarded` covers the steered typed arm over E8's own universe (439/439).

With one arm-neutral reference set:

| arm | present | caught by guard | **paid to the oracle** |
|---|---|---|---|
| `no_memory` | 4.817 | 0.00 | **4.82** |
| `untyped` | 4.817 | 4.72 | **0.09** |
| `guard_only` | 4.818 | 4.66 | **0.16** |

`no_memory` vs `untyped`: **RR 52.1 [33.4, 90.8], A12 0.877, p 8e-16** - the
largest effect in the study. Thm 4.3(b) **holds**.

Redundancy *present* is identical - RR 1.000 [1.000, 1.000], A12 0.500, p 1.0 in
every band, 530/530 cells exact. That is not a result to report, it is a
**falsifier that passes**: two unconditioned arms that disagreed on it would mean
the CRN pairing was broken. The claim to make is that memory does not reduce the
redundancy the proposer emits - it converts 98% of it from oracle-paid to
guard-caught.

## 6d. A live contamination bug, found while fixing the above

`summarize_episode` never wrote `typing_random` into the frozen episode.
`analyze._is_main_grid`, `freeze._cell_key` and `measure_redundancy.is_main` all
test `not ep.get("typing_random", False)` against the FROZEN episode, and a key
that is never written reads False - so **E5-random's 90 episodes passed every
main-grid filter and pooled into `typed`**, which is why that arm carried 620
episodes against untyped's 530. c=0.0 is not random assignment, so those 90 are a
different arm.

Fixed. Post-decontamination the grid is 530/530/530 and the headline moves about
1%: Thm 4.3(a) from RR 0.983 [0.944, 1.020] to **0.990 [0.954, 1.024]**,
A12 0.502, p 0.61. Every figure in `data/analysis.json` and in
`docs/RESULTS-SUMMARY.html` is post-fix.

The lesson is structural, not incidental: **three separate arm-identity fields -
`typing_random`, `audit_guarded`, `free_guarded_rounds` - each had to be added to
the frozen episode by hand, and one was missed.** The frozen summary should carry
every field in `cell_signature` by construction rather than by a hand-maintained
list, and `check_consistency.py` should assert per-arm episode counts match.

---

## 6e. The remaining two claims: one bug, one structural

**Thm 4.2(i) was a denominator bug.** `ncdr = n_oracle_distinct / n_oracle`
divides distinct failure classes by EVERY unguarded round - including the accept
that ends the episode and every inconclusive or truncated one. None of those
carries a type, so none can ever enter the numerator: the metric had a ceiling
below 1.0 built into it. Over the rounds that actually reached a refutation:

| arm | NCDR (old denominator) | **NCDR over refutations** |
|---|---|---|
| `no_memory` | 0.388 | 0.612 |
| `untyped` | 0.565 | **0.964** |
| `typed` | 0.618 | **0.966** |

Thm 4.2(i) predicts NCDR -> 1.0 for the memory arms. It **holds**. Both are
reported: the old one is the share of oracle CALLS that bought a new class, the
new one is the share of REFUTATIONS that were novel, and only the second is what
the theorem is about.

The same CRN join fixes the censoring in FSRR, the type entropy and the revisit
curve. Two falsifiers now pass that could not before: `no_memory` and `untyped`
agree exactly on FSRR (0.388) and on type entropy (1.695 bits), as byte-identical
draw sequences must. The typed arm's FSRR is 0.179 - a 54% drop - and that gap,
now that it is not confounded with censoring, is the **steering effect isolated**:
the exclusion block makes the proposer emit half as many repeated failure
signatures.

Fixing FSRR's denominator was forced by the recovery, and the mismatch announced
itself: with `revisits` counted over recovered rounds and the denominator still
excluding guarded ones, the flat arm scored FSRR = 3.23. A rate above 1.

**Def. 3.1 is not a bug.** The manipulation check passes - mistyping fires at
0.825 / 0.633 / 0.432 / 0.181 / 0.077 against a predicted 1-c, the shortfall at
low c being the documented one (an early store has no other location to move to).
And `c` does drive the guard, monotonically:

| c | 0.00 | 0.25 | 0.50 | 0.75 | 0.90 | 1.00 | random |
|---|---|---|---|---|---|---|---|
| block rate | 0.225 | 0.282 | 0.291 | 0.289 | 0.318 | 0.319 | 0.290 |
| success | 0.711 | 0.667 | 0.700 | 0.722 | 0.722 | 0.722 | 0.678 |

Coherence moves the guard's **recall** by 42% relative, and success does not move
at all - for exactly the reason Cor. 4.4 is untestable. Under a charged budget the
guard cannot reach the outcome, so neither can anything that only acts through the
guard. E5 was never a standalone experiment; it is an E9 question. That also
explains the random null landing mid-sweep: `_still_refutes` verifies by
EXECUTION, so a mistyped bucket still blocks correctly whenever the counterexample
it holds still refutes. Typing decides which counterexamples get tried - recall
and cost - never whether a block is sound.

---

## 6f. E6-transcript removed, unrun

Deleted from `src/memory.py`, `src/proposer.py`, `src/loop.py`, `src/metrics.py`,
the five analysis scripts, `scripts/eval_shard.sh` and the notebook - along with
the `transcript_window` knob it was the only user of. `--modes transcript` and
`--exp E6-transcript` now fail with an unknown-choice error rather than half-running.

Two reasons, both from the data.

**It tested no surviving claim.** Thm 4.2(i), Thm 4.3(a), Thm 4.3(b) and Prop 4.5
are all internal to the no_memory / untyped / typed triangle. E6 served the
related-work paragraph, not a hypothesis.

**The claim it was built for is already falsified without it.** Metric #16 - "a
typed index is flat in the round index where a transcript grows linearly" - is
answered by the typed arm alone:

| arm | round 1 | round 20 | slope |
|---|---|---|---|
| `no_memory` | 514 | 515 | **0.1 tok/round** |
| `untyped` | 514 | 581 | **3.5 tok/round** |
| `typed` | 514 | 2,047 | **80.7 tok/round** |

The typed arm is the steepest of the three - a 4x prompt over twenty rounds, and
23x untyped's growth. Whatever a transcript does, typed is not the flat one. A
transcript run could only have established by how much a transcript is worse; it
could not have rescued the sentence. That slope is also what the 1.75x token
total is made of, and it should be reported as a cost of the exclusion block
rather than as evidence for the index.

The cheap version of the question survives if it is ever needed: `build_prompt`
is separable from `propose`, so a transcript prompt can be reconstructed from the
Attempts already in `data/episodes.jsonl` and tokenised - the whole
context-growth comparison with no model calls. Only "does transcript steering
beat typed steering on success" ever needed a run.

Five orphan rounds from one pilot episode remain in `data/episodes.jsonl`.
`freeze_results.py` drops them by `RETIRED_MODES` and says so, rather than
rewriting the primary artifact to erase a run that happened.

---

## 7. What to change

### Code — in priority order

1. **`--free-guarded-rounds`** (`src/loop.py:335`). A guarded round costs a model
   call but not an attempt. This is what makes the guard able to affect an
   outcome at all. Report both accountings.
2. **Sandbox-run cost accounting** as the primary unit, everywhere.
   `runs = examples_tried` on an oracle round, `guard_evaluations` on a guarded
   round. Add to `src/metrics.py`, make it the headline in `scripts/analyze.py`.
   Keep oracle calls as a secondary, clearly labelled as excluding guard work.
3. **Deterministic sandbox budget** instead of wall-clock timeout (§5).
4. **Token-matched placebo arm**: inject ~73 tokens of semantically inert filler
   into `untyped`'s prompt. If placebo ~ typed, the steering effect is *length*;
   if placebo ~ untyped, it is *content*. This one ablation converts "it did not
   work" into "we know why". Highest value per CPU-hour in the whole plan.
5. **Cross-bucket miss rate** as a first-class metric (the 949/3170 above). It is
   the empirical price of the O(1) index and belongs in the paper as a number,
   not a footnote.
6. `--jobs` for `measure_typing_coherence.py` — 193,783 sandbox runs, ~34 h
   serial, ~4 h at 8 threads. Cost is concentrated: 8 tasks = 75% of wall time,
   `abc287_g/40891564` alone is 7h23m.

### Statistics — currently the weakest surface

- Paired design over cells; **Wilcoxon signed-rank**, not Mann-Whitney (the arms
  are paired by CRN and the pairing is currently thrown away).
- **Vargha-Delaney A12** with bootstrap CI for count outcomes; **Fisher exact +
  odds ratio** for success@B. Arcuri & Briand (STVR 2014) is the citation, and it
  explicitly *advises against* Bonferroni — the current BH correction needs a
  stated justification either way.
- Every "no difference" sentence is an equivalence claim and needs **TOST** with
  a declared margin (Lakens 2017), not a non-significant NHST result. This
  applies to Cor. 4.4 and to Thm 4.3(a)'s restatement in §3.
- Justify the repetition count (Baltes et al., *Guidelines for Empirical Studies
  in SE involving LLMs*, AIware 2025) — n=90 per E5 cell will not survive.

### Metrics to adopt

- **ARI / AMI** (Vinh, Epps & Bailey, JMLR 2010) between theta and the behavioral
  signature. Chance-corrected, so a random labelling scores 0 in expectation —
  which *replaces* the E5-random arm with a correction. "ARI ~ 0 with a tight
  bootstrap CI" is a positive measurement finding; "flat sweep indistinguishable
  from a random null" reads as a failed experiment. Same data, publishable framing.
- **Purity / inverse purity / over-counting / under-counting** (GPTrace, ICSE
  2026, over the Igor crash-dedup benchmark). Four numbers that answer "is the
  taxonomy sound?" far better than a coherence sweep.
- Klees et al. (CCS 2018) is the warning to cite about dedup heuristics:
  57,142 "unique" AFL crashes collapsed to 9 real bugs. The typed memory is a
  dedup heuristic and inherits every pitfall in that paper.
- Bohme, Szekeres & Metzman (ICSE 2022): report proxy *and* ground truth. Guard
  evaluations and redundant attempts are proxies; sandbox runs and success@B are
  ground truth. Spearman rho > 0.90 between proxy and truth still gave only 0.498
  rank agreement — correlation does not license substitution.
- **NCDR has no prior art.** No published metric by that name or definition. The
  LLM guidelines say prior adoption alone is insufficient justification; zero
  adoption is worse. Either ground it in JPlag-style JDiv or supply a construct
  validity argument.
- **F2P/P2P** framing (SWE-bench harness) for the regression report already added
  in `src/oracle.py`.

### Threats that are currently unwritten

- **Contamination.** ConDefects covers Oct 2021-Sep 2023 (paper) or through Jun
  2024 (repo — the two disagree; state the snapshot actually used). Any 2024+
  open-weight model's cutoff *encloses* that window. Cheapest fix: prompt the
  model with the AtCoder problem statement alone and measure whether it emits the
  reference solution. ~2 hours, and it de-risks the whole paper.
- **External validity.** 49-LOC competitive-programming Python has a distinctive
  failure distribution (off-by-one, bounds, I/O parsing, overflow) that flatters a
  location-based taxonomy.

---

## 8. Reframing

The result that survives is §3: **typed and untyped cost the same in program
executions (1.00x), and the O(1) index is paid for by a 30% cross-bucket miss
rate.** That is a real, measured, counter-intuitive finding with a mechanism.

The closest published precedent is Lin et al., *To Run or Not to Run: Analyzing
the Cost-Effectiveness of Code Execution in LLM-Based Program Repair* (ISSTA
2026), which got in on a **null result about oracle feedback** — prohibiting
execution cost 1.25pp (not significant) while saving 56-62% of tokens. Their
framing is the one to borrow: *execution is a resource with an explicit
cost-benefit tradeoff, not a default capability.* Substitute "typed memory".

Suggested shape:

> Typed failure memory does not reduce the cost of LLM program repair. The
> O(1) type index is exactly cost-neutral against a flat O(m) scan in program
> executions, because the index's saved scan work equals the oracle work it
> fails to avoid — it misses 30% of the refutations a flat scan catches. The
> apparent 2.5x saving reported under an "oracle calls" unit disappears (to
> 1.19x) once the guard's own executions are counted.

Claims: (i) falsification with a mechanism, (ii) a measurement instrument (honest
cost unit + cross-bucket miss rate + chance-corrected taxonomy quality), (iii) an
actionable rule (guarded rounds must not consume the attempt budget; exclusion
blocks must be budgeted, not appended).

**Calendar.** FSE 2026 has already happened (Montreal, July 2026). The live cycle
is **FSE 2027, full papers due Fri 2 Oct 2026 AoE**, Shenzhen. Roughly five weeks.
Fallbacks that explicitly want this shape of result: ICSME 2026 Replication and
Negative Results, EASE 2026 Reproducibility and Negative Results, ISSRE 2026 RENE,
or an ESEM/MSR/EMSE Registered Report.
