"""Statistics: bootstrap CIs, Wilcoxon signed-rank, Vargha-Delaney A12,
Benjamini-Hochberg correction - the protocol the paper already specifies
(Section 5), applied to data/results_real.json instead of the synthetic
model. Produces the Table-2/3-style comparison (oracle calls to repair,
redundant attempts) across the three memory conditions, per stratum and
overall, paired by task.

The four statistical primitives below are pure functions (no file I/O), kept
separate from compare_conditions() so scripts/check_consistency.py can call
the same functions directly on freshly-loaded data instead of trusting this
script's own output file.
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys

import numpy as np
from scipy import stats as scipy_stats

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.select_corpus import BANDS, PRIMARY_BANDS  # noqa: E402

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
MODES = ("no_memory", "untyped", "typed")
def _strata_in_use() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(all strata, strata the BH family corrects across), read off the freeze.

    Hardcoding these was wrong: the corpus can be frozen two ways and they carry
    different labels. scripts/select_corpus.py bands on measured pi and marks
    two of five bands as controls; a rating-floor corpus has no pi measurement
    at all and is split into three proxy terciles, every one of which carries
    the same prediction. Reading data/strata.json keeps the analysis honest
    about which corpus it is analysing instead of asserting one.
    """
    path = DATA_DIR / "strata.json"
    if not path.exists():
        return tuple(name for name, _, _ in BANDS), PRIMARY_BANDS
    blob = json.loads(path.read_text())
    seen = []
    for t in blob.get("tasks", []):
        if t["stratum"] not in seen:
            seen.append(t["stratum"])
    primary = tuple(blob.get("primary_bands") or seen)
    return tuple(seen), primary


STRATA, BH_STRATA = _strata_in_use()


def bootstrap_ci(values: list[float], *, n_resamples: int = 10_000, alpha: float = 0.05, seed: int = 0) -> dict:
    """95% CI on the mean, resampling `values` (one value per task) with replacement.

    `values` is sorted first, and that is load-bearing, not tidiness. The
    resampler draws *indices*, so permuting the input - a dict's .values() built
    in a different insertion order, say - yields a different realization of the
    same bootstrap distribution, and so an interval that differs in the 4th
    decimal, even with the seed pinned. scripts/check_consistency.py recomputes
    these numbers from episodes it re-sorted on the way in; without the sort the
    frozen and recomputed CIs disagree and the consistency check fails on data
    that is perfectly correct. Sorting picks a canonical realization; the
    multiset, hence the distribution being sampled, is untouched.
    """
    arr = np.sort(np.asarray(values, dtype=float))
    if arr.size == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_resamples, arr.size))
    resample_means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(resample_means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"mean": float(arr.mean()), "lo": float(lo), "hi": float(hi), "n": int(arr.size)}


def vargha_delaney_a12(xs: list[float], ys: list[float]) -> float | None:
    """P(X > Y) + 0.5*P(X == Y). 0.5 = no effect; >0.5 means xs tends larger.

    None, not NaN, when either side is empty: this value is written straight
    into data/analysis.json, and json.dumps renders a NaN as a bare `NaN`
    token that only Python's own parser will read back.
    """
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if xs.size == 0 or ys.size == 0:
        return None
    gt = (xs[:, None] > ys[None, :]).sum()
    eq = (xs[:, None] == ys[None, :]).sum()
    return float((gt + 0.5 * eq) / (xs.size * ys.size))


def wilcoxon_paired(xs: list[float], ys: list[float]) -> dict:
    """scipy's paired Wilcoxon signed-rank test; degenerates to p=1.0 when
    every pair is tied (scipy raises on an all-zero difference vector)."""
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if xs.size == 0 or np.allclose(xs, ys):
        return {"statistic": None, "pvalue": 1.0, "n": int(xs.size)}
    stat, p = scipy_stats.wilcoxon(xs, ys)
    return {"statistic": float(stat), "pvalue": float(p), "n": int(xs.size)}


