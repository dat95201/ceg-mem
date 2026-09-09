"""Two corrections to numbers.json, applied on the device where episodes.jsonl lives.

  C1  The typing sweep's c=1.0 point was computed over every main-grid typed
      round, while c<1 points are the 30-task sweep at seeds 1-3. Different
      task universes are not comparable on one axis, so the sweep's own c=1.0
      bucket-hit rate is recomputed over the sweep cells alone. The full-grid
      rate is kept under its own name, because that is the number the RQ3 text
      quotes ("share of guard decisions the index settles").

  C2  The paper claims a sensitivity analysis excluding the one fault whose
      candidate patches sit at the sandbox timeout edge. Compute it: which
      fault, which cells diverge, and every headline number with it removed.

  C3  `bucket_index`: the O(1) path in vivo - of the guarded rounds, the share
      the type-matched bucket settled alone (`bucket_hit`) and, given a hit, the
      share whose bucket really refuted the candidate (`bucket_hit_refuted`),
      on the main grid and per level of the typing sweep. tools/make_figures.py
      reads this block; it used to be produced outside the repository.

  C4  `e5_control_tests`: typed at c=1.0 against each control arm (the random
      partition and c in {0.0, 0.5, 0.75}), paired on the same 90 sweep cells,
      exact sign tests on six per-cell quantities. The RQ2 paragraph "Is it the
      types, or just partitioning?" quotes these.

Usage: python3 fix_numbers.py <run_dir> <numbers.json>
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import statistics
import sys

RUN = pathlib.Path(sys.argv[1])
NUMP = pathlib.Path(sys.argv[2])
NUM = json.loads(NUMP.read_text())
results = json.loads((RUN / "results_real.json").read_text())
sweep_tasks = {l.strip() for l in (RUN / "sweep_programs.txt").read_text().splitlines()
               if l.strip() and not l.startswith("#")}


def cfg(e):
    return (e["mode"], e["guard_on"], e["steer_on"], e["force_full_budget"],
            e["max_examples"], e["typing_noise_c"], bool(e.get("typing_random")),
            bool(e.get("free_guarded_rounds")), bool(e.get("audit_guarded")))


groups = collections.defaultdict(list)
for e in results["episodes"]:
    groups[(e["task"], e["seed"]) + cfg(e)].append(e)
dedup = {k: min(v, key=lambda e: e["episode_id"]) for k, v in groups.items()}
ep_cfg = {e["episode_id"]: (k[0], k[1]) + cfg(e) for k, e in dedup.items()}


def arm(mode, *, guard=True, steer=True, ffb=None, k=100, c=1.0, rand=False,
        fgr=False, audit=False):
    if ffb is None:
        ffb = (mode == "no_memory") and not fgr
    return {(kk[0], kk[1]): e for kk, e in dedup.items()
            if cfg(e) == (mode, guard, steer, ffb, k, c, rand, fgr, audit)}


NM, UN, TY = arm("no_memory"), arm("untyped"), arm("typed")
GO, SO = arm("typed", steer=False), arm("typed", guard=False)


def sign(pairs):
    w = sum(1 for a, b in pairs if a < b)
    l = sum(1 for a, b in pairs if a > b)
    n = w + l
    p = 1.0 if n == 0 else min(
        1.0, 2 * sum(math.comb(n, k) for k in range(min(w, l) + 1)) / 2 ** n)
    return {"wins": w, "losses": l, "ties": len(pairs) - n, "p": p}


mean = lambda xs: statistics.mean(xs) if xs else None

# ── C1: bucket-hit rate restricted to the sweep universe ───────────────────
# (C3 rides on the same pass: `bucket_hit_refuted` given `bucket_hit`.)
hit = collections.Counter()
tot = collections.Counter()
hit_ref = collections.Counter()
grid_hit = grid_tot = grid_hit_ref = 0
with (RUN / "episodes.jsonl").open() as f:
    seen = set()
    for line in f:
        r = json.loads(line)
        key = (r["episode_id"], r["round_index"])
        if key in seen:
            continue
        seen.add(key)
        c = ep_cfg.get(r["episode_id"])
        if c is None or not r.get("guard_evaluations"):
            continue
        task, seed, mode, guard, steer, ffb, kx, cc, rand, fgr, audit = c
        if mode != "typed" or not guard or kx != 100 or fgr or audit:
            continue
        if cc == 1.0 and not rand:
            grid_tot += 1
            grid_hit += bool(r.get("bucket_hit"))
            grid_hit_ref += bool(r.get("bucket_hit")) and bool(r.get("bucket_hit_refuted"))
        # the sweep axis: same tasks, same seeds, at every level of c
        if task in sweep_tasks and seed in (1, 2, 3):
            lab = "random" if rand else str(cc)
            tot[lab] += 1
            hit[lab] += bool(r.get("bucket_hit"))
            hit_ref[lab] += bool(r.get("bucket_hit")) and bool(r.get("bucket_hit_refuted"))

NUM["e5_typing_noise_bucket_hit"] = {
    "_note": "sweep universe only (30 tasks x seeds 1-3) so every level of c "
             "is measured over the same cells",
    **{lab: round(hit[lab] / tot[lab], 4) for lab in sorted(tot) if tot[lab]},
    "_n_rounds": {lab: tot[lab] for lab in sorted(tot)},
}
NUM["coherence"]["in_vivo_bucket_hit_rate_main_grid"] = {
    "rate": round(grid_hit / grid_tot, 4), "n_guarded_rounds": grid_tot,
    "_note": "share of guard decisions the type-matched bucket settles alone, "
             "typed + guard-only arms, full corpus",
}
NUM["coherence"].pop("in_vivo_bucket_hit_rate", None)

# ── C3: the O(1) path in vivo: hit rate, and refuted given a hit ──────────
NUM["bucket_index"] = {
    "_note": "the O(1) path, in vivo",
    "main_grid": {
        "guarded_rounds": grid_tot,
        "hit_rate": round(grid_hit / grid_tot, 4),
        "refuted_given_hit": round(grid_hit_ref / grid_hit, 4),
    },
    "sweep_by_level": {
        lab: {"hit_rate": round(hit[lab] / tot[lab], 4),
              "refuted_given_hit": round(hit_ref[lab] / hit[lab], 4)}
        for lab in ("1.0", "0.9", "0.75", "0.5", "0.25", "0.0", "random") if tot.get(lab)
    },
    "_caveat": "refuted|hit RISES as coherence falls because a mistyped bucket "
               "accumulates counterexamples from many true locations and is "
               "therefore fuller and more heterogeneous; it is not a clean "
               "coherence proxy. Def. 3.1's coherence is measured offline "
               "instead (coherence_report).",
}

# ── C2: the timeout-edge fault, and the study without it ──────────────────
gc = sorted(set(GO) & set(UN))
diverge = [c for c in gc
           if not (GO[c]["success_at_b"] == UN[c]["success_at_b"]
                   and GO[c]["n_oracle_calls"] == UN[c]["n_oracle_calls"]
                   and GO[c]["n_rounds"] == UN[c]["n_rounds"])]
flaky_tasks = sorted({c[0] for c in diverge})

common = sorted(set(NM) & set(UN) & set(TY))
keep = [c for c in common if c[0] not in flaky_tasks]


def head(cells, keys):
    e = [cells[k] for k in keys]
    acc = [x for x in e if x["accepted"]]
    return {
        "n_cells": len(e),
        "success": round(mean([x["success_at_b"] for x in e]), 4),
        "oracle_calls": round(mean([x["n_oracle_calls"] for x in e]), 4),
        "sandbox_runs": round(mean([x["sandbox_runs"] for x in e]), 3),
        "o2a": round(mean([x["oracle_calls_to_accept"] for x in acc]), 4),
        "redundancy_paid": round(mean([x["redundancy_paid"] for x in e]), 4),
        "tokens_in": round(mean([x["tokens_in"] for x in e]), 1),
    }


def curve(cells, keys, upto=8):
    n = len(keys)
    o2a = [cells[k]["oracle_calls_to_accept"] for k in keys]
    return {str(b): round(sum(1 for x in o2a if x is not None and x <= b) / n, 4)
            for b in range(1, upto + 1)}


sens = {
    "excluded_tasks": flaky_tasks,
    "excluded_cells_diverging_in_ablation": [f"{t}|{s}" for t, s in diverge],
    "n_cells_kept": len(keep),
    "no_memory": head(NM, keep), "untyped": head(UN, keep), "typed": head(TY, keep),
    "typed_vs_untyped_oracle_calls": sign(
        [(TY[c]["n_oracle_calls"], UN[c]["n_oracle_calls"]) for c in keep]),
    "budget_curve_b1_b8": {
        "no_memory": curve(NM, keep), "untyped": curve(UN, keep),
        "typed": curve(TY, keep)},
}
sens["x_oracle_typed_vs_nomem"] = round(
    sens["no_memory"]["oracle_calls"] / sens["typed"]["oracle_calls"], 3)
sens["x_paid_typed_vs_nomem"] = round(
    sens["no_memory"]["redundancy_paid"] / sens["typed"]["redundancy_paid"], 2)
gck = [c for c in gc if c[0] not in flaky_tasks]
sens["guard_sec_sign_excl"] = sign([(GO[c].get("guard_sec") or 0.0,
                                     UN[c].get("guard_sec") or 0.0) for c in gck])
sens["guard_sec_untyped"] = round(mean([UN[c].get("guard_sec") or 0.0 for c in gck]), 3)
sens["guard_sec_guard_only"] = round(mean([GO[c].get("guard_sec") or 0.0 for c in gck]), 3)
sens["outcome_identical_excl"] = sum(
    1 for c in gck
    if GO[c]["success_at_b"] == UN[c]["success_at_b"]
    and GO[c]["n_oracle_calls"] == UN[c]["n_oracle_calls"]
    and GO[c]["n_rounds"] == UN[c]["n_rounds"])
sens["n_cells_kept_ablation"] = len(gck)
NUM["sensitivity_exclude_timeout_edge"] = sens

# ── C4: typed c=1.0 against each control, paired on the same 90 sweep cells ─
# Sweep cells: the 30 sweep tasks x seeds 1-3. The c=1.0 point is E2's typed
# arm restricted to those cells (fix C1's universe), the controls are the E5
# arms. 'wins' = c=1.0 better: MORE redundancy caught, FEWER paid redundant
# verifications / guard seconds / entries consulted / oracle calls, MORE
# repairs. Only cells present in both arms are paired.
def _sweep_cells(cells):
    return {k: e for k, e in cells.items() if k[0] in sweep_tasks and k[1] in (1, 2, 3)}


CTRL = {
    "random": arm("typed", rand=True),
    "c0.0": arm("typed", c=0.0),
    "c0.5": arm("typed", c=0.5),
    "c0.75": arm("typed", c=0.75),
}
TY_SWEEP = _sweep_cells(TY)
LOWER_IS_BETTER = ("redundancy_paid", "guard_sec", "guard_evaluations", "oracle_calls")


def _val(e, field):
    if field == "oracle_calls":
        return e["n_oracle_calls"]
    if field == "success":
        return e["success_at_b"]
    return e.get(field) or 0.0


ctl_tests = {}
for name, ctl in CTRL.items():
    cells = sorted(set(TY_SWEEP) & set(_sweep_cells(ctl)))
    block = {"n_cells": len(cells)}
    for field in ("redundancy_caught", "redundancy_paid", "guard_sec",
                  "guard_evaluations", "oracle_calls", "success"):
        pairs = []
        for k in cells:
            a, b = _val(TY_SWEEP[k], field), _val(ctl[k], field)
            # sign() counts a < b as a win, so flip the pair where MORE is better.
            pairs.append((a, b) if field in LOWER_IS_BETTER else (b, a))
        t = sign(pairs)
        t["p"] = round(t["p"], 6)
        block[field] = t
    ctl_tests[name] = block
NUM["e5_control_tests"] = {
    "_note": "typed c=1.0 vs each control, paired on the same 90 sweep cells; "
             "'wins' = c=1.0 better",
    "tests": ctl_tests,
}

# ── while here: the two numbers the results text needs and did not have ───
NUM["typed_vs_untyped"]["tokens_in_ratio"] = round(
    NUM["main"]["typed"]["tokens_in"] / NUM["main"]["untyped"]["tokens_in"], 4)
NUM["typed_vs_untyped"]["wall_sec_ratio_main"] = round(
    NUM["main"]["typed"]["wall_sec"] / NUM["main"]["untyped"]["wall_sec"], 4)
NUM["wall_clock_five_arms"]["typed_over_guard_only"] = round(
    NUM["wall_clock_five_arms"]["typed"] / NUM["wall_clock_five_arms"]["guard_only"], 3)

NUMP.write_text(json.dumps(NUM, indent=1) + "\n")
print("bucket-hit (sweep universe):",
      {k: v for k, v in NUM["e5_typing_noise_bucket_hit"].items() if not k.startswith("_")})
print("bucket-hit (main grid):", NUM["coherence"]["in_vivo_bucket_hit_rate_main_grid"])
print("bucket index, refuted|hit (main grid):", NUM["bucket_index"]["main_grid"]["refuted_given_hit"])
print("c=1.0 vs random, redundancy caught:", NUM["e5_control_tests"]["tests"]["random"]["redundancy_caught"])
print("flaky task(s):", flaky_tasks, "diverging cells:", sens["excluded_cells_diverging_in_ablation"])
print("sensitivity, typed vs no-mem oracle:", sens["x_oracle_typed_vs_nomem"],
      "| paid:", sens["x_paid_typed_vs_nomem"],
      "| b=2 curve:", {k: v["2"] for k, v in sens["budget_curve_b1_b8"].items()})
print("sensitivity guard sec:", sens["guard_sec_untyped"], "->", sens["guard_sec_guard_only"],
      sens["guard_sec_sign_excl"], "identical", sens["outcome_identical_excl"],
      "/", sens["n_cells_kept_ablation"])
