# Point-by-point response to the SANER 2027 review packet

Every point in `codexreview.md`, in the review's own order, with what was done
and where. Nothing omitted, including the points I could not resolve and the
ones where the review is wrong.

**Legend** — ✅ resolved · 🟡 partial (needs your decision or an experiment) ·
⬜ not done · ❌ review point is factually incorrect (evidence given)

**Score so far:** 92 ✅ · 14 🟡 · 17 ⬜ · 5 ❌ (128 points)

Build state: `main-ieee.pdf` **21 pages, 0 errors**; `main.pdf` 23 pages,
0 errors. Both compile clean with no overfull box above 6 pt.

---

## A. Decision snapshot

| # | Point | What was done | Status |
|---|---|---|---|
| A1 | Desk reject: 19 pp, ACM-style layout | `main-ieee.tex` added: `\documentclass[10pt,conference]{IEEEtran}`, no compsoc. Format now compliant. Length is **not**: 21 pp against 10+2 | 🟡 format ✅, length ⬜ |
| A2 | Technical weak reject / major revision | All three named causes addressed: formal core rewritten (§E), novelty boundary corrected (§D), strongest claims scoped (§G4) | ✅ |

---

## B. Paper and evidence map — the seven "reviewer risks"

| # | Risk | What was done | Status |
|---|---|---|---|
| B1 | "Oracle call" obscures guard-side re-execution; this is not "no test execution" | **The review is wrong on the fact**, and I verified it in your code: `src/loop.py` bills every row `sandbox_runs = guard_evaluations + st_runs + examples_tried`, so guard replays were always inside the reported execution metric. What was missing was saying so. New `tables/testwork.tex` splits total executions into guard and oracle per arm: **95.68 → 43.01, ×2.22**, guard share 22%. The thesis wording is scoped everywhere from "not run the tests" to "skip a full validation after a targeted replay" | ❌ fact / ✅ disclosure |
| B2 | Typed-vs-untyped changes indexing *and* steering; closest baselines missing | New attribution paragraph in §7.2 states the contrast is a package and points to `guard-only` vs `untyped` (295/297 identical episodes) as the index-only isolation. Baselines: 🟡 planned, not run | ✅ attribution / 🟡 baselines |
| B3 | Prop. 6's proof ignores fallback and flat-guard short-circuiting | Proposition rewritten from scratch. No asymptotic claim survives; it is now a per-round statement about *where* the refuting entry sits, and it explicitly says the worst cases coincide | ✅ |
| B4 | Dead-band claim borderline, subgroup-specific, not family-corrected | Threats now names it "the weakest link we report as a positive", states it is one band of five at p=0.047, and says the interaction was not tested | ✅ disclosure / ⬜ interaction test |
| B5 | "True repair" overclaimed | Replaced throughout with pool adequacy; new nesting $G^\star \subseteq G_{\rm pool} \subseteq G_k$; exact upper bounds added | ✅ |
| B6 | Formal core internally inconsistent | §04-theory and the appendix proofs rewritten end to end — see §E below | ✅ |
| B7 | Reproducibility not verifiable | `sections/11b-availability.tex` created, placed after Conclusion, lists the four extractors and the replay guarantee. **No DOI yet** — placeholder marked | 🟡 |

---

## C. Venue compliance — 5 findings

| # | Finding | What was done | Status |
|---|---|---|---|
| C1 | Page limit violated (P0) | Measured honestly: 21 pp. Section-by-section cut plan with page targets in `PLAN-experiments.md` | ⬜ |
| C2 | Wrong proceedings format (P0) | `main-ieee.tex`, verified against the live CFP text. `make ieee` also prints the page count against the limit | ✅ |
| C3 | Data Availability placement/content (P1) | New section, immediately after Conclusion, in all three drivers. Content lists archive contents, anonymisation, and replay properties | ✅ placement / 🟡 DOI |
| C4 | Double anonymity — audit artifact metadata | Checklist item added to README ("cache keys, absolute paths, usernames, git history, repository ownership"). Not executed — the archive does not exist yet | 🟡 |
| C5 | AI/ML scope statement missing (P2) | New paragraph in §I naming the maintenance problem: build minutes and latency spent re-confirming failures the agent has already seen | ✅ |

---

## D. Review 1 — Novelty and related work

