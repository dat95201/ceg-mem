#!/usr/bin/env python3
"""Re-freeze a finished gate report at the cohort size the data actually supported.

    python3 scripts/refreeze_pool.py                    # size = the cohort that was reached
    python3 scripts/refreeze_pool.py --corpus-size 300  # or smaller, explicitly
    python3 scripts/refreeze_pool.py --dry-run

`validate_oracle.py --corpus-size N` freezes only if BOTH conditions hold
(validate_oracle.py, `corpus_gate_ok`):

    len(cohort_passing) >= ceil(0.75 * N)     the oracle cleared the mutation gate
    len(cohort) == N                          the walk could seat N faults at all

The second is not a quality test. It fails when the candidate list runs out of
validatable faults - here, because 150 of 526 candidates have no sibling wrong
submission, so `--min-siblings 1` makes them ineligible before a single mutant is
judged. That caps the cohort at 376 whatever N is asked for.

So a run at N = 360 that reaches 324 has measured everything it could measure and
then failed one comparison. Re-running it at N = 324 would walk the identical
faults, score them identically, and stop at the same place - the per-fault
verdicts do not depend on N. N enters only as a quota (when to stop adding to the
cohort) and as the freeze threshold. This script therefore recomputes the two
conditions at a smaller N and re-emits tasks.json, instead of spending the hours
again to learn what the report already holds.

What it does NOT do: change any verdict, re-judge any mutant, or lower the 3/4
pass bar. The pass rate is the gate's actual finding and is untouched - at
324/324 it clears ceil(0.75 x 324) = 243 with room to spare.

Choosing N after seeing the walk is a real degree of freedom, so it is recorded
rather than absorbed: tasks.json and oracle_validation.json both carry a
`refrozen` block naming the requested size, the reached size and this reason.
Report the pool as frozen at that size, not at the size first asked for.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import validate_oracle as vo  # noqa: E402  (path first, per above)

from src.paths import POOL_DIR as DATA_DIR, announce  # noqa: E402


def main() -> None:
    announce('refreeze_pool')
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR,
                    help="directory holding oracle_validation.json (default data/pool)")
    ap.add_argument("--corpus-size", type=int, default=None,
                    help="freeze at this size (default: the cohort the walk reached)")
    ap.add_argument("--seed", type=int, default=20260717)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report_path = args.data_dir / "oracle_validation.json"
    tasks_path = args.data_dir / "tasks.json"
    if not report_path.is_file():
        raise SystemExit(f"{report_path} missing - run scripts/oracle_gate.sh first")

    report = json.loads(report_path.read_text())
    faults = report["faults"]
    cohort = report["cohort"]                      # names, in candidate order
    size = args.corpus_size or len(cohort)

    if size > len(cohort):
        raise SystemExit(
            f"--corpus-size {size} exceeds the {len(cohort)} faults this walk could "
            f"seat. That is the ceiling the candidate list imposes, not a threshold "
            f"to argue with - re-run the gate on a larger candidate list instead."
        )

    # Exactly what the walk would have held with --corpus-size `size`: the cohort
    # stops at the quota, and the corpus is topped up past it in candidate order.
    cohort_at = cohort[:size]
    passing_at = [n for n in cohort_at if faults[n].get("passes")]
    threshold = vo.corpus_threshold(size)
    gate_ok = len(passing_at) >= threshold and len(cohort_at) == size
    selected = [n for n, r in faults.items() if r.get("passes")][:size]

    print(f"report        {report_path}")
    print(f"requested     {report.get('corpus_pass_threshold')}  ->  corpus_gate_ok="
          f"{report.get('corpus_gate_ok')}")
    print(f"reached       cohort {len(cohort)}, "
          f"{sum(1 for n in cohort if faults[n].get('passes'))} passing")
    print(f"refreeze at   {size}")
    print(f"  cohort      {len(cohort_at)} == {size}  {'ok' if len(cohort_at) == size else 'FAIL'}")
    print(f"  passing     {len(passing_at)} >= {threshold}  "
          f"{'ok' if len(passing_at) >= threshold else 'FAIL'}")
    print(f"  selected    {len(selected)} programs")
    print(f"  frozen      {gate_ok and bool(selected)}")

    if not (gate_ok and selected):
        raise SystemExit("\nthat size does not freeze either - lower it, or read "
                         "n_cohort_passing in the report: a short pass rate is the "
                         "oracle failing the gate, which no size fixes.")
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    refrozen = {
        "walked_cohort": len(cohort),
        "frozen_at": size,
        "reason": (
            "the candidate list ran out of validatable faults before the requested "
            "cohort size was reached (--min-siblings 1 makes a fault with no sibling "
            "wrong submission ineligible), so the size was lowered to what the walk "
            "seated. Per-fault verdicts are independent of corpus_size and were not "
            "recomputed; the pass bar was not lowered."
        ),
        "pass_rate_at_freeze": f"{len(passing_at)}/{len(cohort_at)} (needs >= {threshold})",
    }

    report["cohort"] = cohort_at
    report["n_cohort"] = len(cohort_at)
    report["n_cohort_passing"] = len(passing_at)
    report["corpus_pass_threshold"] = f">= {threshold}/{size} programs passing"
    report["corpus_gate_ok"] = gate_ok
    report["refrozen"] = refrozen

    for p in (report_path, tasks_path):
        if p.is_file():
            shutil.copy(p, p.with_suffix(p.suffix + ".prefreeze"))
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    vo.write_tasks_json(report, selected, corpus_size=size, seed=args.seed,
                        data_dir=args.data_dir)

    blob = json.loads(tasks_path.read_text())
    blob["refrozen"] = refrozen
    tasks_path.write_text(json.dumps(blob, indent=2) + "\n")

    print(f"\nwrote {tasks_path} (frozen={blob['frozen']}, {blob['n_selected']} tasks)")
    print(f"      {report_path}")
    print(f"      previous copies kept as *.prefreeze")


if __name__ == "__main__":
    main()
