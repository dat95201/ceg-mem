# CEGMem — paper draft (English, LaTeX)

Draft of *Never Twice the Same Mistake: Counterexample-Guided Program Repair
with a Typed Memory of Refuted Attempts*.

The framing and the theory follow `paper/main_proposal.txt`. **Every number in
the experiments comes from the real runs**: the local proposer's
`data/official-2026-09-01` and the second proposer's `data/gpto4mini-2026-09-06`
(installed from the artifacts, see `RUNBOOK.md`) — none of the proposal's
simulated figures survive in this draft. `scripts/reproduce.sh` regenerates
every JSON below from those two directories and fails if one differs.

## Layout: one body, one directory per venue

```
paper/
  common/      THIS directory: the shared body - sections/, tables/, figures/,
               preamble*.tex, refs.bib, the JSON provenance chain, tools/,
               the pre-specification register, and main.tex (portable preprint)
  saner2027/   SANER 2027 driver  main-saner-2027.tex -> main-saner-2027.pdf
               (IEEEtran 10pt conference; 10 content + 2 reference-only pages)
  fse2027/     FSE 2027 driver    main-fse-2027.tex   -> main-fse-2027.pdf
               (acmart acmsmall, single column, review+anonymous; 18 content
               + 4 reference pages)
```

The venue drivers reach this directory through `\input@path{{../common/}}`
and `\graphicspath{{../common/}}`, so every body file keeps its plain
`sections/...`, `tables/...`, `figures/...` path and the three builds can never
drift. **Edit text only here.** Venue-specific choices - page budget, what is
held out of a build, `\suppmatter` - live in the driver.

## Build

```bash
make          # main.pdf                    - portable preprint, with appendix
make saner    # ../saner2027/main-saner-2027.pdf   (or: make -C ../saner2027)
make fse      # ../fse2027/main-fse-2027.pdf       (or: make -C ../fse2027)
make check    # check-policies + check-numbers + undefined refs, wide boxes, leftover TODOs
make figures  # regenerate figures/*.pdf from numbers.json + figdata.json
make numbers  # regenerate every provenance JSON in place from RUN and CLOUD_RUN
make check-numbers   # regenerate into .regen/ and compare with the committed JSONs
```

Each venue directory has its own `make check` that prints the page on which
the references start against that venue's budget.

### Three drivers, one body

| driver | class | appendix | today |
|---|---|---|---|
| `common/main.tex` | `article`, twocolumn | yes | preprint |
| `saner2027/main-saner-2027.tex` | `[10pt,conference]{IEEEtran}` | no (`\suppmattertrue`) | 13 pp: body ends on p. 12 (~11.5 content pages against 10) |
| `fse2027/main-fse-2027.tex` | `[acmsmall,screen,review,anonymous]{acmart}` | no (`\suppmattertrue`) | 20 pp: body ends on p. 18 at ~40 % (~17.3 content pages against 18) |

SANER 2027 requires `\documentclass[10pt,conference]{IEEEtran}` without
`compsoc`/`compsocconf`, caps the paper at **10 content pages + 2
reference-only pages**, and states that violations "will be desk-rejected
without review". FSE 2027 requires
`\documentclass[acmsmall,screen,review,anonymous]{acmart}` (single column),
**18 pages for all text and figures + 4 pages of references** at submission
(20 + 4 for a major revision), and a **Data Availability** section after the
Conclusion that is not counted; the appendix counts, and with it and the
extended tables inline the FSE build runs to ~27 content pages, which is why
that driver also sets `\suppmattertrue`.

`\suppmattertrue` routes every reference to appendix content - proofs, the
race lemma, the sensitivity table, the extended tables - to "the supplementary
material" instead of a dangling `\cref`. Set it in any driver that drops the
appendix.

In single column, three tables set for a 7in two-column spread (Table I, Table
II, the primary-band table) are wider than acmsmall's 13.9cm text block; the
FSE driver scales any over-wide `tabular` down to the text width
(`adjustbox`, `max width=\textwidth`) until they are re-set for that layout.

Install the classes with `tlmgr install acmart IEEEtran` (`apt install
texlive-publishers`); acmart also needs the `libertine`, `newtx` and
`inconsolata` fonts (`apt install texlive-fonts-extra texlive-plain-generic`).

### Two build notes

* The portable driver picks its typewriter and sans fonts at build time
  (`\IfFileExists{lmodern.sty}`). This matters: with T1 encoding and neither
  `lmodern` nor `cm-super` installed, `\texttt{}` resolves to a *bitmap* font
  and `microtype`'s font expansion then aborts the build outright. If you hit
  `auto expansion is only possible with scalable fonts`, install `lmodern`.
