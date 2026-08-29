> **SUPERSEDED — 2026-08-29.** Written before five measurement defects were found
> and fixed (docs/DIAGNOSIS.md). Its headline numbers are wrong in both directions:
> the 2.6x / 2.735x oracle-call saving is 1.23x once the guard's own sandbox
> executions are counted, and Thm 4.3(a), Thm 4.3(b) and Thm 4.2(i) are reported
> here as failing when all three hold. Kept as the record of what was believed at
> the time. For the current position see docs/RESULTS-SUMMARY.html.

# Results — E1–E8 against the paper's claims

**Corpus** ConDefects (Python), 106 faults frozen on measured π̂ · **Proposer** `qwen2.5-coder:7b`,
temperature 1.0, 32 768-token window · **Oracle** differential test, `max_examples = 100`,
30 s sandbox · **Budget** B = 20 · **Granularity** fine · single model, single granularity,
no reasoning effort — verified across all 35 755 rounds.

Every figure below is traceable to `data/analysis.json`, `data/results_real.json`,
`data/redundancy.json`, `data/patch_quality.json`, `data/anchoring.json` or
`data/theory_fit.json`. Rate ratios are paired per task with bootstrap CIs; `*BH`
marks significance after Benjamini–Hochberg across the three primary bands.

---

## 0. What ran, and what did not

| | |
|---|---|
| Episodes | **3 217** (35 755 rounds) |
| Main grid (E1 + E2) | **1 590 / 1 590 cells — complete**, 106 tasks × 3 arms × 5 seeds |
| E3 ablation | 318 + 318 cells (106 tasks × 3 seeds) |
| E4 oracle sweep | 3 × 90 (30-task sweep subset × 3 seeds) |
| E5 coherence sweep | 5 × 90, plus **E5-random** 90 (the null) |
| E8 redundancy audit | 2 × 90 |
| **E6 transcript** | **1 episode — not run.** A leftover smoke-test cell, not an arm |

**Two defects were found in the analysis path while producing this report, and both
had already corrupted the first pass.** They are fixed; every number here is from the
corrected re-run.

1. `freeze_results._stratum_by_task()` returns `{}` **without warning** when
   `data/strata.json` is absent. On Colab `data/` is a symlink into Drive, the file
   was not there when the freeze ran, and the entire stratified design was silently
   lost — the first `analysis.json` carried only an `overall` column.
2. `summarize_episode` did not emit `typing_random`, so the three main-grid
   predicates that filter on it were no-ops and **E5-random's 90 episodes pooled into
   the `typed` arm** (620 episodes where 530 were expected).

---

## 1. Memory works, and the paper's headline number replicates

`oracle_calls_to_accept`, no-memory → untyped:

| band | no_memory | untyped | rate ratio | A12 | |
|---|---|---|---|---|---|
| hard | 8.68 | 2.35 | **3.703** [3.050, 4.370] | 0.941 | *BH |
| medium | 6.05 | 2.21 | **2.740** [2.291, 3.212] | 0.902 | *BH |
| easy | 3.41 | 1.96 | **1.741** [1.527, 1.964] | 0.817 | *BH |
| overall | 5.58 | 2.04 | **2.735** [2.387, 3.090] | 0.784 | p = 5.2e-13 |

The abstract claims 2.6×; the measurement is **2.7× overall**, significant in every
primary band, and the effect grows with difficulty exactly as Theorem 4.3(a) predicts.

**This is memory's result, not typing's** — the arm that produces it shows the
proposer nothing.

## 2. Theorem 4.3(a) is rejected: typing *costs* oracle calls

The theorem predicts `typed ≡ untyped` on this metric. It does not hold.

| band | untyped | typed | rate ratio (untyped/typed) | | |
|---|---|---|---|---|---|
| hard | 2.35 | 4.76 | **0.521** [0.436, 0.635] | A12 = 0.146 | *BH |
| medium | 2.21 | 3.29 | **0.671** [0.600, 0.765] | A12 = 0.250 | *BH |
| easy | 1.96 | 2.83 | **0.693** [0.604, 0.803] | A12 = 0.294 | *BH |
| overall | 2.04 | 3.35 | **0.632** [0.576, 0.693] | A12 = 0.289 | p = 3.4e-11 |

