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
  ablation     (E3): all frozen programs x mode=typed x 3 seeds x
               {guard=off (steering-only), steer=off (guard-only)}
  oracle_sweep (E4): a program subset x seeds x max_examples in {100,20,8,3}
  typing_sweep (E5): the same program subset x seeds x typing_noise_c in
                     {1.0,0.9,0.75,0.5,0.25,0.0}
  transcript   (E6): all frozen programs x mode=transcript x 5 seeds - the
                     ChatRepair baseline, reported beside the three arms

--experiment restricts the check to one sub-grid; default "all" checks the
union of all four. The oracle/typing sweeps default to the first 30 (sorted) frozen programs -
always override with --sweep-programs, and pass the same list run_eval.py was
given, drawn stratified across the pi bands so a sweep level is not confounded
with task difficulty.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds, summarize_episode

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# The paper's three arms. Deliberately NOT src.loop.MODES, which also holds
# `transcript` - the ChatRepair baseline (src/memory.py), a separate sub-grid
# with its own preset and its own --experiment value, not a fourth cell of the
# main grid. Importing the wider tuple made every main-grid freeze report the
# whole transcript arm as missing.
MODES = ("no_memory", "untyped", "typed")

DEFAULT_SEEDS_MAIN = (1, 2, 3, 4, 5)
DEFAULT_SEEDS_SWEEP = (1, 2, 3)
# 100 is the reference level E2 already ran; 20/8/3 are the sweep's own.
# 300 is gone: src.oracle._sample returns the whole pool once max_examples
# reaches len(cases), and the largest test pool in ConDefects is 148 cases, so
# 300 was never distinguishable from 100 anywhere in the benchmark - it would
# have expected a whole extra level of cells that duplicate the reference.
DEFAULT_MAX_EXAMPLES_SWEEP = (100, 20, 8, 3)
# 0.25 and 0.0 were added for the crossover c*: four points down to 0.5 give a
# degradation slope but never cross the untyped arm, so c* could only be
# extrapolated. Note DESIGN.md SS4 - TypedMemory mistypes only the location half
# and cannot mistype an episode's first store, so c=0.0 is NOT a 100% mistype
# rate. Report the measured rate (stored_type != fine_type) beside the nominal c.
DEFAULT_TYPING_C_SWEEP = (1.0, 0.9, 0.75, 0.5, 0.25, 0.0)
DEFAULT_SWEEP_N_PROGRAMS = 30


def _frozen_programs() -> list[str]:
    tasks_json = DATA_DIR / "tasks.json"
    if not tasks_json.exists():
        raise SystemExit(f"{tasks_json} missing - run scripts/select_corpus.py first")
    data = json.loads(tasks_json.read_text())
    if not data.get("frozen"):
        raise SystemExit(f"{tasks_json} is not frozen - re-run scripts/select_corpus.py")
    return [t["name"] for t in data["tasks"]]


def _stratum_by_task() -> dict[str, str]:
    strata_json = DATA_DIR / "strata.json"
    if not strata_json.exists():
        return {}
    data = json.loads(strata_json.read_text())
    return {t["name"]: t["stratum"] for t in data.get("tasks", [])}


def expected_cells(programs: list[str], sweep_programs: list[str], experiment: str,
                   transcript_window: int = 0) -> set[tuple]:
    """(task, mode, seed, guard_on, steer_on, max_examples, typing_noise_c,
    force_full_budget, audit_guarded, transcript_window, typing_random) tuples.

    The last three are always at their defaults here: every experiment this
    function knows about is a main-grid or sweep cell, and the sub-grids that
    set them (E8-audit, a windowed E6, E5-random) are reported beside the grid
    rather than expected inside it. Carrying the fields anyway is what stops an
    E8 cell from matching - and so quietly satisfying - an E2 cell that never ran."""
    cells: set[tuple] = set()

    if experiment in ("all", "main"):
        for task in programs:
            for mode in MODES:
                # The no-memory arm is E1's full-budget corpus, reused: running
                # it past the first accept is what makes pi_hat/q_hat unbiased
                # (scripts/fit_theory.py), and src.metrics.summarize_episode
                # truncates the extra rounds back out so the arms stay
                # comparable. The memory arms stop at their first accept.
                full = mode == "no_memory"
                for seed in DEFAULT_SEEDS_MAIN:
                    cells.add((task, mode, seed, True, True, 100, 1.0, full, False, 0, False))

    if experiment in ("all", "ablation"):
        # Three seeds, not five: E3 attributes the effect between guard and
        # steering, it does not need the main grid's precision, and at B=20 the
        # two extra seeds would cost as much as the whole typing sweep.
        for task in programs:
            for seed in DEFAULT_SEEDS_SWEEP:
                cells.add((task, "typed", seed, False, True, 100, 1.0, False, False, 0, False))  # steering-only
                cells.add((task, "typed", seed, True, False, 100, 1.0, False, False, 0, False))  # guard-only

    if experiment in ("all", "oracle_sweep"):
        for task in sweep_programs:
            for seed in DEFAULT_SEEDS_SWEEP:
                for n in DEFAULT_MAX_EXAMPLES_SWEEP:
                    cells.add((task, "typed", seed, True, True, n, 1.0, False, False, 0, False))

    if experiment in ("all", "typing_sweep"):
        for task in sweep_programs:
            for seed in DEFAULT_SEEDS_SWEEP:
                for c in DEFAULT_TYPING_C_SWEEP:
                    cells.add((task, "typed", seed, True, True, 100, c, False, False, 0, False))

    # The ChatRepair baseline (E6). Its own sub-grid, not part of "main": it is a
    # baseline the paper reports beside the three arms, and folding it into the
    # main freeze would make a grid that never ran it look incomplete. Full seeds,
    # because it goes in the main table rather than in an ablation.
    if experiment in ("all", "transcript"):
        for task in programs:
            for seed in DEFAULT_SEEDS_MAIN:
                cells.add((task, "transcript", seed, True, True, 100, 1.0, False,
                           False, transcript_window, False))

    return cells