| # | Point | What was done | Status |
|---|---|---|---|
| D1 | Closest prior work missing (equivalence, quotient space, fault-recorded prioritization) | §II restructured into four subsections around *what the decision is computed from*. Every literature figure re-verified against primary sources, which caught **four errors in our own prose**: AE's 186-vs-3,252 is over the **45** defects it and GenProg both repair (not 105); ExpressAPR **does eliminate executions**, via online test-equivalence (Table I said "accelerates" only); AE's **TestStrat puts a test-ordering mechanism in the same paper** as the quotient space, so the two-family split was a false dichotomy; Lou et al.'s "12 systems" are **12 APR tools on 395 bugs from 6 projects** | ✅ |
| D2 | Distinction from test prioritization overstated | **The review was right and the second-round rewrite concedes it.** A reader can inline the guard into the oracle — stored counterexamples first, stop at the first failure — and perform byte-identical executions; no oracle call is removed. §II-B now states that identity in bold rather than arguing with it, and the claim is restated as an **ordering effect with a measured magnitude** (1.13 executions on a guard-resolved round against 18.9 on one reaching the oracle). Table I puts CEGMem in the *same* "reorders, stops early" cell as Qi'13/Venugopal'20 | ✅ |
| D3 | "First execution of the LLM CEGIS loop" falsified by Orvalho et al. | Claim deleted from the contributions list. Orvalho et al. now cited as **AAAI 2025** (the review said arXiv — wrong) with its real scope: 1,431 student C programs, six models. Our novelty restated as the *second* use of the same evidence | ✅ |
| D4 | Typing's marginal value is modest | Stated in the paper's own voice: "real, signed and small", A12 = 0.52, CI upper end two hundredths of a call. The recommendation is the guard, not typing | ✅ |
| D5 | Composition with faster validators asserted, not evaluated | "The two multiply" deleted; §II-E now ends "Whether the two gains combine, and how much, is an open empirical question we do not answer here" | ✅ |
| D-Q1 | How is this different from test-equivalence / quotient spaces? | Answered in §II-A: they relate candidates *to each other* and need an enumerable space and a modelled edit shape; ours relates a candidate to *previously observed failing inputs* and needs nothing of the candidate space. Also concedes theirs is the stronger guarantee on their ground | ✅ |
| D-Q2 | Why is history-based prioritization not a baseline? | Honest answer added in Threats ("Baselines we did not run"). Plan item 1 shows it can be run **with zero model calls** | 🟡 |
| D-Q3 | What remains novel after narrowing? | Narrowed twice. The claim is no longer "a new mechanism" but **a known mechanism, a new applicability condition, and the first measurement**: prioritization is the one classical family that needs nothing of the candidate space, so it is the one that transfers to an LLM; what is new is the key (edit location) and the evidence (counterexamples the loop discovered, retained across candidates). New §II-C adds the **non-vacuity argument** — in classical CEGIS a constrained synthesizer cannot emit a candidate its counterexamples refute, so replay would never fire — with the measurement that makes it empirical: **24.2% of proposals are byte-identical to an earlier one in the same episode** (`tools/related_addenda.py`, zero executions) | ✅ |
| D-Q3b | Does the λ index earn its place? | **No, and the paper now says so.** New supplement table: memory holds a median of **1** entry at resolution, max 3–5; 60.5% of resolutions happen with ≤1 entry and 86.4% after one consultation, so ordering is indistinguishable from a flat scan on this corpus. The typed guard's 39% cheaper seconds are attributed to deduplication and verdict memoization, not to the index | ✅ |
| D-Q4 | Would the title be more accurate about avoiding *full validation*? | IEEE title changed to "…with a **Location-Indexed** Memory of Refuted Attempts". The "skip" vocabulary is now **purged paper-wide**: abstract, intro, theory, results, threats and conclusion say *resolve the round without an oracle call* / *terminate on the first case*, never *skip* or *remove* a validation. The `main.tex` title still says "Typed Memory" | 🟡 |

---

## E. Review 2 — Formal model, notation, proofs

### Seven correctness issues

| # | Point | What was done | Status |
|---|---|---|---|
| E1 | Semantic correctness and observed acceptance conflated | Three sets now distinguished: $G^\star$ (semantic), $G_{\rm pool}$, $G_k$, with the nesting as Eq. 2. **Assumption 1 changed from acceptance-soundness to refutation-soundness** — the only property the guard actually needs, and one that holds by construction | ✅ |
| E2 | Pre-execution type key is unavailable | Verified against `src/memory.py`: the bucket key is `edit_location(buggy, candidate)`, a pure diff. So the paper was describing its own implementation wrongly. Now $\lambda(p)$ (computable pre-execution) and $\theta(p,x)$ (post-execution label) are separate, memory is keyed on $\lambda$ (Eq. 5), and the text says so explicitly | ✅ |
| E3 | Prop. 6 does not follow for the fallback guard | Rewritten. States the flat scan short-circuits too, that the worst cases coincide, that no asymptotic separation exists, and that the measured 39% is deduplication + ordering. Proof rewritten to match | ✅ |
| E4 | Assumption 2 does not make the bucket equivalent to Eq. 2 | §III now says a bucket-only guard is a *partition*, sound as a stopping rule only if its key decides refutation — which $\lambda$ does not, since refutation is decided by the input | ✅ |
| E5 | Thm 4(b) inconsistent with the redundancy definition | New Eq. 6 defines the observed-type history $E^{\rm obs}_t$ from the episode's refutations, arm-independent, so a memory-free arm has a well-defined redundancy count. Theorem and proof both restated on it, and split into *generated* (needs steering) vs *paid* (does not) | ✅ |
| E6 | Budget semantics blurred | New paragraph after Thm 3 names three distinct quantities (oracle calls, generated proposals, charged proposals), states the bound applies to the first two on an unbounded run, and gives the finite-budget form | ✅ |
| E7 | Cor. 5 over-specialized; strictness underspecified | Restated for **any sound guard**, and the strictness condition is now about budget position ("a block that moves a terminal draw from beyond $B$ to within it"), not about $q_\tau$ | ✅ |

### Seven notation and terminology concerns