**Typed spends 1.58× more oracle calls than untyped**, consistently, in all three
primary bands, all BH-significant.

The mechanism is coherent and is visible in the same freeze. A typed guard consults
only the bucket matching the candidate's failure type; an untyped guard replays every
stored counterexample. The exhaustive scan blocks more:

| | untyped | typed | |
|---|---|---|---|
| guard evaluations / episode | 10.55 | 5.74 | typed does **1.84× fewer** (*BH everywhere) |
| known counterexamples blocked | 8.05 | 4.92 | untyped blocks **1.64× more** |
| type repeats, de-censored (E8) | 3.29 | 4.58 | typed repeats **more** classes |

**Typed buys an O(1) guard by letting more candidates through to the oracle.** That is
a real trade, and Proposition 4.5's half of it holds — but the paper currently claims
both halves.

## 3. Proposition 4.5 holds

`guard_evaluations`, untyped → typed: RR **1.838** [1.659, 2.063] overall, *BH in every
primary band (hard 1.811, medium 2.305, easy 2.090). The Θ(m) versus O(1) claim is
supported. No-memory is 0.00 by construction.

## 4. Corollary 4.4: no arm repairs more than any other

`success_at_b`: **0.67 / 0.67 / 0.68** (no_memory / untyped / typed), A12 ≈ 0.500,
p ≈ 1 in every band. Resolve rate on `truly_correct`: **0.670 / 0.668 / 0.677**.

Memory changes *what a repair costs*, not *whether it happens*. Any claim about
budgeted success should be dropped.

## 5. The guard-soundness falsifier passes

RUNBOOK requires untyped and guard-only to reproduce E1's `success@B` exactly — a
guard only blocks candidates that provably still fail a stored counterexample, which
a correct patch cannot do. Across **all 530 E1 cells**: A12 = 0.500, p = 1.0, zero
disagreements. `check_consistency.py` asserts this.

This is the strongest evidence in the study that the implementation matches the model.

## 6. Typed costs 1.75× more tokens, and its prompt is not flat

`tokens_total`, untyped → typed: RR **0.571** [0.512, 0.631] — typed spends **1.75×**
more. `redundant_token_share`: untyped 0.509, typed 0.297 (typed wastes a smaller
*share*, on a larger total).

Metric #16, mean prompt tokens by round index:

| arm | round 1 | round 8 | slope / round |
|---|---|---|---|
| no_memory, untyped | 514 | 562 | **2.25** |
| typed | 514 | 1 283 | **73.0** |

The claim that a typed index is constant-size while a transcript grows is **not
supported as implemented**: the exclusion block grows at 73 tokens per round. Whether
it grows *more slowly than a transcript* cannot be answered — E6 did not run.

## 7. E4 is the study's strongest positive result, and it is a warning

Weakening the oracle raises the apparent repair rate while correctness collapses:

| `--max-examples` | accepted | truly correct | **overfit rate** |
|---|---|---|---|
| 100 | 65 | 65 | 0.000 |
| 20 | 67 | 62 | 0.075 |
| 8 | 73 | 50 | **0.315** |
| 3 | 82 | 43 | **0.476** |

At K = 3 the arm accepts the most patches of any configuration and **48 % of them are
wrong**. Read off `accept`, the sweep concludes that a worse oracle repairs better.
This is a clean, quotable demonstration that acceptance is not repair.

## 8. E5 shows nothing: the coherence axis is flat

| c | 1.00 | 0.90 | 0.75 | 0.50 | 0.25 | 0.00 | **random** |
|---|---|---|---|---|---|---|---|
| success@B | 0.722 | 0.722 | 0.722 | 0.700 | 0.667 | 0.711 | **0.678** |
| oracle calls | 3.26 | 3.22 | 3.25 | 3.79 | 3.22 | 3.86 | 3.66 |

No degradation slope, and no crossover c\*. The **random-typing null** — classes assigned
uniformly at random, the control that the c axis structurally cannot reach — lands
inside the spread of the coherent arms.

At n = 30 tasks × 3 seeds this is underpowered for a small effect, but it is the
result as it stands: **Definition 3.1's coherence has no measurable effect on this
corpus.** Combined with §2, the typed mechanism is not behaving as the theory
describes.

