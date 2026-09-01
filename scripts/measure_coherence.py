"""C1 + waste rate: does theta(p) carve out a real behavioral class, and how
much of a repair agent's budget is burned re-proposing one it already hit?

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
import os
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import TASKS, load
from src.metrics import DEFAULT_METRICS_LOG, load_rounds
from src.oracle import outputs_equal
from src.sandbox import run_program

from src.paths import DATA_DIR, announce  # noqa: E402
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


def _hms(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 3600:d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class _Progress:
    """Line-per-interval progress for a job whose size is known up front.

    This measurement re-executes one logged patch against one shipped test case
    per trial, so it is hours of sandbox time on a corpus of this size and it
    prints nothing at all until it finishes. That is indistinguishable from a
    hang on Colab, where stdout is buffered and the cell just sits there. Every
    print here is flushed for that reason.
    """

    def __init__(self, label: str, total: int, every: float):
        self.label, self.total, self.every = label, total, every
        self.done = 0
        self.runs = 0
        self.start = time.time()
        self.last = 0.0

    def tick(self, *, pairs: int = 0, runs: int = 0, force: bool = False) -> bool:
        """True when this tick printed a line - the caller's cue to checkpoint.

        Returning it, rather than taking a callback, keeps the partial write in
        the function that owns the partial data and keeps this class about
        printing.
        """
        self.done += pairs
        self.runs += runs
        if self.every <= 0:
            return False
        now = time.time()
        if not force and now - self.last < self.every:
            return False
        self.last = now
        elapsed = now - self.start
        frac = self.done / self.total if self.total else 1.0
        # No pair has finished yet on the first line, so there is no rate to
        # extrapolate from. Print it anyway - on Colab it is the only evidence
        # the cell is running at all - but do not invent an ETA for it.
        eta = _hms(elapsed / frac - elapsed) if frac > 0 else "  ?  "
        print(f"  [{self.label}] {self.done:6d}/{self.total} pairs {frac * 100:5.1f}%  "
              f"runs {self.runs:6d}  elapsed {_hms(elapsed)}  eta {eta}",
              flush=True)
        return True

    def finish(self) -> None:
        if self.every > 0:
            self.tick(force=True)
        print(f"  [{self.label}] done in {_hms(time.time() - self.start)} "
              f"({self.runs} sandbox runs)", flush=True)


def plan(rows: list[dict], granularity: str, *, max_pairs_per_type: int) -> tuple[int, int]:
    """(buckets with >=2 members, pairs after the cap) - the size of the job
    below, computed without running anything so the caller can print it first
    and decide whether to lower --max-pairs-per-type before committing hours."""
    by_bucket: dict[tuple[str, str], int] = collections.Counter()
    for row in rows:
        tkey = _type_key(row, granularity)
        if tkey is not None:
            by_bucket[(row["task"], tkey)] += 1
    buckets = pairs = 0
    for n in by_bucket.values():
        if n < 2:
            continue
        buckets += 1
        pairs += min(n * (n - 1) // 2, max_pairs_per_type)
    return buckets, pairs


def _summarise(per_task_trials: dict) -> tuple[float | None, dict]:
    """(pooled rate, per-task rates) from the trials accumulated so far.

    Split out of the tail of cross_refutation_rate so a checkpoint mid-run and
    the final result are computed by the same code - a partial report that used
    a second, simpler formula would be a partial report nobody could trust.
    """
    per_task = {
        t: {"successes": sum(v), "trials": len(v), "rate": sum(v) / len(v) if v else None}
        for t, v in per_task_trials.items()
    }
    total_s = sum(d["successes"] for d in per_task.values())
    total_t = sum(d["trials"] for d in per_task.values())
    return (total_s / total_t if total_t else None), per_task


def cross_refutation_rate(rows: list[dict], granularity: str, *, max_pairs_per_type: int,
                          rng: random.Random, progress: "_Progress | None" = None,
                          checkpoint=None):
    """For each (task, type) bucket with >=2 refuted attempts, sample pairs and
    check both cross-refutation directions. Returns (pooled_rate, ci, per_task).

    `checkpoint(pooled, per_task, done, total)` is called on every printed
    progress line. This measurement is hours of sandbox time; writing only at
    the end means a run killed at hour three - by a Colab timeout, an OOM, a
    closed laptop - leaves nothing at all. The interval is --progress-every, so
    the same flag governs how often it prints and how much work is at risk.
    The bootstrap CI is deliberately NOT computed for a checkpoint: it is 10,000
    resamples and would dominate the interval it is meant to fit inside.
    """
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
                if progress is not None and progress.tick(runs=1) and checkpoint is not None:
                    pooled, per_task = _summarise(per_task_trials)
                    checkpoint(pooled, per_task, progress.done, progress.total)
            if progress is not None and progress.tick(pairs=1) and checkpoint is not None:
                pooled, per_task = _summarise(per_task_trials)
                checkpoint(pooled, per_task, progress.done, progress.total)

    if progress is not None:
        progress.finish()
    pooled, per_task = _summarise(per_task_trials)
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
    announce('measure_coherence')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--max-pairs-per-type", type=int, default=DEFAULT_MAX_PAIRS_PER_TYPE,
                        help="cap on pairs sampled per (task, type) bucket. Cost is close to "
                             "linear in this; the reported CI is not, because _bootstrap_ci "
                             "resamples at the TASK level, so its width is set by the number "
                             "of tasks and barely moves with the cap. Lower it freely.")
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--progress-every", type=float, default=15.0, metavar="SEC",
                        help="seconds between progress lines; 0 turns them off")
    parser.add_argument("--sec-per-run", type=float, default=0.97, metavar="SEC",
                        help="only used to estimate the runtime printed before the job starts. "
                             "0.97 is oracle_sec/sandbox_runs over the frozen no_memory arm.")
    parser.add_argument("--plan-only", action="store_true",
                        help="print the size of the job and the free waste rates, then stop")
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

    # The waste rate is pure bookkeeping over the log - no sandbox, no seconds.
    # Compute and print it FIRST: it is half the deliverable, and burying it
    # behind hours of cross-refutation meant a run that died at hour five
    # produced nothing at all.
    waste: dict = {}
    print(f"{len(refuted)} refuted no_memory rounds in {args.episodes_path}", flush=True)
    for granularity in GRANULARITIES:
        pooled_waste, per_task_waste = waste_rate(refuted, granularity)
        waste[granularity] = (pooled_waste, per_task_waste)
        print(f"[{granularity}] waste rate (redundant re-proposal) = {pooled_waste}", flush=True)

    # Then say how big the expensive half is, before spending any of it.
    print(flush=True)
    total_pairs = 0
    for granularity in GRANULARITIES:
        buckets, pairs = plan(refuted, granularity, max_pairs_per_type=args.max_pairs_per_type)
        total_pairs += pairs
        print(f"[plan] {granularity:6s} {buckets:5d} buckets with >=2 attempts, "
              f"{pairs:6d} pairs after the cap, up to {pairs * 2:6d} sandbox runs", flush=True)
    est = total_pairs * 2 * args.sec_per_run
    print(f"[plan] total up to {total_pairs * 2} sandbox runs, about {_hms(est)} at "
          f"{args.sec_per_run:.2f} s/run. Lower --max-pairs-per-type "
          f"(now {args.max_pairs_per_type}) to cut it.", flush=True)
    print(flush=True)
    if args.plan_only:
        return

    out_path = DATA_DIR / "coherence_report.json"

    def write(blob: dict) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # Written to a sibling and renamed: os.replace is atomic on both POSIX
        # and Windows, so a checkpoint interrupted mid-write cannot leave a
        # half-flushed JSON file where the finished report used to be.
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(blob, indent=2) + "\n")
        os.replace(tmp, out_path)

    for granularity in GRANULARITIES:
        _, pairs = plan(refuted, granularity, max_pairs_per_type=args.max_pairs_per_type)
        progress = _Progress(granularity, pairs, args.progress_every)
        pooled_waste_g, per_task_waste_g = waste[granularity]

        def checkpoint(pooled, per_task, done, total, _g=granularity,
                       _w=(pooled_waste_g, per_task_waste_g)):
            """Same shape as the finished report, plus a `partial` block saying
            how far in it is. A reader that ignores `partial` still parses it;
            one that checks knows not to quote the number."""
            report["granularities"][_g] = {
                "cross_refutation_rate": {"pooled": pooled, "ci95": None,
                                          "per_task": per_task},
                "waste_rate": {"pooled": _w[0], "per_task": _w[1]},
                "partial": {"pairs_done": done, "pairs_total": total,
                            "note": "checkpoint, not a result - ci95 is not "
                                    "computed until the granularity finishes"},
            }
            report["partial"] = True
            write(report)

        pooled_xr, ci_xr, per_task_xr = cross_refutation_rate(
            refuted, granularity, max_pairs_per_type=args.max_pairs_per_type, rng=rng,
            progress=progress, checkpoint=checkpoint,
        )
        # Complete: the `partial` block goes away and the CI appears, which is
        # how a reader tells a finished granularity from a checkpointed one.
        report["granularities"][granularity] = {
            "cross_refutation_rate": {"pooled": pooled_xr, "ci95": list(ci_xr), "per_task": per_task_xr},
            "waste_rate": {"pooled": pooled_waste_g, "per_task": per_task_waste_g},
        }
        report["partial"] = any("partial" in g for g in report["granularities"].values()) \
            or len(report["granularities"]) < len(GRANULARITIES)
        write(report)
        print(f"[{granularity}] cross-refutation rate = {pooled_xr} (95% CI {ci_xr})", flush=True)

    report["partial"] = False
    write(report)
    print(f"wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
