"""Pilot: measure pi_hat = P[the LLM proposes a correct patch in one shot].

40 independent calls per program, on 5 programs (200 calls total). Every
call uses mode="no_memory" with an empty history - no error feedback, no
conversation, no evidence or exclusion in the prompt - so each call is i.i.d,
matching pi = P[G proposes a correct patch] from the proposal's notation
(Table 1). This is a baseline measurement, not an episode: each call is
scored independently by the oracle and discarded, not fed forward.

Writes data/pi_pilot.json: per-program success count/rate and the pooled
estimate across all calls.
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
from src.proposer import propose

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_PROGRAMS = ("gcd", "bucketsort", "mergesort", "detect_cycle", "topological_ordering")
DEFAULT_CALLS_PER_PROGRAM = 40


def _resolve_programs(names: list[str]) -> tuple[str, ...]:
    """"--programs all" -> the frozen task list (data/tasks.json) if present,
    else every task adapter.py supports. Needed to run pi_hat over all 40
    tasks for stratification (scripts/build_strata.py), not just the 5-task
    pilot sample."""
    if names != ["all"]:
        return tuple(names)
    tasks_json = DATA_DIR / "tasks.json"
    if tasks_json.exists():
        data = json.loads(tasks_json.read_text())
        if data.get("frozen"):
            return tuple(t["name"] for t in data["tasks"])
    return SUPPORTED_PROGRAMS


def measure(
    programs: tuple[str, ...],
    calls_per_program: int,
    *,
    model: str | None,
    max_examples: int,
    seed: int,
) -> dict:
    per_program = {}
    t0 = time.monotonic()

    for name in programs:
        task = TASKS[name]
        program = load(name)
        calls = []
        for i in range(calls_per_program):
            # Distinct nonce per call. The no-memory prompt is byte-identical
            # across all `calls_per_program` calls (empty history, no feedback -
            # that is the point of the measurement), so without it src.llm's
            # cache would answer calls 2..N with call 1's completion and pi_hat
            # could only ever come out 0.0 or 1.0.
            patch = propose(
                name, program.buggy_source, task.entry_point,
                mode="no_memory", history=[], model=model,
                nonce=f"pi-pilot|{name}|seed{seed}|call{i}",
            )
            result = differential_test(
                task, patch, program.correct_source,
                max_examples=max_examples, seed=seed + i,
            )
            calls.append({"call": i + 1, "accept": result.accept, "reason": result.reason})
            print(f"{name} [{i + 1:2d}/{calls_per_program}] {'accept' if result.accept else 'reject'}")

        successes = sum(c["accept"] for c in calls)
        per_program[name] = {
            "successes": successes,
            "calls": calls_per_program,
            "pi_hat": successes / calls_per_program,
            "detail": calls,
        }

    total_successes = sum(p["successes"] for p in per_program.values())
    total_calls = sum(p["calls"] for p in per_program.values())

    return {
        "programs": list(programs),
        "calls_per_program": calls_per_program,
        "model": model,
        "seed": seed,
        "pi_hat_pooled": total_successes / total_calls,
        "total_successes": total_successes,
        "total_calls": total_calls,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "per_program": per_program,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--programs", nargs="+", default=list(DEFAULT_PROGRAMS))
    parser.add_argument("--calls-per-program", type=int, default=DEFAULT_CALLS_PER_PROGRAM)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-examples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()

    programs = _resolve_programs(args.programs)
    unknown = [p for p in programs if p not in TASKS]
    if unknown:
        raise SystemExit(f"unknown program(s): {unknown}")

    report = measure(
        programs, args.calls_per_program,
        model=args.model, max_examples=args.max_examples, seed=args.seed,
    )

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "pi_pilot.json").write_text(json.dumps(report, indent=2) + "\n")

    print()
    print(f"pooled pi_hat = {report['pi_hat_pooled']:.3f} over {report['total_calls']} calls")
    for name, p in report["per_program"].items():
        print(f"  {name:25s} pi_hat={p['pi_hat']:.3f} ({p['successes']}/{p['calls']})")
    print("wrote data/pi_pilot.json")


if __name__ == "__main__":
    main()