def benjamini_hochberg(pvalues: list[float], *, alpha: float = 0.05) -> list[bool]:
    """Standard BH step-up procedure. Returns a reject/accept flag per input
    p-value, in the original order (not the sorted order used internally)."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    max_rank_ok = -1
    for rank, i in enumerate(order):
        if pvalues[i] <= (rank + 1) / m * alpha:
            max_rank_ok = rank
    reject = [False] * m
    for rank in range(max_rank_ok + 1):
        reject[order[rank]] = True
    return reject


def _is_main_grid(ep: dict) -> bool:
    """The E2 comparison grid.

    force_full_budget has to match the arm: the no-memory arm *is* E1's
    full-budget corpus (see scripts/run_eval.py and scripts/fit_theory.py),
    while the memory arms stop at their first accept. Pinning it here keeps a
    stray second no-memory run - `run_eval.py --modes no_memory` without
    --force-full-budget, say - out of the averages instead of quietly pooling
    two arms that were produced under different stopping rules.
    """
    return (
        ep["guard_on"] and ep["steer_on"]
        and ep["max_examples"] == 100 and ep["typing_noise_c"] == 1.0
        and ep.get("force_full_budget", False) == (ep["mode"] == "no_memory")
    )


def _per_task_means(episodes: list[dict], mode: str, stratum: str | None, metric: str) -> dict[str, float]:
    """task -> mean of `metric` over that task's seeds, for one mode/stratum.

    metric == "oracle_calls_to_accept": averaged only over accepted episodes
    (unrepaired episodes have no verification-round count to report, exactly
    as Table 2's "oracle calls to repair" column is defined). This is the one
    metric the conditioning applies to, and it is why the proposal's Hard band
    needs a co-primary that survives non-acceptance.
    every other metric: averaged over every episode, accepted or not -
    redundant_attempts, guard_evaluations (Prop. 4.5), proposals, and
    success_at_b (Cor. 4.4, the budgeted-success rate).
    """
    by_task: dict[str, list[float]] = {}
    for ep in episodes:
        if ep["mode"] != mode or not _is_main_grid(ep):
            continue
        if stratum is not None and ep.get("stratum") != stratum:
            continue
        if metric == "oracle_calls_to_accept" and not ep["accepted"]:
            continue
        value = ep[metric]
        if value is None:
            continue
        by_task.setdefault(ep["task"], []).append(value)
    return {t: float(np.mean(v)) for t, v in by_task.items() if v}


def compare_conditions(episodes: list[dict], metric: str) -> dict:
    out: dict = {"metric": metric, "strata": {}}
    pending_bh: dict[tuple[str, str], list[tuple[str, float]]] = {}  # (comparison) -> [(stratum, pvalue)]

    for stratum_label in (*STRATA, "overall"):
        stratum = None if stratum_label == "overall" else stratum_label
        per_mode = {mode: _per_task_means(episodes, mode, stratum, metric) for mode in MODES}

        summary = {}
        for mode in MODES:
            # by task name, so the input order does not depend on how the caller
            # happened to order its episodes - see bootstrap_ci's docstring
            summary[mode] = bootstrap_ci([per_mode[mode][t] for t in sorted(per_mode[mode])])

        comparisons = {}
        for a, b in itertools.combinations(MODES, 2):
            shared_tasks = sorted(set(per_mode[a]) & set(per_mode[b]))
            xs = [per_mode[a][t] for t in shared_tasks]
            ys = [per_mode[b][t] for t in shared_tasks]
            wilcoxon = wilcoxon_paired(xs, ys)
            a12 = vargha_delaney_a12(xs, ys)
            comparisons[f"{a}_vs_{b}"] = {"n_tasks": len(shared_tasks), "a12": a12, **wilcoxon}
            if stratum_label in BH_STRATA:
                pending_bh.setdefault((a, b), []).append((stratum_label, wilcoxon["pvalue"]))

        out["strata"][stratum_label] = {"summary": summary, "comparisons": comparisons}

    for (a, b), entries in pending_bh.items():
        strata_labels = [s for s, _ in entries]
        pvalues = [p for _, p in entries]
        rejected = benjamini_hochberg(pvalues)
        for stratum_label, is_significant in zip(strata_labels, rejected):
            out["strata"][stratum_label]["comparisons"][f"{a}_vs_{b}"]["bh_significant"] = is_significant

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-path", type=pathlib.Path, default=DATA_DIR / "results_real.json")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "analysis.json")
    args = parser.parse_args()

    if not args.results_path.exists():
        raise SystemExit(f"{args.results_path} missing - run scripts/freeze_results.py first")
    episodes = json.loads(args.results_path.read_text())["episodes"]

    report = {
        "results_path": str(args.results_path),
        # The proposal's SS5 metric list, in its order. The first two were the
        # only ones computed before; the other three are recoverable from the
        # episode summaries that were already being written, at no extra cost.
        #   oracle_calls_to_accept  Thm 4.3(a), primary cost. Accepted episodes only.
        #   redundant_attempts      Thm 4.3(b). Every episode.
        #   success_at_b            Cor. 4.4, budgeted success. Every episode -
        #                           the co-primary that keeps the low-pi strata
        #                           informative when the round count cannot be
        #                           defined because nothing accepted.
        #   guard_evaluations       Prop. 4.5, the Theta(m) vs O(1) guard cost.
        #                           Predicted to *grow* with difficulty, so it is
        #                           measured best exactly where the round count
        #                           is measured worst.
        #   proposals               model calls, the other budget a practitioner pays.
        "metrics": {
            "oracle_calls_to_accept": compare_conditions(episodes, "oracle_calls_to_accept"),
            "redundant_attempts": compare_conditions(episodes, "redundant_attempts"),
            "success_at_b": compare_conditions(episodes, "success_at_b"),
            "guard_evaluations": compare_conditions(episodes, "guard_evaluations"),
            "proposals": compare_conditions(episodes, "proposals"),
        },
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    for metric, result in report["metrics"].items():
        print(f"\n=== {metric} ===")
        for stratum, data in result["strata"].items():
            means = {m: data["summary"][m]["mean"] for m in MODES}
            print(f"  [{stratum:8s}] " + "  ".join(f"{m}={v}" for m, v in means.items() if v is not None))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
