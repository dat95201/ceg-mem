# CEGMem — paper draft (English, LaTeX)

Draft of *Never Twice the Same Mistake: Counterexample-Guided Program Repair
with a Typed Memory of Refuted Attempts*.

The framing and the theory follow `paper/main_proposal.pdf`. **Every number in
the experiments comes from the real runs** in `runs/2026-09-01` on branch
`main` — none of the proposal's simulated figures survive in this draft.

## Build

```bash
make          # main.pdf          — portable preprint (23 pp, with appendix)
make ieee     # main-ieee.pdf     — SANER submission driver (21 pp today)
make acm      # main-acmart.pdf   — ACM sigconf driver (needs acmart)
make check    # undefined refs, wide boxes, leftover TODOs
make figures  # regenerate figures/*.pdf from numbers.json
make numbers  # regenerate numbers.json, figdata.json and addenda.json
```

### Three drivers, one body

| driver | class | appendix | today |
|---|---|---|---|
| `main.tex` | `article`, twocolumn | yes | 23 pp |
| `main-ieee.tex` | `[10pt,conference]{IEEEtran}` | no (`\suppmattertrue`) | **21 pp** |
| `main-acmart.tex` | `[sigconf,anonymous,review]{acmart}` | yes | needs acmart |

All three `\input` the identical `sections/`, `tables/`, `figures/` and
`refs.bib`. `main-ieee.tex` is the one SANER 2027 accepts: the CFP requires
`\documentclass[10pt,conference]{IEEEtran}` without `compsoc`/`compsocconf`,
caps the paper at **10 content pages + 2 reference-only pages**, and states
that violations "will be desk-rejected without review". At 21 pages the
submission build is over that limit; `sections/09-threats.tex` and the
per-section page map in the session notes are the starting point for the cut.

`\suppmattertrue` (set by the IEEE driver) routes every reference to appendix
content — proofs, the race lemma, the sensitivity table — to "the supplementary
material" instead of a dangling `\cref`. Set it in any driver that drops the
appendix.

Install the classes with `tlmgr install acmart` / `apt install
texlive-publishers` (IEEEtran ships in `texlive-publishers`).

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
main.tex               portable preprint driver
main-ieee.tex          SANER submission driver (IEEEtran, 10pt conference)
main-acmart.tex        ACM sigconf driver (anonymous, review)
preamble.tex           shared macros: notation, arm names, palette, \appref switch
preamble-cleveref.tex  shared \cref names (input after cleveref in every driver)
sections/00..12        abstract, body, appendix — one file per section
sections/11b-…         Data Availability, required directly after the Conclusion
tables/                one file per table; all values traceable to a JSON below
figures/               8 PDFs built by tools/make_figures.py, + algorithm.tex
numbers.json           THE authoritative numbers (see provenance below)
figdata.json           per-cell paired arrays the figures need
addenda.json           attrition, total test work, task-level tests, exact CIs
tools/                 the extractors; nothing else may produce a paper number
```

Figures are `\includegraphics` at natural size — 3.33 in for one column,
7.0 in for the three `figure*` spreads — so nothing is scaled and the figure
text lands at 8 pt against 9 pt captions. Their body font is Liberation Serif
(metric-compatible with the paper's Times) and their math is STIX, so the
figures and the text share a typeface. All fonts embed as CID TrueType; there
are no Type 3 fonts, which both ACM and IEEE preflight reject.

## Provenance of every number

```
runs/2026-09-01/  --extract_numbers.py-->  numbers.json  --> tables/*.tex
                  --fix_numbers.py------>  (2 corrections)   sections/*.tex
                  --figdata.py---------->  figdata.json  --> figures/*.pdf
                  --review_addenda.py-->  addenda.json  --> tables/attrition,
                                                            testwork, tasklevel
```

Four extractors, three JSONs, no other source. **If a number in the text disagrees
with `numbers.json`, the text is wrong.** Re-run the whole chain with
`make numbers RUN=../../runs/2026-09-01 && make figures`.

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
