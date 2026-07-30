"""Check the accumulated data/episodes.jsonl matrix is complete, then
aggregate + freeze data/results_real.json.

The plan names only the output file ("Files: data/results_real.json") for
the Week-6 "kiem tra ma tran khong thieu o, dong bang du lieu" step, without
naming a script - this fills that gap, playing the same role
scripts/validate_oracle.py plays for data/tasks.json: verify completeness
against an explicit checklist, then write a frozen artifact that downstream
analysis (scripts/analyze.py, scripts/fit_theory.py,
scripts/check_consistency.py) treats as ground truth. It does *not* compute
statistics itself - only scripts/run_eval.py's raw per-episode summaries
(src.metrics.summarize_episode), plus the stratum label from data/strata.json.
Table-level aggregation (means, CIs, tests) lives in scripts/analyze.py so
there is exactly one place that turns raw episodes into reported numbers.

Checks four sub-grids, matching the plan's own experiment definitions
(all overridable):
  main         (E2): all frozen programs x 3 modes x seeds, guard=on steer=on,
               max_examples=100, typing_noise_c=1.0
  ablation     (E3): all frozen programs x mode=typed x seeds x
               {guard=off (steering-only), steer=off (guard-only)}
  oracle_sweep (E4): a program subset x seeds x max_examples in {300,100,30,10}
  typing_sweep (E5): the same program subset x seeds x typing_noise_c in {1.0,0.9,0.75,0.5}

--experiment restricts the check to one sub-grid; default "all" checks the
union of all four. The oracle/typing sweeps default to the first 20 (sorted)
frozen programs, since the plan says "20 task" without naming which -
override with --sweep-programs if a different subset was actually run.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.loop import MODES
from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds, summarize_episode

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_SEEDS_MAIN = (1, 2, 3, 4, 5)
DEFAULT_SEEDS_SWEEP = (1, 2, 3)
DEFAULT_MAX_EXAMPLES_SWEEP = (300, 100, 30, 10)
DEFAULT_TYPING_C_SWEEP = (1.0, 0.9, 0.75, 0.5)
DEFAULT_SWEEP_N_PROGRAMS = 20


def _frozen_programs() -> list[str]:
    tasks_json = DATA_DIR / "tasks.json"
    if not tasks_json.exists():
        raise SystemExit(f"{tasks_json} missing - run scripts/validate_oracle.py first")
    data = json.loads(tasks_json.read_text())
    if not data.get("frozen"):
        raise SystemExit(f"{tasks_json} is not frozen - re-run scripts/validate_oracle.py")
    return [t["name"] for t in data["tasks"]]


def _stratum_by_task() -> dict[str, str]:
    strata_json = DATA_DIR / "strata.json"
    if not strata_json.exists():
        return {}
    data = json.loads(strata_json.read_text())
    return {t["name"]: t["stratum"] for t in data.get("tasks", [])}


def expected_cells(programs: list[str], sweep_programs: list[str], experiment: str) -> set[tuple]:
    """(task, mode, seed, guard_on, steer_on, max_examples, typing_noise_c) tuples."""
    cells: set[tuple] = set()

    if experiment in ("all", "main"):
        for task in programs:
            for mode in MODES:
                for seed in DEFAULT_SEEDS_MAIN:
                    cells.add((task, mode, seed, True, True, 100, 1.0))

    if experiment in ("all", "ablation"):
        for task in programs:
            for seed in DEFAULT_SEEDS_MAIN:
                cells.add((task, "typed", seed, False, True, 100, 1.0))  # steering-only
                cells.add((task, "typed", seed, True, False, 100, 1.0))  # guard-only

    if experiment in ("all", "oracle_sweep"):
        for task in sweep_programs:
            for seed in DEFAULT_SEEDS_SWEEP:
                for n in DEFAULT_MAX_EXAMPLES_SWEEP:
                    cells.add((task, "typed", seed, True, True, n, 1.0))

    if experiment in ("all", "typing_sweep"):
        for task in sweep_programs:
            for seed in DEFAULT_SEEDS_SWEEP:
                for c in DEFAULT_TYPING_C_SWEEP:
                    cells.add((task, "typed", seed, True, True, 100, c))

    return cells


def _cell_key(summary: dict) -> tuple:
    return (
        summary["task"], summary["mode"], summary["seed"],
        summary["guard_on"], summary["steer_on"], summary["max_examples"], summary["typing_noise_c"],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", default="all",
                         choices=["all", "main", "ablation", "oracle_sweep", "typing_sweep"])
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--sweep-programs", nargs="+", default=None,
                         help=f"default: first {DEFAULT_SWEEP_N_PROGRAMS} frozen programs, sorted")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "results_real.json")
    parser.add_argument("--force", action="store_true", help="overwrite an already-frozen output file")
    parser.add_argument("--allow-partial", action="store_true",
                         help="freeze anyway even if cells are missing (prints a warning, not an error)")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        existing = json.loads(args.out.read_text())
        if existing.get("frozen"):
            raise SystemExit(f"{args.out} is already frozen - pass --force to overwrite")

    programs = _frozen_programs()
    sweep_programs = args.sweep_programs or sorted(programs)[:DEFAULT_SWEEP_N_PROGRAMS]
    expected = expected_cells(programs, sweep_programs, args.experiment)

    rows = load_rounds(args.episodes_path)
    episodes = group_by_episode(rows)
    summaries = [summarize_episode(ep_rows) for ep_rows in episodes.values()]
    have = {_cell_key(s) for s in summaries}

    missing = sorted(expected - have)
    print(f"expected {len(expected)} cells, have {len(have & expected)} of them "
          f"({len(have - expected)} extra cells present too, e.g. from other experiments)")

    if missing:
        print(f"MISSING {len(missing)} cells, e.g.:")
        for cell in missing[:20]:
            print(f"  task={cell[0]!r} mode={cell[1]!r} seed={cell[2]} guard_on={cell[3]} "
                  f"steer_on={cell[4]} max_examples={cell[5]} typing_noise_c={cell[6]}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        if not args.allow_partial:
            raise SystemExit(
                f"\nmatrix incomplete: {len(missing)} cells missing - "
                "run scripts/run_eval.py to fill them in, or pass --allow-partial to freeze anyway"
            )
        print("--allow-partial set: freezing anyway.")

    strata = _stratum_by_task()
    for s in summaries:
        s["stratum"] = strata.get(s["task"])

    report = {
        "frozen": True,
        "experiment": args.experiment,
        "episodes_path": str(args.episodes_path),
        "n_episodes": len(summaries),
        "n_expected_cells": len(expected),
        "n_missing_cells": len(missing),
        "strata_source": str(DATA_DIR / "strata.json") if strata else None,
        "episodes": summaries,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nwrote {args.out} (frozen, {len(summaries)} episodes)")


if __name__ == "__main__":
    main()
