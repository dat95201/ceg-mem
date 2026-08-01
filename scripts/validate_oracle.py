"""Validate the oracle: does it catch known-bad mutants?

For every one of the 40 QuixBugs tasks, build the 3 hand-authored mutants
(data/mutants.py - one per fault type from proposal section 3.5) and run the
Hypothesis-driven differential oracle (src/oracle.py) of each mutant against
the correct reference. A mutant is "caught" when the oracle finds a
counterexample (accept=False).

Freeze criterion (from the task spec):
    a task PASSES if it catches >= 2/3 of its mutants;
    the corpus is frozen only if >= 30/40 tasks pass.

Writes:
    data/oracle_validation.json - full per-mutant detail, for audit
    data/tasks.json             - the frozen task list, only meaningful
                                   metadata a proposer/oracle needs downstream
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import SUPPORTED_PROGRAMS, TASKS, load
from src.oracle import differential_test
from data.mutants import build_mutants

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
TASK_PASS_THRESHOLD = 2   # of 3 mutants
CORPUS_PASS_THRESHOLD = 30  # of 40 tasks


def validate(max_examples: int, seed: int) -> dict:
    per_task = {}
    t0 = time.monotonic()

    for i, name in enumerate(SUPPORTED_PROGRAMS, 1):
        task = TASKS[name]
        program = load(name)
        mutants = build_mutants(name, program.correct_source)

        mutant_results = []
        for mutant in mutants:
            result = differential_test(
                task, mutant.source, program.correct_source,
                max_examples=max_examples, seed=seed,
            )
            # "Caught" means a counterexample was produced, so an inconclusive
            # search (src.oracle.OracleResult.oracle_error) counts as a miss
            # rather than silently passing the mutant on a non-verdict.
            caught = not result.accept and result.oracle_error is None
            mutant_results.append({
                "fault_type": mutant.fault_type,
                "note": mutant.note,
                "caught": caught,
                "examples_tried": result.examples_tried,
                "counterexample_args": result.args if caught else None,
                "reason": result.reason if caught else None,
                "oracle_error": result.oracle_error,
            })

        n_caught = sum(m["caught"] for m in mutant_results)
        per_task[name] = {
            "mutants_caught": n_caught,
            "mutants_total": len(mutant_results),
            "passes": n_caught >= TASK_PASS_THRESHOLD,
            "mutants": mutant_results,
        }
        status = "PASS" if per_task[name]["passes"] else "FAIL"
        print(f"[{i:2d}/{len(SUPPORTED_PROGRAMS)}] {name:30s} {n_caught}/3 caught  {status}")

    n_passing = sum(t["passes"] for t in per_task.values())
    frozen = n_passing >= CORPUS_PASS_THRESHOLD
    elapsed = time.monotonic() - t0

    return {
        "task_pass_threshold": f">= {TASK_PASS_THRESHOLD}/3 mutants caught",
        "corpus_pass_threshold": f">= {CORPUS_PASS_THRESHOLD}/{len(SUPPORTED_PROGRAMS)} tasks passing",
        "n_tasks_passing": n_passing,
        "n_tasks_total": len(SUPPORTED_PROGRAMS),
        "frozen": frozen,
        "max_examples": max_examples,
        "seed": seed,
        "elapsed_sec": round(elapsed, 1),
        "tasks": per_task,
    }


def write_tasks_json(report: dict) -> None:
    passing = [name for name, t in report["tasks"].items() if t["passes"]]
    tasks_json = {
        "frozen": report["frozen"],
        "criterion": (
            f"a task is selected if it catches {report['task_pass_threshold']}; "
            f"the corpus is frozen only if {report['corpus_pass_threshold']}"
        ),
        "n_selected": len(passing),
        "n_total": report["n_tasks_total"],
        "seed": report["seed"],
        "tasks": [
            {
                "name": name,
                "mutants_caught": report["tasks"][name]["mutants_caught"],
                "fault_types_caught": [
                    m["fault_type"] for m in report["tasks"][name]["mutants"] if m["caught"]
                ],
            }
            for name in passing
        ],
    }
    (DATA_DIR / "tasks.json").write_text(json.dumps(tasks_json, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-examples", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()

    report = validate(args.max_examples, args.seed)

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "oracle_validation.json").write_text(json.dumps(report, indent=2) + "\n")
    write_tasks_json(report)

    print()
    print(f"{report['n_tasks_passing']}/{report['n_tasks_total']} tasks pass "
          f"({report['task_pass_threshold']}) in {report['elapsed_sec']}s")
    print(f"corpus {'FROZEN' if report['frozen'] else 'NOT FROZEN'} "
          f"(need {report['corpus_pass_threshold']})")
    if not report["frozen"]:
        failing = [name for name, t in report["tasks"].items() if not t["passes"]]
        print(f"failing tasks: {failing}")
    print("wrote data/oracle_validation.json and data/tasks.json")


if __name__ == "__main__":
    main()
