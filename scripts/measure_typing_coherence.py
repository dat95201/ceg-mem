"""Typing coherence, measured rather than assumed: how well does theta(p) carve
out the classes a *complete* oracle would carve out?

**No model calls.** Every patch scored here was already proposed and logged;
this script only re-executes them against the full test pool. Sandbox time, no
money.

Why this exists
---------------
Def. 3.1's coherence `c` is the parameter every result depends on, and the E5
sweep varies it by *injecting* noise - which measures how the method degrades
under a hypothetical c, not what c actually is. Section 9 names real type
coherence as the single decisive open quantity. scripts/measure_coherence.py
gets partway there with a cross-refutation rate but says outright that it
cannot separate rho from c, because on real data there is no ground-truth type
to compare against.

There is one here, and it costs nothing but CPU.

The ground truth: behavioral signature
--------------------------------------
For a refuted patch p, let

    sigma(p) = the set of pool test cases p fails

Two patches with the same sigma are indistinguishable to a complete oracle:
no test in the benchmark separates them. sigma therefore induces the finest
partition any test-based notion of "same failure" could justify, and it is
exactly the role ground-truth root cause plays in the crash-deduplication
literature (Igor, Semantic Crash Bucketing) - with the advantage that it is
computable here instead of hand-labelled.

Scoring theta against sigma is then the standard clustering comparison, and
the two directions are the two failure modes that literature named:

  homogeneity(theta | sigma)   1 - H(sigma|theta)/H(sigma)
      Low means UNDER-counting: one theta bucket mixes patches that fail
      different tests. This is the direction that hurts - it is what makes an
      elimination unsound, because refuting one member does not refute the
      bucket. Reported as `c_hat`: it is the closest operational analogue of
      Def. 3.1's "attributes to the correct class with probability c".

  completeness(theta | sigma)  1 - H(theta|sigma)/H(theta)
      Low means OVER-counting: one real failure class is split across several
      theta buckets. This wastes elimination but is not unsound - the method
      just saves less than it could.

  V-measure                    harmonic mean of the two
  adjusted Rand index          agreement corrected for chance, which the two
                               entropies are not - report it whenever the
                               bucket counts differ a lot between the two
                               partitions, which here they will.

`c_hat` is an operationalisation of `c`, not `c` itself, and the write-up has
to say so: Def. 3.1's c is a per-attribution probability, this is a partition
statistic. What it does establish is a measured floor for how coherent this
theta is on real failures, in place of a Dirichlet assumption.

Caps, stated rather than silent
-------------------------------
Running every logged patch against every pool case is quadratic in the worst
case (~8.6k patches x ~50 cases on a full grid). Both axes are capped, the caps
are CLI flags, and what was dropped is written into the report - a truncated
measurement reported as complete is worse than no measurement.

Writes data/typing_coherence.json.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import TASKS, load  # noqa: E402
from src.metrics import DEFAULT_METRICS_LOG, load_rounds  # noqa: E402
from src.oracle import outputs_equal  # noqa: E402
from src.sandbox import run_program  # noqa: E402

from src.paths import DATA_DIR, announce  # noqa: E402
GRANULARITIES = ("coarse", "fine")


# ── clustering comparison ───────────────────────────────────────────────────
# Written out rather than imported from sklearn: this project's requirements.txt
# is numpy/scipy/matplotlib plus the LLM client, and adding a dependency for
# four entropies would be a poor trade. The formulas are the standard ones
# (Rosenberg & Hirschberg 2007 for V-measure, Hubert & Arabie 1985 for ARI).

def _entropy(labels: list) -> float:
    n = len(labels)
    if n <= 1:
        return 0.0
    counts = collections.Counter(labels)
    return -sum((c / n) * math.log(c / n) for c in counts.values())


def _conditional_entropy(a: list, b: list) -> float:
    """H(a | b)."""
    n = len(a)
    if n <= 1:
        return 0.0
    joint = collections.Counter(zip(a, b))
    b_counts = collections.Counter(b)
    return -sum((cnt / n) * math.log(cnt / b_counts[bv])
                for (_av, bv), cnt in joint.items())


def homogeneity_completeness(theta_labels: list, sigma_labels: list) -> dict:
    """(homogeneity, completeness, v_measure) of theta against sigma."""
    h_sigma, h_theta = _entropy(sigma_labels), _entropy(theta_labels)
    # The 1.0-by-vacuity convention is sklearn's and it is kept, but it is
    # POISON for the headline mean and has to be flagged rather than averaged.
    # h_sigma == 0 means every scored patch on this task fails the same set of
    # cases: sigma carries no information at all, so the task scores a perfect
    # 1.0 no matter what theta did. On a weak pool that is the common case, and
    # it is the case measure_pool_strength.py exists to quantify. 60 vacuous
    # tasks averaged with 40 genuinely-measured ones at 0.45 reports 0.78.
    vacuous_sigma, vacuous_theta = h_sigma == 0, h_theta == 0
    homogeneity = 1.0 if vacuous_sigma else 1 - _conditional_entropy(sigma_labels, theta_labels) / h_sigma
    completeness = 1.0 if vacuous_theta else 1 - _conditional_entropy(theta_labels, sigma_labels) / h_theta
    v = (0.0 if homogeneity + completeness == 0
         else 2 * homogeneity * completeness / (homogeneity + completeness))
    return {"homogeneity": homogeneity, "completeness": completeness, "v_measure": v,
            # True = this score is a convention, not a measurement.
            "vacuous_sigma": vacuous_sigma, "vacuous_theta": vacuous_theta}


def adjusted_rand_index(a: list, b: list) -> float:
    """Hubert & Arabie's ARI. 0 = chance, 1 = identical partitions."""
    n = len(a)
    if n < 2:
        return 1.0
    joint = collections.Counter(zip(a, b))
    a_counts, b_counts = collections.Counter(a), collections.Counter(b)
    c2 = lambda x: x * (x - 1) / 2  # noqa: E731
    index = sum(c2(v) for v in joint.values())
    exp_a, exp_b = sum(c2(v) for v in a_counts.values()), sum(c2(v) for v in b_counts.values())
    expected = exp_a * exp_b / c2(n)
    maximum = (exp_a + exp_b) / 2
    return 1.0 if maximum == expected else (index - expected) / (maximum - expected)