| # | Point | What was done | Status |
|---|---|---|---|
| E-N1 | Separate symbols for semantic vs finite-oracle correctness | $G^\star$ / $G_{\rm pool}$ / $G_k$ / $\mathcal{O}_k$, all in the notation table's own block | ✅ |
| E-N2 | Define the computable key separately | $\lambda(p)$ vs $\theta(p,x)$, in their own notation block | ✅ |
| E-N3 | §7.5 claims coherence but measures cross-refutation ($\rho$) | Paragraph relabelled: it estimates $\rho$, not $c$; the paper now states plainly that **$c$ is never directly validated** and is only perturbed, and that earlier versions mislabelled it | ✅ |
| E-N4 | Define $K$ over positive-mass types | $K = \lvert\{\tau : q_\tau > 0\}\rvert$, in Assumption 3 and the notation table | ✅ |
| E-N5 | Eq. (3)'s tuple indexing is nonstandard | Replaced with scalar base mass $w_s$ ($w_\top = \pi$, $w_\tau = q_\tau$) | ✅ |
| E-N6 | $B$ and $b$ alternate | Notation table has a dedicated block: $B$ proposal budget, $b$ oracle budget, $k$ oracle depth, with a pointer sentence in §III | ✅ |
| E-N7 | Complexity claims must be qualified; execution is not constant work | All $O(1)$ complexity language for the guard removed. "$O(1)$ index-hit rate" renamed "bucket-only resolution rate". Prop. 6 counts re-executions, not abstract steps | ✅ |

### Five questions to the authors

| # | Question | Answer now in the paper | Status |
|---|---|---|---|
| E-Q1 | Which key is computed before any test runs? | §III-B: $\lambda(p)$, the edit location, from a diff | ✅ |
| E-Q2 | Is the fallback part of the analyzed algorithm? | Yes — §III-C and Prop. 6 both analyze bucket-first-then-fallback; §V records that bucket-only was the original bug | ✅ |
| E-Q3 | What supports expected $O(1)$ after fallback? | Nothing, and the paper now says so: no asymptotic claim is made | ✅ |
| E-Q4 | Is "correct" semantic, full-pool, or depth-$k$? | Explicit: theorems are about $G_k$; audits establish $G_{\rm pool}$; $G^\star$ is never claimed | ✅ |
| E-Q5 | What eliminated set labels no-memory attempts redundant? | $E^{\rm obs}$, Eq. 6 | ✅ |

---

## F. Review 3 — Empirical design, statistics, reproducibility

### Ten major weaknesses

| # | Point | What was done | Status |
|---|---|---|---|
| F1 | One model, one temperature, one narrow corpus | Threats sharpened and the corpus filter disclosed. **No second model run** — plan item 2 (495 primary-band cells, ≈11 h) | ⬜ |
| F2 | 526→99 attrition lacks a table | `tables/attrition.tex` built from the frozen screening record: 526 examined → 112 ineligible (64 reference disagrees/raises, 48 slow) → 7 unresolved → 407 usable → 295 pool → 196 quota-unfilled → 99 | ✅ |
| F3 | Resource boundary: report guard-side executions combined with oracle-side | `tables/testwork.tex` — and the metric already included them (see B1). Also noted the split is *conservative against* the memory arms because memoized consultations are still billed | ✅ |
| F4 | Cells are not independent inferential units | `tables/tasklevel.tex`: every headline comparison at both units plus a task-clustered bootstrap CI (10,000 resamples of 99 tasks). All survive; typed-vs-untyped correctly weakens p=0.007 → 0.030 | ✅ |
| F5 | Multiplicity only partly controlled | New §VI paragraph "The confirmatory family, fixed in advance": two outcomes on three arms confirmatory, everything else labelled exploratory | ✅ |
| F6 | Hardest-band result fragile | Disclosed as the weakest positive claim; interaction test not run | 🟡 |
| F7 | Free-guarded condition incomplete and unbalanced | Explained exactly: 60/57/54 of a target 60 per arm; the nine absent are free-condition runs that did not finish; **every free cell has its charged twin**, so all 171 comparisons are matched pairs | ✅ |
| F8 | "Pre-registered" is unverifiable | Every occurrence changed to "pre-specified" (13 sites). Threats explains what that means here and gives the chronology of the guard correction relative to the reported run | ✅ |
| F9 | Artifact claims cannot be checked | Data Availability section written; archive not yet minted | 🟡 |
| F10 | Full-pool correctness is not semantic correctness | Stated in §VII-D and Threats: what is established is $G_{\rm pool}$, "which no test pool can certify" for $G^\star$ | ✅ |

### Seven requested additional analyses

| # | Request | What was done | Status |
|---|---|---|---|
| F-A1 | Task-clustered bootstrap CIs and task-level tests, consistently | Done for all 8 headline contrasts and 4 budget points — `tools/review_addenda.py`, `tables/tasklevel.tex` | ✅ |
| F-A2 | Total sandbox executions and seconds, guard + oracle | Counts done (`tables/testwork.tex`), with guard seconds and oracle seconds both extracted into `addenda.json`. Seconds not yet in a table | ✅ counts / 🟡 seconds |
| F-A3 | Attrition flow with exclusion counts | `tables/attrition.tex`, plus the profile of the 48 removed by the slow filter (rating 2042 vs 940) | ✅ |
| F-A4 | A larger/different model, or a repo-level benchmark, or narrow all claims | Claims narrowed in Threats; second model is plan item 2 | 🟡 |
| F-A5 | Compare against fault-history prioritization and test-equivalence baselines | Not run. Plan item 1 shows the prioritization family costs **zero model calls** under CRN | ⬜ |
| F-A6 | CIs on zero-failure rates | Exact one-sided Clopper–Pearson bounds: overfitting ≤0.24% (0/1256), regression ≤0.32% (0/939), missed mutant ≤1.2% (0/249) | ✅ |
| F-A7 | Test the difficulty × steering interaction | Not run — plan item 3, half a day of analysis, no new runs | ⬜ |

