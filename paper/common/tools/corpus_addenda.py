"""What the corpus record actually says, recomputed from the frozen artifacts.

Nothing here executes a program or calls a model.  Three blocks:

  migration   The band a task was SELECTED into (a 40-call pre-treatment
              screen) against the band it REPORTS in (the no-memory arm, ~100
              rounds).  strata.json records n_moved = 0 and a diagonal
              migration matrix; both are wrong, and this recomputes them.
              The cause is resolution: 40 calls quantise pi-hat to 0.025 while
              the hard band spans [0.02, 0.08), so one success in forty decides
              dead against hard.

  expense     What the slow-reference filter actually left behind.  It
              thresholds the REFERENCE's per-case latency over the first 20
              cases; the cost that matters is per CALL, which is
              (cases run) x (the slower of candidate and reference).

  gradient    The cost reduction among the KEPT faults, split by how expensive
              their oracle is.  This is the evidence for what the dropped
              faults would have shown, in place of an assertion about it.

Usage: python3 corpus_addenda.py <run_dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import pathlib
import statistics
import sys

RUN = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])


def _mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def cfg(r):
    return (r["mode"], r["guard_on"], r["steer_on"], r["force_full_budget"],
            r["max_examples"], r["typing_noise_c"], bool(r.get("typing_random")),
            bool(r.get("free_guarded_rounds")), bool(r.get("audit_guarded")))


ARMS = {
    ("no_memory", True, True, True, 100, 1.0, False, False, False): "no_memory",
    ("typed", True, True, False, 100, 1.0, False, False, False): "typed",
}

out = {"_provenance": {
    "run": str(RUN),
    "rule": "no program was executed and no model was called to produce any "
            "number in this file; every quantity is a recomputation over the "
            "frozen strata.json, tasks.json and episodes.jsonl",
}}

# ── 1. the band migration strata.json denies ──────────────────────────────
strata = json.loads((RUN / "strata.json").read_text())
BANDS = strata["bands"]
ORDER = ["dead", "hard", "medium", "easy", "too_easy"]


def band_of(p):
    for name in ORDER:
        lo, hi = BANDS[name]
        if lo <= p < hi:
            return name
    return ORDER[-1]


mig = collections.Counter()
moved = 0
for t in strata["tasks"]:
    a, b = band_of(t["screen_pi_hat"]), band_of(t["reported_pi_hat"])
    mig[(a, b)] += 1
    moved += a != b

sel = json.loads((RUN / "tasks.json").read_text())
sel = sel["tasks"] if isinstance(sel, dict) and "tasks" in sel else sel
calls = {t.get("screen_calls") for t in sel}

out["migration"] = {
    "n_tasks": len(strata["tasks"]),
    "n_moved_recomputed": moved,
    "n_moved_as_recorded": strata.get("n_moved"),
    "screen_calls_per_task": sorted(c for c in calls if c),
    "screen_resolution": round(1 / max(c for c in calls if c), 4),
    "attainable_screen_values_per_band": {
        b: sum(1 for i in range(max(c for c in calls if c) + 1)
               if BANDS[b][0] <= i / max(c for c in calls if c) < BANDS[b][1])
        for b in ORDER},
    "screen_band_counts": {b: sum(v for (x, _), v in mig.items() if x == b)
                           for b in ORDER},
    "reported_band_counts": {b: sum(v for (_, y), v in mig.items() if y == b)
                             for b in ORDER},
    "matrix_screen_to_reported": {a: {b: mig[(a, b)] for b in ORDER if mig[(a, b)]}
                                  for a in ORDER},
}

# ── 2 & 3. oracle expense among the kept faults, and the cost gradient ────
cell = collections.defaultdict(
    lambda: {"sec": 0.0, "runs": 0.0, "ocalls": [], "done": False})
for line in (RUN / "episodes.jsonl").open():
    r = json.loads(line)
    a = ARMS.get(cfg(r))
    if a is None:
        continue
    d = cell[(a, r["task"], r["seed"])]
    if d["done"]:
        continue                        # cost is counted to first acceptance
    d["sec"] += (r.get("guard_sec") or 0.0) + (r.get("oracle_sec") or 0.0)
    d["runs"] += r.get("sandbox_runs") or 0
    if r.get("oracle_sec") is not None and not r.get("guarded"):
        d["ocalls"].append(r["oracle_sec"])
    if r.get("accept"):
        d["done"] = True

by_task = collections.defaultdict(dict)
osec = collections.defaultdict(list)
for (a, t, s), d in cell.items():
    by_task[t].setdefault(a, []).append(d)
    if a == "no_memory":
        osec[t] += d["ocalls"]

# expense is a property of the FAULT, so it is measured on the no-memory arm
expense = {t: _mean(v) for t, v in osec.items() if v}
worst = {t: max(v) for t, v in osec.items() if v}
tasks = sorted(t for t in expense if "no_memory" in by_task[t] and "typed" in by_task[t])

out["expense"] = {
    "note": "the slow-reference filter dropped a fault if its reference needed "
            ">10 s on one of its first 20 cases; these are the oracle CALLS "
            "the kept corpus still contains",
    "n_tasks": len(worst),
    "slowest_call_sec": round(max(worst.values()), 1),
    "median_of_task_worst_call_sec": round(statistics.median(worst.values()), 2),
    "kept_faults_with_a_call_slower_than": {
        f"{thr}s": sum(1 for v in worst.values() if v > thr)
        for thr in (5, 10, 20, 30, 60)},
    "mean_sec_per_call_min": round(min(expense.values()), 3),
    "mean_sec_per_call_max": round(max(expense.values()), 1),
}


def agg(t, a, k):
    return _mean(d[k] for d in by_task[t][a])


def block(sub, label):
    ns, ts = _mean(agg(t, "no_memory", "sec") for t in sub), _mean(agg(t, "typed", "sec") for t in sub)
    nr, tr = _mean(agg(t, "no_memory", "runs") for t in sub), _mean(agg(t, "typed", "runs") for t in sub)
    return {"label": label, "n_tasks": len(sub),
            "mean_sec_per_oracle_call": round(_mean(expense[t] for t in sub), 2),
            "sandbox_sec_no_memory": round(ns, 2), "sandbox_sec_typed": round(ts, 2),
            "fold_seconds": round(ns / ts, 2) if ts else None,
            "executions_no_memory": round(nr, 2), "executions_typed": round(tr, 2),
            "fold_executions": round(nr / tr, 2) if tr else None}


ranked = sorted(tasks, key=lambda t: expense[t])
med = statistics.median(expense[t] for t in tasks)
n = len(ranked)
out["gradient"] = {
    "median_split": [block([t for t in tasks if expense[t] <= med], "cheap oracle"),
                     block([t for t in tasks if expense[t] > med], "expensive oracle")],
    "terciles": [block(ranked[i * n // 3:(i + 1) * n // 3], f"tercile {i + 1}")
                 for i in range(3)],
    "median_expense_sec_per_call": round(med, 2),
}

# Spearman rank correlation between a fault's oracle expense and the
# per-fault reduction in sandbox seconds, hand-rolled (no scipy here).
pairs = [(expense[t], (agg(t, "no_memory", "sec") + 1e-9) / (agg(t, "typed", "sec") + 1e-9))
         for t in tasks]


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    rk = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            rk[order[k]] = avg
        i = j + 1
    return rk


rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
mx, my = _mean(rx), _mean(ry)
num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
out["gradient"]["spearman_expense_vs_second_reduction"] = round(num / den, 3) if den else None

OUT.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps({k: v for k, v in out.items() if k != "_provenance"}, indent=2))
