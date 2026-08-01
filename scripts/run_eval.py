"""Experiment driver: program x condition x seed. Resumable.

Checkpoint after every episode so an interrupted run restarts free - in fact
after every *round*, since src.loop.run_episode already appends one
RoundRecord per round to data/episodes.jsonl as it goes (src.metrics). This
script's own job is just: enumerate the (task, mode, seed, config) grid,
skip cells already complete in the log, run the rest, and stop cleanly when
the budget cap is hit.

One driver, many experiments - all of E1-E5 are this same grid with
different flags:
  E1 (no-memory arm + pi_hat corpus):  --modes no_memory --force-full-budget
  E2 (memory arms):              --modes untyped typed --check-overfit
  E3 (ablation):                 --modes typed --guard off   (steering-only)
                                  --modes typed --steer off  (guard-only)
  E4 (oracle-informativeness sweep):  --max-examples 300|100|30|10
  E5 (typing-coherence sweep):        --typing-noise-c 1.0|0.9|0.75|0.5

E1 *is* the main grid's no-memory arm - run it, then run E2 over the two
memory modes only. Running no_memory a second time without
--force-full-budget would buy a whole second arm that scripts/analyze.py
deliberately refuses to pool with the first (see its _is_main_grid).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import TASKS, load
from src.llm import BudgetExceeded, spent
from src.loop import MODES, run_episode
from src.metrics import DEFAULT_METRICS_LOG, load_rounds
from src.oracle import is_truly_correct

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DEFAULT_OVERFIT_LOG = DATA_DIR / "overfit_checks.jsonl"
DEFAULT_SEEDS = (1, 2, 3, 4, 5)
DEFAULT_BUDGET = 12


def _frozen_programs() -> list[str]:
    tasks_json = DATA_DIR / "tasks.json"
    if not tasks_json.exists():
        raise SystemExit(f"{tasks_json} missing - run scripts/validate_oracle.py first to freeze the task list")
    data = json.loads(tasks_json.read_text())
    if not data.get("frozen"):
        raise SystemExit(f"{tasks_json} is not frozen - re-run scripts/validate_oracle.py")
    return [t["name"] for t in data["tasks"]]


def _cell_key(row: dict) -> tuple:
    return (
        row["task"], row["mode"], row["seed"],
        row["guard_on"], row["steer_on"], row["max_examples"], row["typing_noise_c"],
        row.get("force_full_budget", False),
    )


def _completed_cells(episodes_path: pathlib.Path, budget: int, force_full_budget: bool) -> set[tuple]:
    """Cells where src.loop.run_episode would not need to do any more work.

    A cell is done if it already ran to the full budget, or - when the loop
    is allowed to stop early - it already contains an accepted round.
    """
    by_cell: dict[tuple, list[dict]] = {}
    for row in load_rounds(episodes_path):
        by_cell.setdefault(_cell_key(row), []).append(row)

    done = set()
    for cell, rows in by_cell.items():
        rounds_seen = max(r["round_index"] for r in rows)
        already_accepted = any(r["accept"] for r in rows)
        if rounds_seen >= budget or (already_accepted and not force_full_budget):
            done.add(cell)
    return done


def run_sweep(
    programs: list[str],
    modes: list[str],
    seeds: list[int],
    *,
    budget: int,
    model: str | None,
    granularity: str,
    max_examples: int,
    typing_noise_c: float,
    guard_on: bool,
    steer_on: bool,
    force_full_budget: bool,
    check_overfit: bool,
    episodes_path: pathlib.Path,
    overfit_path: pathlib.Path,
) -> None:
    done = _completed_cells(episodes_path, budget, force_full_budget)
    total = len(programs) * len(modes) * len(seeds)
    n = 0

    for task_name in programs:
        program = load(task_name)
        task = TASKS[task_name]
        for mode in modes:
            for seed in seeds:
                n += 1
                cell = (task_name, mode, seed, guard_on, steer_on, max_examples,
                        typing_noise_c, force_full_budget)
                if cell in done:
                    print(f"[{n:4d}/{total}] {task_name:28s} {mode:10s} seed={seed} - already complete, skipping")
                    continue
                try:
                    result = run_episode(
                        task_name, mode,
                        budget=budget, model=model, granularity=granularity,
                        max_examples=max_examples, seed=seed,
                        metrics_path=episodes_path,
                        guard_on=guard_on, steer_on=steer_on,
                        typing_noise_c=typing_noise_c,
                        force_full_budget=force_full_budget,
                    )
                except BudgetExceeded as exc:
                    print(f"\nBUDGET CAP REACHED ({exc}) - stopping the sweep cleanly.")
                    print(f"Completed {n - 1}/{total} cells; re-run this command later to resume.")
                    return

                status = f"repaired@{result.first_accept_round}" if result.accepted_patch else "exhausted"
                print(
                    f"[{n:4d}/{total}] {task_name:28s} {mode:10s} seed={seed} - {status} "
                    f"(guard_evals={result.guard_evaluations}, spent=${spent():.4f})"
                )

                if check_overfit and result.accepted_patch is not None:
                    truly_correct = is_truly_correct(task, program, result.accepted_patch)
                    overfit_path.parent.mkdir(parents=True, exist_ok=True)
                    with overfit_path.open("a") as f:
                        f.write(json.dumps({
                            "episode_id": result.episode_id, "task": task_name, "mode": mode, "seed": seed,
                            "first_accept_round": result.first_accept_round,
                            "truly_correct": truly_correct, "overfit": not truly_correct,
                        }) + "\n")
                    if not truly_correct:
                        print(f"           overfit flagged: oracle accepted but is_truly_correct() rejected")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--programs", nargs="+", default=None, help="default: the frozen list in data/tasks.json")
    parser.add_argument("--modes", nargs="+", default=list(MODES), choices=list(MODES))
    parser.add_argument("--seeds", nargs="+", type=int, default=list(DEFAULT_SEEDS))
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    parser.add_argument("--model", default=None)
    parser.add_argument("--granularity", default="fine", choices=["coarse", "fine"])
    parser.add_argument("--max-examples", type=int, default=100, help="oracle informativeness sweep (E4)")
    parser.add_argument("--typing-noise-c", type=float, default=1.0, help="typing coherence sweep (E5)")
    parser.add_argument("--guard", choices=["on", "off"], default="on", help="ablation (E3)")
    parser.add_argument("--steer", choices=["on", "off"], default="on", help="ablation (E3)")
    parser.add_argument("--force-full-budget", action="store_true", help="don't stop at first accept (E1)")
    parser.add_argument("--check-overfit", action="store_true", help="run is_truly_correct on every accept (E2)")
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--overfit-path", type=pathlib.Path, default=DEFAULT_OVERFIT_LOG)
    args = parser.parse_args()

    programs = args.programs or _frozen_programs()
    unknown = [p for p in programs if p not in TASKS]
    if unknown:
        raise SystemExit(f"unknown program(s): {unknown}")

    run_sweep(
        programs, args.modes, args.seeds,
        budget=args.budget, model=args.model, granularity=args.granularity,
        max_examples=args.max_examples, typing_noise_c=args.typing_noise_c,
        guard_on=args.guard == "on", steer_on=args.steer == "on",
        force_full_budget=args.force_full_budget, check_overfit=args.check_overfit,
        episodes_path=args.episodes_path, overfit_path=args.overfit_path,
    )
    print(f"\ntotal spent so far: ${spent():.4f}")


if __name__ == "__main__":
    main()