### Five questions to the authors

| # | Question | Answer | Status |
|---|---|---|---|
| F-Q1 | Do sandbox case-executions include guard replays? | **Yes** — `loop.py` bills them on every row. Now stated in the paper and tabulated | ✅ |
| F-Q2 | How were seeds implemented — independent draws or cache identifiers? | Answered in §V and Threats: the nonce is (task, seed, round), the cache is keyed on prompt+nonce, and inference is *conditional on a fixed draw table* | ✅ |
| F-Q3 | Was the guard correction made before or after inspecting treatment outcomes? | Now stated in Threats: after inspecting an **earlier** run's guard accounting, **before** the reported run; every number is from the corrected guard | ✅ |
| F-Q4 | Why only 939 of 1,256 in the regression audit? | A regression verdict needs at least one case the buggy version already passed; 317 acceptances belong to faults whose pool has none. Now in §VII-D | ✅ |
| F-Q5 | How many shipped tests per fault; how often is $k{=}100$ the whole pool? | For **97 of 99 faults the entire pool is ≤100 cases** (median 33, mean 38.9, max 144), so at $k{=}100$ the oracle usually *is* the whole pool. Now in §VII-D | ✅ |

---

## G. Review 4 — Presentation, citation hygiene, AI-writing risk

### Seven major weaknesses

| # | Point | What was done | Status |
|---|---|---|---|
| G1 | Noncompliance prevents review | IEEEtran driver done; length not | 🟡 |
| G2 | Over-length even before conversion | Confirmed: 21 pp. Per-section cut plan written | ⬜ |
| G3 | Rhetorical meta-commentary overused | All eleven flagged phrases replaced — see the table below | ✅ |
| G4 | Categorical phrases unsafe | "the field lacks", "none", "first", "cannot hurt", "composes multiplicatively", "true repair" all removed or scoped | ✅ |
| G5 | "CEGMEM" extracts as "CEGM EM" | Not fixed. The small-caps macro is what splits under `pdftotext`; a real fix needs `ActualText` or dropping `\textsc` for the name | ⬜ |
| G6 | PDF metadata empty; untagged | `\hypersetup` added: title, subject, keywords, `pdfdisplaydoctitle`; author deliberately empty for anonymity. **Verified in the built PDF.** Tagged PDF (PDF/UA) not attempted | ✅ metadata / ⬜ tagging |
| G7 | Captions make interpretive claims | Partially: Fig. 4's caption corrected and several trimmed. A full caption-shortening pass belongs with the page cut | 🟡 |

### The eleven-phrase audit

| # | Phrase | Replacement | Status |
|---|---|---|---|
| G-P1 | "The field lacks a principled answer" | Deleted; replaced by an accurate statement that classical APR answers it and why those mechanisms are unavailable here | ✅ |
| G-P2 | "The cost is now measured, and most of it does not pay." | → "The cost of that loop is now measured." | ✅ |
| G-P3 | Table 1 caption "the mechanism nobody in these lines has" | Table rebuilt; caption now describes the columns as factual properties, "not a scoring of it" | ✅ |
| G-P4 | "A correction worth reporting" | → "An implementation correction." | ✅ |
| G-P5 | "a reviewer is right to discount a tautology" | → "so the comparison is confirmatory of nothing" | ✅ |
| G-P6 | "A banding caveat we do not hide" | → "Regression to the mean in band construction" | ✅ |
| G-P7 | "The honesty panel" | → "counts the same data two ways" | ✅ |
| G-P8 | "The honest reading is…" | → direct statement of the effect size | ✅ |
| G-P9 | "the cleanest test in the paper" | → "This comparison holds the episode fixed, which is what isolates…" | ✅ |
| G-P10 | "One anomaly recurs and we name it once" | → "One anomaly accounts for every inconsistency below" | ✅ |
| G-P11 | "the one we would most want a reader to carry away" | → "the one that changes the recommendation" | ✅ |

---

## H. Cross-cutting audit table — all 23 rows