def random_baseline(theta_labels: list, sigma_labels: list, *, rng,
                    n_draws: int = 200) -> dict:
    """What homogeneity a partition with theta's SHAPE gets by chance.

    This is the number that makes c_hat mean anything. `homogeneity = 0.4` is
    uninterpretable on its own: how much of it is theta knowing something about
    failure, and how much is arithmetic - a partition with many small classes
    scores high against almost any sigma. So theta's own class-size profile is
    shuffled over the same items, `n_draws` times, and the mean is reported
    beside the real score. c_hat below its null is theta doing worse than a coin
    with the same number of buckets.
    """
    labels = list(theta_labels)
    scores = []
    for _ in range(n_draws):
        rng.shuffle(labels)
        scores.append(homogeneity_completeness(labels, sigma_labels)["homogeneity"])
    return {"homogeneity_null_mean": sum(scores) / len(scores),
            "homogeneity_null_max": max(scores),
            "n_draws": n_draws}


def competing_bucketings(patch_rows: list[dict], sigma_labels: list) -> dict:
    """Cheap alternative partitions, scored the same way as theta.

    theta beating chance is necessary and not sufficient. The question a
    reviewer asks is whether theta beats the obvious thing you would do without
    a type lattice - so two of those are scored on the identical items:

      first_case   bucket by the NAME of the counterexample that refuted it.
                   Free, and roughly what a crash-deduplication baseline gets.
      property     bucket by theta's `property` half alone (exception class /
                   Timeout / WrongValue), dropping the edit location. Isolates
                   how much of theta's information is the location half - which
                   is the half the c-sweep noises.

    If theta does not beat both of these, the lattice is not carrying its keep.
    """
    def score(labels):
        hc = homogeneity_completeness(labels, sigma_labels)
        return {"n_classes": len(set(labels)), "homogeneity": hc["homogeneity"],
                "v_measure": hc["v_measure"],
                "adjusted_rand_index": adjusted_rand_index(labels, sigma_labels)}
    first_case = [(r.get("counterexample_args") or [None])[0] for r in patch_rows]
    # FailureType.key is "granularity::task::location::property"
    prop = [(r.get("fine_type") or "::::").split("::")[-1] for r in patch_rows]
    return {"first_case_name": score(first_case), "property_only": score(prop)}


# ── the ground-truth signature ──────────────────────────────────────────────

def behavioral_signature(task_name: str, patch: str, cases, reference_outputs: dict) -> frozenset | None:
    """The set of case names `patch` fails. None if it fails none of them.

    None means the pool cannot refute this patch at all - it is either correct
    or lands in an equivalent-mutant hole (scripts/measure_pool_strength.py
    measures how large those holes are). Either way it has no failure class,
    so it is excluded from the partition rather than given an empty one.
    """
    failing = []
    for case in cases:
        expected = reference_outputs.get(case.name)
        if expected is None:
            continue   # the reference itself could not run this case; not a verdict
        got = run_program(patch, case.input_text)
        if got.timed_out or not got.ok or not outputs_equal(got.value, expected):
            failing.append(case.name)
    return frozenset(failing) if failing else None


