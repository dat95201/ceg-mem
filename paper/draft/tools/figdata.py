"""Per-cell and per-round arrays the figures need and numbers.json omits.

numbers.json is the paper's provenance artifact: one line per reported
quantity, readable by a human checking a claim. Plotting needs the raw arrays
behind some of those quantities, and they would drown it. So they live here,
computed from the same frozen log, with the same dedup rule and the same arm
filters as extract_numbers.py -- that identity is the point, and the reason
this file re-derives the round-level series instead of copying the pipeline's
analysis.json: two independent computations of one series is how a figure ends
up labelled with a number the text does not have.

Usage: python3 figdata.py <run_dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
RUN = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])

results = json.loads((RUN / "results_real.json").read_text())


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


UN = arm("untyped")
GO = arm("typed", steer=False)
TY = arm("typed")
gc = sorted(set(GO) & set(UN))
tu = sorted(set(TY) & set(UN))

# ── round-level pass: prompt tokens by round, main grid only ───────────────
# The arm identity is pinned by the same five-way config map extract_numbers.py
# uses, NOT by mode alone: a bare `mode == "no_memory"` test also collects the
# free-guarded condition's control rows, which is what made the no-memory slope
# disagree between artifacts.
MAIN_ARM = {
    ("no_memory", True, True, True): "no_memory",
    ("untyped", True, True, False): "untyped",
    ("typed", True, True, False): "typed",
}
arm_of = {e["episode_id"]: cfg(e) for e in dedup.values()}
tokens_by_round = collections.defaultdict(lambda: collections.defaultdict(list))

with (RUN / "episodes.jsonl").open() as f:
    seen = set()
    for line in f:
        r = json.loads(line)
        key = (r["episode_id"], r["round_index"])
        if key in seen:
            continue
        seen.add(key)
        a = arm_of.get(r["episode_id"])
        if a is None:
            continue
        mode, guard, steer, ffb, kx, c, rand, fgr, audit = a
        if kx != 100 or c != 1.0 or rand or fgr or audit:
            continue
        name = MAIN_ARM.get((mode, guard, steer, ffb))
        if name is None or r.get("prompt_tokens") is None:
            continue
        tokens_by_round[name][r["round_index"]].append(r["prompt_tokens"])


def series(d):
    xs = sorted(d)
    ys = [sum(d[i]) / len(d[i]) for i in xs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) if den else 0.0
    return {"rounds": xs,
            "mean_by_round": [round(y, 2) for y in ys],
            "n_by_round": [len(d[i]) for i in xs],
            "slope": round(slope, 3)}


fig = {
    "provenance": {
        "run": str(RUN),
        "dedup": "episodes keyed on (task, seed, full config); "
                 "lexicographically-first episode_id wins",
        "note": "same filters as extract_numbers.py; if a number here and in "
                "numbers.json disagree, one of the two filters is wrong",
    },
    "guard_sec_paired": {
        "note": "guard seconds per episode on the 297 cells where guard-only and "
                "untyped produce the same episodes; the clean cost test",
        "cells": [f"{t}|{s}" for t, s in gc],
        "untyped": [round(UN[c].get("guard_sec") or 0.0, 4) for c in gc],
        "guard_only": [round(GO[c].get("guard_sec") or 0.0, 4) for c in gc],
        "guard_evaluations_untyped": [UN[c]["guard_evaluations"] for c in gc],
        "guard_evaluations_guard_only": [GO[c]["guard_evaluations"] for c in gc],
    },
    "oracle_calls_paired_typed_untyped": {
        "cells": [f"{t}|{s}" for t, s in tu],
        "typed": [TY[c]["n_oracle_calls"] for c in tu],
        "untyped": [UN[c]["n_oracle_calls"] for c in tu],
    },
    "context_tokens_by_round": {
        "note": "mean prompt tokens at each round index, main grid only "
                "(k=100, c=1.0, no random partition, no free-guarded rounds, "
                "no audit condition); slope is least squares over the rounds "
                "present. Later rounds average over fewer episodes -- n_by_round "
                "is carried so a reader can see the survivorship.",
        **{name: series(d) for name, d in sorted(tokens_by_round.items())},
    },
}
OUT.write_text(json.dumps(fig) + "\n")
print(f"wrote {OUT}: {len(gc)} guard-paired cells, {len(tu)} typed/untyped cells")
for name, d in sorted(tokens_by_round.items()):
    s = series(d)
    print(f"  {name:10s} slope {s['slope']:+7.3f} tok/round  "
          f"r1 {s['mean_by_round'][0]:.1f} -> r{s['rounds'][-1]} "
          f"{s['mean_by_round'][-1]:.1f}  (n {s['n_by_round'][0]} -> {s['n_by_round'][-1]})")