## 9. Redundancy is censored ~100× without E8, and de-censoring hurts typed

| arm | type repeats, no audit | with `--audit-guarded` |
|---|---|---|
| untyped | 0.03 | **3.29** |
| typed | 0.12 | **4.58** |

Guarded rounds carry no failure type unless the oracle is paid on them. Without E8 the
metric is not merely noisy, it is off by two orders of magnitude — and it reverses the
ranking. Any θ-based redundancy number reported without E8 is uninterpretable.

Duplicate-Patch Rate, the one redundancy metric that is arm-neutral and uncensored,
finds **no difference at all**: 0.217 / 0.217 / 0.219.

## 10. Two metrics are vacuous at K = 100

`correct/plausible = 1.000` and F2P/P2P `regression rate = 0.000` for both memory arms.
This is structural, not a finding: `max_examples = 100` exceeds the shipped pool for
almost every task (mean 39.6 cases, max 144), so the "sample" *is* the pool and
acceptance implies whole-pool correctness by construction. Both metrics only become
informative under E4's lower K — where, per §7, they are decisive.

## 11. Anchoring falls as typing degrades

10.2 % (c = 1.0) → 4.4 % (c = 0.0). Mechanically explicable and worth stating plainly:
anchoring is defined as *the correct class was excluded*, and a mistyped store excludes
some **other** class instead. The metric therefore does not capture the harm of
mistyping — that harm, if any, has to show up in rounds or tokens, and §8 finds it in
neither.

## 12. The closed form does not fit

`theory_fit.json`: no-memory arm r = **0.561**, relative MAE **153 %**, n = 81. The
memory arm could not be fit at all (`r = None`, MAE `nan`).

---

## What this means for the paper

**Defensible as written**

- Memory cuts oracle calls 2.7×, growing with difficulty (§1) — the headline result.
- The typed guard is cheaper per round, 1.84× (§3) — Proposition 4.5.
- Weakening the oracle inflates apparent repair and destroys correctness (§7).
- The implementation matches the model where it is checkable (§5).

**Not supported, and currently claimed**

- *Typed ≡ untyped on oracle calls* (Thm 4.3(a)) — **rejected**, typed is 1.58× worse (§2).
- *Typed memory is constant-size* — its prompt grows 73 tokens/round (§6).
- *Budgeted success improves* (Cor 4.4) — no difference between any arm (§4).
- *Typing coherence degrades performance as c falls* (Def 3.1) — flat, and
  indistinguishable from a random-assignment null (§8).

**The abstract's "cuts verification rounds 2.6×" is attributable to memory, not to
typing, and should say so.** The honest framing that survives this data is narrower and
still worth stating: *a typed index achieves memory's benefit at an O(1) guard cost,
paying for it in oracle calls and prompt tokens.*

## What could not be measured

- **E6 transcript.** The straw-man defence and the typed-vs-transcript token comparison
  — the measurement §6 exists for — are both unavailable.
- **ĉ, typing coherence measured against behavioural signatures.**
  `measure_typing_coherence.py` has not been run (≈327 k sandbox runs, 14–36 h).
  Given §8, this is now the highest-value remaining measurement: it would say whether θ
  carries any information about failure at all, and it must be read against the random
  baseline and competing bucketings the script now prints beside it.

## Threats a reviewer will raise

1. **One benchmark, one 7B proposer, 106 tasks.** Single-file competitive-programming
   programs. Position as a mechanism study, not a SOTA claim.
2. **Power.** Primary bands are 30 / 20 / 21 tasks; `easy` is 9 short of its designed
   quota, all nine lost to the `--min-siblings` requirement at the oracle gate.
3. **`dead` pools into `overall`.** All 20 `dead` tasks have π̂ = 0 exactly, so
   `oracle_calls_to_accept` is undefined there (n = 3 accepted episodes out of 100).
   The `overall` column mixes defined and undefined populations and should not be the
   reported figure — the per-band rows should.
4. **E4/E5 run on 30 tasks × 3 seeds.** Adequate for E4's large effect, underpowered
   for E5's absence to be called a null rather than a non-detection.