| # | Pri | Finding | Status |
|---|---|---|---|
| H1 | P0 | 19 pp + wrong template | 🟡 template ✅, length ⬜ |
| H2 | P0 | $\mathcal{O}$/$G$/semantic/sampled conflated | ✅ |
| H3 | P0 | $\theta(p)$ needs a violated property the guard cannot have | ✅ |
| H4 | P0 | Fallback guard lacks the proved $\Theta(K)$ cost | ✅ |
| H5 | P1 | Thm 4(b) vs $E = \emptyset$ in the no-memory arm | ✅ |
| H6 | P1 | §7.5 measures $\rho$, labelled $c$ | ✅ |
| H7 | P1 | "not run the tests at all" contradicts guard replay | ✅ |
| H8 | P1 | "true repair" means shipped-pool agreement | ✅ |
| H9 | P1 | "pre-registered" has no record | ✅ |
| H10 | P1 | Frozen log/scripts/cache unavailable | 🟡 |
| H11 | P1 | 526→99 without per-stage counts | ✅ |
| H12 | P1 | Typing attribution confounds index with steering | ✅ |
| H13 | P1 | Per-cell tests treat seeds as independent | ✅ |
| H14 | P1 | Free-round 171/180, unequal arms | ✅ |
| H15 | P1 | "Composes multiplicatively" not demonstrated | ✅ |
| H16 | P2 | "Five arms, 1,485 cells" misleads | ✅ (abstract now gives the per-arm split) |
| H17 | P2 | "Full oracle depth" is $k{=}100$ | ✅ (all sites → "the reported depth", plus the 97/99 pool fact) |
| H18 | P2 | "Saturates at $k \approx 20$" rests on two points | ✅ (→ "no difference between the two deepest depths we tested") |
| H19 | P2 | $B$ / $b$ alternate | ✅ |
| H20 | P2 | "$O(1)$ index-hit rate" names a percentage as a complexity class | ✅ |
| H21 | P2 | §9 claims Prop. 6's advantage grows with memory | ✅ (removed) |
| H22 | P2 | Data Availability after references | ✅ |
| H23 | P3 | Fig. 4 caption "Both axes start at zero" | ✅ (→ "Both $y$-axes") |

---

## I. Proof sanity summary — all 7

| # | Result | Review's verdict | What was done | Status |
|---|---|---|---|---|
| I1 | Lemma 8 (race) | plausible | Unchanged; now explicitly scoped to an unbounded i.i.d. process | ✅ |
| I2 | Thm 2 (soundness) | tautological or mismatched | Restated as **guard** soundness against $G_k$, resting only on refutation-soundness. No longer tautological: it says a blocked candidate is refuted by an input the oracle already holds | ✅ |
| I3 | Thm 3 ($K{+}1$) | idealized, budget unreconciled | Scoped to unbounded runs; finite-budget form stated separately; $K$ defined over positive-mass types | ✅ |
| I4 | Thm 4(a) | plausible, needs stopping conditions | Scoped to unbounded runs | ✅ |
| I5 | Thm 4(b) | incorrectly stated for no memory | Rebuilt on $E^{\rm obs}$; split generated vs paid | ✅ |
| I6 | Cor. 5 | applies more broadly; strictness vague | Restated for any sound guard; strictness given in budget-position terms | ✅ |
| I7 | Prop. 6 | not proved for the implemented guard | Replaced with a per-round position/cost statement; proof rewritten; no asymptotics | ✅ |

---

## J. Citation audit

| # | Point | What was done | Status |
|---|---|---|---|
| J1–J3 | All [1]–[23] resolve; no uncited entries; no unresolved markers | Still true after adding six references (now 30 entries; the IEEE build cites 24 with Future Work commented out) | ✅ |
| J4 | No `.bib` was provided to the reviewer | `refs.bib` ships in the draft tree and in the artifact | ✅ |
| J5 | [5], [6], [16] are arXiv; [10] forthcoming | Statuses preserved in the entries; the prose does not present them as archival | ✅ |
| J6 | [11] ExpeRepair metadata should be updated | Marked "FSE 2026, to appear" — needs final volume/pages/DOI when published | 🟡 |
| J7 | Direct quotations need page/section locators | Not added. Five external quotations (lin2026run, mu2026experepair, vo2026merit, solarlezama2006sketching, wu2024condefects) | ⬜ |
| J8 | Seven unsupported/overextended claims | All seven fixed: "field lacks" (deleted), "none of five lines" (table rebuilt), "first execution" (deleted), "licenses attributing to typing" (narrowed to the one responding column), "cannot hurt" (→ "cheap to add"), "compose multiplicatively" (→ open question), "every accepted patch truly correct" (→ pool adequacy + bounds) | ✅ |

---

## K. Required related-work additions — all 10

| # | Work | Status |
|---|---|---|
| K1 | Weimer, Fry & Forrest, AE, ASE'13 | ✅ cited, discussed as the closest equivalence mechanism, with its real result (3,252 → 186 test evaluations) |
| K2 | Mechtaev et al., test-equivalence, TOSEM'18 | ✅ cited; described correctly (one execution decides a class, via the modified expression's runtime value) |
| K3 | Qi, Mao & Lei, fault-recorded prioritization, ICSM'13 | ✅ cited; named as a planned baseline |
| K4 | Lou et al., APR meets regression testing, TOSEM'24 | ✅ cited with the real scale (2,532,915 patches, 12 systems) |
| K5 | Venugopal et al., modification-point aware, 2020 | ✅ cited; flagged as the closest mechanism-level analogue to our $\lambda$ index |
| K6 | Orvalho et al., LLM CEGIS | ✅ cited **as AAAI 2025**, and the "first" claim withdrawn |
| K7 | Lin et al., ISSTA'26 | ✅ kept; no longer extended beyond its studied setting |
| K8 | ExpeRepair | ✅ comparison deepened (index purpose, and it does validate) |
| K9 | Vo et al., MERIT | ✅ labelled concurrent; its own null quoted |
| K10 | ExpressAPR oversimplification corrected | ✅ now described as spanning dedup + prioritization + virtualization, not only acceleration |

