# P1-1 fired: the narrowed claim, and the text that carries it

Falsifier **F4** fired (`PRESPEC-2026-09.md` §3). This file holds the narrowed
claim and every edit the paper needs to state it. Written 2026-09-07, after the
result — the register says so and so does §Threats.

---

## 1. What the measurement says

495 cells / 99 tasks / 5 seeds, `no_memory` universe, depth $k=100$, five
policies replayed over byte-identical candidates. 0 model calls. 0 cells
skipped. Matrix gate: coverage 1.0000, reproduction 0.9993 — PASS.

| Policy | Exec./ep (mean) | Exec. to refutation (med) | Oracle calls/ep | Accepts/ep |
|---|---:|---:|---:|---:|
| `oracle-k` (as reported) | 248.4 | 2.91 | 19.99 | 4.10 |
| `qi13` | **174.1** | 1.44 | 19.99 | 4.10 |
| `venugopal20` | **173.7** | **1.38** | 19.99 | 4.10 |
| `dedup-guard` | 180.1 | 1.54 | **5.85** | 4.10 |
| `cegmem-guard` | 179.7 | 1.50 | **5.85** | 4.10 |

Task-level, `cegmem-guard` against the better of the two classical policies
(win / tie / loss of 99):

| Axis | Result | One-sided $p$ | Median penalty |
|---|---|---|---|
| Executions to refutation | 1 / 18 / 80 | $1.0$ — **criterion NOT MET** | $+4.6\%$ |
| Total program executions | 2 / 6 / 91 | $1.0$ | $+4.8\%$ |
| **Oracle invocations** | **99 / 0 / 0** | $< 10^{-17}$ | $-70.8\%$ |

**Two corrections to the first draft of this file, both verified against
`policies_cells.json` on 2026-09-07.** Total executions is **2 / 6 / 91**, not
2 / 5 / 92 — no aggregation (per-task mean, median or sum; cell level; or
per-task better-of-the-two-classical) reproduces the latter. And the 80th
"loss" on the criterion axis has a per-task difference of $2.2\times10^{-16}$:
it is a tie that exact float equality missed. We keep **1 / 18 / 80** because
that is what `policies.json` records and what the pre-declared script computed;
changing it after the fact would move a goalpost, harmlessly but visibly.
Neither correction changes any conclusion.

**The margin, which the first draft omitted and the paper now reports.** The
loss is unanimous in sign and small in size: the median task pays $+4.6\%$
($+0.077$ of one execution). An ideal order refutes on its first case, at
$1.0$ execution; measured against that floor, `venugopal20` captures $84.6\%$
of the headroom `oracle-k` leaves and `cegmem-guard` $83.5\%$ — a gap of $1.1$
points. Reporting the sign without the size overstates the damage by an order
of magnitude, and a reviewer who recomputes it will say so.

**Why the axis affords so little, which is an external-validity limit on the
verdict itself.** Among refuted candidates the median share of pool cases that
refute is $0.581$; $21.3\%$ of refuted candidates fail *every* case. A
uniformly random order therefore finds evidence in ${\approx}1.7$ executions
against an ideal $1.0$, and all four informed policies land between $1.38$ and
$1.54$. On this corpus the ordering axis is nearly saturated. That does not
rescue the guard — it lost — but it bounds what the loss generalizes to, and
§Threats now says so.

**The horizon difference, which must be stated or the two experiments read as
contradictory.** The replay walks all 20 rounds instead of stopping at the
first acceptance — that is what keeps the candidate stream policy-independent.
So `accepts_per_episode` is $4.10$ (a run that stopped at first accept could
not exceed $1$) and `oracle_rounds_per_episode` is $19.99$, against the
reported arm's $9.47$. The reduction here is therefore $\times3.42$, not the
$\times5.17$ of Table II, and only ratios *within* the replay carry over.

And the index, `cegmem-guard` against `dedup-guard`:

- Oracle calls and blocked rounds: **identical on every one of the 495 cells**.
- Executions to refutation: 46 / 37 / 16 tasks, $p = 1.7\times10^{-5}$; medians
  1.50 vs 1.54 **at the cell level** (task-level medians are 1.57 vs 1.64).
- Total executions: 45 / 39 / 15 tasks, $p = 1.3\times10^{-5}$, worth $0.25\%$
  of the total. The first draft said the gain was "confined to executions per
  refutation"; it is confined to the *execution* axes, both of them, and moves
  nothing on the oracle axis.

## 2. The narrowed claim, stated once

> The guard is **not** a cheaper validation than a classical prioritizer. On
> this corpus it is more expensive — 3.4% more program executions per episode
> than a modification-point–aware prioritizer, and it loses the
> executions-to-refutation comparison on 80 of 99 tasks. What it is, and what
> no prioritizer in that line is, is a decision procedure that **removes oracle
> invocations**: 19.99 per episode to 5.85, on 99 of 99 tasks, with accepted
> rounds identical by construction. The two mechanisms are not substitutes and
> the paper should stop implying they compete on one axis.

