# Project status — CEGMem

**State: clean. No results are held.**

Every pilot result, run log, oracle report and corpus freeze was deleted on
2026-08-11 so that planning is anchored on `paper/main_proposal.txt` alone and
not on measurements taken under a superseded design. `data/` now contains source
only (`mutants.py`).

## Which numbers are synthetic

All numbers **in the paper** are synthetic. §5 states it plainly: the proposer
and the oracle are simulated, no model is prompted and no test suite is executed.
That is deliberate — soundness and non-repetition are only checkable against a
known candidate space and a known set of correct patches, which no real benchmark
provides.

No number in this repository is currently real either, because no run has been
made since the reset. When one is, `scripts/check_consistency.py` rebuilds every
frozen file from `data/episodes.jsonl` and deep-diffs it against what is on disk,
so a reported number cannot drift from the artifact that produced it.

## Steps to a real implementation

[PLAN.md](PLAN.md) — the ordered run plan, each step naming the paper claim it
serves and what it costs. Summary of the sequence:

| | step | API cost |
|---|---|---|
| 1 | prerequisites; commit the two working-tree fixes | — |
| 2 | E0 — candidate pool + oracle gate | none |
| 3 | E0b — screen π̂ | ~$57 |
| 4 | freeze the corpus on the π bands | none |
| 5 | oracle blind-spot measurement | none |
| 6 | E1 — no-memory arm | ~$59 |
| 7 | E2 — memory arms | ~$61 |
| 8 | E3 — guard/steer ablation | ~$37 |
| 9 | E4/E5 — ρ and c sweeps | ~$26 |
| 10 | analysis, figures, consistency | none |

## Open items

1. **Two fixes are uncommitted** in the working tree and must land before any
   billable run. Both corrupt the quantities the paper's claims are read off:
   `src/loop.py` included `budget` in the episode id, which double-counted rounds
   in every round-averaged estimator; `scripts/run_eval.py` built a resume key
   the index could never match, so every cell re-ran at full price.
2. **The corpus must be rebuilt from scratch** — steps 2–4 of PLAN.md. The
   previous corpus was calibrated by AtCoder rating, which SELECTION.md explains
   is the wrong instrument.
3. **Real type coherence is unmeasured**, and §9 calls it the single decisive
   quantity. `measure_coherence.py` gives an automated proxy but cannot separate
   ρ from c; `label_tool.py` is the only route to human ground truth.
4. **Gaps against the paper's §10** (`gen_synthetic.py`, `data/results.json`,
   `data/schema.md` all absent) are recorded at the end of PLAN.md.

## The deletion is staged, not committed

`data/episodes.jsonl` is deleted in the index but the commit has not been made. A
stray `git checkout -- data/` or a stash pop restores the superseded B=12 round
log into **the exact path E1 appends to** — silently recreating the double-count
that fix (1) exists to remove. Commit the deletion before running anything, or
verify `data/episodes.jsonl` is absent immediately before step 6.

## Recovering the deleted artifacts

Everything deleted was either tracked in git or copied to a backup archive first.
Tracked artifacts come back with:

```bash
git checkout HEAD -- data/tasks.json data/pool data/pilot_v0 data/retired \
                     data/hard_120.json data/oracle_validation.json \
                     data/pool_strength.json data/episodes.jsonl \
                     scripts/select_hard_tasks.py scripts/salvage_test_zip.py
```

Do not rewrite or amend the commit that carries those blobs, or the recovery path
goes with it.
