"""How strong is each task's shipped test pool? Planted mutants, measured not gated.

scripts/validate_oracle.py validates the oracle against *natural* mutants -
other people's wrong submissions to the same coding task. That answers "does the
oracle catch real developer mistakes", and on this corpus the answer is 240/240.

It cannot answer a second question, and the reason is structural: a natural
mutant is a submission AtCoder rejected, so the shipped pool refutes it *by
construction*. Feeding the pool a program it is already known to reject can
never reveal a case the pool would miss. The measurement is real but bounded.

The unanswered question is the one this script is for:

    Can the pool tell a *small perturbation* of the correct program from the
    correct program itself?

That matters because it is the population the repair loop actually judges. An
LLM patch is a near miss - a few tokens away from something that works - not a
stranger's from-scratch reimplementation. A planted mutant (one targeted AST
edit to correctVersion.py) is the closer proxy, and the verdict this script
exists to produce is `equivalent`: the mutant is wrong, and *no test in the
pool distinguishes it from the reference*.

An equivalent mutant is a hole in the pool. In the loop, a patch that lands in
one is accepted while being wrong - a false accept, which is exactly what
Theorem 4.1's soundness claim rules out by assumption. Nothing else in this
pipeline estimates how large that hole is:

  * the loop's oracle runs at max_examples=100 and 109 of the 120 tasks ship
    80 cases or fewer, so for those tasks the sampled oracle *is* the whole
    pool;
  * src.oracle.is_truly_correct, the overfitting audit, also runs the whole
    pool - so on those same tasks it agrees with the loop's oracle trivially
    and can detect nothing.

The equivalent rate is therefore the only available upper bound on how much
patch overfitting could be happening invisibly, and it is worth its CPU.

Deliberately NOT a gate. It reads the already-frozen data/tasks.json, changes
nothing, and selects nothing; the corpus is what validate_oracle.py froze. A
task with a weak pool is reported, not dropped - dropping it would be selecting
the corpus on a property measured after the freeze.

Writes data/pool_strength.json.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.mutants import FAULT_TYPES, candidate_mutants, mutant_capacity
from src.adapter import TASKS, load
from src.oracle import differential_test
from src.sandbox import DEFAULT_TIMEOUT

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DEFAULT_JOBS = max(1, (os.cpu_count() or 4) - 2)


def _judge(task, program, mutant, *, max_examples: int, seed: int, timeout: float,
           full_pool_cap: int) -> dict:
    """caught | missed | equivalent | inconclusive, with the evidence.

    Same three-way split scripts/validate_oracle.py uses, and for the same
    reason: a mutant the sample accepts has to be asked of the whole pool before
    it can be called anything, because "the sample was too small" and "no test
    can see this" are different findings and only the second is about the pool.
    """
    sampled = differential_test(
        task, mutant.source, program.correct_source,
        max_examples=max_examples, seed=seed, timeout=timeout,
    )
    record = {
        "fault_type": mutant.fault_type,
        "site": mutant.site,
        "note": mutant.note,
        "diff": mutant.diff,
        "examples_tried": sampled.examples_tried,
        "counterexample": sampled.args[0] if sampled.args else None,
    }
    if sampled.oracle_error is not None:
        return {**record, "verdict": "inconclusive", "reason": sampled.reason}
    if not sampled.accept:
        return {**record, "verdict": "caught"}

    full = differential_test(
        task, mutant.source, program.correct_source,
        max_examples=full_pool_cap, seed=seed + 1, timeout=timeout,
    )
    record["full_pool_examples_tried"] = full.examples_tried
    record["full_pool_counterexample"] = full.args[0] if full.args else None
    if full.oracle_error is not None:
        return {**record, "verdict": "inconclusive", "reason": full.reason}
    # missed: the pool can see it, this sample did not draw the case that shows
    # it. equivalent: the pool cannot see it at all - the hole this script hunts.
    return {**record, "verdict": "missed" if not full.accept else "equivalent"}


def measure_task(name: str, *, max_examples: int, seed: int, timeout: float,
                 full_pool_cap: int, sites_per_type: int) -> dict:
    """Plant up to `sites_per_type` mutants per fault type and judge each.

    More than one site per type because the question is about the *pool*, not
    about one edit: a single off-by-one that happens to land on a well-covered
    line says little about whether the pool covers boundaries generally.
    """
    task, program = TASKS[name], load(name)
    capacity = mutant_capacity(name, program.correct_source)
    results = []
    for fault_type in FAULT_TYPES:
        candidates = candidate_mutants(name, program.correct_source, fault_type)
        if not candidates:
            results.append({"fault_type": fault_type, "verdict": "unavailable",
                            "note": "the program admits no site for this fault type"})
            continue
        for index, mutant in enumerate(candidates[:sites_per_type]):
            results.append(_judge(
                task, program, mutant, max_examples=max_examples,
                # A distinct seed per mutant: two edits to one program must not
                # be judged on the identical sample of test cases.
                seed=seed + 31 * index, timeout=timeout, full_pool_cap=full_pool_cap,
            ))
    verdicts = collections.Counter(r["verdict"] for r in results)
    return {
        "n_test_cases": len(task.test_cases),
        "mutant_capacity": capacity,
        "n_planted": sum(verdicts[v] for v in ("caught", "missed", "equivalent")),
        **{f"n_{v}": verdicts[v] for v in
           ("caught", "missed", "equivalent", "inconclusive", "unavailable")},
        "mutants": results,
    }


def catch_rate_curve(examples_tried: list[int], points=(1, 2, 3, 5, 10, 20, 40, 80)) -> dict:
    """Share of mutants a sample of k cases would have refuted.

    The oracle draws its k cases uniformly without replacement, so the count it
    needed is an order statistic of that draw and the ECDF of those counts *is*
    the catch rate as a function of max_examples. This is what makes the number
    reportable across the whole RQ2 sweep without re-running anything.
    """
    if not examples_tried:
        return {}
    n = len(examples_tried)
    return {str(k): round(sum(1 for x in examples_tried if x <= k) / n, 4) for k in points}


def natural_mutant_curve(validation_path: pathlib.Path) -> dict:
    """The same curve for validate_oracle.py's natural mutants, for comparison."""
    if not validation_path.is_file():
        return {}
    faults = json.loads(validation_path.read_text()).get("faults", {})
    tried = [
        m["examples_tried"]
        for record in faults.values() if record.get("passes")
        for m in (record.get("mutants") or {}).values()
        if m.get("verdict") == "caught" and m.get("examples_tried")
    ]
    return {"n": len(tried), "catch_rate_by_max_examples": catch_rate_curve(tried)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks-path", type=pathlib.Path, default=DATA_DIR / "tasks.json")
    parser.add_argument("--programs", nargs="+", default=None,
                        help="measure exactly these faults instead of the frozen corpus")
    parser.add_argument("--max-examples", type=int, default=80,
                        help="cases the sampling oracle may run per mutant")
    parser.add_argument("--full-pool-cap", type=int, default=300,
                        help="cases the equivalent-mutant second opinion may run")
    parser.add_argument("--sites-per-type", type=int, default=1,
                        help="mutants planted per fault type per task. 1, not 2: "
                             "the second site doubles the runtime and barely moves "
                             "the rate, and a slow task costs 2s x n_cases x 2 x "
                             "(sample + full-pool second opinion)")
    parser.add_argument("--resume", action="store_true",
                        help="skip tasks already present in --out and merge into it")
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS)
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "pool_strength.json")
    args = parser.parse_args()

    if args.programs:
        names = list(args.programs)
        source = "--programs"
    else:
        if not args.tasks_path.is_file():
            raise SystemExit(f"{args.tasks_path} missing - run scripts/validate_oracle.py first")
        frozen = json.loads(args.tasks_path.read_text())
        if not frozen.get("frozen"):
            raise SystemExit(f"{args.tasks_path} is not frozen")
        names = [t["name"] for t in frozen["tasks"]]
        source = str(args.tasks_path)
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        raise SystemExit(f"unknown fault(s): {unknown}")

    print(f"corpus: {len(names)} faults from {source}", flush=True)
    print(f"planting up to {args.sites_per_type} mutants x {len(FAULT_TYPES)} fault types "
          f"per task, judged at max_examples={args.max_examples}", flush=True)

    import concurrent.futures
    t0 = time.monotonic()
    per_task: dict[str, dict] = {}

    # Resume: a task already measured is skipped whole. The unit is the task,
    # not the mutant, because a task's mutants share the planting and the
    # program load - restarting mid-task would redo them anyway.
    if args.resume and args.out.is_file():
        prior = json.loads(args.out.read_text())
        per_task.update(prior.get("per_task", {}))
        print(f"resume: {len(per_task)} task already in {args.out}", flush=True)
    todo = [n for n in names if n not in per_task]
    if not todo:
        print("nothing to do", flush=True)

    def work(name: str) -> tuple[str, dict]:
        return name, measure_task(
            name, max_examples=args.max_examples, seed=args.seed,
            timeout=args.timeout, full_pool_cap=args.full_pool_cap,
            sites_per_type=args.sites_per_type,
        )

    # as_completed, not map: map yields in submission order, so one slow task
    # (a program at 2s per case x 2 x n_cases runs is ~23 minutes on its own)
    # holds back every result behind it and the run looks hung when it is not.
    # Checkpoint after each task, so a kill costs one task rather than all of them.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(work, n): n for n in todo}
        for i, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            name, result = future.result()
            per_task[name] = result
            print(f"[{i:>3}/{len(todo)}] {name:<26} "
                  f"{result['n_caught']} caught · {result['n_missed']} missed · "
                  f"{result['n_equivalent']} equivalent", flush=True)
            args.out.write_text(json.dumps(
                {"complete": False, "n_tasks_measured": len(per_task),
                 "per_task": per_task}, indent=2) + "\n")

    totals = collections.Counter()
    by_type: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    tried: list[int] = []
    for result in per_task.values():
        for m in result["mutants"]:
            totals[m["verdict"]] += 1
            by_type[m["fault_type"]][m["verdict"]] += 1
            if m["verdict"] == "caught" and m.get("examples_tried"):
                tried.append(m["examples_tried"])
    scored = totals["caught"] + totals["missed"] + totals["equivalent"]

    report = {
        "corpus": source,
        "n_tasks": len(names),
        "max_examples": args.max_examples,
        "full_pool_cap": args.full_pool_cap,
        "sites_per_type": args.sites_per_type,
        "seed": args.seed,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "planted": {
            "n_planted": scored,
            "n_caught": totals["caught"],
            "n_missed": totals["missed"],
            "n_equivalent": totals["equivalent"],
            "n_inconclusive": totals["inconclusive"],
            "n_unavailable": totals["unavailable"],
            # The two headline rates. `catch_rate` is about the *sample*;
            # `equivalent_rate` is about the *pool*, and is the number this
            # script exists to produce.
            "catch_rate": round(totals["caught"] / scored, 4) if scored else None,
            "equivalent_rate": round(totals["equivalent"] / scored, 4) if scored else None,
            "catch_rate_by_max_examples": catch_rate_curve(tried),
            "by_fault_type": {t: dict(c) for t, c in sorted(by_type.items())},
        },
        "natural": natural_mutant_curve(DATA_DIR / "oracle_validation.json"),
        "complete": len(per_task) == len(names),
        "per_task": per_task,
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    p = report["planted"]
    print()
    print(f"planted mutants: {p['n_planted']} scored "
          f"({p['n_unavailable']} unavailable, {p['n_inconclusive']} inconclusive)")
    print(f"  caught      {p['n_caught']:>4}  ({p['catch_rate']:.1%})")
    print(f"  missed      {p['n_missed']:>4}   the pool sees it, this sample did not")
    print(f"  equivalent  {p['n_equivalent']:>4}  ({p['equivalent_rate']:.1%})  "
          f"<- no test in the pool distinguishes it")
    print()
    print("catch rate if the oracle had sampled only k cases:")
    for k, rate in p["catch_rate_by_max_examples"].items():
        nat = report["natural"].get("catch_rate_by_max_examples", {}).get(k)
        print(f"  k={k:>3}   planted {rate:>6.1%}" + (f"   natural {nat:>6.1%}" if nat else ""))
    print(f"\nwrote {args.out} in {report['elapsed_sec']:.0f}s")


if __name__ == "__main__":
    main()
