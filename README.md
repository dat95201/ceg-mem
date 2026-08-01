# CEGMem: counterexample-guided repair with a typed memory of refuted attempts

Replication package. All results are regenerated from a single seed; no number
in the paper is written by hand.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your API key and model prices
git clone https://github.com/jkoppel/QuixBugs external/QuixBugs
```

## Reproduce

```bash
python3 scripts/validate_oracle.py                        # freezes data/tasks.json
python3 scripts/measure_pi.py --programs all              # -> data/pi_pilot.json
python3 scripts/build_strata.py                           # freezes data/strata.json

python3 scripts/run_eval.py --modes no_memory --force-full-budget   # E1
python3 scripts/run_eval.py --modes untyped typed --check-overfit   # E2

python3 scripts/freeze_results.py --experiment main       # -> data/results_real.json
python3 scripts/analyze.py                                # -> data/analysis.json
python3 scripts/fit_theory.py                             # -> data/theory_fit.json
python3 figures/make_figures.py
python3 scripts/check_consistency.py   # asserts every reported number matches
```

E1 *is* the main grid's no-memory arm. It runs to the full budget so that
`pi_hat` and `q_hat` are estimated without early-stopping bias; the summary
step then truncates its rounds back at the first accept, so the arm stays
comparable to the memory arms it is tabulated against. Do not run `no_memory`
a second time without `--force-full-budget` — that is a different cell, and
`scripts/analyze.py` will refuse to pool the two.

`run_eval.py` is resumable and caches every model call on disk, so an
interrupted run costs nothing to restart. Both properties come from the same
place: every episode's id, typing-noise RNG, and per-round cache nonce are
derived from its experiment cell, so a re-run reproduces the episode exactly
and rewrites its own rows rather than appending a second copy.

## Layout

| Path | Contents |
|---|---|
| `src/tasks.py` | loads a QuixBugs program into a repair task |
| `src/sandbox.py` | runs candidate code under a timeout, captures failures |
| `src/oracle.py` | differential-testing counterexample oracle |
| `src/typer.py` | failure-type function, two granularities |
| `src/memory.py` | no-memory / untyped / typed stores |
| `src/loop.py` | the repair loop (Algorithm 1) |
| `src/llm.py` | model client with on-disk cache and a cost meter |
| `scripts/` | experiment driver and consistency checker |
| `paper/` | LaTeX source (IEEEtran) |
| `data/`, `cache/`, `external/` | generated or vendored; not tracked |

## Benchmark

We evaluate on the Python subset of QuixBugs. QuixBugs ships a reference
implementation alongside every buggy program, which is what makes a
counterexample oracle possible: inputs are generated with Hypothesis and run
against both versions, and the first divergence is returned as the
counterexample. Benchmarks that expose only a fixed test suite yield a
pass/fail bit rather than a counterexample, and cannot support the class-level
refutation this work studies. The reference implementation is visible to the
oracle only and is never placed in a model prompt.

## Cost

Every model call records input tokens, output tokens and cost to
`data/calls.jsonl`. The driver aborts when the configured cap is reached.

## Data availability

See the paper's Data Availability section.