Three claims must therefore change:

1. **Drop** any suggestion that the guard reduces validation *work* relative to
   prior art. Against the reported oracle it does ($\times 1.38$); against a
   classical prioritizer it does not.
2. **Keep, and strengthen**, the oracle-invocation claim — it is now measured
   against the two named baselines rather than asserted, and it is unanimous.
3. **Weaken further** the index claim. $\loc$ buys a signed but negligible
   ordering gain and nothing at all on the metric the paper leads with.

---

## 3. Edits, paste-ready — **APPLIED 2026-09-07, and superseded**

Every edit below has been applied, but **not verbatim**: the shipped text is
shorter (page budget, §6), carries the corrected `2/6/91`, adds the effect size
and the horizon caveat, and moves the methodology, the index comparison and the
headroom analysis into a new appendix section `app:policies`. Read the files,
not this section; it is kept as the record of what was planned.

| Planned here | Actually shipped in |
|---|---|
| §3.1 macros | `preamble.tex` — as written |
| §3.2 `tables/policies.tex` | five rows only, no comparison block, `\fullonly` |
| §3.3 §VII subsection | `sections/07-results.tex` §`sec:results-policies`, three paragraphs |
| — | `sections/12-appendix.tex` §`app:policies` (new: methodology, horizon, headroom, index) |
| §3.4 related work | `sections/02-related.tex` — shorter |
| §3.5 threats | `sections/09-threats.tex` — two caveats, plus the saturated-axis limit |
| §3.6 positioning | `tables/positioning.tex` — as written |
| §3.7 conclusion | `sections/11-conclusion.tex` — as written |
| §3.8 intro | `sections/01-intro.tex` — as written |
| — | `sections/00-abstract.tex` (new: the boundary clause) |
| — | `sections/11b-availability.tex` (new: hand-entered numbers named) |


### 3.1 Macros — add to `preamble.tex` beside the arm names (line ~88)

```latex
\newcommand{\polk}{\scname{oracle-$k$}}
\newcommand{\polqi}{\scname{qi'13}}
\newcommand{\polven}{\scname{mp-aware}}
\newcommand{\poldedup}{\scname{dedup-guard}}
\newcommand{\polguard}{\scname{cegmem-guard}}
```

### 3.2 New file `tables/policies.tex`

```latex
\begin{table}[t]
\centering
\footnotesize
\caption{Five validation policies over one candidate stream. In \armnomem\ the
proposer sees nothing the validator found, so the candidate stream does not
depend on the policy and all five replay over byte-identical candidates against
a complete candidate\,$\times$\,case verdict matrix; no model is called. The
last column is identical by construction---a policy changes what finding out
costs, not what the proposer writes.}
\label{tab:policies}
\setlength{\tabcolsep}{4pt}
\renewcommand{\arraystretch}{1.05}
\begin{tabular}{@{}L{1.95cm}rrrr@{}}
\toprule
\textbf{\sig{Policy}} & \textbf{\sig{Exec./ep}} & \textbf{\sig{To refut.}} &
\textbf{\sig{Oracle}} & \textbf{\sig{Accepts}} \\
\midrule
\polk\ \sig{(reported)} & $248.4$ & $2.91$ & $19.99$ & $4.10$ \\
\polqi                  & $174.1$ & $1.44$ & $19.99$ & $4.10$ \\
\polven                 & \best{$173.7$} & \best{$1.38$} & $19.99$ & $4.10$ \\
\poldedup               & $180.1$ & $1.54$ & \best{$5.85$} & $4.10$ \\
\polguard               & $179.7$ & $1.50$ & \best{$5.85$} & $4.10$ \\
\midrule
\multicolumn{5}{@{}l@{}}{\sig{\polguard\ vs.\ better classical, task-level
  (win/tie/loss of $99$):}}\\
\sig{to refutation} & \multicolumn{4}{r@{}}{$1/18/80$, \pval{1.0}} \\
\sig{total exec.}   & \multicolumn{4}{r@{}}{$2/5/92$, \pval{1.0}} \\
\sig{oracle calls}  & \multicolumn{4}{r@{}}{$99/0/0$, \plt{10^{-17}}} \\
\bottomrule
\end{tabular}
\end{table}
```

Columns: executions per episode and accepts per episode are means; executions
to first refutation is a median.

### 3.3 New subsection in `sections/07-results.tex`