def _reference_outputs(program, cases) -> dict:
    out = {}
    for case in cases:
        res = run_program(program.correct_source, case.input_text)
        if res.ok and not res.timed_out:
            out[case.name] = res.value
    return out


def main() -> None:
    announce('measure_typing_coherence')
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "typing_coherence.json")
    parser.add_argument("--max-patches-per-task", type=int, default=200,
                        help="cap on refuted patches scored per task (default 200, "
                             "the figure the plan specifies). Sampled with a fixed "
                             "seed and the drop count reported")
    parser.add_argument("--max-cases-per-task", type=int, default=60,
                        help="cap on pool cases used to build sigma (default 60). "
                             "A smaller pool makes sigma coarser, so c_hat is an "
                             "UPPER bound on coherence at this cap - reported with it")
    parser.add_argument("--modes", nargs="+", default=["no_memory"],
                        help="which arms to draw patches from (default: no_memory, "
                             "the unbiased corpus - its prompt never changes, so its "
                             "refuted patches are an i.i.d. sample of what the "
                             "proposer produces rather than of what steering left)")
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--limit-tasks", type=int, default=None,
                        help="score only the first N tasks; for a quick pilot")
    args = parser.parse_args()

    rows = load_rounds(args.episodes_path)
    if not rows:
        raise SystemExit(f"{args.episodes_path} is empty or missing - run the grid first")

    # Refuted, non-guarded rounds that carry a patch and a type. A guarded round
    # has no type outside an --audit-guarded cell, and an accepted one has none
    # by definition (theta's top element).
    candidates = [
        r for r in rows
        if r["mode"] in args.modes and not r["accept"] and not r.get("guarded", False)
        and r.get("patch") and r.get("fine_type") and r.get("coarse_type")
        and not r.get("oracle_error") and not r.get("proposal_error")
    ]
    by_task: dict[str, list[dict]] = {}
    for r in candidates:
        by_task.setdefault(r["task"], []).append(r)

    task_names = sorted(by_task)
    if args.limit_tasks:
        task_names = task_names[:args.limit_tasks]

    rng = random.Random(args.seed)
    per_task: list[dict] = []
    pooled: dict[str, list] = {"coarse": [], "fine": [], "sigma": []}
    dropped_patches = dropped_cases = 0
    t0 = time.time()

    for n, task_name in enumerate(task_names, 1):
        if task_name not in TASKS:
            continue
        task = TASKS[task_name]
        program = load(task_name)
        cases = list(task.test_cases)
        if not cases:
            continue
        if len(cases) > args.max_cases_per_task:
            dropped_cases += len(cases) - args.max_cases_per_task
            cases = rng.sample(cases, args.max_cases_per_task)

        patches = by_task[task_name]
        # Dedupe on the patch text first: the same source proposed twice is one
        # data point about theta, not two, and leaving both in would let a
        # proposer that repeats itself inflate whichever score it happens to hit.
        seen: dict[str, dict] = {}
        for r in patches:
            seen.setdefault(r["patch"], r)
        patches = list(seen.values())
        if len(patches) > args.max_patches_per_task:
            dropped_patches += len(patches) - args.max_patches_per_task
            patches = rng.sample(patches, args.max_patches_per_task)

        refs = _reference_outputs(program, cases)
        if not refs:
            continue   # the reference cannot run this task's pool; nothing to compare to

        coarse, fine, sigma, scored_rows = [], [], [], []
        n_unrefuted = 0
        for r in patches:
            sig = behavioral_signature(task_name, r["patch"], cases, refs)
            if sig is None:
                n_unrefuted += 1
                continue
            coarse.append(r["coarse_type"])
            fine.append(r["fine_type"])
            sigma.append(sig)
            scored_rows.append(r)

        if len(sigma) < 2:
            continue

        entry = {"task": task_name, "n_patches": len(sigma), "n_cases": len(cases),
                 "n_unrefuted_by_pool": n_unrefuted,
                 "n_sigma_classes": len(set(sigma))}
        for gran, labels in (("coarse", coarse), ("fine", fine)):
            entry[gran] = {
                "n_theta_classes": len(set(labels)),
                **homogeneity_completeness(labels, sigma),
                "adjusted_rand_index": adjusted_rand_index(labels, sigma),
                **random_baseline(labels, sigma, rng=rng),
            }
        entry["competing"] = competing_bucketings(scored_rows, sigma)
        per_task.append(entry)
        pooled["coarse"] += coarse
        pooled["fine"] += fine
        pooled["sigma"] += sigma

        print(f"[{n:4d}/{len(task_names)}] {task_name:28s} "
              f"{len(sigma):3d} patches, {len(set(sigma)):3d} sigma-classes, "
              f"fine c_hat={entry['fine']['homogeneity']:.3f} "
              f"({time.time() - t0:.0f}s)", flush=True)

    if not per_task:
        raise SystemExit("no task produced two or more pool-refuted patches - "
                         "nothing to score theta against")

    def _pool_stats(gran: str) -> dict:
        return {
            "n_patches": len(pooled[gran]),
            "n_theta_classes": len(set(pooled[gran])),
            "n_sigma_classes": len(set(pooled["sigma"])),
            **homogeneity_completeness(pooled[gran], pooled["sigma"]),
            "adjusted_rand_index": adjusted_rand_index(pooled[gran], pooled["sigma"]),
        }

    # Per-task means, not just the pooled figure: pooling mixes tasks whose
    # class structures have nothing to do with each other, and a task with many
    # patches would dominate. The pooled number is reported too, labelled.
    # Vacuous tasks are excluded from the mean and counted, not averaged in.
    # A task whose sigma has one class scores 1.0 by convention regardless of
    # theta, so including it measures the pool's weakness and reports it as
    # coherence.
    measured = [t for t in per_task if t["n_sigma_classes"] > 1]
    n_vacuous = len(per_task) - len(measured)

    def _mean(gran: str, field: str) -> float | None:
        vals = [t[gran][field] for t in measured if t[gran].get(field) is not None]
        return (sum(vals) / len(vals)) if vals else None

    report = {
        "episodes_path": str(args.episodes_path),
        "modes": args.modes,
        "ground_truth": "behavioral signature: the set of pool cases a patch fails",
        "caps": {
            "max_patches_per_task": args.max_patches_per_task,
            "max_cases_per_task": args.max_cases_per_task,
            "patches_dropped_to_cap": dropped_patches,
            "cases_dropped_to_cap": dropped_cases,
            # The previous note claimed a coarser sigma "can only RAISE
            # homogeneity", so c_hat was an upper bound. That is false and
            # falsifiable in three lines: theta=[A,A,A,B,B] against
            # sigma=[1,1,2,3,3] scores 0.638, and merging sigma's classes 2 and
            # 3 takes it to 0.433. Over random 8-item partitions, coarsening
            # LOWERED homogeneity in about 60% of cases. The cap biases c_hat in
            # an unknown direction, which is why it is reported rather than
            # characterised - and why a narrow-signature patch surviving the cap
            # matters: at 60 of 148 cases a patch refuted by a single case
            # survives with probability 0.41, and those are the patches carrying
            # the most partition information.
            "note": "the cap biases c_hat in an UNKNOWN direction - a coarser "
                    "sigma raises homogeneity on some partitions and lowers it "
                    "on others. Re-run with larger caps to see which way it "
                    "moves on this corpus; do not assume a bound.",
            # Recorded because they change the number: --limit-tasks is a cap
            # like any other and the seed selects which patches and cases were
            # sampled. Without them a 20-task pilot and a full run are
            # indistinguishable in the output, and both write to --out.
            "limit_tasks": args.limit_tasks,
            "seed": args.seed,
            "modes": args.modes,
        },
        "n_tasks_scored": len(per_task),
        "n_tasks_in_mean": len(measured),
        "n_tasks_vacuous_sigma": n_vacuous,
        "vacuity_note": ("tasks whose behavioural partition has a single class score "
                         "1.0 by convention no matter what theta did, so they are "
                         "counted here and kept OUT of per_task_mean"),
        "elapsed_sec": round(time.time() - t0, 1),
        "per_task_mean": {
            gran: {
                "c_hat": _mean(gran, "homogeneity"),
                "completeness": _mean(gran, "completeness"),
                "v_measure": _mean(gran, "v_measure"),
                "adjusted_rand_index": _mean(gran, "adjusted_rand_index"),
            } for gran in GRANULARITIES
        },
        "pooled": {gran: _pool_stats(gran) for gran in GRANULARITIES},
        "per_task": per_task,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n")

    print(f"\n{len(per_task)} tasks scored, {len(pooled['fine'])} patches\n")
    print(f"{'granularity':12s} {'c_hat':>8s} {'complete':>9s} {'V':>7s} {'ARI':>7s}")
    print("-" * 47)
    for gran in GRANULARITIES:
        m = report["per_task_mean"][gran]
        print(f"{gran:12s} {m['c_hat']:>8.3f} {m['completeness']:>9.3f} "
              f"{m['v_measure']:>7.3f} {m['adjusted_rand_index']:>7.3f}")
    print("\nc_hat is homogeneity of theta against the behavioral signature - the "
          "operational\nstand-in for Def. 3.1's c, not c itself. Low c_hat = one theta "
          "bucket mixes\npatches that fail different tests, which is the direction that "
          "makes an\nelimination unsound.")
    if dropped_patches or dropped_cases:
        print(f"\nCAPPED: {dropped_patches} patches and {dropped_cases} cases were "
              f"dropped to the caps.\nReport them with the number.")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
