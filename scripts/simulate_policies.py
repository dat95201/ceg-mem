#!/usr/bin/env python3
"""P1-1, stage 2 - five validation policies over one candidate stream.

Reviews 1 and 3 both ask the same question and it is the one that decides the
novelty boundary: how does the guard compare against the classical mechanisms it
resembles - fault-recorded test prioritization (Qi et al., ICSM'13) and
modification-point aware prioritization (Venugopal et al., 2020)? Until now
Table I answered it by argument. This answers it with the study's own numbers.

The comparison is exact rather than approximate, and the reason is worth stating
because it is what makes the whole thing cheap. In the `no_memory` arm the
proposer is shown nothing the validator found, so the candidate stream is a
function of (task, seed, round) only. It does not depend on which cases the
validator ran, in what order, or whether it stopped early. Five policies can
therefore be replayed over **byte-identical candidates** - no new model calls, no
new sandbox runs beyond the verdict matrix stage 1 already built, and no
appeal to "the arms are comparable because round 1 matched".

The five:

  oracle-k       what the paper reports: a seeded draw of min(k, |pool|) cases,
                 walked in draw order, stopping at the first that separates.
  qi13           fault-recorded prioritization: order cases by how many earlier
                 candidates in this episode each one killed.
  venugopal20    the same, keyed first on the candidate's own edit location, so
                 the ordering knowledge is per-modification-point. This is the
                 mechanism CEGMem's index most resembles.
  dedup-guard    replay every distinct counterexample found so far, in discovery
                 order, before paying the oracle. No index.
  cegmem-guard   the same store, consulted bucket-first by edit location, then
                 the rest. `src.memory.TypedMemory.guard`, offline.

    python3 scripts/simulate_policies.py                    # after stage 1
    python3 scripts/simulate_policies.py --policies qi13 cegmem-guard

PRE-DECLARED CRITERION (written before the matrix was built; see
PRESPEC-2026-09.md): cegmem-guard reaches its first refutation in no more case
executions than the better of qi13 / venugopal20 on at least 60 of the 99 tasks,
with a one-sided task-level Wilcoxon p < 0.05. If that fails, the finding is that
a classical prioritizer matches the guard on this corpus, the novelty claim
narrows to the key and the evidence rather than the saving, and Table I moves
CEGMem into the same cell as Qi'13. Either outcome is reportable; neither is a
reason to leave the comparison out.

WHAT THIS DOES NOT MEASURE. Repair rate is a property of the candidate stream,
which is identical by construction, so no policy can change which patches exist.
What a policy changes is the *cost* of finding out, and - because a shallow draw
can miss a failing case a full-pool order would find - whether a candidate is
accepted at all. Both are reported.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import paths
from src.adapter import load
from src.typer import edit_location

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_verdict_matrix import load_matrix, patch_sha, stream_rounds  # noqa: E402

POLICIES = ("oracle-k", "qi13", "venugopal20", "dedup-guard", "cegmem-guard")
CLASSICAL = ("qi13", "venugopal20")


# ---------------------------------------------------------------------------
# the episode stream
# ---------------------------------------------------------------------------

def cells_from_log(episodes: pathlib.Path, mode: str, depth: int) -> dict[tuple, list[dict]]:
    """(task, seed) -> the round list, in round order.

    Only the reported main-grid cells: one arm, one oracle depth, the full-budget
    accounting E1 ran under. Mixing depths here would put two instruments on one
    axis, which is the error `fix_numbers.py` exists to undo elsewhere.
    """
    cells: dict[tuple, list[dict]] = collections.defaultdict(list)
    rows, sources = stream_rounds(episodes, source_modes=(mode,))
    for row in rows:
        if row.get("mode") != mode:
            continue
        if (row.get("max_examples") or 100) != depth:
            continue
        if row.get("typing_random") or row.get("audit_guarded") or row.get("free_guarded_rounds"):
            continue
        if not row.get("patch_sha"):
            continue
        row["patch"] = sources[row["patch_sha"]]
        cells[(row["task"], row["seed"])].append(row)
    for k in cells:
        cells[k].sort(key=lambda r: r.get("round_index") or 0)
    return dict(cells)


# ---------------------------------------------------------------------------
# the policies
# ---------------------------------------------------------------------------

class Policy:
    """One validation policy, replayed over one cell.

    `round()` returns (executions, refuted_case, accepted, blocked). `executions` counts
    every case run in the sandbox this round, guard replays included - the same
    accounting `src.metrics.RoundRecord.sandbox_runs` uses, so the numbers here
    are comparable with the ones already in the paper.
    """

    name = "?"

    def __init__(self, task: str, seed: int, pool: list[str], usable: list[str],
                 depth: int):
        # `pool` and `usable` come from verdicts_cases.json, not from the adapter:
        # src.adapter.test_cases_for caches the full TEXT of every case and one
        # AtCoder task reaches 84 MB, so a 99-task simulation that touched it
        # would be killed. Names are all a policy orders, so names are all this
        # reads - and the simulation then needs no benchmark checkout.
        self.task, self.seed, self.depth = task, seed, depth
        self.all_cases, self.usable = pool, usable
        self.index = {c: i for i, c in enumerate(usable)}

    def draw(self, round_index: int) -> list[str]:
        """The oracle's own seeded draw, reproduced exactly.

        `src.oracle._sample` returns the pool unchanged when k >= |pool| and a
        `random.Random(seed).sample` otherwise; `src.loop` passes
        seed + round_index. Unusable cases are walked over without being charged,
        exactly as `differential_test` continues past a reference it cannot
        establish.
        """
        cases = self.all_cases
        if self.depth >= len(cases):
            picked = cases
        else:
            picked = random.Random(self.seed + round_index).sample(list(cases), self.depth)
        return [c for c in picked if c in self.index]

    def round(self, round_index: int, sha: str, source: str, fails: set[str]):
        raise NotImplementedError

    @staticmethod
    def _walk(order, fails) -> tuple[int, str | None]:
        for n, case in enumerate(order, start=1):
            if case in fails:
                return n, case
        return len(order), None


class OracleK(Policy):
    name = "oracle-k"

    def round(self, round_index, sha, source, fails):
        n, hit = self._walk(self.draw(round_index), fails)
        return n, hit, hit is None, False


class Qi13(Policy):
    """Fault-recorded testing prioritization (Qi, Mao and Lei, ICSM 2013).

    Order the suite by how often each case has already killed a candidate in
    this episode; ties keep pool order, so round 1 - when nothing has killed
    anything - is plain pool order and the policy adds no magic at the start.
    """

    name = "qi13"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.kills: collections.Counter = collections.Counter()

    def order(self, source):
        return sorted(self.usable, key=lambda c: (-self.kills[c], self.index[c]))

    def round(self, round_index, sha, source, fails):
        n, hit = self._walk(self.order(source), fails)
        if hit is not None:
            self.kills[hit] += 1
        return n, hit, hit is None, False


class Venugopal20(Qi13):
    """Modification-point aware prioritization (Venugopal, Phung and Lee, 2020).

    Kill history is kept per edit location as well as globally, and the
    candidate's own location's history sorts first. This is the closest published
    mechanism to CEGMem's index: same key, but the key orders a *suite* rather
    than a store of counterexamples, and it keeps no evidence across candidates
    beyond the counts.
    """

    name = "venugopal20"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.by_loc: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self._loc = None

    def order(self, source):
        loc = self._loc
        local = self.by_loc[loc]
        return sorted(self.usable, key=lambda c: (-local[c], -self.kills[c], self.index[c]))

    def round(self, round_index, sha, source, fails):
        self._loc = _location(self.task, source)
        n, hit = self._walk(self.order(source), fails)
        if hit is not None:
            self.kills[hit] += 1
            self.by_loc[self._loc][hit] += 1
        return n, hit, hit is None, False


class DedupGuard(Policy):
    """Replay the stored counterexamples, then pay the oracle if none fires.

    The guard half of CEGMem with the index removed, so the difference between
    this and `cegmem-guard` is the index and nothing else.
    """

    name = "dedup-guard"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.store: list[str] = []

    def consult(self, source) -> list[str]:
        return self.store

    def remember(self, source, case) -> None:
        if case not in self.store:
            self.store.append(case)

    def round(self, round_index, sha, source, fails):
        seen: set[str] = set()
        consulted = 0
        for case in self.consult(source):
            if case in seen:
                continue
            seen.add(case)
            consulted += 1
            if case in fails:
                return consulted, case, False, True   # blocked: the oracle is never paid
        n, hit = self._walk(self.draw(round_index), fails)
        if hit is not None:
            self.remember(source, hit)
        return consulted + n, hit, hit is None, False


class CEGMemGuard(DedupGuard):
    """`src.memory.TypedMemory.guard`, offline: bucket first, then the rest.

    The index says where to look FIRST, never where to STOP - the correction
    recorded in `src/memory.py` on 2026-08-31. Consulting the whole store after
    the bucket is what keeps the block sound; the bucket is what makes the common
    case cheap.
    """

    name = "cegmem-guard"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.buckets: dict[str, list[str]] = collections.defaultdict(list)
        self._loc = None

    def consult(self, source):
        self._loc = _location(self.task, source)
        bucket = self.buckets[self._loc]
        rest = [c for c in self.store if c not in set(bucket)]
        return bucket + rest

    def remember(self, source, case):
        super().remember(source, case)
        loc = self._loc if self._loc is not None else _location(self.task, source)
        if case not in self.buckets[loc]:
            self.buckets[loc].append(case)


POLICY_CLASSES = {c.name: c for c in (OracleK, Qi13, Venugopal20, DedupGuard, CEGMemGuard)}

_BUGGY: dict[str, str] = {}
_LOC_MEMO: dict[tuple[str, int], str] = {}


def _location(task: str, source: str) -> str:
    """lambda(p) - the edit location, memoised.

    `edit_location` is a difflib pass over two whole files and the proposer
    repeats itself often (duplicate_patch_rate 0.217), so without the memo the
    diff dominates the simulation's own runtime.
    """
    key = (task, hash(source))
    if key not in _LOC_MEMO:
        if task not in _BUGGY:
            _BUGGY[task] = load(task).buggy_source
        _LOC_MEMO[key] = edit_location(_BUGGY[task], source)
    return _LOC_MEMO[key]


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def wilcoxon_one_sided(diffs: list[float]) -> float:
    """P(cegmem <= other) by signed-rank, normal approximation with a tie
    correction. scipy when it is installed, this when it is not - the repo's
    requirements.txt pins neither, and a missing dependency must not silently
    turn a reported test into no test."""
    try:
        from scipy.stats import wilcoxon  # type: ignore
        nz = [d for d in diffs if d != 0]
        if not nz:
            return 1.0
        return float(wilcoxon(nz, alternative="less").pvalue)
    except Exception:
        pass
    nz = [d for d in diffs if d != 0]
    n = len(nz)
    if n == 0:
        return 1.0
    order = sorted(range(n), key=lambda i: abs(nz[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(nz[order[j + 1]]) == abs(nz[order[i]]):
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_neg = sum(r for r, d in zip(ranks, nz) if d < 0)
    mu = n * (n + 1) / 4
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    if sigma == 0:
        return 1.0
    z = (w_neg - mu - 0.5) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2))


def summarise(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", type=pathlib.Path, default=paths.EPISODES)
    ap.add_argument("--matrix", type=pathlib.Path, default=None,
                    help="default: <data dir>/verdicts.jsonl")
    ap.add_argument("--cases", type=pathlib.Path, default=None,
                    help="default: <data dir>/verdicts_cases.json")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="default: <data dir>/policies.json")
    ap.add_argument("--mode", default="no_memory")
    ap.add_argument("--depth", type=int, default=100)
    ap.add_argument("--policies", nargs="*", default=list(POLICIES))
    args = ap.parse_args()

    paths.announce("policies")
    matrix_path = args.matrix or (paths.DATA_DIR / "verdicts.jsonl")
    cases_path = args.cases or (paths.DATA_DIR / "verdicts_cases.json")
    out_path = args.out or (paths.DATA_DIR / "policies.json")
    for p in (matrix_path, cases_path):
        if not p.is_file():
            print(f"missing {p} - run scripts/build_verdict_matrix.py first", file=sys.stderr)
            return 2

    matrix = load_matrix(matrix_path)
    usable_by_task = json.loads(cases_path.read_text())
    cells = cells_from_log(args.episodes, args.mode, args.depth)
    print(f"cells {len(cells)}   matrix rows {len(matrix)}   tasks {len(usable_by_task)}")

    per_cell: dict[str, dict[str, dict]] = {p: {} for p in args.policies}
    skipped = 0

    for (task, seed), rounds in sorted(cells.items()):
        spec = usable_by_task.get(task)
        if not spec or not spec.get("usable"):
            skipped += 1
            continue
        pool, usable = spec["pool"], spec["usable"]
        shas = [(r.get("round_index") or 0, r["patch_sha"], r["patch"]) for r in rounds]
        if any((task, s) not in matrix for _, s, _ in shas):
            skipped += 1        # an incomplete matrix row would bias every policy alike
            continue

        for pname in args.policies:
            pol = POLICY_CLASSES[pname](task, seed, list(pool), list(usable), args.depth)
            total = first_ref_costs = 0
            refuted = accepted_rounds = blocked = oracle_rounds = 0
            exec_to_accept = None
            for ri, sha, source in shas:
                fails = matrix[(task, sha)]
                n, hit, accepted, was_blocked = pol.round(ri, sha, source, fails)
                total += n
                if hit is not None:
                    refuted += 1
                    first_ref_costs += n
                if accepted:
                    accepted_rounds += 1
                    if exec_to_accept is None:
                        exec_to_accept = total
                if was_blocked:
                    blocked += 1
                else:
                    oracle_rounds += 1
            per_cell[pname][f"{task}|{seed}"] = {
                "task": task, "seed": seed, "rounds": len(shas),
                "executions": total,
                "exec_per_refutation": round(first_ref_costs / refuted, 3) if refuted else None,
                "refuted": refuted, "accepts": accepted_rounds,
                "oracle_rounds": oracle_rounds, "blocked": blocked,
                "exec_to_first_accept": exec_to_accept,
            }

    # ---- aggregate, and the pre-declared test -----------------------------
    report: dict = {
        "provenance": {"episodes": str(args.episodes), "matrix": str(matrix_path),
                       "mode": args.mode, "depth": args.depth,
                       "cells": len(next(iter(per_cell.values()), {})), "skipped_cells": skipped},
        "policies": {},
    }
    for pname in args.policies:
        rows = list(per_cell[pname].values())
        report["policies"][pname] = {
            "cells": len(rows),
            "executions_per_episode": summarise([r["executions"] for r in rows]),
            "exec_per_refutation": summarise([r["exec_per_refutation"] for r in rows
                                              if r["exec_per_refutation"] is not None]),
            "accepts_per_episode": summarise([r["accepts"] for r in rows]),
            "oracle_rounds_per_episode": summarise([r["oracle_rounds"] for r in rows]),
        }

    if "cegmem-guard" in args.policies and any(c in args.policies for c in CLASSICAL):
        by_task_ours: dict[str, list[float]] = collections.defaultdict(list)
        by_task_best: dict[str, list[float]] = collections.defaultdict(list)
        for key, row in per_cell["cegmem-guard"].items():
            if row["exec_per_refutation"] is None:
                continue
            others = [per_cell[c][key]["exec_per_refutation"] for c in CLASSICAL
                      if c in per_cell and key in per_cell[c]
                      and per_cell[c][key]["exec_per_refutation"] is not None]
            if not others:
                continue
            by_task_ours[row["task"]].append(row["exec_per_refutation"])
            by_task_best[row["task"]].append(min(others))
        tasks = sorted(by_task_ours)
        diffs = [statistics.fmean(by_task_ours[t]) - statistics.fmean(by_task_best[t])
                 for t in tasks]
        wins = sum(1 for d in diffs if d < 0)
        ties = sum(1 for d in diffs if d == 0)
        p = wilcoxon_one_sided(diffs)
        report["criterion"] = {
            "statement": ("cegmem-guard <= best classical on exec-to-first-refutation, "
                          ">=60 of 99 tasks and one-sided task-level Wilcoxon p<0.05"),
            "n_tasks": len(tasks),
            "wins": wins, "ties": ties, "losses": len(tasks) - wins - ties,
            "wins_or_ties": wins + ties,
            "p_one_sided": round(p, 6),
            "met": bool((wins + ties) >= 60 and p < 0.05),
        }

    paths.ensure(paths.DATA_DIR)
    out_path.write_text(json.dumps(report, indent=1))
    (out_path.parent / "policies_cells.json").write_text(json.dumps(per_cell, indent=1))

    print()
    print(f"{'policy':14s} {'exec/episode':>13s} {'exec/refutation':>16s} "
          f"{'oracle rounds':>14s} {'accepts':>8s}")
    for pname in args.policies:
        s = report["policies"][pname]
        print(f"{pname:14s} {s['executions_per_episode'].get('mean', 0):13.2f} "
              f"{s['exec_per_refutation'].get('mean', 0):16.3f} "
              f"{s['oracle_rounds_per_episode'].get('mean', 0):14.2f} "
              f"{s['accepts_per_episode'].get('mean', 0):8.3f}")
    if "criterion" in report:
        c = report["criterion"]
        print(f"\ncriterion: {c['wins_or_ties']}/{c['n_tasks']} tasks, "
              f"p={c['p_one_sided']} -> {'MET' if c['met'] else 'NOT MET'}")
        if not c["met"]:
            print("  -> the narrowing paragraph in PRESPEC-2026-09.md is the one that ships.")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
