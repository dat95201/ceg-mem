#!/usr/bin/env python3
"""Re-derive every number in paper/draft/tables/policies.tex and
sections/07-results.tex from the P1-1 artifacts, independently of
simulate_policies.py.

The paper's provenance rule is that no number has any origin other than the
frozen log plus an extractor (see paper/draft/Makefile). tab:policies broke
that rule: its numbers are hand-entered from policies.json and the task-level
counts are not in policies.json at all. This script closes the gap. It reads
only the shipped artifacts, recomputes from the per-cell records rather than
from the summary, and exits non-zero on any disagreement.

    python3 scripts/verify_policies.py data/official-2026-09-01

Two known corrections it enforces, both found on 2026-09-07:
  * total executions, task-level, is 2/6/91 - an earlier draft said 2/5/92.
  * the 80th "loss" on the criterion axis differs by 2.2e-16 and is a tie by
    any sane tolerance. We report the exact-equality count (1/18/80), which is
    what the pre-declared script computed, and print the tolerant count beside
    it so the artifact and the truth are both visible.
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import statistics
import sys

POLICIES = ["oracle-k", "qi13", "venugopal20", "dedup-guard", "cegmem-guard"]

# What the paper prints. (exec/ep mean, to-refut. median, oracle/ep, accepts/ep)
PAPER_TABLE = {
    "oracle-k":     (248.4, 2.91, 19.99, 4.10),
    "qi13":         (174.1, 1.44, 19.99, 4.10),
    "venugopal20":  (173.7, 1.38, 19.99, 4.10),
    "dedup-guard":  (180.1, 1.54,  5.85, 4.10),
    "cegmem-guard": (179.7, 1.50,  5.85, 4.10),
}
# What the prose prints.
PAPER_PROSE = {
    "criterion_wtl":        (1, 18, 80),
    "total_exec_wtl":       (2, 6, 91),
    "oracle_wtl":           (99, 0, 0),
    "median_penalty_e2r":   0.046,
    "median_penalty_total": 0.048,
    "blocked_share":        0.708,
    "headroom_ven":         0.846,
    "headroom_guard":       0.835,
    "index_e2r_wtl":        (46, 37, 16),
    "index_total_wtl":      (45, 39, 15),
    "candidates":           5564,
    "case_executions":      221304,
    "sandbox_hours":        111,
    "refute_density_med":   0.58,
    "random_order_e2r":     1.7,
}

FAILURES: list[str] = []


def check(label: str, got, want, tol=0.0) -> None:
    ok = (abs(got - want) <= tol) if isinstance(want, (int, float)) and not isinstance(want, bool) \
        else (got == want)
    print(f"  {'PASS' if ok else 'FAIL'}  {label:46s} got {got!r:>22}  paper {want!r}")
    if not ok:
        FAILURES.append(label)


def wilcoxon_one_sided_less(diffs: list[float]) -> float:
    """P(observed or more extreme | median >= 0). scipy when available; the
    normal approximation with tie correction otherwise, which is what the
    n=99 comparisons here are well inside."""
    nz = [d for d in diffs if d != 0]
    if not nz:
        return 1.0
    try:
        from scipy.stats import wilcoxon  # type: ignore
        return float(wilcoxon(nz, alternative="less").pvalue)
    except Exception:
        pass
    order = sorted(range(len(nz)), key=lambda i: abs(nz[i]))
    ranks = [0.0] * len(nz)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(nz[order[j + 1]]) == abs(nz[order[i]]):
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_minus = sum(r for r, d in zip(ranks, nz) if d < 0)
    n = len(nz)
    mu = n * (n + 1) / 4
    sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (w_minus - mu) / sd if sd else 0.0
    return 0.5 * math.erfc(z / math.sqrt(2))


def task_means(cells: dict, policy: str, field: str) -> dict[str, float]:
    by = collections.defaultdict(list)
    for rec in cells[policy].values():
        if rec[field] is not None:
            by[rec["task"]].append(rec[field])
    return {t: statistics.fmean(v) for t, v in by.items()}


def wtl(a: dict, b: dict, tol: float = 0.0) -> tuple[int, int, int, list[float]]:
    tasks = sorted(set(a) & set(b))
    d = [a[t] - b[t] for t in tasks]
    w = sum(1 for x in d if x < -tol)
    l = sum(1 for x in d if x > tol)
    return w, len(d) - w - l, l, d


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1] if len(argv) > 1 else "data/official-2026-09-01")
    cells = json.loads((root / "policies_cells.json").read_text())
    summary = json.loads((root / "policies.json").read_text())
    matrix = [json.loads(l) for l in (root / "verdicts.jsonl").read_text().splitlines() if l.strip()]
    cases = json.loads((root / "verdicts_cases.json").read_text())

    print("== shape ==")
    for p in POLICIES:
        tasks = {r["task"] for r in cells[p].values()}
        check(f"{p}: cells", len(cells[p]), 495)
        check(f"{p}: tasks", len(tasks), 99)

    print("\n== the five table rows, recomputed from the per-cell records ==")
    for p in POLICIES:
        ex = [r["executions"] for r in cells[p].values()]
        e2r = [r["exec_per_refutation"] for r in cells[p].values()
               if r["exec_per_refutation"] is not None]
        orc = [r["oracle_rounds"] for r in cells[p].values()]
        acc = [r["accepts"] for r in cells[p].values()]
        want = PAPER_TABLE[p]
        check(f"{p}: exec/ep mean", round(statistics.fmean(ex), 1), want[0], 0.05)
        check(f"{p}: to-refut. median", round(statistics.median(e2r), 2), want[1], 0.005)
        check(f"{p}: oracle/ep mean", round(statistics.fmean(orc), 2), want[2], 0.005)
        check(f"{p}: accepts/ep mean", round(statistics.fmean(acc), 2), want[3], 0.005)
        # and against the summary the paper's provenance names
        js = summary["policies"][p]
        check(f"{p}: agrees with policies.json",
              round(statistics.fmean(ex), 4), round(js["executions_per_episode"]["mean"], 4), 5e-4)

    print("\n== identical by construction ==")
    for field in ("accepts", "refuted", "rounds"):
        same = all(cells["oracle-k"][k][field] == cells[p][k][field]
                   for p in POLICIES for k in cells["oracle-k"])
        check(f"all five policies identical on {field}", same, True)
    for field in ("oracle_rounds", "blocked"):
        same = all(cells["cegmem-guard"][k][field] == cells["dedup-guard"][k][field]
                   for k in cells["cegmem-guard"])
        check(f"guard == dedup-guard on {field} (495 cells)", same, True)

    print("\n== task-level comparisons the prose prints ==")
    best = "venugopal20"   # lower is better on both execution axes; verified below
    for p in ("qi13",):
        assert statistics.fmean([r["executions"] for r in cells[best].values()]) <= \
               statistics.fmean([r["executions"] for r in cells[p].values()])

    g_e2r, v_e2r = task_means(cells, "cegmem-guard", "exec_per_refutation"), \
                   task_means(cells, best, "exec_per_refutation")
    w, t, l, d = wtl(g_e2r, v_e2r)
    check("criterion axis, exact ties (the artifact)", (w, t, l), PAPER_PROSE["criterion_wtl"])
    wt, tt, lt, _ = wtl(g_e2r, v_e2r, tol=1e-9)
    print(f"  NOTE  criterion axis with a 1e-9 tolerance: {wt}/{tt}/{lt}"
          f"  (the 80th loss differs by ~2e-16)")
    p_e2r = wilcoxon_one_sided_less(d)
    print(f"  NOTE  one-sided Wilcoxon p = {p_e2r:.4g}   (paper: 1.0, criterion NOT MET)")
    check("criterion met?", bool(w + t >= 60 and p_e2r < 0.05), False)

    g_ex, v_ex = task_means(cells, "cegmem-guard", "executions"), task_means(cells, best, "executions")
    check("total executions, task-level", wtl(g_ex, v_ex)[:3], PAPER_PROSE["total_exec_wtl"])

    g_or, v_or = task_means(cells, "cegmem-guard", "oracle_rounds"), task_means(cells, best, "oracle_rounds")
    w, t, l, d = wtl(g_or, v_or)
    check("oracle calls, task-level", (w, t, l), PAPER_PROSE["oracle_wtl"])
    print(f"  NOTE  oracle-call Wilcoxon p = {wilcoxon_one_sided_less(d):.3g}   (paper: < 1e-17)")

    print("\n== effect sizes ==")
    rel_e2r = statistics.median([(g_e2r[k] - v_e2r[k]) / v_e2r[k] for k in g_e2r if v_e2r[k]])
    rel_ex = statistics.median([(g_ex[k] - v_ex[k]) / v_ex[k] for k in g_ex if v_ex[k]])
    check("median penalty, to refutation", round(rel_e2r, 3), PAPER_PROSE["median_penalty_e2r"], 0.0005)
    check("median penalty, total exec", round(rel_ex, 3), PAPER_PROSE["median_penalty_total"], 0.0005)

    k_e2r = statistics.fmean(list(task_means(cells, "oracle-k", "exec_per_refutation").values()))
    v_m = statistics.fmean(list(v_e2r.values()))
    g_m = statistics.fmean(list(g_e2r.values()))
    check("headroom captured, mp-aware", round((k_e2r - v_m) / (k_e2r - 1.0), 3),
          PAPER_PROSE["headroom_ven"], 0.0005)
    check("headroom captured, guard", round((k_e2r - g_m) / (k_e2r - 1.0), 3),
          PAPER_PROSE["headroom_guard"], 0.0005)
    check("gap, as a share of headroom", round((g_m - v_m) / (k_e2r - 1.0), 3), 0.011, 0.0005)

    rounds = sum(r["rounds"] for r in cells["cegmem-guard"].values())
    blocked = sum(r["blocked"] for r in cells["cegmem-guard"].values())
    check("rounds resolved before the oracle", round(blocked / rounds, 3),
          PAPER_PROSE["blocked_share"], 0.0005)

    print("\n== the index, against its own control ==")
    d_e2r = task_means(cells, "dedup-guard", "exec_per_refutation")
    d_ex = task_means(cells, "dedup-guard", "executions")
    check("guard vs dedup, to refutation", wtl(g_e2r, d_e2r)[:3], PAPER_PROSE["index_e2r_wtl"])
    check("guard vs dedup, total exec", wtl(g_ex, d_ex)[:3], PAPER_PROSE["index_total_wtl"])
    gm = statistics.fmean([r["executions"] for r in cells["cegmem-guard"].values()])
    dm = statistics.fmean([r["executions"] for r in cells["dedup-guard"].values()])
    check("index worth, share of total exec", round(1 - gm / dm, 4), 0.0025, 0.0002)

    print("\n== the matrix, and why the axis affords little ==")
    check("distinct candidates", len(matrix), PAPER_PROSE["candidates"])
    check("every row complete", all(r["complete"] for r in matrix), True)
    check("case executions", sum(r["n_cases"] for r in matrix), PAPER_PROSE["case_executions"])
    check("sandbox hours", round(sum(r["sec"] for r in matrix) / 3600), PAPER_PROSE["sandbox_hours"], 0.5)
    check("tasks in the case index", len(cases), 99)
    dens = [r["n_fail"] / r["n_cases"] for r in matrix if r["n_fail"] > 0]
    check("median refute density", round(statistics.median(dens), 2),
          PAPER_PROSE["refute_density_med"], 0.005)
    check("random-order executions to refutation",
          round(statistics.median([1 / d for d in dens]), 1), PAPER_PROSE["random_order_e2r"], 0.05)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} DISAGREEMENT(S) between the paper and the artifacts:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("every number the paper prints for the policy replay reproduces from the artifacts.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
