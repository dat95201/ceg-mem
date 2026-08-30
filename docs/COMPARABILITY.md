# How to make this paper comparable

The problem is real: no external system was run, and no published number is
comparable. But the framing "we have no baseline" is wrong. **The strongest
cheap alternative to a repair loop is i.i.d. repeated sampling, and 530 episodes
of it are already in `data/episodes.jsonl`** — the `no_memory --force-full-budget`
arm is exactly k independent draws from the same model. It has been sitting in
the log labelled as an ablation instead of as the baseline it is.

---

## Tier 0 — already measured, zero new runs

### 0.1 Matched-budget repeated sampling — this is the comparison

Olausson et al. (ICLR 2024, *Is Self-Repair a Silver Bullet for Code
Generation?*, arXiv:2306.09896) give the protocol: count everything a repair
tree spends, then compare against that many i.i.d. samples from the same model.
Their finding — repair gains "are often modest… and are sometimes not present at
all" once cost is counted — is the closest published result to this paper's
thesis, on a different mechanism.

Run that protocol on the existing log, in program executions:

| sandbox runs | repeated sampling | untyped | typed |
|---|---|---|---|
| 20 | 5.8% | 5.1% | 5.8% |
| 30 | 20.0% | 18.7% | 18.7% |
| 40 | 33.6% | 33.8% | 34.0% |
| 50 | 40.8% | 42.8% | 42.6% |
| **60** | **48.1%** | **51.1%** | **52.3%** |
| **80** | **55.8%** | **61.3%** | **60.8%** |
| **100** | **60.6%** | **64.7%** | **65.1%** |
| 150 | 63.4% | 67.0% | 67.5% |
| 200 | 65.7% | 67.0% | 67.9% |

Two things a reviewer can use, and neither needed a new run:

**There is a crossover.** Below ~40 executions memory is level or slightly behind
— it has nothing stored yet, so the guard costs without paying back. Above ~50 it
leads by **4–5 percentage points** and holds the lead to saturation. A crossover
with a mechanism is a much better result than a flat ratio.

**It contradicts Olausson in a specific direction.** They found repair loses to
matched-budget sampling. Counterexample memory wins by 4–5pp, and the reason is
in this paper's own data: the guard's cost is sublinear in the oracle work it
avoids (Thm 4.3(b): redundancy paid falls 4.82 → 0.09). That is a positioning
sentence no other arm can supply.

The pass@k curve this rests on is **already computed** —
`data/redundancy.json → repeated_sampling.pass_at_k_empirical`, 0.209 @1 rising
to 0.670 @20, with the analytic curve beside it. It has never been reported.

### 0.2 An independent corroboration exists

**MERIT** (arXiv:2608.05906, Aug 2026) conditions retrieval on a typed failure
classifier over a dual-polarity memory, using Qwen2.5-7B-Instruct on Spider and
BIRD — a different task, a comparable model. Its ablation reports that *"untyped
dynamic retrieval performs comparably on both benchmarks."* That is a
contemporaneous, independent replication of this paper's typed-vs-untyped null,
arrived at by another group on other data. Cite it in related work and in the
discussion; it converts a suspicious null into a corroborated one.

### 0.3 The methodological cover, quoted verbatim

The ACM SIGSOFT Empirical Standards (Engineering Research) make comparison an
essential attribute in this form: *"Either empirically compares to
state-of-the-art alternatives **OR** benchmarks **OR** provides a clear and
convincing rationale for why comparative evaluation is impractical."* The same
standard lists among **invalid criticisms**: *"demanding comparisons with
unavailable or non-functional competing approaches."*

That clause is usable here because it is factually true, and checkable:

- **No ConDefects leaderboard, canonical subset, or standard protocol exists.**
  The dataset paper (Wu et al., arXiv:2310.16253) reports fault-localisation
  results only, no repair. The 2025 LLM-repair survey (arXiv:2506.23749) does not
  mention ConDefects. AwesomeLLM4APR lists it with zero papers attached.
- **Every LLM-APR system with released code that could serve as a baseline is
  Java-coupled**: SRepair, RepairAgent, ThinkRepair, AlphaRepair, RepairBench all
  target Defects4J and need a JDK toolchain. ChatRepair released patches, not a
  tool, and an independent team (arXiv:2503.15050) reports it was *"unavailable"*
  and had to be re-implemented from the paper.

Write that paragraph with those citations and the absence stops being a hole and
becomes a finding about the sub-field.

**The price of using the clause: the 106 must be justified.** State the selection
rule, the date window, and the difficulty filter explicitly, or it reads as
convenience sampling. Krafczyk & Schmid (arXiv:2604.26674) — 21.6% of Defects4J
defects unsuitable for rigorous evaluation — is the precedent for principled
subsetting.

---

## Tier 1 — cheap, days not weeks

### 1.1 A positioning number on ConDefects-Python