* `main.log` contains ~19 lines of `Overfull \hbox (4.5pt too wide) detected
  at line N` inside the `siunitx` `S` columns. Those are the bold numbers in
  `tables/main.tex` and `tables/ablation.tex` exceeding the width `siunitx`
  reserved for a non-bold digit string. No table overflows its column — the
  boxes are internal. Everything else in the log is clean.

## Layout

```
main.tex               portable preprint driver (the venue drivers are in
                       ../saner2027/ and ../fse2027/)
preamble.tex           shared macros: notation, arm names, palette, \appref switch
preamble-cleveref.tex  shared \cref names (input after cleveref in every driver)
sections/00..12        abstract, body, appendix — one file per section
sections/11b-…         Data Availability, required directly after the Conclusion (SANER and FSE)
tables/                one file per table; all values traceable to a JSON below
figures/               8 PDFs built by tools/make_figures.py, + algorithm.tex
numbers.json           THE authoritative numbers (see provenance below)
figdata.json           per-cell paired arrays the figures need
addenda.json           attrition, total test work, task-level tests, exact CIs
corpus.json            the recomputed migration matrix and corpus expense (errata below)
related.json           the positioning-table numbers
review3.json           Review 3's recomputations
secondproposer.json    the second proposer (gpt-4o-mini): the right block of Table II,
                       RQ6, the primary-band supplement table and the second-proposer
                       sentences of the abstract, introduction and conclusion
tools/                 the extractors; nothing else may produce a paper number.
                       tools/second_proposer.py writes secondproposer.json from both
                       run directories; tools/check_numbers.py compares a regenerated
                       set of JSONs with the committed ones (what `make check-numbers`
                       and scripts/reproduce.sh stage 6 run)
```

