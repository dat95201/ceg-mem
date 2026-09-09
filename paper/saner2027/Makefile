# ===========================================================================
# CEGMem paper - SANER 2027 submission build (paper/saner2027/)
#
#   make          build main-saner-2027.pdf
#   make check    page budget + undefined refs + overfull boxes
#   make clean    remove intermediates (keeps the PDF)
#
# The body is shared: this driver \inputs ../common/{sections,tables,figures,
# preamble*.tex,refs.bib}. Edit the text there, never here.
# SANER 2027: \documentclass[10pt,conference]{IEEEtran}, no compsoc;
# 10 content pages + 2 reference-only pages; violations desk-rejected.
# ===========================================================================
LATEXMK ?= latexmk
# Deterministic PDF bytes (see paper/common/Makefile).
export SOURCE_DATE_EPOCH ?= 1788220800
export FORCE_SOURCE_DATE ?= 1
DRIVER  := main-saner-2027
COMMON  := ../common
DEPS    := $(DRIVER).tex $(COMMON)/preamble.tex $(COMMON)/preamble-cleveref.tex \
           $(COMMON)/refs.bib $(wildcard $(COMMON)/sections/*.tex) \
           $(wildcard $(COMMON)/tables/*.tex) $(wildcard $(COMMON)/figures/*)

.PHONY: all check clean distclean

all: $(DRIVER).pdf

$(DRIVER).pdf: $(DEPS)
	@kpsewhich IEEEtran.cls >/dev/null 2>&1 || { \
	  echo "IEEEtran.cls not found (tlmgr install IEEEtran / apt install texlive-publishers)"; exit 1; }
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(DRIVER).tex

check: $(DRIVER).pdf
	@echo "--- SANER 2027 budget: 10 content pages + 2 reference-only pages ---"
	@pdfinfo $(DRIVER).pdf | grep Pages
	@echo "--- REFERENCES heading is on page:"
	@for p in $$(seq 1 $$(pdfinfo $(DRIVER).pdf | awk '/Pages/{print $$2}')); do \
	  pdftotext -f $$p -l $$p -layout $(DRIVER).pdf - | grep -q 'R EFERENCES\|REFERENCES' && echo "    $$p" && break; done
	@echo "--- undefined references / citations ---"
	@grep -h "LaTeX Warning: \(Reference\|Citation\)" $(DRIVER).log | sort -u | grep . || echo "    (none)"
	@echo "--- overfull boxes wider than 5pt ---"
	@grep -h "Overfull .hbox ([5-9][0-9.]*pt\|Overfull .hbox ([0-9]\{3\}" $(DRIVER).log | sort -u | grep . || echo "    (none)"

clean:
	$(LATEXMK) -c $(DRIVER).tex 2>/dev/null || true
	rm -f *.bbl *.blg *.brf *.aux *.out *.log *.fls *.fdb_latexmk *.synctex.gz

distclean: clean
	rm -f $(DRIVER).pdf
