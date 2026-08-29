# Is this competitive for FSE 2027?

**No — and the reason is not the empirics.**

Full papers are due 2 Oct 2026. Today is 29 Aug. Five weeks.

---

## The blocking fact

`paper/main_proposal.txt` is not a draft of this study. It is the **synthetic**
proposal: 1,183 lines about "480 synthetic repair tasks", abstract claiming
"memory cuts verification rounds 2.6x" and "typed memory attains exactly zero
redundant attempts", §9 stating outright *"the results are synthetic... No model
is..."*, §10 promising `gen_synthetic.py` and `data/schema.md` that do not exist.
The header targets **FSE 2026, Montreal** — a conference that has already
happened.

**Prose written about the real ConDefects study: zero words.** Everything in
`docs/` is working notes.

Nothing else on this list matters until that changes.

---

## The dimensions

| | Verdict | Evidence |
|---|---|---|
| **Novelty** | weak | The claimed differentiator vs ChatRepair/Reflexion is the *type index*. But every outcome effect in the grid comes from the prompt exclusion block (DIAGNOSIS SS1), and prompt-level "don't repeat this" is what Reflexion already does. What is actually new is the cost-unit correction and the cross-bucket miss price — a **measurement** contribution. |
| **Claims** | borderline | 4 of 6 hold after the fixes, but the ones that hold are an equivalence, a counting exercise, and a redundancy result that is largely a measurement repair. The two strongest assets are not theorem-driven: the difficulty gradient (1.53 / 1.22 / 1.02) and the oracle-strength dose-response on overfitting (0.476 -> 0.000 over 1,368 audits). |
| **Design** | near-fatal | One 7B model, one benchmark, one language, ~49-LOC AtCoder programs. theta is edit-location x exception-class — exactly the taxonomy competitive-programming code flatters. A negative claim about typed memory *in general* will not survive this. The cloud backend is already wired (`scripts/eval_shard.sh`) and PLAN quotes ~$3 for a second proposer on 30 tasks. It was never spent. |
| **Statistics** | above median, four gaps | `scripts/analyze.py` does paired cluster bootstrap, Wilcoxon with `n_effective` after ties, A12, BH step-up, strata off the freeze. Missing: **no TOST anywhere** (zero grep hits) while the headline result is an equivalence; success@B gets Wilcoxon on per-task means instead of Fisher exact + OR; cost-to-repair conditions on success (`_ACCEPTED_ONLY`) with no co-primary for the hard band; no power analysis for any n. |
| **Threats** | unwritten | Contamination (ConDefects Oct 2021–Jun 2024 sits wholly inside the model's window; DESIGN.md waves it off, no probe exists). Guard nondeterminism from a wall-clock sandbox timeout — the artifact does not reproduce bit-for-bit. Prompt-length confound: typed prompts are 1.69x longer and no token-matched placebo ran. NCDR has no prior art. |
| **Artifact** | not close | No `LICENSE`, `Dockerfile`, `CITATION.cff`, `INSTALL.md`, or the `data/schema.md` the paper promises. `tests/` holds **one** file. `figures/make_figures.py` predates every real result — **no figure in the repo plots the actual data**. The benchmark needs a 6.4 GB `Test.zip` that cannot be redistributed: a hard blocker for Reusable. Genuinely good: `check_consistency.py`, the content-addressed cache, `pipeline.sh`'s stage guard. |

---

## Five weeks, in order

1. **Start writing today.** New draft on the DIAGNOSIS SS8 framing — falsification
   with a mechanism, in the shape of *To Run or Not to Run* (ISSTA 2026). Full
   draft by 15 Sept. No experiment may delay this.
2. **A second proposer.** gpt-4o-mini, 30 tasks, ~$3, code already wired. This
   removes the single most likely desk-reject line for the price of a coffee.
3. ~~E6-transcript~~ — **removed unrun** (DIAGNOSIS SS6f). The claim it existed
   for is falsified by the typed arm's own prompt growth (80.7 tok/round against
   untyped's 3.5), and it tested no surviving theorem. The related-work paragraph
   cites the literature instead. If a reviewer insists, the context-growth half
   is reconstructible from the logs with no model calls.
4. **E9-freeguard.** Written and tested. Without it Cor. 4.4 reads as "we ran an
   experiment that could not answer our question", and Def. 3.1 has nowhere to
   land either.
5. **Contamination probe (~2 h) and the token-matched placebo (~1 day).** The
   first de-risks everything; the second converts "steering worked, the guard
   didn't" into a mechanism.

**E8-corpus** (no model calls, ~2.3 h on 6 shards) rides along with any of these
and makes the typed arm's type-keyed metrics valid beyond 30 tasks.

## Cut

The synthetic study entirely. The 2.6x. The "better repair agent" framing. E5's
c-sweep as a standalone result. NCDR as a headline. Keep the theory to
Thm 4.3(a)/(b) and Prop. 4.5.

## Consider cutting the venue

What survives is a negative result with a measurement contribution, on one 7B
model and 106 small faults. ICSME Replication and Negative Results, ISSRE RENE,
or EASE Reproducibility and Negative Results are the honest homes for it — and
their deadlines do not require a paper to materialise from nothing in five weeks.
Submitting a first-ever draft to an FSE full-paper deadline five weeks out, with
no baseline and one model, is the low-probability play.