```latex
\subsection{Against the mechanism the guard resembles}
\label{sec:results-policies}

\cref{sec:related-order} puts the guard in the same row as fault-recorded
prioritization~\cite{qi2013efficient} and its modification-point--aware
refinement~\cite{venugopal2020modification}. This measures the distance instead
of arguing it.

\paragraph{The comparison is exact, not approximate.}
In \armnomem\ the proposer is shown nothing the validator found, so the
candidate stream is a function of task, seed and round alone: it does not depend
on which cases ran, in what order, or whether the run stopped early. Five
policies therefore replay over \emph{byte-identical} candidates. We executed
each of the \num{5564} distinct \armnomem\ candidates against its task's entire
shipped pool and replayed the policies against that matrix offline---\num{495}
cells, \num{99} tasks, no cell dropped, and no model call. The matrix reproduces
the counterexample the harness itself found in \num{8306} of \num{8312} logged
oracle rounds; all six exceptions fall on the timeout-edge fault of
\cref{sec:results-integrity}.

\paragraph{The pre-declared criterion fails.}
Before the matrix was built we fixed that \polguard\ would reach its first
refutation in no more case executions than the better classical policy on at
least $60$ of the $99$ tasks, one-sided task-level Wilcoxon \plt{0.05}. It does
so on $1$, ties on $18$, loses on $80$ (\pval{1.0}). The outcome is not the
parity the failure rule anticipated: a classical prioritizer is \emph{better} on
this axis, and on total program executions as well ($2/5/92$ tasks). \polguard\
spends $3.4\%$ more executions per episode than \polven\ (\cref{tab:policies}).

\paragraph{What no prioritizer does.}
Both classical policies leave oracle invocations at $19.99$ per episode, because
both still validate every candidate---they change the order in which a
validation seeks evidence, not whether the validation happens. The guard
resolves $70.8\%$ of rounds before the oracle is reached, taking invocations to
$5.85$: a win on $99$ of $99$ tasks, \plt{10^{-17}}. Accepted rounds are
identical across all five policies by construction, so none of it is bought
with repair.

\paragraph{The index earns little, and here is how little.}
\polguard\ differs from \poldedup\ in one respect, consulting the $\loc$ bucket
before the rest of the store. Oracle calls and blocked rounds are identical on
every one of the $495$ cells; the gain is confined to executions per refutation,
where it is signed but small---$46/37/16$ tasks, \plt{10^{-4}}, medians $1.50$
against $1.54$. That is the shape of the typed-versus-untyped result in
\cref{tab:tasklevel}, and it points the same way.

\begin{finding}{The guard is not a cheaper validation than a classical
prioritizer---on this corpus it costs $3.4\%$ more executions. It is the only
one of the five policies that removes oracle invocations, it does so on every
task, and repair is unchanged.}
\end{finding}

\input{tables/policies}
```

Drop the `finding` environment if the page budget is tight; the paragraph
headings already carry the claims.

### 3.4 Replace the closing sentences of `sections/02-related.tex` §related-order

Current text ends:

> *"The per-modification-point table of~\cite{venugopal2020modification} is the nearest neighbour, and the baseline this paper most needs (\cref{sec:threats})."*

Replace with:

```latex
The per-modification-point table of~\cite{venugopal2020modification} is the
nearest neighbour, and we run it: over byte-identical candidates it reaches a
refutation in fewer executions than our guard does on $80$ of $99$ tasks
(\cref{sec:results-policies}). What separates the two is not cost per
validation but whether a validation happens at all---no prioritizer in this
line moves oracle invocations, and the guard moves them on every task.
```

### 3.5 Replace the last sentences of `sections/09-threats.tex` §Statistical validity

Current text ends:

> *"Finally, the comparison a reader will want is against a per-modification-point prioritizer~\cite{venugopal2020modification}, the mechanism our guard is closest to. We have not implemented or run that arm, so \cref{tab:positioning} is argued mechanically rather than measured: the boundary it draws is a claim about mechanism, not a measured difference."*

Replace with:

```latex
Finally, \cref{tab:positioning} is now measured rather than argued: both
classical prioritizers run over byte-identical candidates
(\cref{sec:results-policies}), and the pre-declared criterion for that
comparison failed. Two limits on it remain. The policies are replayed inside the
\armnomem\ universe, which is what makes the candidate stream policy-independent
and therefore the comparison exact---a prioritizer driving a \emph{steered}
proposer is a different experiment. And the ranking depends on the cost unit:
counting program executions the prioritizers win, counting oracle invocations
the guard wins on every task, and a reader whose suite is cheap should take the
first.
```

### 3.6 `tables/positioning.tex` — the CEGMem row

Change the last cell from `reorders, stops early` to:

```latex
reorders, stops early; \emph{removes oracle invocations}
```

And in the caption, replace

> *"what is new is the key it orders by, the evidence it orders from, and that it needs nothing of the candidate space"*

with

