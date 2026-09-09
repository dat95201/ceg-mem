#!/usr/bin/env python3
"""The gate between E1 and the arms that cost money, for a second proposer.

WHY A GATE.  The task-level test is a one-sided Wilcoxon signed-rank over tasks,
so its n is the number of tasks that still carry signal - not the number run,
and not the number of cells. With n same-signed non-zero pairs the smallest
achievable one-sided p is 2^-n:

    n = 3   0.125    cannot reach 0.05 at any effect size
    n = 4   0.0625   cannot reach 0.05
    n = 5   0.031    the floor
    n = 8   0.0039

So below 5 usable tasks the arms are unbuyable: no amount of E2/E3 money, and no
number of seeds, can produce a significant result. The gate is declared here
before E1 runs, and reads the count off E1's own rounds.

WHAT COUNTS.  pi_hat is the per-round acceptance rate in the no_memory arm - the
same quantity src/... banded the corpus by - and a task is usable when it lands
in a PRIMARY band (hard/medium/easy, read from the frozen strata file). Only
CONFIRMATORY tasks count: the universe's rule was chosen with the pilot on
screen, so the pilot tasks are reported, never counted toward the gate.

    python3 scripts/second_proposer_gate.py --episodes 'data/episodes_eval_E1_o4-mini_*.jsonl'
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import paths

FLOOR = 5          # declared before E1: below this, E2 and E3 do not run


def pi_hat(patterns: list[str]) -> dict[str, tuple[int, int]]:
    """task -> (rounds, accepted). Last-write-wins per (episode, round)."""
    seen: set[tuple] = set()
    agg: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for pat in patterns:
        for f in glob.glob(os.path.expanduser(pat)):
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (row.get("episode_id"), row.get("round_index"))
                    if key in seen or not row.get("task"):
                        continue
                    seen.add(key)
                    agg[row["task"]][0] += 1
                    # accepted: the oracle found no counterexample, and the round
                    # was not blocked by a guard (no_memory has none, but be exact)
                    if not row.get("counterexample_args") and not row.get("guarded"):
                        agg[row["task"]][1] += 1
    return {t: (n, a) for t, (n, a) in agg.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", nargs="+", required=True,
                    help="glob(s) for the second proposer's E1 episode logs")
    ap.add_argument("--meta", type=pathlib.Path, default=None,
                    help="default: <data dir>/hardend_universe_meta.json")
    ap.add_argument("--strata", type=pathlib.Path,
                    default=pathlib.Path("data/official-2026-09-01/strata.json"))
    ap.add_argument("--floor", type=int, default=FLOOR)
    args = ap.parse_args()

    meta_path = args.meta or (paths.DATA_DIR / "hardend_universe_meta.json")
    if not meta_path.is_file():
        print(f"missing {meta_path} - draw the universe first "
              f"(scripts/build_second_proposer_universe.py)", file=sys.stderr)
        return 2
    meta = json.loads(meta_path.read_text())
    confirmatory = set(meta["confirmatory"])
    pilot = set(meta["pilot"])

    strata = json.loads(args.strata.read_text())
    cuts = strata["bands"]
    primary = set(strata["primary_bands"])

    def band(p: float) -> str:
        for name, (lo, hi) in cuts.items():
            if lo <= p < hi:
                return name
        return max(cuts, key=lambda k: cuts[k][1])

    obs = pi_hat(args.episodes)
    if not obs:
        print("no rounds matched those globs - is E1 finished?", file=sys.stderr)
        return 2

    rows = []
    for t, (n, a) in obs.items():
        p = a / n if n else 0.0
        rows.append((t, p, band(p), n, t in confirmatory, t in pilot))

    dist = collections.Counter(b for _, _, b, _, is_c, _ in rows if is_c for b in [b])
    usable = sorted((t, p, n) for t, p, b, n, is_c, _ in rows if is_c and b in primary)
    pilot_usable = [t for t, _, b, _, _, is_p in rows if is_p and b in primary]

    seen_c = sum(1 for r in rows if r[4])
    print(f"episodes            {len(obs)} tasks with rounds")
    print(f"confirmatory seen   {seen_c} of {len(confirmatory)}"
          + ("   <- E1 is not finished; the gate is premature"
             if seen_c < len(confirmatory) else ""))
    print(f"bands (confirmatory only)  " +
          "  ".join(f"{b}={dist.get(b, 0)}" for b in
                    ("dead", "hard", "medium", "easy", "too_easy")))
    print(f"pilot usable        {len(pilot_usable)}   reported, NOT counted")
    print()
    print(f"USABLE CONFIRMATORY TASKS   n = {len(usable)}   floor = {args.floor}")
    for t, p, n in usable:
        print(f"    pi={p:.3f}  {n:4d} rounds  {t}")
    best = 2.0 ** -len(usable) if usable else 1.0
    print(f"\nsmallest one-sided p reachable at this n: {best:.4g}")
    if len(usable) < args.floor:
        print("STOP - do not run E2 or E3. Report the band shift descriptively; "
              "the task-level test cannot reach p<0.05 at this n.")
        return 1
    print("CONTINUE - run E2, then E3-steer-only, on --universe hardend.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
