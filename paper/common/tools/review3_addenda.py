"""Statistics Review 3 asked for that the frozen log already contains.

Nothing here executes a program or calls a model: every number is a
recomputation over runs/<date>/episodes.jsonl, so it costs no sandbox runs and
regenerates from the shipped artifact alone.  Arm identity is the cfg() rule of
review_addenda.py, so a round counted here is a round counted there.

  seeds          Whether the five seeds of a task are independent draws or
                 identifiers into one cached completion.  src/llm.py sends no
                 sampling seed to the backend -- the seed only enters the cache
                 key -- so the draws should differ; this measures whether they
                 do.

  interaction    A formal difficulty-by-steering interaction test at the TASK
                 unit, replacing the paper's five band-wise p-values.  Bands
                 come from pi-hat recomputed here the way sec:design describes
                 it: the no-memory arm run past its first accept.

  free_guarded   A cluster permutation test for the pooled free-guarded
                 result, which pools two arms that share tasks.  Permuting the
                 free/base label for a whole task at once respects that
                 dependence; the sign test in the paper does not.

  test_work      Total sandbox SECONDS, guard-side and oracle-side, to sit
                 beside the execution counts already reported.

Usage: python3 review3_addenda.py <run_dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import random
import statistics
import sys


def _mean(xs):
    """statistics.mean goes through Fraction and dominates the
    permutation loops; these are plain floats. math.fsum, not sum: fsum is
    exactly rounded, so the result does not depend on the Python version
    (3.12 changed sum() to compensated summation) or the platform."""
    xs = list(xs)
    return math.fsum(xs) / len(xs) if xs else 0.0


# Permutation p-values count a permuted statistic that TIES the observed one as
# at least as extreme. The per-task effects are means of small integers, so ties
# are exact in rational arithmetic and frequent; a last-bit rounding difference
# must not decide them (it did: 0.0075 vs 0.0098 for the same data on two
# machines before this tolerance existed).
TIE = 1e-9

RUN = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])
RNG = random.Random(20260906)
NPERM = 20000
NBOOT = 10000


def cfg(r):
    return (r["mode"], r["guard_on"], r["steer_on"], r["force_full_budget"],
            r["max_examples"], r["typing_noise_c"], bool(r.get("typing_random")),
            bool(r.get("free_guarded_rounds")), bool(r.get("audit_guarded")))


ARMS = {
    ("no_memory", True, True, True, 100, 1.0, False, False, False): "no_memory",
    ("untyped", True, True, False, 100, 1.0, False, False, False): "untyped",
    ("typed", True, True, False, 100, 1.0, False, False, False): "typed",
    ("typed", True, False, False, 100, 1.0, False, False, False): "guard_only",
    ("typed", False, True, False, 100, 1.0, False, False, False): "steer_only",
    ("untyped", True, True, False, 100, 1.0, False, True, False): "untyped_free",
    ("typed", True, True, False, 100, 1.0, False, True, False): "typed_free",
}

# ── load ──────────────────────────────────────────────────────────────────
cells = collections.defaultdict(list)
for line in (RUN / "episodes.jsonl").open():
    r = json.loads(line)
    name = ARMS.get(cfg(r))
    if name is not None:
        cells[(name, r["task"], r["seed"])].append(r)

out = {"_provenance": {
    "run": str(RUN),
    "rule": "arm identity identical to review_addenda.py cfg(); no program was "
            "executed and no model was called to produce any number here",
    "n_perm": NPERM, "n_boot": NBOOT, "rng_seed": 20260906,
}}


# ── 1. are the seeds independent draws? ───────────────────────────────────
by_task_round1 = collections.defaultdict(set)
n_seeds = collections.defaultdict(set)
for (arm, task, seed), rounds in cells.items():
    if arm != "no_memory":
        continue
    n_seeds[task].add(seed)
    for r in rounds:
        if r["round_index"] <= 1 and r.get("patch") is not None:
            by_task_round1[task].add(r["patch"])

distinct = [len(v) for v in by_task_round1.values()]
seeds_per_task = [len(v) for v in n_seeds.values()]
out["seeds"] = {
    "note": "src/llm.py sends no sampling seed to the backend; the seed enters "
            "only the cache key, so two seeds are two independent draws from "
            "the proposer at T=1.0. Round 1 has an identical prompt across "
            "seeds, so identical patches would mean the draws were not "
            "independent.",
    "tasks": len(by_task_round1),
    "seeds_per_task_min": min(seeds_per_task), "seeds_per_task_max": max(seeds_per_task),
    "distinct_round1_patches_mean": round(_mean(distinct), 2),
    "tasks_with_all_seeds_distinct": sum(1 for d, s in
                                         zip(distinct, seeds_per_task) if d == s),
    "tasks_with_one_patch_for_every_seed": sum(1 for d in distinct if d == 1),
}


# ── shared helpers ────────────────────────────────────────────────────────
def repaired(rounds):
    return any(r.get("accept") for r in rounds)


def cell_map(arm):
    return {(t, s): repaired(rs) for (a, t, s), rs in cells.items() if a == arm}


def boot_ci(per_task_values, n=NBOOT):
    """Task-clustered bootstrap: resample tasks, average their values."""
    if not per_task_values:
        return None
    keys = list(per_task_values)
    draws = []
    for _ in range(n):
        s = [per_task_values[RNG.choice(keys)] for _ in keys]
        draws.append(math.fsum(s) / len(s))
    draws.sort()
    lo = draws[int(0.025 * len(draws))]
    hi = draws[int(0.975 * len(draws)) - 1]
    return [round(lo, 4), round(hi, 4)]


# ── 2. difficulty x steering interaction, at the task unit ────────────────
# pi-hat exactly as sec:design describes: the no-memory arm run past its first
# accept, so early stopping cannot bias it.
acc = collections.Counter()
tot = collections.Counter()
for (arm, task, seed), rounds in cells.items():
    if arm != "no_memory":
        continue
    for r in rounds:
        if r.get("proposal_error") or r.get("oracle_error"):
            continue
        tot[task] += 1
        acc[task] += bool(r.get("accept"))

BANDS = [("dead", 0.0, 0.02), ("hard", 0.02, 0.08), ("medium", 0.08, 0.18),
         ("easy", 0.18, 0.3501), ("too_easy", 0.3501, 1.01)]


def band_of(p):
    for name, lo, hi in BANDS:
        if lo <= p < hi:
            return name
    return BANDS[-1][0]


pihat = {t: acc[t] / tot[t] for t in tot if tot[t]}
band = {t: band_of(p) for t, p in pihat.items()}

CONTRASTS = [
    ("steer_only_vs_no_memory", "steer_only", "no_memory"),
    ("typed_vs_no_memory", "typed", "no_memory"),   # the paper's p=0.047 claim
]
out["interaction"] = {"_note":
    "the dead-vs-rest contrast was chosen after seeing the band-wise results "
    "and is exploratory; the omnibus spread test needed no such choice. Both "
    "contrasts are reported because the paper's band claim is stated for "
    "typed-vs-no-memory and decomposed through steer-only."}

for label, treat, control in CONTRASTS:
    T, C = cell_map(treat), cell_map(control)
    shared = sorted(set(T) & set(C))
    per_task = collections.defaultdict(list)
    for k in shared:
        per_task[k[0]].append(int(T[k]) - int(C[k]))
    effect = {t: _mean(v) for t, v in per_task.items() if t in band}

    by_band = collections.defaultdict(dict)
    for t, e in effect.items():
        by_band[band[t]][t] = e

    def wmean(d):
        return math.fsum(d.values()) / len(d) if d else 0.0

    stat_contrast = wmean(by_band.get("dead", {})) - wmean(
        {t: e for t, e in effect.items() if band[t] != "dead"})
    grand = wmean(effect)
    stat_spread = math.fsum(len(d) * (wmean(d) - grand) ** 2 for d in by_band.values())

    tasks = list(effect)
    labels = [band[t] for t in tasks]
    vals = [effect[t] for t in tasks]
    hit_c = hit_s = 0
    for _ in range(NPERM):
        RNG.shuffle(labels)
        g = collections.defaultdict(list)
        for lab, v in zip(labels, vals):
            g[lab].append(v)
        d = _mean(g["dead"]) if g.get("dead") else 0.0
        rest = [v for lab, v in zip(labels, vals) if lab != "dead"]
        c = d - (_mean(rest) if rest else 0.0)
        gm = _mean(vals)
        sp = math.fsum(len(v) * (_mean(v) - gm) ** 2 for v in g.values())
        hit_c += abs(c) >= abs(stat_contrast) - TIE
        hit_s += sp >= stat_spread - TIE

    # the within-band paired test the paper reports, at the task unit
    dead_tasks = by_band.get("dead", {})
    w = sum(1 for e in dead_tasks.values() if e > 0)
    l = sum(1 for e in dead_tasks.values() if e < 0)
    n = w + l
    p_sign = (min(1.0, 2 * sum(math.comb(n, i) for i in range(min(w, l) + 1))
                  / 2 ** n) if n else 1.0)

    out["interaction"][label] = {
        "unit": "task", "n_tasks": len(effect), "n_cells": len(shared),
        "by_band": {b: {"n_tasks": len(d), "mean": round(wmean(d), 4),
                        "ci95": boot_ci(d)} for b, d in sorted(by_band.items())},
        "dead_within_band_sign_test": {"wins": w, "losses": l,
                                       "ties": len(dead_tasks) - n,
                                       "p": round(p_sign, 4)},
        "dead_minus_rest": round(stat_contrast, 4),
        "p_perm_dead_vs_rest": round((hit_c + 1) / (NPERM + 1), 4),
        "p_perm_any_band_difference": round((hit_s + 1) / (NPERM + 1), 4),
    }


# ── 3. free guarded: a permutation test that respects the shared tasks ────
pairs = []          # (task, free_repaired, base_repaired)
for arm, base in (("untyped_free", "untyped"), ("typed_free", "typed")):
    F, B = cell_map(arm), cell_map(base)
    for k in sorted(set(F) & set(B)):
        pairs.append((k[0], int(F[k]), int(B[k])))

obs = sum(f - b for _, f, b in pairs)
task_ids = sorted({t for t, _, _ in pairs})
hit = 0
for _ in range(NPERM):
    flip = {t: RNG.choice((1, -1)) for t in task_ids}   # cluster = task
    s = sum(flip[t] * (f - b) for t, f, b in pairs)
    hit += abs(s) >= abs(obs)

out["free_guarded"] = {
    "n_matched_pairs": len(pairs), "n_tasks": len(task_ids),
    "free_better": sum(1 for _, f, b in pairs if f > b),
    "base_better": sum(1 for _, f, b in pairs if f < b),
    "ties": sum(1 for _, f, b in pairs if f == b),
    "statistic_free_minus_base": obs,
    "p_cluster_permutation": round((hit + 1) / (NPERM + 1), 4),
    "note": "the sign test in the paper treats cells as independent; this "
            "permutes the free/base label for an entire task at once, which is "
            "the dependence the two pooled arms actually have",
}


# ── 4. total sandbox seconds, guard-side and oracle-side ──────────────────
# The no-memory arm is run past its first accept so that pi-hat is unbiased, so
# every cost quantity in the paper is truncated at the first accepting round.
# We truncate the same way, and check the convention by recomputing the
# execution counts the paper reports: they must come back as numbers.json has
# them, or the seconds below are not comparable either.
sec = collections.defaultdict(
    lambda: {"guard": 0.0, "oracle": 0.0, "runs": 0.0, "cells": 0})
for (arm, task, seed), rounds in cells.items():
    if arm.endswith("_free"):
        continue
    d = sec[arm]
    d["cells"] += 1
    for r in sorted(rounds, key=lambda r: r["round_index"]):
        d["guard"] += r.get("guard_sec") or 0.0
        d["oracle"] += r.get("oracle_sec") or 0.0
        d["runs"] += r.get("sandbox_runs") or 0
        if r.get("accept"):
            break                      # cost is counted to first acceptance

out["test_work_seconds"] = {
    arm: {"n_cells": d["cells"],
          "guard_sec_per_cell": round(d["guard"] / d["cells"], 2),
          "oracle_sec_per_cell": round(d["oracle"] / d["cells"], 2),
          "total_sandbox_sec_per_cell": round((d["guard"] + d["oracle"]) / d["cells"], 2),
          "executions_per_cell_check": round(d["runs"] / d["cells"], 2)}
    for arm, d in sorted(sec.items())}

OUT.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps({k: v for k, v in out.items() if k != "_provenance"}, indent=2))