```latex
what is new is not a cheaper validation---a classical prioritizer beats ours on
executions (\cref{sec:results-policies})---but that the same evidence removes
oracle invocations, which no row above does.
```

### 3.7 `sections/11-conclusion.tex`

Replace

> *"Whether it survives a per-modification-point prioritizer (\cref{sec:related-order}), another model scale, or the expensive-oracle faults we filtered out, are the three questions we would want answered next."*

with

```latex
It does not survive a per-modification-point prioritizer on executions---that
prioritizer is cheaper on $80$ of $99$ tasks---and it is the only policy of five
that removes oracle invocations at all, on every task
(\cref{sec:results-policies}). Whether that holds at another model scale, or on
the expensive-oracle faults we filtered out, are the two questions we would want
answered next.
```

### 3.8 `sections/01-intro.tex` — one clause

The intro currently says the second classical mechanism "transfers" and asks
what it should order from. That framing survives, but the results sentence

> *"Memory cuts oracle calls \fold{5.2} and, counting every guard replay as the execution it is, total program executions \fold{2.2}, at unchanged repair rate"*

should gain the boundary, e.g. append:

```latex
---though against a classical prioritizer replayed on identical candidates the
execution saving reverses and only the oracle-call reduction survives
(\cref{sec:results-policies}).
```

---

## 4. Page accounting

| Item | Pages |
|---|---:|
| `tab:policies` | $+0.22$ |
| §VII-x prose (four short paragraphs) | $+0.18$ |
| `finding` box | $+0.05$ (droppable) |
| Table I caption shortens | $-0.03$ |
| §Threats sentence shortens | $-0.02$ |
| **Net** | $\approx +0.40$ |

`CUT-rationale.md` names $0.50$ pages of reserve: Table I's "Presumes about
candidates" column ($0.15$), §VII-F to three sentences ($0.15$), the nested
explanation in §III-A ($0.10$), the arm-list gloss in §VI-B ($0.10$). Spending
$0.40$ of it here leaves $0.10$ — which is less than the second-proposer table
needs, so that collision is real and belongs to Gate G4, not to the freeze week.

---

## 5. Checks before this ships

- [x] Every number above and in `tables/policies.tex` recomputed from
      `policies_cells.json` / `verdicts.jsonl` independently of
      `simulate_policies.py`. Two disagreed; both are corrected in §1.
- [x] `tools/` had **no** `check_consistency.py` — the earlier draft of this
      file named one that does not exist, and `make check` was a grep for
      undefined refs and TODOs. Closed: `scripts/verify_policies.py` re-derives
      every figure in `tab:policies` and §`sec:results-policies` from the
      artifacts, independently of `simulate_policies.py`, and exits non-zero on
      disagreement. `make check-policies` runs it and `make check` depends on
      it. All 60 checks pass. §Data Availability names it.
- [ ] `\num{8306}` / `\num{8312}` (matrix reproduction) is the one claim not
      reproducible from the four shipped artifacts — it needs
      `episodes.jsonl` and the `build_verdict_matrix.py --verify` output.
      Re-check it before submission.
- [x] "pre-declared" vs "pre-specified": the paper now says *pre-specified*
      throughout, matching §Threats. `simulate_policies.py`'s docstring still
      says "PRE-DECLARED CRITERION"; leave it, it is the timestamped artifact.
- [x] Both drivers build clean: `main.pdf` 18 pp, `main-ieee.pdf` 12 pp, no
      undefined references or citations, worst overfull box 4.0 pt.

## 6. Page budget — the estimate in §4 was wrong

§4 estimated $+0.40$ page. The honest cost of the treatment as first drafted
was $\approx +1.15$ pages (678 words of new prose plus the table). After
pushing the methodology, the index comparison and the headroom analysis into
`app:policies` and cutting the table to five rows in the full build only, the
body cost is $\approx +0.45$ page.

The submission build was already at exactly 10.0 content pages with **zero**
slack, so `main-ieee.pdf` now carries content into page 11's left column: **11
content pages, one over the SANER limit.** Nothing in the new material can be
cut further without dropping the pre-specified criterion result itself.

Closing it costs reserve. `CUT-rationale.md` lists $0.50$ page in four items;
spending $\approx 0.45$ of it lands the paper at 10 pages and leaves $0.05$ for
F7's second-proposer table, which needs more than that. The collision §4
predicted is therefore real and unavoidable, and it is a Gate G4 decision, not
a freeze-week one:

| Option | Cost | Consequence |
|---|---|---|
| Spend reserve items 1--4 now | $-0.50$ page | 10 pages today; F7 must find its own space |
| Leave at 11 pages | $0$ | Desk-reject risk if submitted as-is; fine for the preprint |
| Cut §VII-x to the criterion + oracle result only | $-0.20$ page | Loses the effect size, which is what keeps the loss honest |
