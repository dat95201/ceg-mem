"""Recompute every number the paper draft prints, from runs/2026-09-01 only.

Writes paper/draft/numbers.json. Nothing in the LaTeX may cite a figure that
does not appear here: one script, one artifact, one provenance chain.

Definitions used throughout, stated once:
  cell            one (task, seed) pair within one arm
  effective round a round at or before the episode's first accept (E1 runs past
                  it under force_full_budget; src.metrics truncates the extra
                  rounds back out, and so do we)
  oracle round    an effective round that actually reached the oracle - not
                  guarded, not self-test-blocked, not randomly skipped
  o2a             oracle rounds up to and including the accepting one
  success@n       fraction of cells with o2a <= n  (the pre-registered primary
                  metric: budget counted in oracle calls, not proposals)
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import statistics
import sys

RUN = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "numbers.json")
REPO = pathlib.Path(__file__).resolve().parents[3]   # paper/common/tools -> repo root


def portable(o):
    """Copy o with every absolute path under the repository rewritten as
    'data/<run>/...': provenance blocks (strata.json's band_source) record the
    machine-specific absolute path they read, and a reproduction on another
    machine must not differ from the committed reference by that path alone."""
    if isinstance(o, dict):
        return {k: portable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [portable(v) for v in o]
    if isinstance(o, str) and o.startswith("/"):
        head = o.split(" ", 1)          # "…/tasks.json screen_pi_hat (…)" keeps its tail
        try:
            rel = pathlib.Path(head[0]).resolve().relative_to(REPO)
        except ValueError:
            return o
        return " ".join([rel.as_posix()] + head[1:])
    return o

BANDS = (("dead", 0.0, 0.02), ("hard", 0.02, 0.08), ("medium", 0.08, 0.18),
         ("easy", 0.18, 0.3501), ("too_easy", 0.3501, 1.01))
PRIMARY = ("hard", "medium", "easy")


# ── statistics, implemented here so the artifact needs no scipy ─────────────
def sign_test(pairs):
    """Exact two-sided sign test on (a, b): wins = a < b (a is 'better')."""
    w = sum(1 for a, b in pairs if a < b)
    l = sum(1 for a, b in pairs if a > b)
    n = w + l
    if n == 0:
        return {"wins": 0, "losses": 0, "ties": len(pairs), "p": 1.0}
    p = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(w, l) + 1)) / 2 ** n)
    return {"wins": w, "losses": l, "ties": len(pairs) - n, "p": p}


def a12(xs, ys):
    """Vargha-Delaney A12 for 'xs smaller than ys' (lower is better)."""
    if not xs or not ys:
        return None
    s = sum((1 if x < y else 0.5 if x == y else 0) for x in xs for y in ys)
    return s / (len(xs) * len(ys))


def wilcoxon_p(deltas):
    """Two-sided Wilcoxon signed-rank, exact for n<=20 else normal approx.

    Used only where a paired per-task test is reported alongside the per-cell
    sign test; ties at zero are dropped, as scipy's default does."""
    d = [x for x in deltas if x != 0]
    n = len(d)
    if n == 0:
        return 1.0
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    w_minus = sum(r for r, x in zip(ranks, d) if x < 0)
    w = min(w_plus, w_minus)
    if n <= 20:
        # exact: enumerate sign vectors
        from itertools import product
        cnt = 0
        tot = 0
        for signs in product((0, 1), repeat=n):
            s = sum(r for r, b in zip(ranks, signs) if b)
            tot += 1
            if min(s, sum(ranks) - s) <= w:
                cnt += 1
        return min(1.0, cnt / tot)
    mu = n * (n + 1) / 4
    sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (w - mu) / sd
    return min(1.0, 2 * 0.5 * math.erfc(abs(z) / math.sqrt(2)))


def bh(pvals):
    """Benjamini-Hochberg adjusted p-values, order preserved."""
    m = len(pvals)
    idx = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    prev = 1.0
    for rank, i in enumerate(reversed(idx), start=1):
        k = m - rank + 1
        prev = min(prev, pvals[i] * m / k)
        adj[i] = prev
    return adj


def mean(xs):
    return statistics.mean(xs) if xs else None


def ci95_boot(xs, n=2000, seed=20260904):
    """Percentile bootstrap of the mean - the interval analyze.py reports."""
    if not xs:
        return None
    import random
    rng = random.Random(seed)
    k = len(xs)
    means = sorted(statistics.mean(rng.choices(xs, k=k)) for _ in range(n))
    return [means[int(0.025 * n)], means[int(0.975 * n)]]


# ── load ────────────────────────────────────────────────────────────────────
def jload(name):
    p = RUN / name
    return json.loads(p.read_text()) if p.exists() else None


results = jload("results_real.json")
analysis = jload("analysis.json")
theory = jload("theory_fit.json")
coherence = jload("coherence_report.json")
redundancy = jload("redundancy.json")
pool = jload("pool_strength.json")
patchq = jload("patch_quality.json")
anchoring = jload("anchoring.json")
taxonomy = jload("failure_taxonomy.json")
strata = jload("strata.json")

overfit = [json.loads(l) for l in (RUN / "overfit_checks.jsonl").read_text().splitlines() if l.strip()]

# One episode per (cell, config): the merged log holds duplicate runs of some
# cells (E2 was re-run); outcomes agree except for the abc285_e timing flake, so
# the lexicographically first episode_id is taken deterministically.
def cfg(e):
    return (e["mode"], e["guard_on"], e["steer_on"], e["force_full_budget"],
            e["max_examples"], e["typing_noise_c"], bool(e.get("typing_random")),
            bool(e.get("free_guarded_rounds")), bool(e.get("audit_guarded")))


groups = collections.defaultdict(list)
for e in results["episodes"]:
    groups[(e["task"], e["seed"]) + cfg(e)].append(e)
dedup = {k: min(v, key=lambda e: e["episode_id"]) for k, v in groups.items()}
n_dup_groups = sum(1 for v in groups.values() if len(v) > 1)
disagree = [k for k, v in groups.items() if len(v) > 1
            and len({(x["success_at_b"], x["n_oracle_calls"]) for x in v}) > 1]


def arm(mode, *, guard=True, steer=True, ffb=None, k=100, c=1.0, rand=False,
        fgr=False, audit=False):
    if ffb is None:
        ffb = (mode == "no_memory") and not fgr
    out = {}
    for key, e in dedup.items():
        task, seed = key[0], key[1]
        if cfg(e) == (mode, guard, steer, ffb, k, c, rand, fgr, audit):
            out[(task, seed)] = e
    return out


NM = arm("no_memory")
UN = arm("untyped")
TY = arm("typed")
GO = arm("typed", steer=False)
SO = arm("typed", guard=False)
E9 = {m: arm(m, fgr=True, ffb=False) for m in ("no_memory", "untyped", "typed")}
E4 = {k: arm("typed", k=k) for k in (20, 8, 3)}
E5 = {c: arm("typed", c=c) for c in (0.9, 0.75, 0.5, 0.25, 0.0)}
E5R = arm("typed", rand=True)

# π̂ per task from E1 (all forced-budget rounds), and the band it implies
pi = {t: v["pi_hat"] for t, v in theory["pi_q_by_task"].items()} if theory else {}


def band_of(p):
    for n, lo, hi in BANDS:
        if lo <= p < hi:
            return n
    return None


band = {t: band_of(p) for t, p in pi.items()}
# strata.json is the frozen artifact the analyzer used; prefer it, fall back to
# the recomputation above for any task it does not carry.
if strata:
    for row in strata.get("tasks", []):
        band[row["name"]] = row["stratum"]

numbers = {
    "provenance": {
        "run": str(RUN),
        "frozen": bool(results.get("frozen")),
        "experiment": results.get("experiment"),
        "model": results.get("model"),
        "n_episodes_frozen": results.get("n_episodes"),
        "n_expected_cells": results.get("n_expected_cells"),
        "n_missing_cells": results.get("n_missing_cells"),
        "band_source": portable((strata or {}).get("band_source")),
    },
    "protocol": {
        "corpus_tasks": len({t for t, _ in NM}),
        "seeds_main": sorted({s for _, s in NM}),
        "seeds_ablation": sorted({s for _, s in GO}),
        "budget": (redundancy or {}).get("budget"),
        "oracle_k": 100,
        "cells_main_per_arm": len(NM),
        "cells_ablation_per_arm": len(GO),
        "bands": {n: sum(1 for t in {t for t, _ in NM} if band.get(t) == n)
                  for n, _, _ in BANDS},
    },
}

# ── the arm-level metric block ──────────────────────────────────────────────
FIELDS = {
    "success": lambda e: e["success_at_b"],
    "oracle_calls": lambda e: e["n_oracle_calls"],
    "sandbox_runs": lambda e: e["sandbox_runs"],
    "o2a": lambda e: e["oracle_calls_to_accept"],
    "redundancy_paid": lambda e: e["redundancy_paid"],
    "redundancy_caught": lambda e: e["redundancy_caught"],
    "redundancy_present": lambda e: e["redundancy_present"],
    "redundancy_unknown": lambda e: e["redundancy_unknown"],
    "blocked": lambda e: e["n_guarded"],
    "guard_evaluations": lambda e: e["guard_evaluations"],
    "guard_sec": lambda e: e.get("guard_sec") or 0.0,
    "tokens_in": lambda e: e["tokens_in"],
    "tokens_out": lambda e: e["tokens_out"],
    "wall_sec": lambda e: e.get("wall_sec") or 0.0,
    "proposals": lambda e: e["proposals"],
    "rounds": lambda e: e["n_rounds"],
}


def describe(cells, keys=None):
    out = {"n_cells": len(cells)}
    for name, f in FIELDS.items():
        vals = [f(e) for e in (cells.values() if keys is None
                               else [cells[k] for k in keys])]
        vals = [v for v in vals if v is not None]
        out[name] = round(mean(vals), 4) if vals else None
    return out


common_main = sorted(set(NM) & set(UN) & set(TY))
numbers["main"] = {
    "paired_cells": len(common_main),
    "no_memory": describe(NM, common_main),
    "untyped": describe(UN, common_main),
    "typed": describe(TY, common_main),
}

# reduction factors vs no_memory
for a in ("untyped", "typed"):
    m = numbers["main"][a]
    nmm = numbers["main"]["no_memory"]
    m["x_oracle_vs_nomem"] = round(nmm["oracle_calls"] / m["oracle_calls"], 3)
    m["x_sandbox_vs_nomem"] = round(nmm["sandbox_runs"] / m["sandbox_runs"], 3)
    m["x_paid_vs_nomem"] = (round(nmm["redundancy_paid"] / m["redundancy_paid"], 2)
                            if m["redundancy_paid"] else None)

# paired tests, typed vs untyped, per cell
tests = {}
for name, f in FIELDS.items():
    pairs = [(f(TY[c]), f(UN[c])) for c in common_main
             if f(TY[c]) is not None and f(UN[c]) is not None]
    if pairs:
        tests[name] = sign_test(pairs)
tests["success_flips"] = {
    "typed_wins": sum(1 for c in common_main if TY[c]["success_at_b"] > UN[c]["success_at_b"]),
    "untyped_wins": sum(1 for c in common_main if TY[c]["success_at_b"] < UN[c]["success_at_b"]),
}
tests["success_flips"]["p"] = sign_test(
    [(-TY[c]["success_at_b"], -UN[c]["success_at_b"]) for c in common_main])["p"]
tests["a12_oracle_calls"] = round(
    a12([TY[c]["n_oracle_calls"] for c in common_main],
        [UN[c]["n_oracle_calls"] for c in common_main]), 4)
prim = [c for c in common_main if band.get(c[0]) in PRIMARY]
tests["primary_bands_oracle_calls"] = sign_test(
    [(TY[c]["n_oracle_calls"], UN[c]["n_oracle_calls"]) for c in prim])
tests["primary_bands_oracle_calls"]["n_cells"] = len(prim)
# per-task Wilcoxon on the two headline cost metrics, for the unit note
for metric, f in (("oracle_calls", lambda e: e["n_oracle_calls"]),
                  ("sandbox_runs", lambda e: e["sandbox_runs"])):
    by_task_t, by_task_u = collections.defaultdict(list), collections.defaultdict(list)
    for c in common_main:
        by_task_t[c[0]].append(f(TY[c]))
        by_task_u[c[0]].append(f(UN[c]))
    deltas = [mean(by_task_t[t]) - mean(by_task_u[t]) for t in sorted(by_task_t)]
    tests[f"per_task_wilcoxon_{metric}"] = {"n_tasks": len(deltas),
                                            "p": round(wilcoxon_p(deltas), 5)}
numbers["typed_vs_untyped"] = tests

# ── success@oracle-budget: the pre-registered primary metric ───────────────
def curve(cells, upto=20):
    n = len(cells)
    o2a = [e["oracle_calls_to_accept"] for e in cells.values()]
    return {str(b): round(sum(1 for x in o2a if x is not None and x <= b) / n, 4)
            for b in range(1, upto + 1)}


numbers["success_at_oracle_budget"] = {
    "note": "fraction of cells repaired within b ORACLE calls; b counted in "
            "oracle calls, not proposals - the resource the guard spends",
    "no_memory": curve(NM), "untyped": curve(UN), "typed": curve(TY),
    "guard_only": curve(GO), "steer_only": curve(SO),
}
# proposal-budget counterpart, for the tautology guard in the text
def curve_proposals(cells, upto=20):
    n = len(cells)
    return {str(b): round(sum(1 for e in cells.values()
                              if e["first_accept_round"] is not None
                              and e["first_accept_round"] <= b) / n, 4)
            for b in range(1, upto + 1)}


numbers["success_at_proposal_budget"] = {
    "no_memory": curve_proposals(NM), "untyped": curve_proposals(UN),
    "typed": curve_proposals(TY),
}

# ── per band ───────────────────────────────────────────────────────────────
per_band = {}
for name, _, _ in BANDS:
    cs = [c for c in common_main if band.get(c[0]) == name]
    if not cs:
        continue
    row = {"n_tasks": len({c[0] for c in cs}), "n_cells": len(cs)}
    for arm_name, cells in (("no_memory", NM), ("untyped", UN), ("typed", TY)):
        row[arm_name] = {
            "oracle_calls": round(mean([cells[c]["n_oracle_calls"] for c in cs]), 3),
            "sandbox_runs": round(mean([cells[c]["sandbox_runs"] for c in cs]), 2),
            "success": round(mean([cells[c]["success_at_b"] for c in cs]), 4),
            "redundancy_paid": round(mean([cells[c]["redundancy_paid"] for c in cs]), 4),
            "tokens_in": round(mean([cells[c]["tokens_in"] for c in cs]), 1),
        }
    row["x_typed_vs_nomem_oracle"] = round(
        row["no_memory"]["oracle_calls"] / row["typed"]["oracle_calls"], 2)
    row["x_typed_vs_nomem_paid"] = (
        round(row["no_memory"]["redundancy_paid"] / row["typed"]["redundancy_paid"], 1)
        if row["typed"]["redundancy_paid"] else None)
    row["typed_vs_untyped_oracle"] = sign_test(
        [(TY[c]["n_oracle_calls"], UN[c]["n_oracle_calls"]) for c in cs])
    row["typed_vs_untyped_success_flips"] = {
        "typed": sum(1 for c in cs if TY[c]["success_at_b"] > UN[c]["success_at_b"]),
        "untyped": sum(1 for c in cs if TY[c]["success_at_b"] < UN[c]["success_at_b"])}
    per_band[name] = row
numbers["per_band"] = per_band

# ── E3: the two ablations ──────────────────────────────────────────────────
gc = sorted(set(GO) & set(UN))
ident = sum(1 for c in gc
            if GO[c]["success_at_b"] == UN[c]["success_at_b"]
            and GO[c]["n_oracle_calls"] == UN[c]["n_oracle_calls"]
            and GO[c]["n_rounds"] == UN[c]["n_rounds"])
numbers["e3_guard_only"] = {
    "paired_cells": len(gc),
    "outcome_identical_cells": ident,
    "untyped": describe(UN, gc), "guard_only": describe(GO, gc),
    "guard_sec_sign": sign_test([(GO[c].get("guard_sec") or 0.0,
                                  UN[c].get("guard_sec") or 0.0) for c in gc]),
    "guard_evaluations_sign": sign_test([(GO[c]["guard_evaluations"],
                                          UN[c]["guard_evaluations"]) for c in gc]),
    "wall_sec_sign": sign_test([(GO[c].get("wall_sec") or 0.0,
                                 UN[c].get("wall_sec") or 0.0) for c in gc]),
    "guard_sec_reduction": round(
        1 - mean([GO[c].get("guard_sec") or 0.0 for c in gc])
        / mean([UN[c].get("guard_sec") or 0.0 for c in gc]), 4),
}
sc = sorted(set(SO) & set(NM))
numbers["e3_steer_only"] = {
    "paired_cells": len(sc),
    "no_memory": describe(NM, sc), "steer_only": describe(SO, sc),
    "oracle_calls_sign": sign_test([(SO[c]["n_oracle_calls"], NM[c]["n_oracle_calls"])
                                    for c in sc]),
    "tokens_in_sign": sign_test([(SO[c]["tokens_in"], NM[c]["tokens_in"]) for c in sc]),
    "success_flips": {
        "steer_only": sum(1 for c in sc if SO[c]["success_at_b"] > NM[c]["success_at_b"]),
        "no_memory": sum(1 for c in sc if SO[c]["success_at_b"] < NM[c]["success_at_b"])},
    "tokens_in_increase": round(mean([SO[c]["tokens_in"] for c in sc])
                                / mean([NM[c]["tokens_in"] for c in sc]) - 1, 4),
}
numbers["e3_steer_only"]["success_flips"]["p"] = sign_test(
    [(-SO[c]["success_at_b"], -NM[c]["success_at_b"]) for c in sc])["p"]

# dead-band decomposition, the one place steering acts
dead = [c for c in sc if band.get(c[0]) == "dead"]
numbers["dead_band"] = {
    "n_cells_ablation": len(dead),
    "no_memory": round(mean([NM[c]["success_at_b"] for c in dead]), 4),
    "untyped": round(mean([UN[c]["success_at_b"] for c in dead if c in UN]), 4),
    "guard_only": round(mean([GO[c]["success_at_b"] for c in dead if c in GO]), 4),
    "steer_only": round(mean([SO[c]["success_at_b"] for c in dead]), 4),
    "typed": round(mean([TY[c]["success_at_b"] for c in dead if c in TY]), 4),
    "typed_vs_untyped_per_task_p": None,
}
dead_main = [c for c in common_main if band.get(c[0]) == "dead"]
bt_t = collections.defaultdict(list)
bt_u = collections.defaultdict(list)
for c in dead_main:
    bt_t[c[0]].append(TY[c]["success_at_b"])
    bt_u[c[0]].append(UN[c]["success_at_b"])
numbers["dead_band"]["typed_vs_untyped_per_task_p"] = round(
    wilcoxon_p([mean(bt_t[t]) - mean(bt_u[t]) for t in sorted(bt_t)]), 5)
numbers["dead_band"]["typed_main_success"] = round(
    mean([TY[c]["success_at_b"] for c in dead_main]), 4)
numbers["dead_band"]["untyped_main_success"] = round(
    mean([UN[c]["success_at_b"] for c in dead_main]), 4)
numbers["dead_band"]["no_memory_main_success"] = round(
    mean([NM[c]["success_at_b"] for c in dead_main]), 4)

# wall clock across all five arms on the shared ablation cells
shared5 = sorted(set(NM) & set(UN) & set(TY) & set(GO) & set(SO))
numbers["wall_clock_five_arms"] = {
    "n_cells": len(shared5),
    **{k: round(mean([cells[c].get("wall_sec") or 0.0 for c in shared5]), 2)
       for k, cells in (("no_memory", NM), ("untyped", UN), ("typed", TY),
                        ("guard_only", GO), ("steer_only", SO))},
}

# ── E4: oracle depth, joined against the overfit audit ─────────────────────
ep_cfg = {e["episode_id"]: e for e in results["episodes"]}
ov_by_ep = {}
for r in overfit:
    ov_by_ep.setdefault(r["episode_id"], r)


def overfit_rate(cells):
    accepted = [e for e in cells.values() if e["success_at_b"]]
    aud = [ov_by_ep[e["episode_id"]] for e in accepted if e["episode_id"] in ov_by_ep]
    if not aud:
        return None, 0, 0
    n_ov = sum(1 for r in aud if r.get("overfit"))
    return n_ov / len(aud), len(aud), n_ov


e4 = {}
sweep_tasks = {t for t, _ in E4[20]}
sweep_cells_100 = {c: TY[c] for c in TY if c[0] in sweep_tasks and c[1] in {1, 2, 3}}
for k, cells in [(100, sweep_cells_100)] + sorted(E4.items(), reverse=True):
    rate, n_aud, n_ov = overfit_rate(cells)
    acc = mean([e["success_at_b"] for e in cells.values()])
    e4[str(k)] = {
        "n_cells": len(cells),
        "accepted_rate": round(acc, 4),
        "n_audited": n_aud, "n_overfit": n_ov,
        "overfit_rate": round(rate, 4) if rate is not None else None,
        "true_success": round(acc * (1 - rate), 4) if rate is not None else None,
        "oracle_calls": round(mean([e["n_oracle_calls"] for e in cells.values()]), 3),
        "sandbox_runs": round(mean([e["sandbox_runs"] for e in cells.values()]), 2),
        "tokens_in": round(mean([e["tokens_in"] for e in cells.values()]), 1),
    }
numbers["e4_oracle_depth"] = e4

# ── E5: typing noise + the random-partition control ────────────────────────
e5 = {}
for label, cells in [("1.0", sweep_cells_100)] + \
        [(str(c), v) for c, v in sorted(E5.items(), reverse=True)] + \
        [("random", E5R)]:
    if not cells:
        continue
    e5[label] = {
        "n_cells": len(cells),
        "success": round(mean([e["success_at_b"] for e in cells.values()]), 4),
        "oracle_calls": round(mean([e["n_oracle_calls"] for e in cells.values()]), 3),
        "redundancy_paid": round(mean([e["redundancy_paid"] for e in cells.values()]), 4),
        "redundancy_caught": round(mean([e["redundancy_caught"] for e in cells.values()]), 3),
        "guard_sec": round(mean([e.get("guard_sec") or 0.0 for e in cells.values()]), 3),
        "guard_evaluations": round(mean([e["guard_evaluations"] for e in cells.values()]), 3),
    }
numbers["e5_typing_noise"] = e5

# ── E9: free guarded rounds ────────────────────────────────────────────────
e9 = {}
for m, cells in E9.items():
    base = {"no_memory": NM, "untyped": UN, "typed": TY}[m]
    cs = sorted(set(cells) & set(base))
    if not cs:
        continue
    e9[m] = {
        "n_cells": len(cs),
        "base": {"success": round(mean([base[c]["success_at_b"] for c in cs]), 4),
                 "oracle_calls": round(mean([base[c]["n_oracle_calls"] for c in cs]), 3),
                 "proposals": round(mean([base[c]["proposals"] for c in cs]), 2),
                 "tokens_in": round(mean([base[c]["tokens_in"] for c in cs]), 1)},
        "free": {"success": round(mean([cells[c]["success_at_b"] for c in cs]), 4),
                 "oracle_calls": round(mean([cells[c]["n_oracle_calls"] for c in cs]), 3),
                 "proposals": round(mean([cells[c]["proposals"] for c in cs]), 2),
                 "tokens_in": round(mean([cells[c]["tokens_in"] for c in cs]), 1)},
        "success_flips": {
            "free": sum(1 for c in cs if cells[c]["success_at_b"] > base[c]["success_at_b"]),
            "base": sum(1 for c in cs if cells[c]["success_at_b"] < base[c]["success_at_b"])},
    }
    e9[m]["success_flips"]["p"] = sign_test(
        [(-cells[c]["success_at_b"], -base[c]["success_at_b"]) for c in cs])["p"]
# pooled over the two memory arms
pooled = []
for m in ("untyped", "typed"):
    base = {"untyped": UN, "typed": TY}[m]
    for c in sorted(set(E9[m]) & set(base)):
        pooled.append((-E9[m][c]["success_at_b"], -base[c]["success_at_b"]))
e9["pooled_memory_arms"] = sign_test(pooled)
numbers["e9_free_guarded"] = e9

# ── soundness, assumption checks, integrity ────────────────────────────────
k100_overfit = [r for r in overfit
                if ep_cfg.get(r["episode_id"], {}).get("max_examples") == 100]
numbers["soundness"] = {
    "k100_audited_accepts": len(k100_overfit),
    "k100_overfit": sum(1 for r in k100_overfit if r.get("overfit")),
    "k100_regression_audited": sum(1 for r in k100_overfit
                                   if r.get("regression_rate") is not None),
    "k100_regression_broken": sum(1 for r in k100_overfit
                                  if (r.get("p2p_broken") or 0) > 0),
    "pool_strength": {
        "n_planted": pool["planted"]["n_planted"],
        "n_caught": pool["planted"]["n_caught"],
        "n_missed": pool["planted"]["n_missed"],
        "n_equivalent": pool["planted"]["n_equivalent"],
        "catch_rate": pool["planted"]["catch_rate"],
    } if pool else None,
}
numbers["coherence"] = {
    g: {"pooled": d["cross_refutation_rate"]["pooled"],
        "ci95": d["cross_refutation_rate"]["ci95"],
        "n_tasks": len(d["cross_refutation_rate"]["per_task"])}
    for g, d in (coherence or {}).get("granularities", {}).items()
}
if TY:
    numbers["coherence"]["in_vivo_bucket_hit_rate"] = None  # filled from rounds below
numbers["redundancy_metrics"] = {
    a: {k: v for k, v in d.items() if not isinstance(v, (dict, list))}
    for a, d in (redundancy or {}).get("arms", {}).items()
}
numbers["repeated_sampling"] = {
    k: v for k, v in (redundancy or {}).get("repeated_sampling", {}).items()
    if not isinstance(v, (dict, list))
}
numbers["patch_quality"] = {
    a: {k: d.get(k) for k in ("n_accepted_episodes", "loc_ratio", "hunk_ratio",
                              "correct_over_plausible", "overfit_rate",
                              "regression_rate")}
    for a, d in (patchq or {}).get("arms", {}).items()
}
numbers["anchoring"] = {
    c: {"n_episodes": d["n_episodes"], "anchoring_rate": d["anchoring_rate"],
        "exclusion_rate": d["exclusion_rate"],
        "per_stratum": {b: {"anchoring_rate": s["anchoring_rate"],
                            "n_episodes": s["n_episodes"]}
                        for b, s in d.get("per_stratum", {}).items()}}
    for c, d in (anchoring or {}).get("by_typing_coherence", {}).items()
}
numbers["failure_taxonomy"] = {k: v for k, v in (taxonomy or {}).get("failure_taxonomy", {}).items()
                               if not isinstance(v, list)}
numbers["theory_fit"] = {
    k: {kk: vv for kk, vv in v.items() if not isinstance(vv, list)}
    for k, v in (theory or {}).get("fit", {}).items()
}
numbers["integrity"] = {
    "duplicate_cell_groups": n_dup_groups,
    "duplicate_groups_disagreeing": len(disagree),
    "disagreeing_examples": [list(d[:2]) for d in disagree[:4]],
}

# ── round-level pass for bucket hits + CRN identity + prompt growth ────────
bucket_hit = collections.Counter()
bucket_ref = collections.Counter()
r1_patch = {}
tokens_by_round = collections.defaultdict(lambda: collections.defaultdict(list))
arm_of = {}
for key, e in dedup.items():
    arm_of[e["episode_id"]] = cfg(e)

with (RUN / "episodes.jsonl").open() as f:
    seen = set()
    for line in f:
        r = json.loads(line)
        k = (r["episode_id"], r["round_index"])
        if k in seen:
            continue
        seen.add(k)
        a = arm_of.get(r["episode_id"])
        if a is None:
            continue
        mode, guard, steer, ffb, kx, c, rand, fgr, audit = a
        if mode == "typed" and guard and r.get("guard_evaluations"):
            lab = "random" if rand else str(c)
            if kx == 100 and not fgr and not audit:
                bucket_hit[lab] += 1
                bucket_ref[lab] += bool(r.get("bucket_hit"))
        # round-1 draws, for the CRN identity check
        if r["round_index"] == 1 and kx == 100 and c == 1.0 and not rand and not fgr and not audit:
            name = {("no_memory", True, True, True): "E1",
                    ("untyped", True, True, False): "untyped",
                    ("typed", True, True, False): "typed",
                    ("typed", True, False, False): "guard_only",
                    ("typed", False, True, False): "steer_only"}.get((mode, guard, steer, ffb))
            if name:
                r1_patch.setdefault(name, {})[(r["task"], r["seed"])] = r.get("patch")
        # prompt-token growth by round, main grid only
        if kx == 100 and c == 1.0 and not rand and not fgr and not audit and guard and steer:
            if r.get("prompt_tokens") is not None:
                tokens_by_round[mode][r["round_index"]].append(r["prompt_tokens"])

numbers["coherence"]["in_vivo_bucket_hit_rate"] = {
    lab: round(bucket_ref[lab] / bucket_hit[lab], 4)
    for lab in sorted(bucket_hit) if bucket_hit[lab]
}
numbers["e5_typing_noise_bucket_hit"] = numbers["coherence"]["in_vivo_bucket_hit_rate"]

crn = {}
e1r = r1_patch.get("E1", {})
for name, d in r1_patch.items():
    if name == "E1":
        continue
    common = [c for c in d if c in e1r]
    crn[name] = {"n": len(common),
                 "identical": sum(1 for c in common if d[c] == e1r[c])}
numbers["integrity"]["crn_round1_identity_vs_E1"] = crn

growth = {}
for mode, d in tokens_by_round.items():
    xs = sorted(d)
    ys = [mean(d[i]) for i in xs]
    n = len(xs)
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    growth[mode] = {
        "slope_tokens_per_round": round(
            sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom, 3) if denom else None,
        "round1_mean": round(ys[0], 1), "round20_mean": round(ys[-1], 1),
    }
numbers["prompt_growth"] = growth

OUT.write_text(json.dumps(numbers, indent=1, sort_keys=False) + "\n")
print(f"wrote {OUT}")
for k in numbers:
    print(" ", k)