---

## L. Meta-review — 5 probability-moving revisions

| # | Revision | Status |
|---|---|---|
| L1 | Satisfy 10+2 IEEE rules; place Data Availability correctly | 🟡 (format ✅, placement ✅, length ⬜) |
| L2 | Repair or remove the formal claims | ✅ |
| L3 | Add the missing literature and at least one closest baseline | ✅ literature / ⬜ baseline |
| L4 | Release an anonymized replayable artifact + pre-specification record | 🟡 |
| L5 | Report total guard+oracle test work; make task-level inference primary | ✅ |

---

## M. Revision checklist

### P0 — 9 items

| # | Item | Status |
|---|---|---|
| M-P0-1 | Convert to `[10pt,conference]{IEEEtran}`, no compsoc | ✅ |
| M-P0-2 | Reduce to 10 + 2 pages | ⬜ |
| M-P0-3 | Decide Research vs Agentic AI4SE and align framing | 🟡 (recommended AI4SE, 23 Oct; **your call**) |
| M-P0-4 | Separate semantic / full-pool / depth-$k$ acceptance | ✅ |
| M-P0-5 | Define the pre-execution index key | ✅ |
| M-P0-6 | Rewrite or withdraw Prop. 6 | ✅ |
| M-P0-7 | Correct Thm 4(b) | ✅ |
| M-P0-8 | Remove/qualify "first", "none", "field lacks", "provably wrong", "true repair", "cannot hurt", "multiplicatively" | ✅ |
| M-P0-9 | Add and contrast the six missing works | ✅ |

### P1 — 13 items

| # | Item | Status |
|---|---|---|
| M-P1-1 | Add prioritization / equivalence baselines, or justify | ⬜ (plan item 1) |
| M-P1-2 | Report all sandbox work: guard + oracle, counts and wall-clock | ✅ counts / 🟡 wall-clock table |
| M-P1-3 | Make task-level and clustered analyses primary | ✅ |
| M-P1-4 | Define confirmatory hypotheses and multiplicity families | ✅ |
| M-P1-5 | 526→99 attrition table with reasons + reference-runtime selection | ✅ |
| M-P1-6 | Explain 939/1,256 | ✅ |
| M-P1-7 | Explain the nine missing free-round cells; rerun matched | ✅ |
| M-P1-8 | "pre-registered" → "pre-specified"; document the correction chronology | ✅ |
| M-P1-9 | Anonymous artifact URL with code, prompts, logs, hashes, seeds | 🟡 |
| M-P1-10 | Add a different/larger model, or narrow every conclusion | 🟡 (narrowed ✅, model ⬜) |
| M-P1-11 | Binomial bounds for zero-failure results | ✅ |
| M-P1-12 | Test the difficulty × steering interaction | ⬜ |
| M-P1-13 | Move Data Availability after the Conclusion | ✅ |

### P2 — 10 items

| # | Item | Status |
|---|---|---|
| M-P2-1 | Replace "oracle call" with a precise term; define what guard replay includes | ✅ |
| M-P2-2 | Replace "true repair" with pool language | ✅ |
| M-P2-3 | Distinguish $\rho$ from $c$, and measure both if both are claimed | ✅ (relabelled; the paper now says $c$ is *not* directly measured) |
| M-P2-4 | Replace $(\pi,\{q_\tau\})_s$ with scalar base mass | ✅ |
| M-P2-5 | Define $K$ over reachable positive-mass types | ✅ |
| M-P2-6 | Separate variables for proposal / charged / guard / oracle budgets | ✅ |
| M-P2-7 | Rename "$O(1)$ index-hit rate" | ✅ |
| M-P2-8 | State cell counts by arm | ✅ |
| M-P2-9 | Replace "saturates at $k \approx 20$" | ✅ |
| M-P2-10 | Fix Fig. 4's caption | ✅ |

### P3 — 8 items

| # | Item | Status |
|---|---|---|
| M-P3-1 | Remove self-certifying phrases | ✅ |
| M-P3-2 | Reduce "not X but Y" constructions and slogan headings | 🟡 (headings de-sloganed; the construction still appears) |
| M-P3-3 | Make Table 1 a neutral capability matrix | ✅ |
| M-P3-4 | Page/section locators for quotations | ⬜ |
| M-P3-5 | Update [11] and other 2026 entries when published | 🟡 |
| M-P3-6 | Plain-text-safe CEGMEM spelling in abstract, metadata, bookmarks | 🟡 (metadata ✅ via `pdftitle`; body small-caps still splits under `pdftotext`) |
| M-P3-7 | Populate PDF title/subject/keywords; tagged PDF | ✅ metadata / ⬜ tagging |
| M-P3-8 | Shorten captions by moving interpretation into the text | 🟡 |

---

## What is left, grouped by what it needs

**Needs only your decision (2)**
1. Research Track (25 Sep) vs Agentic AI4SE (23 Oct) — M-P0-3
2. Whether to rename the title in `main.tex` to match the IEEE one — D-Q4

