# ===========================================================================
# CEGMem paper - FSE 2027 submission build (paper/fse2027/)
#
#   make          build main-fse-2027.pdf
#   make check    page budget + undefined refs + overfull boxes
#   make clean    remove intermediates (keeps the PDF)
#
# The body is shared: this driver \inputs ../common/{sections,tables,figures,
# preamble*.tex,refs.bib}. Edit the text there, never here.
# FSE 2027 Research Papers: \documentclass[acmsmall,screen,review,anonymous]
# {acmart}, single column; 18 pages for all text and figures + 4 pages of
# references at submission (20 + 4 for a major revision); a "Data
# Availability" section after the Conclusion, not counted. Needs acmart with
# the libertine, newtx and inconsolata fonts (tlmgr install acmart libertine
# newtx inconsolata; Debian: texlive-publishers texlive-fonts-extra
# texlive-plain-generic).
# ===========================================================================
LATEXMK ?= latexmk
# Deterministic PDF bytes (see paper/common/Makefile).
export SOURCE_DATE_EPOCH ?= 1788220800
export FORCE_SOURCE_DATE ?= 1
DRIVER  := main-fse-2027
COMMON  := ../common
DEPS    := $(DRIVER).tex $(COMMON)/preamble.tex $(COMMON)/preamble-cleveref.tex \
           $(COMMON)/refs.bib $(wildcard $(COMMON)/sections/*.tex) \
           $(wildcard $(COMMON)/tables/*.tex) $(wildcard $(COMMON)/figures/*)

.PHONY: all check clean distclean

all: $(DRIVER).pdf

$(DRIVER).pdf: $(DEPS)
	@kpsewhich acmart.cls >/dev/null 2>&1 || { \
	  echo "acmart.cls not found (tlmgr install acmart / apt install texlive-publishers)"; exit 1; }
	@kpsewhich libertine.sty newtxmath.sty zi4.sty >/dev/null 2>&1 || { \
	  echo "acmart fonts missing (tlmgr install libertine newtx inconsolata / apt install texlive-fonts-extra)"; exit 1; }
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(DRIVER).tex

check: $(DRIVER).pdf
	@echo "--- FSE 2027 budget: 18 content pages (+4 references; Data Availability not counted) ---"
	@pdfinfo $(DRIVER).pdf | grep Pages
	@echo "--- 'Data Availability' / 'References' headings are on pages:"
	@for p in $$(seq 1 $$(pdfinfo $(DRIVER).pdf | awk '/Pages/{print $$2}')); do \
	  t=$$(pdftotext -f $$p -l $$p -layout $(DRIVER).pdf -); \
	  echo "$$t" | grep -qi 'DATA AVAILABILITY' && echo "    Data Availability: $$p"; \
	  echo "$$t" | grep -q 'REFERENCES' && echo "    References: $$p" && break; done
	@echo "--- undefined references / citations ---"
	@grep -h "LaTeX Warning: \(Reference\|Citation\)" $(DRIVER).log | sort -u | grep . || echo "    (none)"
	@echo "--- overfull boxes wider than 5pt ---"
	@grep -h "Overfull .hbox ([5-9][0-9.]*pt\|Overfull .hbox ([0-9]\{3\}" $(DRIVER).log | sort -u | grep . || echo "    (none)"

clean:
	$(LATEXMK) -c $(DRIVER).tex 2>/dev/null || true
	rm -f *.bbl *.blg *.brf *.aux *.out *.log *.fls *.fdb_latexmk *.synctex.gz

distclean: clean
	rm -f $(DRIVER).pdf
