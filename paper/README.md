# CEGMem — paper

One body, one directory per venue. Edit text in `common/` only; each venue
directory holds a driver, its PDF and the documents that concern that venue
alone.

```
common/      shared body: sections/, tables/, figures/, preamble*.tex, refs.bib,
             the provenance JSONs (numbers, figdata, addenda, corpus, related,
             review3, secondproposer), tools/ (the extractors), Makefile,
             PRESPEC-2026-09.md (pre-specification register), NARROWING-P1-1.md,
             main.tex (portable preprint)                     -> make
saner2027/   main-saner-2027.tex -> main-saner-2027.pdf  (IEEEtran, 10 + 2 pages)
fse2027/     main-fse-2027.tex   -> main-fse-2027.pdf    (acmart acmsmall, 18 + 4 pages)
main.pdf     the reference build of the preprint (scripts/reproduce.sh stage 7)
main_proposal.txt          the proposal the framing and theory follow
```

`common/README.md` documents the build, the provenance chain and the writing
conventions. Venue drivers reach the body through `\input@path{{../common/}}`.
