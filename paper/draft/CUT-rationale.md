# How the paper got from 21 pages to 10 + 1

**Result:** `main-ieee.pdf` — `\documentclass[10pt,conference]{IEEEtran}`, no
compsoc, **10 content pages + 1 reference page**, 0 errors, 0 undefined
references, no overfull box above 5 pt, all fonts embedded (CID TrueType and
Type 1, no Type 3), PDF title/subject/keywords populated, author field empty
for double-anonymous review.

The preprint `main.pdf` still builds (15 pp) and carries everything the
submission drops.

## The arithmetic I worked to

| | start | end |
|---|---:|---:|
| body words | 14,760 | ~8,700 |
| floats | 19 + algorithm | 5 |
| content pages | 19.5 | **10** |

A full IEEE 10pt two-column page is ~1,100 words of pure text, and a float
costs 0.3–0.6 of a page. So 10 pages ≈ 5 floats + ~8,500 words, and everything
below follows from that budget rather than from taste.

## The three rules I cut by

**1. A number stays; the sentence explaining the number twice goes.**
Nothing measured was deleted. Every figure in the abstract, the intro preview,
and the results section is still there, and the tables still carry the sign
tests, the task-level tests and the intervals. What went is the second and
third telling of each: the intro preview restated the abstract nearly verbatim,
Discussion restated RQ3, and the Conclusion restated both.

**2. A float whose content is one sentence becomes that sentence.**
Fourteen floats moved to the supplement. Each survives in the text as the
number it carried, with its reference rerouted so nothing dangles.

**3. Theory that the empirical claims do not use moves to the supplement.**
This is the review's own advice — Reviewer 3 said the empirical core is
borderline-acceptable *if the theory is narrowed*, and the meta-review said the
strongest result does not need the riskiest claims. So the body now states in
full only the two results the study actually uses.

## What is where now

### Kept in the body (5 floats)

| Float | Why it survived |
|---|---|
| Table I, positioning | The novelty defence. Reviewers 1 and 4 both turn on it |
| Table II, main comparison | The core result — and now carries **both** units of analysis and the clustered CIs, absorbing the separate task-level table |
| Table III, ablation | The paper's headline: guard works, steering does not |
| Fig. 1, budget curve | The pre-specified primary metric |
| Table IV, notation† | †supplement-only in the submission build |

### Moved to the supplement

**Figures (7):** budget-both, per-band, guard-cost, typing-dose, oracle-depth,
prompt-growth, wall-clock.
**Tables (7):** notation, protocol, attrition, test-work, per-band,
free-guarded, integrity.
**Algorithm 1**, whose content is one sentence of §III.
**Theory:** the round-bound theorem, the redundancy theorem, the index-cost
proposition, the two idealizing assumptions, the observed-type-history
definition, and all proofs — stated formally in the supplement, summarized in
one paragraph in §IV.
**Appendix:** proofs and the timeout-edge sensitivity table.

The `\suppmattertrue` switch in `main-ieee.tex` does all of this. Every
reference to relocated content goes through `\appref{label}{fallback text}`,
which renders a normal `\cref` in the preprint and a phrase like "the
supplement" in the submission, so the two builds share one prose source and
neither can dangle.

## What I judged not really necessary, and why

