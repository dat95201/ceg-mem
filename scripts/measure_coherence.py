"""C1 + waste rate: does theta(p) carve out a real behavioral class, and how
much of a transcript agent's budget is burned re-proposing one it already hit?

Two measurements, both computed only from the no_memory corpus in
data/episodes.jsonl (E1) - no extra API calls:

  cross-refutation rate ("does same-type mean same-counterexample?")
    For every pair of refuted no_memory attempts on the *same task* that
    theta() assigned the *same* failure type, re-run attempt A's stored
    counterexample test case against attempt B's patch (and vice versa) via
    src.sandbox.run_program, and check whether it *still* diverges from the
    expected output. Each direction is one Bernoulli trial. This is the practical,
    checkable proxy for whether a real (imperfect) type function behaves like
    the paper's idealized theta - the formal model splits that into oracle
    informativeness rho (Def. 3.1) and typing coherence c; on real data
    without a ground-truth type there is only one thing to check, so this
    script reports it as "cross-refutation rate" and leaves the rho/c
    attribution to the write-up, not the code.

  waste rate ("how often does no-memory repeat a type it already refuted?")
    Within one episode, the fraction of refuted rounds whose theta-assigned
    type already appears among that episode's earlier refuted rounds - i.e.
    exactly the rounds a typed guard would have blocked for free. This is C2.

Both are computed at coarse and fine granularity separately, per task and
pooled, with a task-level bootstrap CI on the pooled rate (pairs within a
task are correlated, so resampling trials directly would understate the CI).

Writes data/coherence_report.json.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import TASKS, load
from src.metrics import DEFAULT_METRICS_LOG, load_rounds
from src.oracle import outputs_equal
from src.sandbox import run_program

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
GRANULARITIES = ("coarse", "fine")
DEFAULT_MAX_PAIRS_PER_TYPE = 30


def _type_key(row: dict, granularity: str) -> str | None:
    return row[f"{granularity}_type"]


def _case(task_name: str, args: list):
    """The stored counterexample's test case. `args` is [case name]; see
    src.oracle.OracleResult."""
    if not args:
        return None
    return TASKS[task_name].case(args[0])


def _reference_value(task_name: str, args: list) -> str | None:
    """Expected output for a stored counterexample: the shipped out/ file, or
    the reference implementation run if that case has none."""
    case = _case(task_name, args)
    if case is None:
        return None
    if case.expected_output is not None:
        return case.expected_output
    outcome = run_program(load(task_name).correct_source, case.input_text)
    return outcome.value if outcome.ok else None


def _still_diverges(task_name: str, patch: str, args: list, ref_value) -> bool:
    case = _case(task_name, args)
    if case is None:
        return False
    cand = run_program(patch, case.input_text)
    if cand.timed_out or not cand.ok:
        return True
    return not outputs_equal(cand.value, ref_value)


def _bootstrap_ci(pooled_by_task: list[tuple[int, int]], n_resamples: int, rng: random.Random) -> tuple[float, float]:
    """95% CI on the pooled rate, resampling at the *task* level.

    pooled_by_task: list of (successes, trials) per task.
    """
    tasks_with_trials = [(s, t) for s, t in pooled_by_task if t > 0]
    if not tasks_with_trials:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(n_resamples):
        sample = [tasks_with_trials[rng.randrange(len(tasks_with_trials))] for _ in tasks_with_trials]
        s_sum = sum(s for s, _ in sample)
        t_sum = sum(t for _, t in sample)
        means.append(s_sum / t_sum if t_sum else float("nan"))
    means = sorted(m for m in means if m == m)  # drop NaN
    if not means:
        return (float("nan"), float("nan"))
    lo = means[int(0.025 * len(means))]
    hi = means[min(int(0.975 * len(means)), len(means) - 1)]
    return (lo, hi)


def cross_refutation_rate(rows: list[dict], granularity: str, *, max_pairs_per_type: int, rng: random.Random):
    """For each (task, type) bucket with >=2 refuted attempts, sample pairs and
    check both cross-refutation directions. Returns (pooled_rate, ci, per_task)."""
    by_bucket: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        tkey = _type_key(row, granularity)
        if tkey is None:
            continue
        by_bucket[(row["task"], tkey)].append(row)

    per_task_trials: dict[str, list[bool]] = collections.defaultdict(list)
    ref_cache: dict[tuple[str, tuple], object] = {}

    for (task_name, _tkey), members in by_bucket.items():
        if len(members) < 2:
            continue
        pairs = list(itertools.combinations(range(len(members)), 2))
        if len(pairs) > max_pairs_per_type:
            pairs = rng.sample(pairs, max_pairs_per_type)
        for i, j in pairs:
            a, b = members[i], members[j]
            for src, dst in ((a, b), (b, a)):
                args = src["counterexample_args"]
                cache_key = (task_name, json.dumps(args, sort_keys=True))  # args may nest lists - not hashable as-is
                if cache_key not in ref_cache:
                    # _reference_value already unwraps the Outcome and returns
                    # the expected stdout (or None when the case has no
                    # establishable answer) - it is a str, not an Outcome.
                    ref_cache[cache_key] = _reference_value(task_name, args)
                ref_value = ref_cache[cache_key]
                if ref_value is None:
                    continue  # no expected output for this case - not a valid comparison point
                held = _still_diverges(task_name, dst["patch"], args, ref_value)
                per_task_trials[task_name].append(held)

    per_task = {
        t: {"successes": sum(v), "trials": len(v), "rate": sum(v) / len(v) if v else None}
        for t, v in per_task_trials.items()
    }
    total_s = sum(d["successes"] for d in per_task.values())
    total_t = sum(d["trials"] for d in per_task.values())
    pooled = total_s / total_t if total_t else None
    ci = _bootstrap_ci([(d["successes"], d["trials"]) for d in per_task.values()], 10_000, rng)
    return pooled, ci, per_task


def waste_rate(rows: list[dict], granularity: str) -> tuple[float | None, dict]:
    """Fraction of refuted no_memory rounds whose type already appeared earlier
    in the same episode - the rounds a typed guard would have blocked for free."""
    by_episode: dict[str, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_episode[row["episode_id"]].append(row)

    wasted, total = 0, 0
    per_task: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for ep_rows in by_episode.values():
        ep_rows.sort(key=lambda r: r["round_index"])
        seen_types: set[str] = set()
        for row in ep_rows:
            if row["accept"]:
                continue
            tkey = _type_key(row, granularity)
            if tkey is None:
                continue
            total += 1
            per_task[row["task"]][1] += 1
            if tkey in seen_types:
                wasted += 1
                per_task[row["task"]][0] += 1
            seen_types.add(tkey)

    pooled = wasted / total if total else None
    per_task_out = {t: {"wasted": w, "total": n, "rate": w / n if n else None} for t, (w, n) in per_task.items()}
    return pooled, per_task_out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--max-pairs-per-type", type=int, default=DEFAULT_MAX_PAIRS_PER_TYPE)
    parser.add_argument("--seed", type=int, default=20260717)
    args = parser.parse_args()

    all_rows = [r for r in load_rounds(args.episodes_path) if r["mode"] == "no_memory"]
    refuted = [r for r in all_rows if not r["accept"] and not r.get("guarded", False)]
    if not refuted:
        raise SystemExit(
            f"no refuted no_memory rounds found in {args.episodes_path} - "
            "run scripts/run_eval.py --modes no_memory --force-full-budget first"
        )

    rng = random.Random(args.seed)
    report: dict = {"episodes_path": str(args.episodes_path), "seed": args.seed, "granularities": {}}

    for granularity in GRANULARITIES:
        pooled_xr, ci_xr, per_task_xr = cross_refutation_rate(
            refuted, granularity, max_pairs_per_type=args.max_pairs_per_type, rng=rng,
        )
        pooled_waste, per_task_waste = waste_rate(refuted, granularity)
        report["granularities"][granularity] = {
            "cross_refutation_rate": {"pooled": pooled_xr, "ci95": list(ci_xr), "per_task": per_task_xr},
            "waste_rate": {"pooled": pooled_waste, "per_task": per_task_waste},
        }
        print(f"[{granularity}] cross-refutation rate = {pooled_xr} (95% CI {ci_xr})")
        print(f"[{granularity}] waste rate (redundant re-proposal) = {pooled_waste}")

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / "coherence_report.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
