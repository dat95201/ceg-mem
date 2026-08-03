"""Pilot: measure pi_hat = P[the LLM proposes a correct patch in one shot].

40 independent calls per program, on 5 programs (200 calls total). Every
call uses mode="no_memory" with an empty history - no error feedback, no
conversation, no evidence or exclusion in the prompt - so each call is i.i.d,
matching pi = P[G proposes a correct patch] from the proposal's notation
(Table 1). This is a baseline measurement, not an episode: each call is
scored independently by the oracle and discarded, not fed forward.

Writes data/pi_pilot.json: per-program success count/rate and the pooled
estimate across all calls.

Every (program, call) unit is independent by construction - its own nonce, its
own oracle seed, no state carried between them - so --workers fans them out
over a process pool. Processes, not threads: each unit runs a Hypothesis
search, and Hypothesis swaps the *global* `random` state in and out around
every run (hypothesis.internal.entropy.deterministic_PRNG), which two threads
would interleave. Results are reassembled by index, so the report does not
depend on completion order.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import functools
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import SUPPORTED_PROGRAMS, TASKS, load
from src.llm import BudgetExceeded
from src.oracle import differential_test
from src.proposer import propose

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_PROGRAMS = ("gcd", "bucketsort", "mergesort", "detect_cycle", "topological_ordering")
DEFAULT_CALLS_PER_PROGRAM = 40


def default_workers() -> int:
    """One worker short of the core count, and never more than 8.

    Deliberately conservative. A worker is not pure compute - it spends much of
    its unit waiting on the model API - so the machine could absorb more. But
    src.sandbox kills a candidate at SANDBOX_TIMEOUT_SEC of *wall* time, and
    every oracle round spawns up to 2 x max_examples of those subprocesses.
    oversubscribe the CPU and a correct-but-slow patch starts getting killed as
    a timeout, which turns pi_hat into a function of machine load. Raise
    --workers only together with SANDBOX_TIMEOUT_SEC in .env.
    """
    return max(1, min(8, (os.cpu_count() or 2) - 1))


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


@functools.lru_cache(maxsize=None)
def _program(name: str):
    """Sources for one program, read once per worker process."""
    return load(name)


def one_call(job: tuple[str, int, str | None, int, int]) -> tuple[str, int, bool, str | None]:
    """Score one independent draw: propose a patch, then run the oracle on it.

    Top-level and argument-closed on purpose - this is what a pool worker runs,
    and under the "spawn" start method it has to be picklable by reference and
    re-derive its own task/program rather than receive them. Nothing here reads
    or writes state shared with another unit; the only shared resources are
    src.llm's on-disk cache and log, which are keyed per call.
    """
    name, i, model, max_examples, seed = job
    task = TASKS[name]
    program = _program(name)
    # Distinct nonce per call. The no-memory prompt is byte-identical across
    # all `calls_per_program` calls (empty history, no feedback - that is the
    # point of the measurement), so without it src.llm's cache would answer
    # calls 2..N with call 1's completion and pi_hat could only ever come out
    # 0.0 or 1.0.
    patch = propose(
        name, program.buggy_source, task.entry_point,
        mode="no_memory", history=[], model=model,
        nonce=f"pi-pilot|{name}|seed{seed}|call{i}",
    )
    result = differential_test(
        task, patch, program.correct_source,
        max_examples=max_examples, seed=seed + i,
    )
    return name, i, bool(result.accept), result.reason


def _summarize(
    programs: tuple[str, ...],
    calls_per_program: int,
    outcomes: dict[tuple[str, int], tuple[bool, str | None]],
    *,
    model: str | None,
    seed: int,
    workers: int,
    elapsed: float,
) -> dict:
    """Assemble the report from unit outcomes, in program/call order.

    Reading the outcomes back out by (name, i) rather than in completion order
    is what makes the parallel run produce the same JSON as the sequential one.
    """
    per_program = {}
    for name in programs:
        calls = [
            {"call": i + 1, "accept": outcomes[(name, i)][0], "reason": outcomes[(name, i)][1]}
            for i in range(calls_per_program)
        ]
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
        "workers": workers,
        "pi_hat_pooled": total_successes / total_calls,
        "total_successes": total_successes,
        "total_calls": total_calls,
        "elapsed_sec": round(elapsed, 1),
        "per_program": per_program,
    }


def measure(
    programs: tuple[str, ...],
    calls_per_program: int,
    *,
    model: str | None,
    max_examples: int,
    seed: int,
    workers: int = 1,
) -> dict:
    jobs = [
        (name, i, model, max_examples, seed)
        for name in programs
        for i in range(calls_per_program)
    ]
    total = len(jobs)
    width = len(str(total))
    outcomes: dict[tuple[str, int], tuple[bool, str | None]] = {}
    t0 = time.monotonic()

    def note(done: int, name: str, i: int, accept: bool) -> None:
        print(f"[{done:{width}d}/{total}] {name:25s} call {i + 1:2d}/{calls_per_program} "
              f"{'accept' if accept else 'reject'}", flush=True)

    if workers <= 1:
        for done, job in enumerate(jobs, 1):
            name, i, accept, reason = one_call(job)
            outcomes[(name, i)] = (accept, reason)
            note(done, name, i, accept)
    else:
        # A unit that dies takes the whole run with it: pi_hat over a corpus
        # with a hole in it is not the quantity this script reports, and a
        # partial data/pi_pilot.json that looks complete is worse than none.
        with cf.ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(one_call, job): job for job in jobs}
            try:
                for done, future in enumerate(cf.as_completed(futures), 1):
                    name, i, accept, reason = future.result()
                    outcomes[(name, i)] = (accept, reason)
                    note(done, name, i, accept)
            except BaseException:
                for future in futures:
                    future.cancel()
                raise

    return _summarize(
        programs, calls_per_program, outcomes,
        model=model, seed=seed, workers=workers, elapsed=time.monotonic() - t0,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--programs", nargs="+", default=list(DEFAULT_PROGRAMS))
    parser.add_argument("--calls-per-program", type=int, default=DEFAULT_CALLS_PER_PROGRAM)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-examples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--workers", type=int, default=default_workers(),
                        help="parallel worker processes; 1 = sequential "
                             f"(default on this machine: {default_workers()})")
    args = parser.parse_args()

    programs = _resolve_programs(args.programs)
    unknown = [p for p in programs if p not in TASKS]
    if unknown:
        raise SystemExit(f"unknown program(s): {unknown}")

    workers = max(1, min(args.workers, len(programs) * args.calls_per_program))
    print(f"{len(programs)} programs x {args.calls_per_program} calls "
          f"= {len(programs) * args.calls_per_program} units on {workers} worker(s)\n")

    try:
        report = measure(
            programs, args.calls_per_program,
            model=args.model, max_examples=args.max_examples, seed=args.seed,
            workers=workers,
        )
    except BudgetExceeded as exc:
        # Not written as a partial report on purpose: pi_hat needs every call
        # of every program, and a truncated data/pi_pilot.json is
        # indistinguishable from a complete one downstream. Completions already
        # paid for are in src.llm's cache, so a re-run resumes nearly free.
        raise SystemExit(f"budget cap reached ({exc}) - nothing written; "
                         "raise BUDGET_USD_CAP in .env and re-run")

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "pi_pilot.json").write_text(json.dumps(report, indent=2) + "\n")

    print()
    print(f"pooled pi_hat = {report['pi_hat_pooled']:.3f} over {report['total_calls']} calls "
          f"in {report['elapsed_sec']}s")
    for name, p in report["per_program"].items():
        print(f"  {name:25s} pi_hat={p['pi_hat']:.3f} ({p['successes']}/{p['calls']})")
    print("wrote data/pi_pilot.json")


if __name__ == "__main__":
    main()