**Needs editing time, no experiments (7)**
3. The page cut, 21 → 10 + 2 — M-P0-2, C1, G2, A1
4. Quotation locators — J7 / M-P3-4
5. Caption shortening — G7 / M-P3-8
6. "not X but Y" density — M-P3-2
7. Wall-clock companion to the test-work table — M-P1-2
8. `\textsc` name extraction — G5 / M-P3-6
9. Tagged PDF (PDF/UA) — M-P3-7

**Needs the artifact to exist (4)** — B7, C3, C4, F9, H10, L4, M-P1-9
10. Mint the anonymous DOI, scrub identifiers, upload

**Needs experiments (4)** — the plan document costs each
11. Prioritization / equivalence baselines — D-Q2, F-A5, M-P1-1, L3 · *zero model calls*
12. A second model — F1, F-A4, M-P1-10 · *≈11 h*
13. Difficulty × steering interaction — B4, F6, F-A7, M-P1-12 · *half a day, no runs*
14. ExpeRepair metadata when published — J6, M-P3-5

**Where I disagree with the review (5)**
- B1 / F-Q1: guard replays were *already* in the execution metric — verified in `src/loop.py`
- D3: Orvalho et al. is AAAI 2025, not an arXiv preprint
- K1: the AE title carries a subtitle the review dropped
- K4: Lou et al.'s title uses an em dash, not a colon
- K5: Venugopal et al.'s title is longer than the review's version

The last four are metadata corrections I applied when adding the entries; the
first is a substantive disagreement, and the paper now proves it with a table
rather than asserting it.

---

## E2. Review 2, second pass — what the first pass missed

The first pass answered Review 2 point by point. A second, adversarial audit
against the source and the frozen log found that **two of the seven charges
were still live, and the revision itself had introduced errors**. This section
records what changed.

### The charge that was still live

**C1 in its sharpest form.** `src/oracle.py` draws the oracle's check set per
call — `_sample(cases, max_examples, seed)` with `seed = seed + round_index` —
so $G_k$ is not one set but a family indexed by the draw, and Theorem 1's
"$p \notin G_k$ **for every** $k$" was simply false: a depth-$k$ draw that
omits the witness can accept a patch the guard blocked.

| What changed | Where |
|---|---|
| $X_k$ defined explicitly as the per-call draw of $\min(k, \lvert\text{pool}\rvert)$ inputs; $G_k$ stated as a family indexed by it | §III-A |
| Theorem 1 restated: a block with witness $x$ proves $p \notin G_{\rm pool}$ (hence $\notin G^\star$) **unconditionally**, and $p \notin G_k$ for every check set containing $x$ | §IV |
| The condition under which the strong form holds by construction, measured: $X_k$ exhausts the pool for **97 of 99** faults at $k=100$ | §III-A, §VII-D |
| The residual hazard tested rather than assumed: the audit condition re-puts blocked rounds to the oracle — **0 acceptances in 4,411 rounds** — with the honest caveat that where $X_k$ exhausts the pool the audit only confirms what already holds, and below $k=100$ it was not run | §IV |
| Algorithm 1's `return p_t // sound: p_t ∈ G` — asserting the retracted claim with a symbol the revision deleted — replaced by "passed every $x \in X_k$" | `figures/algorithm.tex` |
| The intro's surviving "it cannot discard a patch the oracle would accept" | §I |

**C4 was answered where it was raised and violated one section later.** The
concession that a bucket-only guard is an unsound stopping rule is in §III-C.
But $\rho = 1$ gives only *within*-class generalization, and Theorems 3 and 4
silently need **class-exclusive** refutation — without it a live-type proposal
can be blocked by a foreign counterexample, consuming a round and eliminating
nothing, which destroys the $K{+}1$ *proposal* bound. Assumption 2 now states
exclusivity explicitly, and says plainly that our $\lambda$ index does not meet
it, so those two results are directional rather than calibrated.

### Errors the revision introduced or left standing

| # | Defect | Fix |
|---|---|---|
| 1 | Appendix claimed "Nothing moves" while its own table shows the guard-second reduction going **−39% → −18%** when one fault is dropped | Stated outright, in the appendix, §IX and §VII-C |
| 2 | Guard seconds reported only per cell, against the paper's own stated policy; the task-clustered interval **[−6.5, −0.5] s** existed and was omitted | Added; the magnitude is now explicitly called not well determined |
| 3 | §VII-C named `guard-only` and `untyped` in one order and gave their numbers in the other | Rewritten |
| 4 | "+93% wall-clock ($224/270$, $p{=}0.043$)" spliced a 297-cell ratio onto a 495-cell test | Both reported, each with its grid (+93% ablation, +81% main) |
| 5 | Depth sweep jumped from $k{=}100$ to $k{=}3$, omitting **7.6%** and **20.8%** false acceptance at $k{=}20$ and $k{=}8$ | All four depths reported, plus the disclosure that $k \le 20$ is exactly where Theorem 1's second clause is unaudited |
| 6 | "Fitting the closed forms" — the **memory-arm form could not be fit at all** (`r` is null) | Says so |
| 7 | "36.5% of guard decisions settled by the bucket" conflated hit rate with resolution; true value **35.0%**, and the random-partition control reaches the same rate | Corrected, and the 39% is attributed to deduplication, not the index |
| 8 | Sensitivity table printed $92/59$; the artifact says **$90/59$** | Corrected |
| 9 | Ablation table annotated 0.704 vs 0.707 as "equal" while §VI states falsifier F1 as strict equality | Both fixed; F1 now stated once, as *no block the oracle accepts* **and** no repair-rate difference |
| 10 | "Acceptance means agreement on all $k$ cases" — false for 97/99 faults | "every case in $X_k$" |
| 11 | "The arms coincide at $b=1$ by construction" — they differ on the timeout cell | "up to the timeout-edge cell" |
| 12 | `\good`, `\Oracle`, $\tau(x_t)$ in Algorithm 1; $b$ reused for a proposal budget; $O(1)$ residue in the held-out future-work section | All corrected |
| 13 | The submission build drops the supplement table, leaving the conclusion's "memories this small" with no visible number | Median-1 memory size now stated in §IV and §IX body text |