def _cell_key(summary: dict) -> tuple:
    # The last three are not decoration. E8-audit and a windowed transcript run
    # every other knob at its main-grid value, so without them an audit cell
    # silently SATISFIED a missing main-grid cell and the completeness check
    # reported a full grid that was not one. expected_cells() only ever builds
    # tuples with all three at their defaults, so a sub-grid cell can no longer
    # match anything it expects.
    return (
        summary["task"], summary["mode"], summary["seed"],
        summary["guard_on"], summary["steer_on"], summary["max_examples"], summary["typing_noise_c"],
        summary.get("force_full_budget", False),
        summary.get("audit_guarded", False),
        summary.get("transcript_window", 0),
        summary.get("typing_random", False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment", default="all",
                         choices=["all", "main", "ablation", "oracle_sweep", "typing_sweep",
                                  "transcript"])
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--sweep-programs", nargs="+", default=None,
                         help=f"default: first {DEFAULT_SWEEP_N_PROGRAMS} frozen programs, sorted")
    parser.add_argument("--sweep-programs-from", type=pathlib.Path, default=None,
                         help="read the sweep subset from a file, one name per "
                              "line - data/sweep_programs.txt, the list "
                              "scripts/eval_shard.sh actually ran E4/E5 over. "
                              "Use this rather than --sweep-programs $VAR: the "
                              "default here is NOT the stratified subset, and a "
                              "shell that does not split the variable turns 24 "
                              "names into one, so both mistakes freeze the wrong "
                              "tasks silently")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "results_real.json")
    parser.add_argument("--transcript-window", type=int, default=0,
                        help="--experiment transcript only: the window E6 actually "
                             "ran under. It is in the cell key, so freezing a "
                             "windowed run against the default 0 reports every "
                             "cell that ran as missing and every cell that did "
                             "not as expected")
    parser.add_argument("--force", action="store_true", help="overwrite an already-frozen output file")
    parser.add_argument("--allow-partial", action="store_true",
                         help="freeze anyway even if cells are missing (prints a warning, not an error)")
    args = parser.parse_args()

    if args.out.exists() and not args.force:
        existing = json.loads(args.out.read_text())
        if existing.get("frozen"):
            raise SystemExit(f"{args.out} is already frozen - pass --force to overwrite")

    if args.sweep_programs and args.sweep_programs_from:
        raise SystemExit("pass --sweep-programs or --sweep-programs-from, not both")

    programs = _frozen_programs()
    if args.sweep_programs_from:
        sweep_programs = [
            line.strip() for line in args.sweep_programs_from.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not sweep_programs:
            raise SystemExit(f"{args.sweep_programs_from} is empty")
        print(f"sweep subset: {len(sweep_programs)} programs from {args.sweep_programs_from}")
    else:
        sweep_programs = args.sweep_programs or sorted(programs)[:DEFAULT_SWEEP_N_PROGRAMS]
    unknown = [p for p in sweep_programs if p not in set(programs)]
    if unknown:
        raise SystemExit(
            f"sweep subset names {len(unknown)} program(s) that are not in the frozen "
            f"corpus, e.g. {unknown[0]!r} - the sweeps were run over a different list "
            "than the one being frozen"
        )
    expected = expected_cells(programs, sweep_programs, args.experiment,
                              transcript_window=args.transcript_window)

    rows = load_rounds(args.episodes_path)

    # `model` is in scripts/run_eval.py's cell key but NOT in _cell_key here -
    # summarize_episode does not carry it - so without this check episodes from
    # a different proposer silently satisfy expected cells, and the frozen
    # artifact never states which model produced the numbers in it. Both matter:
    # pi is a property of the model, so two models in one freeze is two
    # experiments reported as one.
    models = sorted({r.get("model") for r in rows if r.get("model")})
    if len(models) > 1:
        raise SystemExit(
            f"{args.episodes_path} holds episodes from {len(models)} models: "
            f"{', '.join(map(repr, models))}.\n"
            "pi is a property of the model, so these are two experiments, not one "
            "grid. Move the\nforeign rows aside (match on the `model` field) and "
            "re-run. scripts/consolidate_evals.py\nrefuses the same join at merge "
            "time; a hand-run scripts/run_eval.py is the usual way past it."
        )

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
                  f"steer_on={cell[4]} max_examples={cell[5]} typing_noise_c={cell[6]} "
                  f"force_full_budget={cell[7]}")
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
        # Which proposer produced these numbers. SS VI-D-b of the paper asks for
        # it, and nothing else in this file records it.
        "model": models[0] if models else None,
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