| Cut | Reasoning |
|---|---|
| The intro's five-bullet findings list | It restated the abstract, and every number reappears in §VII within four pages. Reduced to three sentences |
| Discussion §VIII, 794 → 180 words | Its "why steering fails" mechanism is a genuine contribution and stayed; its "for agent designers" advice and its dead-band discussion were already in RQ3 and the Conclusion |
| Three theorem statements in the body | The paper's empirical claims use exactly two: guard soundness (falsifier F1) and the budget corollary (which shaped the accounting). The other three are cited by name where they are tested |
| Algorithm 1 | Eight lines of pseudocode restating one prose sentence. In a 10-page paper that is the most expensive sentence in the document |
| RQ2's null-by-null list | "guard seconds, entries consulted, oracle calls and repair rate all give *p* > 0.6" says the same thing as four separate *p*-values |
| Per-band ×factors, middle three | The gradient's endpoints (×12.1 dead, ×1.2 too-easy) carry the claim; the middle three are in the supplement's per-band table |
| Protocol table | A configuration dump. The four settings a reader needs — model, temperature, budget, depth — are one sentence |
| Attrition table | Its one load-bearing fact (the slow filter removed harder faults, rated 2042 vs 940) is now a clause in §VI and a sentence in Threats |
| The `finding` boxes | Replaced by bold run-in **RQ1.**/**RQ2.** takeaways: same skimmability, a third of the space |

## What I deliberately did **not** cut

These are the parts the review said were missing, and cutting them would undo
this week's work:

- The **guard-executes accounting** and its ×2.22 total-execution result. This
  is what answers "the guard hides its cost."
- **Both units of analysis** and the task-clustered intervals, now folded into
  Table II. This answers the strongest methodological objection.
- The **attribution caveat** in RQ2 (typed changes index *and* steering).
- The **ρ-versus-*c* correction** — the paper now says plainly that coherence
  is never directly validated.
- The **exact upper bounds** on the three zero-failure audits.
- The **slow-filter bias disclosure**, in both §VI and Threats.
- The **classical-APR section** and the six added references, plus the neutral
  capability matrix.
- The statement that **no asymptotic separation exists**.

## Two build fixes found while cutting

1. **A dropped float in the preprint.** `\balance` was invoked before the
   bibliography while an appendix followed it, and the package silently
   swallowed the appendix's trailing table — the caption never ran and
   `tab:sensitivity` never reached the `.aux`. Removed; the preprint now
   resolves every reference.
2. **A 95 pt overfull box** in the ablation table: its two `\multicolumn{4}{l}`
   sub-headers were unbreakable lines wider than an IEEE column. Shortened, and
   the numeric columns changed from `S` to `r` (siunitx reserves width by
   declared format, which a compact table cannot afford).

## If you need to find another half page

In this order, cheapest damage first:

1. Table IV (notation) is already supplement-only; the arms list in §VI-B can
   lose its parenthetical glosses — ~0.1 page.
2. §VII-F, free guarded rounds → three sentences — ~0.15 page.
3. §III-A's nesting explanation → one sentence, keeping Eq. 2 — ~0.1 page.
4. Table I loses its "Presumes about candidates" column — ~0.15 page.

Do **not** buy space by shrinking fonts, margins or `\baselinestretch`: SANER
desk-rejects format deviations, and the checks that catch them run before
review.

---

# Second pass: the Related Work rewrite, and paying for it

Review 1 scored novelty 2/5 on the ground that avoiding validation is old news.
Re-reading the primary sources agreed with the reviewer and found **four
factual errors in our own §II**, so the section was rebuilt rather than
patched. The page budget had no slack, so this pass had to be length-neutral —
it is, and the submission is still **10 content pages + 1 reference page**.

## What the sources actually say

| Our old prose | The source | Fixed to |
|---|---|---|
| AE: 3,252 → 186 "on 105 real bugs" | ASE 2013 computes it over the **45** defects AE and GenProg both repair | "on the 45 defects it and GenProg both repair, from a 105-defect benchmark" |
| ExpressAPR "no — accelerates" | TSE 2024 detects patch equivalence **online**; equivalent mutants are never executed | "accelerates; ExpressAPR also removes" |
| "Two mechanisms predate LLM repair" (equivalence *vs* prioritization) | AE contains **both** — the quotient space and **TestStrat**, its test-ordering component | AE now appears in both rows |
| Lou et al.: "2,532,915 patches from 12 systems" | 12 **APR tools**, on 395 bugs from 6 projects | corrected |
| UniAPR grouped with the accelerators | correct, and worth stating: it eliminates **nothing** | now contrasted explicitly |

## The concession that carries the section

The strongest objection available to a reviewer is that the guard is not a new
mechanism at all: *inline it into the oracle, present the stored
counterexamples first, stop at the first failure, and the executions performed
are byte-identical — no oracle call has been removed.* That is true. §II-B now
states it in bold and re-scopes the claim to an **ordering effect with a
measured magnitude**, which the paper's own numbers support: in the typed arm a
guard-resolved round costs **1.13** executions against **18.9** for one that
reaches the oracle.

Two consequences propagate through the paper. The word *skip* is gone
everywhere (abstract, intro, theory, results, threats, conclusion); and Table I
now places CEGMem in the **same** "reorders, stops early" cell as Qi'13 and
Venugopal'20, which is disarming rather than defensive.

What survives as novelty: prioritization is the one classical family that
requires nothing of the candidate space, so it is the one that transfers to an
LLM proposer; the key ($\lambda$, the edit location) and the evidence
(counterexamples the loop found, retained across candidates) are new; and the
non-vacuity measurement below has not been reported before.

## The new zero-execution evidence

`tools/related_addenda.py` replays the frozen log and costs nothing to run —
no sandbox executions, no model calls. It answers the three questions §II now
has to answer:

- **Why does replay fire at all?** In classical CEGIS it cannot: the
  synthesizer is constrained to satisfy every accumulated counterexample.
  An LLM is not, and **24.2%** of its proposals are byte-identical to an
  earlier one in the same episode.
- **Does the index earn its place?** No, and the paper now says so. Memory
  holds a median of **1** entry at resolution (max 3–5); 86.4% of resolutions
  need one consultation. Ordering cannot be distinguished from a flat scan
  here, so the typed guard's 39% cheaper seconds are attributed to
  deduplication, not the index.
- **Is the evidence just the test we were handed?** Partly: **32%** of the
  distinct inputs a cell stores were discovered after round 1.

## Where the space came from

| Cut | Lines | Reasoning |
|---|---:|---|
| Table I to `\scriptsize`, columns rebalanced | ~12 | The old column widths forced deep wrapping; the table is wider now and shorter |
| §I: the "maintenance cost is concrete" paragraph | ~4 | Rhetorical restatement of the Lin et al. figures directly above it |
| §I: CRN verification counts, contributions list | ~5 | The 495/495 and 297/297 counts are in §VI-B and §VII-G |
| §VII-E free guarded rounds | ~5 | Kept the result and the caveat, dropped the per-arm completion counts |
| Abstract | ~4 | 327 → ~290 words |
| §VI-B arm glosses, §VI-A temperature, §VII-B bucket explanation, Data Availability | ~10 | Each said the same thing in fewer words |

A double-rounding error surfaced while checking: `95.69` for no-memory
executions came from re-rounding an already-rounded `95.685`; the value is
`95.6848`, and Table II had it right at `95.68` all along. Fixed in §VII-A and
`tables/testwork.tex`.

---

# Third pass: the formal core, and paying for that too

Review 2's audit of the formal model cost **+21 lines** of new definition,
disclosure and corrected numbers. The submission is still **10 content pages +
1 reference page**. The space came from redundancy the first two passes had
left behind:

| Cut | Lines | Reasoning |
|---|---:|---|
| §IX Threats, rewritten whole | ~9 | Three paragraphs restated §VI and §VII verbatim: the seeds-share-a-fault argument appeared in three sections, the slow-filter numbers in two, the caching sentence in two |
| §I, the three-responses paragraph | ~3 | §II-A and §II-B now argue it properly; the intro only needs the conclusion |
| §IV, the three further results | ~2 | Kept the statements and the new class-exclusivity caveat, dropped one closed form's algebra to the supplement |
| §VII-B partition control, §VI-A filters, §VI-C metrics | ~5 | Each said the same thing in fewer words |
| §V, §VIII, §III-C, availability | ~4 | Same |

Two of those cuts were themselves fixes: the threats section had said the
sensitivity appendix shows "none moves", which is false for the guard-second
row, and §VI-C's restatement of the unit-of-analysis argument had drifted from
§IX's.

**The rule that kept this honest:** every line added in this pass was a
disclosure that makes a claim weaker or narrower, and every line removed was a
sentence the paper already said somewhere else. No measured quantity was
dropped to make room.