Figures are `\includegraphics` at natural size — 3.33 in for one column,
7.0 in for the three `figure*` spreads — so nothing is scaled and the figure
text lands at 8 pt against 9 pt captions. Their body font is Liberation Serif
(metric-compatible with the paper's Times) and their math is STIX, so the
figures and the text share a typeface. All fonts embed as CID TrueType; there
are no Type 3 fonts, which both ACM and IEEE preflight reject.

## Provenance of every number

```
data/official-2026-09-01/  --extract_numbers.py-->  numbers.json  --> tables/*.tex
                           --fix_numbers.py------>  (2 corrections)   sections/*.tex
                           --figdata.py---------->  figdata.json  --> figures/*.pdf
                           --review_addenda.py-->  addenda.json  --> tables/attrition,
                                                                     testwork, tasklevel
                           --corpus_addenda.py-->  corpus.json
                           --related_addenda.py->  related.json  --> tables/related
                           --review3_addenda.py->  review3.json
+ data/gpto4mini-2026-09-06/ --second_proposer.py--> secondproposer.json --> Table II (right block),
                                                                            RQ6, primaryband,
                                                                            abstract/intro/conclusion
                              --check_numbers.py---> regenerated vs committed (exit 1 on a difference)
```

Eight extractors, seven JSONs, no other source. **If a number in the text
disagrees with `numbers.json`, the text is wrong.** Re-run the whole chain with
`make numbers && make figures` (the run directories default to
`../../data/official-2026-09-01` and `../../data/gpto4mini-2026-09-06`), or let
`bash scripts/reproduce.sh` do it and compare the result with the committed
files (`make check-numbers` is the same comparison by hand).

`fix_numbers.py` applies two corrections that must not be lost:

1. The E5 bucket-hit rate at coherence `c = 1.0` is recomputed over the
   **sweep** universe (30 tasks × seeds 1–3), because the `c < 1` points come
   from that sweep and the main grid is a different universe. Mixing them on
   one axis manufactured a monotone dose-response that does not exist — with
   the universes aligned, index-hit rate is flat in `c` (33.1–35.3 %) and the
   random control is indistinguishable. §7.2 and Fig. 4 report the corrected,
   narrower result.
2. The sensitivity analysis excluding the timeout-edge fault
   (`abc285_e/48880084`), which §9 promises and `app:sensitivity` tabulates.

## Writing conventions this draft follows

Aimed at SANER / FSE / ISSTA / ASE review, so:

* **The opener is the ISSTA'26 measurement**, not a generic APR motivation:
  agents average 8.8 test executions per SWE-bench task and "apply execution
  indiscriminately". The paper's question is *how to decide which validations
  to skip when the generator is a language model* — classical APR already
  answers the unqualified version of that question, and §II-A says so.
* **A positioning table** (`tables/positioning.tex`, Table I) is a neutral
  capability matrix: per line of work, what record of past failures it keeps,
  whether it ever skips a validation, and what it presumes about the candidate
  space. Two classical lines answer "skips a validation" yes.
* **The primary metric is pre-specified** and is repair rate at a matched
  *oracle budget* — not total oracle calls, which any arm without a guard
  loses by construction.
* **The task is the inferential unit for every headline claim**
  (`tables/tasklevel.tex`): five seeds of one task share a fault, a model and a
  cache, so the per-cell test is anti-conservative. Both units are reported
  with a task-clustered bootstrap interval and the weaker one carries the claim.
* **Guard replays are billed as executions** (`tables/testwork.tex`). "Skips a
  validation" is never "avoids execution": the guard runs a stored
  counterexample, and total program executions still fall ×2.2.
* **Negative results are reported as results.** Prompt-side steering is null
  on every outcome metric and costs +128 % prompt tokens; the recommendation
  the paper lands on (deploy the guard, drop the prompt blocks) is not the one
  it set out to make. Each RQ ends in a `finding` box that carries the caveat
  next to the claim.
* **Falsifiers are named in advance** (F1–F3, `tables/integrity.tex`) together
  with the pairing checks, so "we pre-registered X" is auditable rather than
  asserted.
* Per-cell and per-task tests are both reported when they differ in strength,
  sweeps are Benjamini–Hochberg adjusted, and modest-power nulls are labelled
  suggestive rather than pooled into a significant-looking aggregate.

## Future Work is commented out

`\input{sections/10-future}` is commented in all three drivers; the file and
`tables/futurearms.tex` are intact. The section is self-contained (it owns the
only `\input` of `tables/futurearms`, and nothing outside it `\cref`s
`sec:future`, `sec:future-arms` or `tab:futurearms`), so uncommenting that one
line brings it back. `sections/09-threats.tex` carries a short
"Baselines we did not run" paragraph so the omission is still disclosed.

## Before submitting to SANER 2027

Deadlines (all AoE): Research Track abstract **21 Sep 2026**, paper **25 Sep
2026**. Agentic AI4SE Track abstract **19 Oct 2026**, paper **23 Oct 2026** —
same 10+2 limit, same template, and a closer topical fit.

- [ ] **Cut to 10 content pages.** `make ieee` reports the count. Today: 21.
      Page map of the current build — intro 1–2, related 3, architecture 4–5,
      theory 6–7, implementation 8, design 9–10, results 11–17, discussion 18,
      threats 18–19, conclusion + data availability 20, references 20–21.
- [ ] Move proofs, the sensitivity table and the overflow tables into the
      supplementary archive (the driver already reroutes their references).
- [ ] Mint the anonymous DOI or attach the archive as EasyChair additional
      material, and fill the placeholder in `sections/11b-availability.tex`.
- [ ] Audit the archive for identifiers: cache keys, absolute paths, usernames,
      git history, repository ownership.
- [ ] `refs.bib` header lists six entries whose obvious citation would be
      wrong (SKETCH never says "CEGIS"; Agentless's published title drops the
      prefix; ConDefects is FSE Companion and says "Complementary"; Reflexion's
      camera-ready has five authors; ExpressAPR is the TSE paper; Law & Kelton
      only through the 3rd edition). Keep those corrections if you re-import
      the entries from a search engine.
- [ ] `sec:availability` promises the frozen log and the extractor. Point it at
      the real artifact DOI once minted.

## Errata in the frozen run artifacts

Found while answering Review 3's attrition questions. Neither touches a
measured outcome; both are records *about* the corpus.

1. **`strata.json` of the frozen run — `n_moved: 0` is false.** The file also
   ships a diagonal `migration_selected_to_reported`. Recomputing from the
   file's own `screen_pi_hat` / `reported_pi_hat` fields gives **42 of 99**
   tasks in a different band than they were selected into (hard 28 → 17,
   draining into dead 16 → 23). The cause is resolution: the selection screen
   ran 40 calls per task, quantising pi-hat to 0.025, while the `hard` band
   spans [0.02, 0.08) — three attainable values, so one success in forty
   separates hard from dead.

2. **`screening.json` of the frozen run — `candidates` is an empty list**, under
   a note promising "every candidate in the pool and what became of it …
   including everything excluded". Screening was sequential: candidates were
   measured at 40 calls each and admitted until each band's quota filled, and
   the ones screened-then-discarded were not recorded. Consequently the 196
   pool members outside the corpus were never measured, **no design weights
   exist**, and post-stratification to the 295-member pool is not computable
   from the shipped artifacts.

The frozen files are left as they are, so the audit trail stays intact.
`tools/corpus_addenda.py` recomputes the correct migration matrix, the oracle
expense distribution among the kept faults, and the cost gradient, and writes
`corpus.json`. The paper cites the recomputed values and says in §VI-A that the
corpus is a quota sample rather than a probability sample.
