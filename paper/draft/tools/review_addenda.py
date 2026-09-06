"""Recomputations a reviewer asked for that numbers.json did not carry.

Six blocks, all from the same frozen run and the same dedup rule as
extract_numbers.py / figdata.py:

  attrition          526 candidates -> 99 corpus, per stage, with reasons
  test_work          total sandbox executions split into guard-side and
                     oracle-side, per arm -- the metric that makes "avoids
                     execution" auditable rather than rhetorical
  task_level         every headline claim recomputed with the TASK as the
                     inferential unit, plus task-clustered bootstrap CIs
  zero_failure_ci    exact one-sided Clopper-Pearson upper bounds for the
                     audits that observed zero failures (0/n is not 0 risk)
  audit_denoms       why the regression audit has a smaller denominator than
                     the overfitting audit
  free_guarded       the missing cells, and the analysis restricted to cells
                     present in every arm

Usage: python3 review_addenda.py <run_dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import random
import sys

RUN = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])

results = json.loads((RUN / "results_real.json").read_text())


# ── shared identity rules (must match extract_numbers.py exactly) ──────────
def cfg(e):
    return (e["mode"], e["guard_on"], e["steer_on"], e["force_full_budget"],
            e["max_examples"], e["typing_noise_c"], bool(e.get("typing_random")),
            bool(e.get("free_guarded_rounds")), bool(e.get("audit_guarded")))


groups = collections.defaultdict(list)
for e in results["episodes"]:
    groups[(e["task"], e["seed"]) + cfg(e)].append(e)
dedup = {k: min(v, key=lambda e: e["episode_id"]) for k, v in groups.items()}


def arm(mode, *, guard=True, steer=True, ffb=None, k=100, c=1.0, rand=False,
        fgr=False, audit=False):
    if ffb is None:
        ffb = (mode == "no_memory") and not fgr
    return {(kk[0], kk[1]): e for kk, e in dedup.items()
            if cfg(e) == (mode, guard, steer, ffb, k, c, rand, fgr, audit)}


NM = arm("no_memory")
UN = arm("untyped")
TY = arm("typed")
GO = arm("typed", steer=False)
SO = arm("typed", guard=False)


# ── statistics, hand-rolled (no scipy on the device) ──────────────────────
def sign_test(pairs):
    """Exact two-sided sign test on (a, b): wins = a < b (a cheaper)."""
    w = sum(1 for a, b in pairs if a < b)
    l = sum(1 for a, b in pairs if a > b)
    n = w + l
    if n == 0:
        return {"wins": w, "losses": l, "ties": len(pairs) - n, "p": 1.0}
    k = min(w, l)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return {"wins": w, "losses": l, "ties": len(pairs) - n,
            "p": round(min(1.0, 2 * tail), 6)}


def wilcoxon(pairs):
    """Exact two-sided signed-rank for n<=20, normal approximation above."""
    d = [a - b for a, b in pairs if a != b]
    n = len(d)
    if n == 0:
        return {"n": 0, "p": 1.0}
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        r = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[order[t]] = r
        i = j + 1
    wp = sum(r for r, x in zip(ranks, d) if x > 0)
    wm = sum(r for r, x in zip(ranks, d) if x < 0)
    w = min(wp, wm)
    if n <= 20:                      # exact enumeration over sign vectors
        cnt = 0
        for mask in range(1 << n):
            s = sum(ranks[i] for i in range(n) if mask >> i & 1)
            if s <= w:
                cnt += 1
        p = min(1.0, 2 * cnt / (1 << n))
    else:
        mu = n * (n + 1) / 4
        sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
        z = (w - mu + 0.5) / sd
        p = min(1.0, 2 * 0.5 * math.erfc(-z / math.sqrt(2)))
    return {"n": n, "w": w, "p": round(p, 6)}


def a12(xs, ys):
    """Vargha-Delaney: P(x < y) + 0.5 P(x = y), i.e. xs is cheaper."""
    if not xs or not ys:
        return None
    lt = eq = 0
    for x in xs:
        for y in ys:
            if x < y:
                lt += 1
            elif x == y:
                eq += 1
    return round((lt + 0.5 * eq) / (len(xs) * len(ys)), 4)


def cluster_boot(by_task, stat, b=10000, seed=20260905):
    """Task-clustered bootstrap CI. by_task maps task -> list of per-cell
    values (all seeds); resampling whole tasks is what keeps seeds of one
    task from being treated as independent replications."""
    rng = random.Random(seed)
    tasks = list(by_task)
    if not tasks:
        return None
    point = stat([v for t in tasks for v in by_task[t]])
    draws = []
    for _ in range(b):
        pick = [tasks[rng.randrange(len(tasks))] for _ in tasks]
        vals = [v for t in pick for v in by_task[t]]
        if vals:
            draws.append(stat(vals))
    draws.sort()
    lo = draws[int(0.025 * len(draws))]
    hi = draws[int(0.975 * len(draws)) - 1]
    return {"point": round(point, 4), "ci95": [round(lo, 4), round(hi, 4)],
             "n_tasks": len(tasks), "n_cells": sum(len(v) for v in by_task.values())}


def mean(xs):
    """Mean over the non-missing values. Some frozen fault records carry a
    null difficulty rating; dropping them beats crashing and beats silently
    treating a missing rating as zero."""
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else 0.0


def cp_upper(n, alpha=0.05):
    """Clopper-Pearson one-sided upper bound for 0 observed failures in n."""
    return round(1 - alpha ** (1 / n), 6) if n else None


out = {"provenance": {"run": str(RUN),
                      "note": "same dedup and arm filters as extract_numbers.py"}}

# ── 1. corpus attrition ───────────────────────────────────────────────────
pool = json.loads((RUN / "pool" / "oracle_validation.json").read_text())
faults = pool["faults"]
tasks99 = {t["name"] for t in json.loads((RUN / "tasks.json").read_text())["tasks"]}

reasons = collections.Counter()
for name, f in faults.items():
    if not f.get("usable"):
        reasons[f.get("reason") or "unrecorded"] += 1
stage = {
    "stage1_examined": pool["n_examined"],
    "stage2_usable": pool["n_usable"],
    "stage2_ineligible": pool["n_ineligible"],
    "stage2_unaccounted": pool["n_examined"] - pool["n_usable"] - pool["n_ineligible"],
    "ineligible_reasons": dict(reasons.most_common()),
    "n_eligible_flag": sum(1 for f in faults.values() if f.get("eligible")),
    "stage3_cohort": pool["n_cohort"],
    "stage3_cohort_passing": pool["n_cohort_passing"],
    "mutant_gate": pool["task_pass_threshold"],
    "corpus_gate": pool["corpus_pass_threshold"],
    "n_slow_excluded": pool["n_slow"],
    "reference_timeout_sec": pool["reference_timeout_sec"],
    "stage4_corpus": len(tasks99),
    "usability_criterion": pool["usability_criterion"],
}
# what the slow filter removed, and whether it was selective on difficulty
slow = [f for f in faults.values() if f.get("slow")]
incorpus = [f for name, f in faults.items() if name in tasks99]
stage["slow_profile"] = {
    "n": len(slow),
    "mean_reference_sec_max_slow": round(mean([f["reference_sec_max"] for f in slow]), 3) if slow else None,
    "mean_reference_sec_max_corpus": round(mean([f["reference_sec_max"] for f in incorpus]), 3),
    "mean_n_test_cases_slow": round(mean([f["n_test_cases"] for f in slow]), 1) if slow else None,
    "mean_n_test_cases_corpus": round(mean([f["n_test_cases"] for f in incorpus]), 1),
    "mean_difficulty_slow": round(mean([f["difficulty"] for f in slow]), 1) if slow else None,
    "mean_difficulty_corpus": round(mean([f["difficulty"] for f in incorpus]), 1),
    "note": "the slow filter is arm-independent but not difficulty-independent; "
            "these are the two distributions a reviewer should see",
}
out["attrition"] = stage

# ── 2. total test work, split guard vs oracle ─────────────────────────────
# sandbox_runs per episode already includes guard replays (src/loop.py bills
# guard_evaluations + self-test runs + oracle examples on every row). The
# guard side is charged at ENTRIES CONSULTED, so the typed arms are billed for
# lookups their per-case memo/dedup did not actually execute: the split below
# is conservative against the typed arms.
work = {}
for name, A in (("no_memory", NM), ("untyped", UN), ("typed", TY),
                ("guard_only", GO), ("steer_only", SO)):
    if not A:
        continue
    tot = [e["sandbox_runs"] for e in A.values()]
    grd = [e["guard_evaluations"] for e in A.values()]
    work[name] = {
        "n_cells": len(A),
        "total_executions": round(mean(tot), 3),
        "guard_side": round(mean(grd), 3),
        "oracle_side": round(mean(tot) - mean(grd), 3),
        "guard_share": round(mean(grd) / mean(tot), 4) if mean(tot) else 0.0,
        "guard_sec": round(mean([e.get("guard_sec") or 0.0 for e in A.values()]), 3),
        "oracle_sec": round(mean([e.get("oracle_sec") or 0.0 for e in A.values()]), 3),
    }
common = sorted(set(NM) & set(UN) & set(TY))
for a, b in (("no_memory", "typed"), ("no_memory", "untyped"), ("untyped", "typed")):
    A = {"no_memory": NM, "untyped": UN, "typed": TY}[a]
    B = {"no_memory": NM, "untyped": UN, "typed": TY}[b]
    pairs = [(B[c]["sandbox_runs"], A[c]["sandbox_runs"]) for c in common]
    work[f"{a}_vs_{b}_total_executions"] = {
        "fold": round(mean([A[c]["sandbox_runs"] for c in common]) /
                      mean([B[c]["sandbox_runs"] for c in common]), 3),
        "sign": sign_test(pairs),
    }
out["test_work"] = work

# ── 3. task as the inferential unit + clustered CIs ───────────────────────
def per_task(A, field, agg=mean):
    d = collections.defaultdict(list)
    for (t, s), e in A.items():
        v = e.get(field)
        if v is not None:
            d[t].append(v)
    return {t: agg(v) for t, v in d.items()}


def by_task_cells(A, fn):
    d = collections.defaultdict(list)
    for (t, s), e in A.items():
        v = fn(e)
        if v is not None:
            d[t].append(v)
    return d


tl = {}
CONTRASTS = [
    ("oracle_calls_nomem_vs_typed", NM, TY, "n_oracle_calls"),
    ("oracle_calls_nomem_vs_untyped", NM, UN, "n_oracle_calls"),
    ("oracle_calls_untyped_vs_typed", UN, TY, "n_oracle_calls"),
    ("total_executions_nomem_vs_typed", NM, TY, "sandbox_runs"),
    ("redundancy_paid_nomem_vs_typed", NM, TY, "redundancy_paid"),
    ("guard_sec_untyped_vs_guardonly", UN, GO, "guard_sec"),
    ("wall_sec_untyped_vs_guardonly", UN, GO, "wall_sec"),
    ("tokens_in_nomem_vs_steeronly", NM, SO, "tokens_in"),
]
for label, A, B, field in CONTRASTS:
    ta, tb = per_task(A, field), per_task(B, field)
    keys = sorted(set(ta) & set(tb))
    pairs = [(tb[k], ta[k]) for k in keys]          # (treatment, reference)
    cells = sorted(set(A) & set(B))
    tl[label] = {
        "unit_task": {"n_tasks": len(keys), "sign": sign_test(pairs),
                      "wilcoxon": wilcoxon(pairs),
                      "mean_reference": round(mean([ta[k] for k in keys]), 4),
                      "mean_treatment": round(mean([tb[k] for k in keys]), 4),
                      "a12": a12([tb[k] for k in keys], [ta[k] for k in keys])},
        "unit_cell": {"n_cells": len(cells),
                      "sign": sign_test([(B[c].get(field) or 0, A[c].get(field) or 0)
                                         for c in cells])},
    }
    # clustered CI on the paired per-cell difference
    diff = collections.defaultdict(list)
    for c in cells:
        va, vb = A[c].get(field), B[c].get(field)
        if va is not None and vb is not None:
            diff[c[0]].append(vb - va)
    tl[label]["paired_diff_ci95_task_clustered"] = cluster_boot(diff, mean)

# repair at a matched oracle budget, task-clustered
for b in (2, 3, 4, 8):
    for name, A in (("no_memory", NM), ("untyped", UN), ("typed", TY)):
        key = f"repair_at_oracle_budget_{b}_{name}"
        tl[key] = cluster_boot(
            by_task_cells(A, lambda e: 1.0 if (e.get("oracle_calls_to_accept")
                                               and e["oracle_calls_to_accept"] <= b) else 0.0),
            mean)
    dnm = collections.defaultdict(list)
    for c in common:
        f = lambda e: 1.0 if (e.get("oracle_calls_to_accept")
                              and e["oracle_calls_to_accept"] <= b) else 0.0
        dnm[c[0]].append(f(TY[c]) - f(NM[c]))
    tl[f"repair_at_oracle_budget_{b}_typed_minus_nomem"] = cluster_boot(dnm, mean)
out["task_level"] = tl

# ── 4/5. the audits: exact upper bounds, and the two denominators ────────
# The audit rows must be joined to their EPISODE's oracle depth, not to a
# max_examples field on the row: rows from the k=3/8/20 sweep otherwise fall
# into the k=100 bucket and drag the shallow oracle's false acceptances with
# them. This is the join extract_numbers.py uses, and it is what makes 1,256
# rather than 1,472 the right denominator.
overfit = [json.loads(l) for l in (RUN / "overfit_checks.jsonl").open() if l.strip()]
ep_depth = {e["episode_id"]: e["max_examples"] for e in dedup.values()}
k100 = [r for r in overfit if ep_depth.get(r["episode_id"]) == 100]
n_ov = sum(1 for r in k100 if r.get("overfit"))
regress = [r for r in k100 if r.get("regression_rate") is not None]
n_broken = sum(1 for r in regress if (r.get("p2p_broken") or 0) > 0)
strength = json.loads((RUN / "pool_strength.json").read_text())
planted = strength["planted"]
n_planted_scored = planted["n_caught"] + planted["n_missed"]


def bound(n, failures):
    """Only a zero-failure count gets the closed-form bound; anything else
    would be a different (and wrong) interval, so we refuse rather than
    print one."""
    return {"n": n, "failures": failures,
            "upper95": cp_upper(n) if failures == 0 else None,
            "note": None if failures == 0 else
                    "non-zero failures: use a full Clopper-Pearson interval"}


out["zero_failure_ci"] = {
    "note": "one-sided 95% Clopper-Pearson upper bound. Zero observed failures "
            "bounds the rate; it does not establish that the rate is zero.",
    "overfitting_k100": bound(len(k100), n_ov),
    "regression_k100": bound(len(regress), n_broken),
    "planted_mutants_missed": {
        **bound(n_planted_scored, planted["n_missed"]),
        "n_equivalent_excluded": planted["n_equivalent"],
        "catch_rate_by_depth": planted["catch_rate_by_max_examples"]},
}

out["audit_denoms"] = {
    "k100_audited_accepts": len(k100),
    "k100_overfit": n_ov,
    "regression_audited": len(regress),
    "not_regression_audited": len(k100) - len(regress),
    "no_p2p_cases": sum(1 for r in k100 if not r.get("p2p_total")),
    "regression_inconclusive": sum(1 for r in k100 if r.get("regression_inconclusive")),
    "explanation": "the regression audit reports a rate only for an accepted "
                   "patch that has at least one shipped case the buggy version "
                   "already passed; a fault whose pool contains no such case "
                   "cannot regress and carries no regression verdict.",
}

# ── 6. free-guarded rounds: missingness and matched subset ───────────────
fg = {}
for name, mode, kw in (("no_memory", "no_memory", {}),
                       ("untyped", "untyped", {}),
                       ("typed", "typed", {})):
    base = arm(mode, **kw)
    free = arm(mode, fgr=True, **kw)
    inter = sorted(set(base) & set(free))
    fg[name] = {
        "n_base_cells": len(base), "n_free_cells": len(free),
        "n_matched": len(inter),
        "free_cells_missing_a_base_twin": sorted(f"{t}|{s}" for t, s in set(free) - set(base)),
        "matched": {
            "success_base": round(mean([1.0 if base[c]["accepted"] else 0.0 for c in inter]), 4),
            "success_free": round(mean([1.0 if free[c]["accepted"] else 0.0 for c in inter]), 4),
            "oracle_base": round(mean([base[c]["n_oracle_calls"] for c in inter]), 3),
            "oracle_free": round(mean([free[c]["n_oracle_calls"] for c in inter]), 3),
            "flips_free_better": sum(1 for c in inter
                                     if free[c]["accepted"] and not base[c]["accepted"]),
            "flips_base_better": sum(1 for c in inter
                                     if base[c]["accepted"] and not free[c]["accepted"]),
        },
    }
# the free-guarded grid was run on a 60-cell target per arm
fg["target_cells_per_arm"] = 60
fg["note"] = ("free-guarded cells exist only where the condition's own run "
              "completed; the base twin is the same task/seed WITHOUT the "
              "free-round accounting, so a matched comparison uses only cells "
              "present in both")
out["free_guarded"] = fg

OUT.write_text(json.dumps(out, indent=1) + "\n")
print(f"wrote {OUT}")
print(f"attrition   526 -> usable {stage['stage2_usable']} -> cohort "
      f"{stage['stage3_cohort']} -> corpus {stage['stage4_corpus']} "
      f"(slow removed {stage['n_slow_excluded']}, unaccounted {stage['stage2_unaccounted']})")
for k, v in work.items():
    if isinstance(v, dict) and "total_executions" in v:
        print(f"test work   {k:11s} total {v['total_executions']:7.2f} "
              f"= guard {v['guard_side']:6.2f} + oracle {v['oracle_side']:7.2f} "
              f"(guard share {v['guard_share']:.1%})")
print("audits: ", {k: (v["failures"], v["n"], v["upper95"])
                    for k, v in out["zero_failure_ci"].items() if isinstance(v, dict)})
for k in ("oracle_calls_nomem_vs_typed", "guard_sec_untyped_vs_guardonly",
          "oracle_calls_untyped_vs_typed"):
    d = tl[k]["unit_task"]
    print(f"task-level  {k:34s} n={d['n_tasks']:3d} "
          f"sign {d['sign']['wins']}/{d['sign']['losses']} p={d['sign']['p']:.4g} "
          f"wilcoxon p={d['wilcoxon']['p']:.4g}")
