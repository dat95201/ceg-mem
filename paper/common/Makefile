# ===========================================================================
# CEGMem paper - shared body (paper/common/)
#
#   make            build the portable preprint  (main.pdf, in this directory)
#   make saner      build ../saner2027/main-saner-2027.pdf  (IEEEtran, 10+2)
#   make fse        build ../fse2027/main-fse-2027.pdf      (acmart acmsmall, 18+4)
#   make figures    regenerate figures/*.pdf from numbers.json + figdata.json
#   make numbers    regenerate EVERY provenance JSON in place from the run directories
#   make regen NUMBERS_OUT=dir   the same, into another directory (what
#                   scripts/reproduce.sh does before it compares)
#   make check-numbers    regenerate into .regen/ and compare with the committed
#                   JSONs (tools/check_numbers.py); non-zero on any difference
#   make check      check-policies + check-numbers + LaTeX log audit
#   make check-policies   re-derive tab:policies from the P1-1 artifacts
#   make clean      remove build intermediates (keeps the PDFs)
#   make distclean  remove intermediates and PDFs
#
# The provenance chain is one-directional and every arrow is a make target:
#
#   data/official-2026-09-01/ --extract_numbers.py--> numbers.json   --+--> tables/*.tex
#                             --fix_numbers.py------> (2 corrections)  |    sections/*.tex
#                             --figdata.py----------> figdata.json    --+--> figures/*.pdf
#                             --review_addenda.py---> addenda.json
#                             --corpus_addenda.py---> corpus.json
#                             --related_addenda.py--> related.json
#                             --review3_addenda.py--> review3.json
#   + data/gpto4mini-2026-09-06/ --second_proposer.py--> secondproposer.json
#
# No number in the paper has any other origin. If a number in the text
# disagrees with numbers.json, the text is wrong. scripts/reproduce.sh
# regenerates all eight and fails when one differs from the committed copy.
# ===========================================================================

LATEXMK  ?= latexmk
PYTHON   ?= python3

# pdfTeX stamps CreationDate/ModDate into the PDF and derives its trailer /ID
# from them. Pinning SOURCE_DATE_EPOCH (2026-09-01T00:00Z, the reported run's
# date) makes a rebuild byte-identical, so a reproduction leaves paper/main.pdf
# unchanged in git. FORCE_SOURCE_DATE makes \today follow the same clock.
export SOURCE_DATE_EPOCH ?= 1788220800
export FORCE_SOURCE_DATE ?= 1

# The two run directories the paper is extracted from: the local proposer
# (qwen2.5-coder:7b) and the second proposer (gpt-4o-mini). Installed from the
# artifacts by scripts/fetch_artifacts.py, or produced by scripts/reproduce.sh.
RUN       ?= ../../data/official-2026-09-01
CLOUD_RUN ?= ../../data/gpto4mini-2026-09-06
# Where `make regen` writes. `.` (the default) is `make numbers`: in place.
NUMBERS_OUT ?= .

MAIN     := main

TEXDEPS  := preamble.tex refs.bib \
            $(wildcard sections/*.tex) \
            $(wildcard tables/*.tex) \
            figures/algorithm.tex \
            $(wildcard figures/*.pdf)

.PHONY: all saner fse figures numbers regen check check-numbers check-policies clean distclean watch

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex $(TEXDEPS)
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(MAIN).tex

# Venue builds live in their own directories and \input this body.
saner:
	$(MAKE) -C ../saner2027

fse:
	$(MAKE) -C ../fse2027

# Rebuild continuously while editing (portable driver only).
watch:
	$(LATEXMK) -pdf -pvc -interaction=nonstopmode $(MAIN).tex

figures: numbers.json figdata.json
	$(PYTHON) tools/make_figures.py .

# Full regeneration from the frozen logs. Order matters: fix_numbers.py
# corrects two blocks that extract_numbers.py computes over the wrong
# universe, and figdata.py exports the per-cell arrays numbers.json omits.
# `regen` writes into $(NUMBERS_OUT); `numbers` is `regen` in place.
regen:
	mkdir -p $(NUMBERS_OUT)
	$(PYTHON) tools/extract_numbers.py $(RUN) $(NUMBERS_OUT)/numbers.json
	$(PYTHON) tools/fix_numbers.py     $(RUN) $(NUMBERS_OUT)/numbers.json
	$(PYTHON) tools/figdata.py         $(RUN) $(NUMBERS_OUT)/figdata.json
	$(PYTHON) tools/review_addenda.py  $(RUN) $(NUMBERS_OUT)/addenda.json
	$(PYTHON) tools/corpus_addenda.py  $(RUN) $(NUMBERS_OUT)/corpus.json
	$(PYTHON) tools/related_addenda.py $(RUN) $(NUMBERS_OUT)/related.json
	$(PYTHON) tools/review3_addenda.py $(RUN) $(NUMBERS_OUT)/review3.json
	$(PYTHON) tools/second_proposer.py --local-run $(RUN) --cloud-run $(CLOUD_RUN) --out $(NUMBERS_OUT)/secondproposer.json

numbers:
	$(MAKE) regen NUMBERS_OUT=.

# Regenerate beside the committed files and compare (provenance paths ignored,
# NaN == NaN, rel_tol 1e-6). This is what scripts/reproduce.sh stage 6 does.
check-numbers:
	rm -rf .regen
	$(MAKE) regen NUMBERS_OUT=.regen
	$(PYTHON) tools/check_numbers.py --regenerated .regen --committed .

# The policy replay's numbers do not come through extract_numbers.py, so they
# get their own verifier: it re-derives every figure in tab:policies and
# sec:results-policies from the shipped artifacts and exits non-zero on any
# disagreement. Runs with the standard library alone.
POLICYDATA ?= ../../data/official-2026-09-01

check-policies:
	$(PYTHON) ../../scripts/verify_policies.py $(POLICYDATA)

check: check-policies check-numbers
	@echo "== undefined references / citations =="
	@grep -h "LaTeX Warning: \(Reference\|Citation\)" $(MAIN).log 2>/dev/null \
	  | sort -u || echo "  (no log yet: run make first)"
	@echo "== overfull boxes wider than 5pt =="
	@grep -h "Overfull .hbox ([5-9][0-9.]*pt\|Overfull .hbox ([0-9]\{3\}" \
	  $(MAIN).log 2>/dev/null | sort -u || true
	@echo "== TODO / FIXME left in the text =="
	@grep -rn "TODO\|FIXME\|XXX" sections tables *.tex || echo "  (none)"

clean:
	$(LATEXMK) -c $(MAIN).tex 2>/dev/null || true
	rm -f *.bbl *.blg *.brf *.aux *.out *.log *.fls *.fdb_latexmk *.synctex.gz
	rm -rf .regen

distclean: clean
	rm -f $(MAIN).pdf
