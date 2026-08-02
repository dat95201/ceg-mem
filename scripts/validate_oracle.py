"""Validate the corpus and freeze the task list.

On a mutation-based setup this script planted known-bad mutants and asked
whether the oracle caught them. ConDefects makes that unnecessary and replaces
it with something stronger: the faults are real, and every one arrives with
the contest test suite that rejected it. What has to be checked is that each
fault is *usable* as an experiment cell, which means all four of

  1. test data is present for the coding task (Test.zip unpacked);
  2. the reference (correctVersion.py) passes every case it is run on - if it
     does not, it is not a reference and no divergence from it means anything;
  3. the faulty version fails at least one case - otherwise the shipped pool
     does not expose this fault and the oracle can never refute a patch, so
     an episode would accept the unmodified program in round 1;
  4. neither version times out, so a repair round is not dominated by a
     candidate the sandbox has to kill.

Faults that pass are sampled down to at most one per coding task: several
submissions can fail the same AtCoder problem, and treating them as separate
tasks would put near-duplicate programs in the corpus and correlate cells that
the analysis assumes are independent.

Writes:
    data/oracle_validation.json - full per-fault detail, for audit
    data/tasks.json             - the frozen task list
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import SUPPORTED_PROGRAMS, TASKS, load, task_dates, task_difficulties
from src.oracle import differential_test, outputs_equal
from src.sandbox import DEFAULT_TIMEOUT, run_program

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_CORPUS_SIZE = 40
DEFAULT_REFERENCE_CASES = 20   # cases the reference must pass to count as one


def _check_reference(program, task, *, n_cases: int, timeout: float) -> tuple[bool, str]:
    """Criterion 2 + half of 4: does correctVersion.py answer its own tests?"""
    cases = task.test_cases[:n_cases]
    for case in cases:
        if case.expected_output is None:
            continue
        outcome = run_program(program.correct_source, case.input_text, timeout=timeout)
        if outcome.timed_out:
            return False, f"reference timed out on {case.name}"
        if not outcome.ok:
            return False, f"reference raised {outcome.error_type} on {case.name}"
        if not outputs_equal(outcome.value, case.expected_output):
            return False, f"reference disagrees with the shipped output on {case.name}"
    return True, ""


def validate(
    names: list[str],
    *,
    max_examples: int,
    reference_cases: int,
    seed: int,
    timeout: float,
) -> dict:
    per_fault: dict[str, dict] = {}
    t0 = time.monotonic()
    dates, difficulties = task_dates(), task_difficulties()

    for i, name in enumerate(names, 1):
        task = TASKS[name]
        record = {
            "task_id": task.task_id,
            "program_id": task.program_id,
            "date": dates.get(task.task_id),
            "difficulty": difficulties.get(task.task_id),
            "n_test_cases": len(task.test_cases),
            "loc": None,
            "fault_lines": None,
            "fault_exposed": False,
            "reference_ok": False,
            "reason": None,
            "passes": False,
        }
        per_fault[name] = record

        if not task.test_cases:
            record["reason"] = "no test data for this coding task"
            print(f"[{i:4d}/{len(names)}] {name:30s} SKIP  {record['reason']}")
            continue

        program = load(name)
        record["loc"] = len(program.buggy_source.splitlines())
        record["fault_lines"] = list(program.fault_lines)

        reference_ok, why = _check_reference(
            program, task, n_cases=reference_cases, timeout=timeout,
        )
        record["reference_ok"] = reference_ok
        if not reference_ok:
            record["reason"] = why
            print(f"[{i:4d}/{len(names)}] {name:30s} FAIL  {why}")
            continue

        # Criterion 3: run the *faulty* version through the same oracle the
        # repair loop uses. A refutation here is the fault being exposed.
        result = differential_test(
            task, program.buggy_source, program.correct_source,
            max_examples=max_examples, seed=seed, timeout=timeout,
        )
        record["fault_exposed"] = not result.accept and result.oracle_error is None
        record["examples_tried"] = result.examples_tried
        record["counterexample"] = result.args[0] if result.args else None
        record["reason"] = result.reason if not record["fault_exposed"] else None
        record["passes"] = record["fault_exposed"]

        status = "PASS" if record["passes"] else "FAIL"
        detail = record["counterexample"] or (record["reason"] or "")
        print(f"[{i:4d}/{len(names)}] {name:30s} {status}  {detail}")

    passing = [n for n, r in per_fault.items() if r["passes"]]
    return {
        "criterion": (
            "test data present; reference passes its own cases; the faulty "
            "version is refuted by the shipped pool; neither version times out"
        ),
        "n_candidates": len(names),
        "n_passing": len(passing),
        "max_examples": max_examples,
        "reference_cases": reference_cases,
        "timeout_sec": timeout,
        "seed": seed,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "faults": per_fault,
    }


def select_corpus(report: dict, *, corpus_size: int, seed: int) -> list[str]:
    """At most one fault per coding task, then a seeded sample of `corpus_size`.

    Ordering is by name before sampling, so the frozen corpus is a function of
    (report, seed) alone and re-running this script reproduces it exactly.
    """
    by_task: dict[str, str] = {}
    for name in sorted(n for n, r in report["faults"].items() if r["passes"]):
        by_task.setdefault(report["faults"][name]["task_id"], name)
    unique = sorted(by_task.values())
    if corpus_size <= 0 or corpus_size >= len(unique):
        return unique
    return sorted(random.Random(seed).sample(unique, corpus_size))


def write_tasks_json(report: dict, selected: list[str], *, corpus_size: int, seed: int) -> None:
    tasks_json = {
        "frozen": bool(selected),
        "benchmark": "ConDefects (Python)",
        "criterion": report["criterion"],
        "selection": (
            f"at most one fault per coding task, then a seed={seed} sample of "
            f"{corpus_size} from the {report['n_passing']} that passed"
        ),
        "n_selected": len(selected),
        "n_candidates": report["n_candidates"],
        "seed": seed,
        "tasks": [
            {
                "name": name,
                "task_id": report["faults"][name]["task_id"],
                "program_id": report["faults"][name]["program_id"],
                "date": report["faults"][name]["date"],
                "difficulty": report["faults"][name]["difficulty"],
                "loc": report["faults"][name]["loc"],
                "fault_lines": report["faults"][name]["fault_lines"],
                "n_test_cases": report["faults"][name]["n_test_cases"],
                "counterexample": report["faults"][name]["counterexample"],
            }
            for name in selected
        ],
    }
    (DATA_DIR / "tasks.json").write_text(json.dumps(tasks_json, indent=2) + "\n")


def _candidates(args) -> list[str]:
    names = list(SUPPORTED_PROGRAMS)
    if args.programs:
        return [n for n in args.programs]

    dates, difficulties = task_dates(), task_difficulties()
    if args.since:
        names = [n for n in names if (dates.get(TASKS[n].task_id) or "") >= args.since]
    if args.until:
        names = [n for n in names if (dates.get(TASKS[n].task_id) or "9999") <= args.until]
    if args.min_difficulty is not None:
        names = [n for n in names if (difficulties.get(TASKS[n].task_id, -1)) >= args.min_difficulty]
    if args.max_difficulty is not None:
        names = [n for n in names if (difficulties.get(TASKS[n].task_id, 10**9)) <= args.max_difficulty]

    # Validating all ~2800 faults costs real wall time and the corpus only
    # needs a few dozen, so shuffle first and stop once enough have passed.
    random.Random(args.seed).shuffle(names)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--programs", nargs="+", default=None, help="validate exactly these faults")
    parser.add_argument("--corpus-size", type=int, default=DEFAULT_CORPUS_SIZE,
                        help="how many faults to freeze; 0 = every fault that passes")
    parser.add_argument("--max-examples", type=int, default=80,
                        help="test cases the oracle may run per fault")
    parser.add_argument("--reference-cases", type=int, default=DEFAULT_REFERENCE_CASES,
                        help="cases the reference must answer correctly")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--since", default=None, help="keep coding tasks on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="keep coding tasks on/before this date (YYYY-MM-DD)")
    parser.add_argument("--min-difficulty", type=int, default=None)
    parser.add_argument("--max-difficulty", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None,
                        help="stop after validating this many faults (default: 6x corpus-size)")
    args = parser.parse_args()

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - run scripts/fetch_condefects.py first")

    names = _candidates(args)
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        raise SystemExit(f"unknown fault(s): {unknown}")
    if args.programs is None:
        cap = args.max_candidates or (max(args.corpus_size, 1) * 6)
        names = names[:cap]

    report = validate(
        names,
        max_examples=args.max_examples,
        reference_cases=args.reference_cases,
        seed=args.seed,
        timeout=args.timeout,
    )
    selected = select_corpus(report, corpus_size=args.corpus_size, seed=args.seed)

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "oracle_validation.json").write_text(json.dumps(report, indent=2) + "\n")
    write_tasks_json(report, selected, corpus_size=args.corpus_size, seed=args.seed)

    print()
    print(f"{report['n_passing']}/{report['n_candidates']} faults usable in {report['elapsed_sec']}s")
    print(f"corpus {'FROZEN' if selected else 'NOT FROZEN'}: {len(selected)} faults "
          f"(one per coding task)")
    if not selected:
        print("nothing passed - check that Test.zip is unpacked (scripts/fetch_condefects.py --check-only)")
    print("wrote data/oracle_validation.json and data/tasks.json")


if __name__ == "__main__":
    main()
