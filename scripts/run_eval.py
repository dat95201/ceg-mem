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
  E4 (oracle-informativeness sweep):  --max-examples 20|8|3 (100 = E2's cell)
  E5 (typing-coherence sweep):        --typing-noise-c 0.9|0.75|0.5 (1.0 = E2's cell)

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
from src.llm import MODEL as LLM_MODEL, BudgetExceeded, spent
from src.loop import MODES, run_episode
from src.metrics import DEFAULT_METRICS_LOG, load_rounds
from src.oracle import is_truly_correct

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DEFAULT_OVERFIT_LOG = DATA_DIR / "overfit_checks.jsonl"
DEFAULT_SEEDS = (1, 2, 3, 4, 5)
# B=20, not the proposal's 10 or the paper's 12. SS VI-A-a's own arithmetic:
# at B=12, Pr[accept] is 0.632 at pi=0.08, so the primary metric does not reach
# the floor of the proposal's Medium band; at B=20 it is 0.811. Note budget is
# deliberately *not* in _cell_key - a 12-round episode is a prefix of a 20-round
# one - so a run that forgets --budget would silently mix arms. Hence the default
# is the value the grid actually uses.
DEFAULT_BUDGET = 20


def _frozen_programs() -> list[str]:
    tasks_json = DATA_DIR / "tasks.json"
    if not tasks_json.exists():
        raise SystemExit(f"{tasks_json} missing - run scripts/select_corpus.py first to freeze the corpus")
    data = json.loads(tasks_json.read_text())
    if not data.get("frozen"):
        raise SystemExit(f"{tasks_json} is not frozen - re-run scripts/select_corpus.py")
    return [t["name"] for t in data["tasks"]]


def _programs_from_file(path: pathlib.Path) -> tuple[list[str], str]:
    """Read the program list from a file, preserving its order.

    Mirrors scripts/measure_pi.py::_programs_from_file, and exists for the same
    two reasons, neither of which is convenience. The order in a shard list is
    the balanced traversal order (scripts/eval_shard.sh cuts the corpus
    round-robin across strata), so every contiguous range is a smaller grid
    rather than a skewed one - passing names on the command line and truncating
    by hand loses that. And a file is quotable: zsh does not word-split an
    unquoted $VAR, so `--programs $SWEEP` arrives as a single 24-name argument
    and the driver rejects it as one unknown program.

    Accepts data/tasks.json, data/candidates.json, or a plain text file with one
    "<task>/<program>" per line (# comments allowed).
    """
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        entries = data.get("tasks") or data.get("candidates") or data.get("selected") or []
        if not entries:
            raise SystemExit(f"{path} carries no tasks/candidates/selected list")
        names = [e["name"] for e in entries]
        return names, f"{path} ({len(names)} entries)"
    names = [
        line.strip() for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not names:
        raise SystemExit(f"{path} is empty")
    return names, f"{path} ({len(names)} names)"


def cell_key(task: str, mode: str, seed: int, guard_on: bool, steer_on: bool,
             max_examples: int, typing_noise_c: float, force_full_budget: bool,
             model: str | None, granularity: str) -> tuple:
    """Identity of one experiment cell.

    model and granularity belong here for the same reason every other knob
    does: they change what the cell *is*. Leaving them out made a second
    model's sweep skip every cell as "already complete" against the first
    model's rows - silently, since the driver prints a skip line either way -
    and would have done the same to a coarse-granularity arm.

    Taken as explicit arguments rather than read off a row, because the two
    callers do not both *have* a row: the resume index is built from logged
    rounds, and the driver has only its own loop variables. When those were two
    separate tuple literals the driver's went on building the pre-model,
    pre-granularity 8-field key while the index built the 10-field one, so no
    lookup could ever match and every cell re-ran - the same drift the
    paragraph above describes, in the other direction. One constructor, so a
    field added here reaches both sites or neither.
    """
    return (task, mode, seed, guard_on, steer_on, max_examples, typing_noise_c,
            force_full_budget, model, granularity)


def _cell_key(row: dict) -> tuple:
    """cell_key for a logged round."""
    return cell_key(
        row["task"], row["mode"], row["seed"],
        row["guard_on"], row["steer_on"], row["max_examples"], row["typing_noise_c"],
        row.get("force_full_budget", False),
        row.get("model"), row.get("granularity", "fine"),
    )


def _completed_cells(episodes_path: pathlib.Path, budget: int, force_full_budget: bool,
                     also: list[pathlib.Path] | None = None) -> set[tuple]:
    """Cells where src.loop.run_episode would not need to do any more work.

    A cell is done if it already ran to the full budget, or - when the loop
    is allowed to stop early - it already contains an accepted round.

    `also` are read-only logs consulted for the same question: the merged
    history, or another shard's log. A grid this long is run in shards across
    sessions and machines (scripts/eval_shard.sh), and re-cutting the shard
    boundaries would otherwise re-walk cells another shard already finished.
    Model calls would replay from cache, but the oracle would not - re-running
    a finished cell costs its sandbox execution over again, which is most of the
    wall clock. Rounds from `also` are never written here; they only answer
    "has this cell been done anywhere".
    """
    by_cell: dict[tuple, list[dict]] = {}
    for path in [episodes_path, *(also or [])]:
        for row in load_rounds(path):
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
    resume_from: list[pathlib.Path] | None = None,
) -> None:
    done = _completed_cells(episodes_path, budget, force_full_budget, also=resume_from)
    total = len(programs) * len(modes) * len(seeds)
    n = 0

    for task_name in programs:
        program = load(task_name)
        task = TASKS[task_name]
        for mode in modes:
            for seed in seeds:
                n += 1
                cell = cell_key(task_name, mode, seed, guard_on, steer_on, max_examples,
                                typing_noise_c, force_full_budget, model, granularity)
                if cell in done:
                    print(f"[{n:4d}/{total}] {task_name:28s} {mode:10s} seed={seed} - already complete, skipping",
                          flush=True)
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
                # flush: a cell is minutes of wall clock, stdout block-buffers
                # the moment it is not a terminal, and `> log` is how this driver
                # is always run - so without it the only sign of life in a
                # multi-day sweep appears 8 KB (about 85 cells) at a time.
                # scripts/measure_pi.py and scripts/validate_oracle.py already
                # do this; this one was missed.
                print(
                    f"[{n:4d}/{total}] {task_name:28s} {mode:10s} seed={seed} - {status} "
                    f"(guard_evals={result.guard_evaluations}, spent=${spent():.4f})",
                    flush=True,
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
                        print("           overfit flagged: oracle accepted but is_truly_correct() rejected",
                              flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--programs", nargs="+", default=None, help="default: the frozen list in data/tasks.json")
    parser.add_argument("--programs-from", type=pathlib.Path, default=None,
                        help="read the program list from a file instead, in file "
                             "order: a shard list from scripts/eval_shard.sh, "
                             "data/tasks.json, or one '<task>/<program>' per line. "
                             "Preferred over --programs for anything past a handful")
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
    parser.add_argument("--resume-from", type=pathlib.Path, nargs="*", default=None,
                        help="extra episode logs consulted for what is already "
                             "done, and never written to - the merged history, or "
                             "another shard's log. Skipping a finished cell saves "
                             "its oracle runs, which the response cache cannot")
    args = parser.parse_args()

    if args.programs and args.programs_from:
        raise SystemExit("pass --programs or --programs-from, not both")
    if args.programs_from:
        programs, corpus_source = _programs_from_file(args.programs_from)
    else:
        programs, corpus_source = (args.programs, "--programs") if args.programs \
            else (_frozen_programs(), "data/tasks.json (frozen corpus)")
    print(f"corpus: {len(programs)} faults from {corpus_source}", flush=True)

    unknown = [p for p in programs if p not in TASKS]
    if unknown:
        raise SystemExit(
            f"unknown program(s): {unknown}\n"
            "If that looks like several names inside one string, the shell did not "
            "split them: zsh word-splits an unquoted $(...) but not an unquoted "
            "$VAR. Use --programs-from FILE."
        )

    # Resolve the model here rather than letting src.llm fall back inside every
    # call: the id has to reach the metrics row, or the artifact cannot state
    # which model produced it (paper SS VI-D-b) and _cell_key cannot separate
    # two models' cells.
    model = args.model or LLM_MODEL
    if not model:
        raise SystemExit("no model configured - set MODEL in .env or pass --model")

    # Free, and run before the first billable call. src.loop handles an
    # unusable oracle correctly per round - it logs the round and leaves memory
    # untouched - but with no test data *every* round is inconclusive, so the
    # sweep would spend its whole budget producing episodes in which nothing
    # was ever refuted and no memory was ever written.
    empty = [p for p in programs if not TASKS[p].test_cases]
    if empty:
        from src.adapter import TEST_DIR
        raise SystemExit(
            f"{len(empty)}/{len(programs)} programs have no test cases under {TEST_DIR} "
            f"(e.g. {empty[0]}).\nSet CONDEFECTS_TEST_DIR or unpack Test.zip - "
            f"`python3 scripts/fetch_condefects.py --check-only` reports what is visible."
        )

    run_sweep(
        programs, args.modes, args.seeds,
        budget=args.budget, model=model, granularity=args.granularity,
        max_examples=args.max_examples, typing_noise_c=args.typing_noise_c,
        guard_on=args.guard == "on", steer_on=args.steer == "on",
        force_full_budget=args.force_full_budget, check_overfit=args.check_overfit,
        episodes_path=args.episodes_path, overfit_path=args.overfit_path,
        resume_from=args.resume_from,
    )
    print(f"\ntotal spent so far: ${spent():.4f}")


if __name__ == "__main__":
    main()