Only one published result is close enough to sit in a table. Sun et al.
(arXiv:2506.13186, Jun 2025) run 563 ConDefects-Python bugs under **method-level
perfect fault localisation**, 30 patches/bug, manually validated:

| model | zero-shot | one-shot | bug-analysis |
|---|---|---|---|
| CodeLlama-7B | 2.7% | 8.2% | 10.7% |
| StarCoder-15.5B | 9.6% | 14.0% | 9.1% |
| DeepSeek-Coder-33B | 16.5% | 21.5% | 12.8% |

CodeLlama-7B is the size match. **This is not a head-to-head** — different subset,
different FL assumption, different validation — and it must be labelled as a
context row, not a comparison. But it is the only 7B-class ConDefects-Python
figure in the literature, and having it makes the paper look situated rather than
isolated.

### 1.2 A second proposer — the highest value per dollar in the whole plan

gpt-4o-mini on the 30-task sweep, ~$3, `--backend cloud` already wired. This does
not buy a baseline; it buys **external validity**, which is the single most likely
desk-reject line ("one 7B model"). Nothing else on this list removes that.

### 1.3 QuixBugs-Python — as a harness check, not an evaluation

`external/QuixBugs` is already vendored (50 Python programs) and `src/tasks.py`
is a 5-line stub of the old adapter. Rebuilding it is roughly half a day.

**But it will not show anything.** Qwen2.5-Coder-7B already scores **38/40** on
QuixBugs-Python with plain repeated sampling (arXiv:2508.16499, replication
package at Zenodo 10.5281/zenodo.15472061); ChatRepair, BaseChatGPT, CodexRepair
and o1-preview all report 40/40. There is no headroom for a memory ablation.

Its legitimate use is one sentence of infrastructure validation: *"our oracle and
loop reproduce 3X/40 on QuixBugs-Python with qwen2.5-coder:7b, consistent with
the 38/40 reported by [2508.16499]."* That is cheap external evidence the harness
is not broken — which, after five measurement defects, is worth having.

---

## Tier 2 — a real baseline, if there is time

**Reflexion** (github.com/noahshinn/reflexion) is the only LLM-repair baseline
with released code that is not Java-coupled. It is OpenAI-API-shaped, so pointing
it at an Ollama endpoint is a small change, and its verbal-reflection memory is
the natural contrast to a typed counterexample store.

Do this **only** after the draft exists. It is the fourth priority, not the first.

Everything else in the field is out of reach in five weeks: Self-Repair's code
needs an unreleased internal `exec_sample`; SRepair needs >20 GB VRAM (the T4 has
16); the rest need Defects4J.

---

## Tier 3 — be comparable to future work

The one thing fully in this paper's control. The Benchmarking standard names
*"collecting aggregated rather than raw measurements"* as an antipattern, and
How2Bench (arXiv:2501.10711, 274 benchmarks) found only **35.4%** repeat
experiments at all — five seeds already beats the field, and the paper should say
so rather than apologise.

Release, and state in the paper:

- per-task, per-seed raw results, not just means
- the exact prompts (52.6% of benchmarks do not disclose them)
- **program executions** as the cost unit, with the guard's own runs counted —
  and say plainly that oracle calls are not a cost unit, because that single
  choice is the difference between a claimed 2.50x and a real 1.23x
- the pass@k reference curve beside every arm, so any future system can be
  dropped onto the same axis
- Dong & Shigida (arXiv:2605.08478) propose **−ln(1−p)/c**, log-failure-likelihood
  per unit cost, as the reporting unit for exactly this comparison. Adopting a
  named unit from the literature is cheaper than defending a new one.

---

## What I would actually do

1. **Reframe `no_memory --force-full-budget` as the repeated-sampling baseline**
   and put the matched-budget table in the paper. Zero runs. This is the
   comparison, and it is a positive result.
2. **Write the absence-of-baselines paragraph** with the Empirical Standards
   clause and the four citations above, plus the explicit 106-selection rule.
3. **Cite MERIT** as independent corroboration of the typed≈untyped null.
4. **Spend the $3** on gpt-4o-mini over 30 tasks.
5. Half a day on QuixBugs-Python, for the harness sentence only.
6. Reflexion, only if the draft is done.

Do not run QuixBugs expecting a result, and do not attempt a Defects4J baseline.

## Unverified — check before citing

- The APRMCTS ConDefects table (arXiv:2507.01827) is internally inconsistent and
  its repository 404s. Do not cite those numbers without verifying.
- The ISSTA-24 QuixBugs-Python figures for AlphaRepair (27/40) and CoCoNuT
  (19/40) are as-reported by Xia & Zhang for Java-oriented tools; confirm they
  are real Python runs before using them.
- "Only two papers run repair on ConDefects-Python" rests on keyword search plus
  two surveys, not an exhaustive citation sweep. Do one manual Google Scholar
  pass over the ConDefects citations before asserting it in print.
