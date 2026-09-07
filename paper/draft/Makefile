# ===========================================================================
# CEGMem paper draft
#
#   make            build the portable preprint  (main.pdf)
#   make acm        build the submission driver  (main-acmart.pdf; needs acmart)
#   make figures    regenerate figures/*.pdf from numbers.json + figdata.json
#   make numbers    regenerate numbers.json from a frozen run directory
#   make check      report anything the text asserts that the log cannot back
#   make check-policies   re-derive tab:policies from the P1-1 artifacts
#   make clean      remove build intermediates (keeps the PDFs)
#   make distclean  remove intermediates and PDFs
#
# The provenance chain is one-directional and every arrow is a make target:
#
#   runs/2026-09-01/  --extract_numbers.py-->  numbers.json  --+--> tables/*.tex
#                     --figdata.py--------->  figdata.json  --+--> figures/*.pdf
#
# No number in the paper has any other origin. If a number in the text
# disagrees with numbers.json, the text is wrong.
# ===========================================================================

LATEXMK  ?= latexmk
PYTHON   ?= python3

# Point this at the frozen run directory (the one on branch main).
RUN      ?= ../../runs/2026-09-01

MAIN     := main
ACM      := main-acmart
IEEE     := main-ieee

TEXDEPS  := preamble.tex refs.bib \
            $(wildcard sections/*.tex) \
            $(wildcard tables/*.tex) \
            figures/algorithm.tex \
            $(wildcard figures/*.pdf)

.PHONY: all acm ieee figures numbers check check-policies clean distclean watch

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex $(TEXDEPS)
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(MAIN).tex

ieee: $(IEEE).tex $(TEXDEPS)
	@kpsewhich IEEEtran.cls >/dev/null 2>&1 || { \
	  echo "IEEEtran.cls not found."; \
	  echo "  TeX Live:  tlmgr install IEEEtran"; \
	  echo "  Debian:    apt install texlive-publishers"; exit 1; }
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(IEEE).tex
	@echo "--- page budget: SANER allows 10 content + 2 reference-only ---"
	@pdfinfo $(IEEE).pdf | grep Pages

acm: $(ACM).tex $(TEXDEPS)
	@kpsewhich acmart.cls >/dev/null 2>&1 || { \
	  echo "acmart.cls not found."; \
	  echo "  TeX Live:  tlmgr install acmart"; \
	  echo "  Debian:    apt install texlive-publishers"; \
	  echo "  or build the preprint instead:  make"; exit 1; }
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(ACM).tex

# Rebuild continuously while editing (portable driver only).
watch:
	$(LATEXMK) -pdf -pvc -interaction=nonstopmode $(MAIN).tex

figures: numbers.json figdata.json
	$(PYTHON) tools/make_figures.py .

# Full regeneration from the frozen log. Order matters: fix_numbers.py
# corrects two blocks that extract_numbers.py computes over the wrong
# universe, and figdata.py exports the per-cell arrays numbers.json omits.
numbers:
	$(PYTHON) tools/extract_numbers.py $(RUN) numbers.json
	$(PYTHON) tools/fix_numbers.py     $(RUN) numbers.json
	$(PYTHON) tools/figdata.py         $(RUN) figdata.json
	$(PYTHON) tools/review_addenda.py  $(RUN) addenda.json

# The policy replay's numbers do not come through extract_numbers.py, so they
# get their own verifier: it re-derives every figure in tab:policies and
# sec:results-policies from the shipped artifacts and exits non-zero on any
# disagreement. Runs with the standard library alone.
POLICYDATA ?= ../../data/official-2026-09-01

check-policies:
	$(PYTHON) ../../scripts/verify_policies.py $(POLICYDATA)

check: check-policies
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
	$(LATEXMK) -c $(ACM).tex  2>/dev/null || true
	$(LATEXMK) -c $(IEEE).tex 2>/dev/null || true
	rm -f *.bbl *.blg *.brf *.aux *.out *.log *.fls *.fdb_latexmk *.synctex.gz

distclean: clean
	rm -f $(MAIN).pdf $(ACM).pdf $(IEEE).pdf