### What Review 2 asked, and where it is now answered

| Question | Answer in the paper |
|---|---|
| Which key is computed before any test is run? | $\lambda(p)$, the edit location, alone (§III-B, §V) |
| Is the fallback scan analyzed, implemented, or both? | Both, said in those words (§III-C) |
| What supports expected $O(1)$ guard cost? | Nothing — the claim is withdrawn everywhere (§IV, abstract, `prop:guardcost`) |
| Is "correct" semantic, full-pool, or depth-$k$? | All three named and each claim assigned (§III-A, §VII-D, §IX) |
| What eliminated set labels no-memory attempts redundant? | $E^{\rm obs}$, the observed-type history (`def:eobs`) |

---

## F. Review 3 — empirical design, statistics, reproducibility

Most of Review 3 was already answered by the first two passes. Three of its
requests were not, and acting on them changed two of the paper's claims.

### What the requested statistics actually said

**The difficulty-by-steering interaction (W6, request 7).** The reviewer asked
for the test instead of five band-wise $p$-values. Running it at the task unit,
permuting band labels over tasks (20,000 draws), gives a two-part answer:

| | dead | hard | medium | easy | too-easy | omnibus |
|---|---:|---:|---:|---:|---:|---:|
| typed − no memory | **+9.6 pp** [2.6, 18.3] | **−11.8 pp** [−28.2, 4.7] | −6.2 | 0.0 | 0.0 | *p* = **0.023** |
| steer-only − no memory | +5.8 [−4.4, 18.8] | +3.9 [−15.7, 21.6] | 0.0 | 0.0 | 0.0 | *p* = **0.87** |

So the full agent's effect **does** depend on difficulty — but as one band up
and one band **down**, not as a dead-band bonus. And isolating the prompt half
erases the pattern entirely. The paper's reading, "steering pays only in the
hardest band," is withdrawn from the abstract, the introduction, §VII-C and the
threats section.

**The free-guarded pooling (W7).** The reviewer asked us not to pool dependent
arms without a hierarchical or permutation analysis. The 111 matched pairs come
from only **19 tasks**; permuting the free/base label a whole task at a time
gives ***p* = 0.061**, not the 0.004 the cell-level sign test reported. Now
stated as suggestive and not confirmed. (The nine "missing" cells were never
missing a twin — every free cell has its base pair; the condition simply
completed 171 of a targeted 180.)

**Total sandbox seconds (request 2).** Executions were already split guard/
oracle; seconds were not summed. Per episode: **72.9 s → 38.2 s** (no memory →
typed), with guard-side 4.2 s of the 38.2. Both units now sit in
`tab:testwork`, and §VII-A says the reduction is not an artifact of the unit.

### The questions, answered

| Question | Answer |
|---|---|
| Do case-executions include guard replays? | Yes, and now in seconds too |
| Are seeds independent draws or cache identifiers? | **Independent draws.** `src/llm.py` sends no sampling seed to the backend; the seed enters only the cache key. Round 1 shares a prompt across a task's five seeds and returns **4.43 distinct patches of five** on average, never one. Pairing is memoization, not a shared RNG — now said in §VI-B |
| Guard correction before or after seeing outcomes? | Already answered: after an *earlier* run's accounting, before the reported run |
| Why 939 of 1,256 in the regression audit? | Already answered: 317 faults ship no case the buggy version passed |
| How often is $k{=}100$ the whole pool? | Already answered: 97 of 99 faults |

### Smaller fixes

- **Attrition (W2, request 3).** The stage counts are now in the body, not only
  in a supplement table the submission build drops: 526 → −64 reference
  disagrees, −48 too slow, −7 unresolved, −112 one fault per coding task, −196
  quota → 99.
- **Artifact (W9).** Data Availability now enumerates what ships: source, log,
  corpus artifacts, prompt templates, pinned model manifest, lockfile, response
  cache, six extractors.
- **"Truly correct" (W10).** `tools/make_figures.py` labelled a series *truly
  correct* and shaded $G_k \setminus G_{\rm pool}$ as *false acceptance*, which
  contradicts the paper's own definitions. Relabelled to pool adequacy.
- **Five orphan supplement tables.** `testwork`, `tasklevel`, `perband`,
  `integrity` and `freeguard` were `\input` by *no* section — they rendered in
  neither build. A regression from the page-cut pass; now wired into the
  preprint, which grows to 16 pages.

### Still open, and why

W1 (one model, one corpus) and the baseline comparisons need runs, not edits.
They are scoped in `PLAN-experiments.md`.
