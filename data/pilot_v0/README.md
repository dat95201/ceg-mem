# pilot v0 — retired, kept as evidence

Nothing in this directory is read by the pipeline. It was moved out of `data/`
on 2026-08-06 when the corpus was re-drawn as 120 hard faults
(`data/hard_120.json`, SELECTION.md), so that no script can pick up a stale
freeze and no reader can mistake these numbers for the current corpus.

They are kept rather than deleted because SELECTION.md's design argument rests
on them and **the new pilot cannot re-derive it**: the evidence that the
AtCoder rating predicts π at all is a correlation measured *across* the rating
range, and the new corpus is hard-only (every task rated ≥1600). Delete these
and the justification for the 1600 floor becomes uncheckable.

| file | what it is |
|---|---|
| `tasks.json` | the 60-fault freeze, tercile strata, drawn against the salvaged partial test tree (`test_dir: external/ConDefects/Test_partial`) |
| `oracle_validation.json` | the mutation gate over those 60 — 102 candidates examined, 57/60 cohort passing, 201 mutants, **0 missed** |
| `pi_pilot.json` | 60 programs × 40 i.i.d. draws = 2,400 calls on `claude-haiku-4-5`; pooled π̂ = 0.369 |
| `pi_pilot_calibration12.json` | an earlier 12-task calibration; superseded by the n=60 run and retained only because STATUS.md §5.3 corrects the record against it |

What they establish, and what SELECTION.md §3 cites them for:

- Spearman(AtCoder rating, π̂) = **−0.350** (n=60) — the only free proxy that
  works; every fault-level feature tested came in under |ρ| = 0.07.
- Median π̂ by proxy stratum: **0.300 easy · 0.275 medium · 0.038 hard** — what
  retired the easy/medium/hard quotas.
- At rating ≥1600 (n=20): median π̂ = 0.038, but bimodal — 8 at π̂ = 0 and 5 at
  π̂ ≥ 0.40, the latter at ratings 2135–2531.

Caveat on all three: measured against the partial test tree, so the corpus they
describe stops at 2023-01-28 and excludes every `agc` and `arc` contest.
