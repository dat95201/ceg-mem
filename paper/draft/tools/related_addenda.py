"""Zero-execution recomputations that the Related Work section needs.

Every quantity here is derived by replaying the frozen round log; nothing is
executed, so these numbers cost no sandbox runs and can be regenerated from the
shipped artifact alone.  The arm-identity rule is the one in review_addenda.py
and extract_numbers.py, so a round counted here is a round counted there.

Four blocks:

  nonvacuity     Of the rounds the guard blocked, how many re-proposed a patch
                 byte-identical to one already refuted this episode, and how
                 many were a *different* patch caught by a stored input.  The
                 first is zero by construction in classical CEGIS, where the
                 synthesizer is constrained to satisfy the accumulated
                 counterexamples; the second is what generalization means here.

  ordering       Whether the lambda-index can be told apart from a flat scan on
                 this corpus: the size of memory when a block happened, and how
                 many entries the guard actually consulted.  Ordering is
                 provably vacuous whenever memory holds at most one entry.

  discovery      Whether the stored counterexamples are just the failing case
                 the benchmark hands over.  Round 1 is byte-identical in every
                 arm under common random numbers and memory is empty there, so
                 the round-1 counterexample of a cell is the "given" one; every
                 distinct input beyond it was found by the loop.

  bucket         How often the lambda-matched bucket held a refuting entry --
                 the index's hit rate, reported separately from its benefit.

Usage: python3 related_addenda.py <run_dir> <out.json>
"""
from __future__ import annotations

import collections
import json
import pathlib
import statistics
import sys

RUN = pathlib.Path(sys.argv[1])
OUT = pathlib.Path(sys.argv[2])

# -- arm identity: identical to review_addenda.py's cfg() ------------------
CFG_FIELDS = ("mode", "guard_on", "steer_on", "force_full_budget",
              "max_examples", "typing_noise_c", "typing_random",
              "free_guarded_rounds", "audit_guarded")


def cfg(r):
    return (r["mode"], r["guard_on"], r["steer_on"], r["force_full_budget"],
            r["max_examples"], r["typing_noise_c"], bool(r.get("typing_random")),
            bool(r.get("free_guarded_rounds")), bool(r.get("audit_guarded")))


# The three main-grid guarded arms, in the same parameterization as the paper.
MAIN = {
    ("untyped", True, True, False, 100, 1.0, False, False, False): "untyped",
    ("typed", True, True, False, 100, 1.0, False, False, False): "typed",
    ("typed", True, False, False, 100, 1.0, False, False, False): "guard_only",
}


def key(x):
    """A hashable, order-insensitive identity for a counterexample input."""
    return json.dumps(x, sort_keys=True, separators=(",", ":"))


# -- replay ---------------------------------------------------------------
cells = collections.defaultdict(list)
for line in (RUN / "episodes.jsonl").open():
    r = json.loads(line)
    name = MAIN.get(cfg(r))
    if name is not None:
        cells[(name, r["task"], r["seed"])].append(r)

stats = {a: {
    "blocks": 0, "verbatim": 0, "generalized": 0,
    "mem_sizes": [], "consultations": collections.Counter(),
    "bucket_hit": 0, "bucket_hit_refuted": 0, "bucket_logged": 0,
    "verbatim_any": 0, "proposals": 0, "repeat_proposals": 0,
    "cells": 0, "cells_with_block": 0,
    "distinct_inputs": [], "inputs_beyond_given": 0, "inputs_total": 0,
    "cells_multi_input": 0,
} for a in set(MAIN.values())}

for (name, task, seed), rounds in cells.items():
    rounds.sort(key=lambda r: r["round_index"])
    s = stats[name]
    s["cells"] += 1
    mem_patches = set()      # patches already refuted *and stored* this episode
    seen_patches = set()     # every patch proposed this episode, any outcome
    mem_inputs = []          # counterexample inputs, in insertion order
    given = None             # the round-1 counterexample, if any
    blocked_here = False

    for r in rounds:
        p = r.get("patch")
        seen_before = p is not None and p in seen_patches
        if p is not None:
            s["proposals"] += 1
            s["repeat_proposals"] += seen_before
            seen_patches.add(p)

        if r.get("guarded"):
            blocked_here = True
            s["blocks"] += 1
            s["mem_sizes"].append(len(mem_inputs))
            s["verbatim_any"] += seen_before
            if p is not None and p in mem_patches:
                s["verbatim"] += 1
            else:
                s["generalized"] += 1
            ge = r.get("guard_evaluations")
            if ge is not None:
                s["consultations"][int(ge)] += 1
            if r.get("bucket_hit") is not None:
                s["bucket_logged"] += 1
                s["bucket_hit"] += bool(r.get("bucket_hit"))
                s["bucket_hit_refuted"] += bool(r.get("bucket_hit_refuted"))
            continue

        ce = r.get("counterexample_args")
        if ce is not None:                      # the oracle refuted this patch
            k = key(ce)
            if given is None and r["round_index"] <= 1:
                given = k
            mem_patches.add(r["patch"])
            mem_inputs.append(k)

    if blocked_here:
        s["cells_with_block"] += 1
    if mem_inputs:
        distinct = set(mem_inputs)
        s["distinct_inputs"].append(len(distinct))
        s["inputs_total"] += len(distinct)
        if given is not None:
            s["inputs_beyond_given"] += len(distinct - {given})
        if len(distinct) > 1:
            s["cells_multi_input"] += 1


# -- report ---------------------------------------------------------------
def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


out = {
    "_provenance": {
        "run": str(RUN),
        "rule": "arm identity identical to review_addenda.py cfg(); "
                "no program was executed to produce any number in this file",
        "arms": {v: dict(zip(CFG_FIELDS, k)) for k, v in MAIN.items()},
    },
    "nonvacuity": {}, "ordering": {}, "discovery": {}, "bucket": {},
    "cost_per_round": {}, "soundness": {},
}

# -- can the depth-k oracle miss a stored counterexample? ------------------
# src/oracle.py draws its check set per call (seed = episode seed + round), so
# G_k is defined relative to a draw.  Two things bound the hazard: the draw is
# the whole pool whenever k reaches the pool size, and the audit condition pays
# the oracle on blocked rounds and asks whether it accepts.
pools = json.loads((RUN / "pool" / "tasks.json").read_text())
pools = pools["tasks"] if isinstance(pools, dict) and "tasks" in pools else pools
corpus = {t for (_, t, _) in cells}
sizes = [t["n_test_cases"] for t in pools
         if isinstance(t, dict) and t.get("name") in corpus]

audited = violations = 0
for line in (RUN / "episodes.jsonl").open():
    r = json.loads(line)
    if r.get("audit_guarded") and r.get("guarded"):
        audited += 1
        violations += "GUARD SOUNDNESS VIOLATION" in (r.get("reason") or "")

out["soundness"] = {
    "corpus_tasks": len(sizes),
    "pool_size_min": min(sizes), "pool_size_median": statistics.median(sizes),
    "pool_size_max": max(sizes),
    "tasks_fully_checked_at_k": {k: sum(1 for x in sizes if x <= k)
                                 for k in (100, 20, 8, 3)},
    "audited_blocked_rounds": audited,
    "guard_soundness_violations": violations,
    # exact one-sided 95% Clopper-Pearson upper bound for 0 failures in n
    "upper95": round(1 - 0.05 ** (1 / audited), 6) if audited else None,
}

# -- what a round costs, by how it ended -----------------------------------
# loop.py bills sandbox_runs = guard_evaluations + steering runs + cases the
# oracle tried, so this is the same execution counter the paper reports.
ALL = dict(MAIN)
ALL[("no_memory", True, True, True, 100, 1.0, False, False, False)] = "no_memory"
cost = collections.defaultdict(lambda: {"guard": [], "oracle": []})
for line in (RUN / "episodes.jsonl").open():
    r = json.loads(line)
    name = ALL.get(cfg(r))
    if name is None or r.get("sandbox_runs") is None:
        continue
    cost[name]["guard" if r.get("guarded") else "oracle"].append(r["sandbox_runs"])

for name, d in sorted(cost.items()):
    out["cost_per_round"][name] = {
        "guard_resolved_rounds": len(d["guard"]),
        "exec_per_guard_resolved": round(statistics.mean(d["guard"]), 2) if d["guard"] else None,
        "oracle_rounds": len(d["oracle"]),
        "exec_per_oracle_round": round(statistics.mean(d["oracle"]), 2) if d["oracle"] else None,
    }

for name, s in sorted(stats.items()):
    n = s["blocks"]
    out["nonvacuity"][name] = {
        "blocks": n,
        "verbatim_redraw": s["verbatim"],
        "verbatim_pct": pct(s["verbatim"], n),
        "generalized": s["generalized"],
        "generalized_pct": pct(s["generalized"], n),
        "verbatim_any_earlier": s["verbatim_any"],
        "verbatim_any_earlier_pct": pct(s["verbatim_any"], n),
        "proposals": s["proposals"],
        "repeat_proposals": s["repeat_proposals"],
        "repeat_proposal_pct": pct(s["repeat_proposals"], s["proposals"]),
    }
    sizes = s["mem_sizes"]
    cons = s["consultations"]
    tot_c = sum(cons.values())
    out["ordering"][name] = {
        "blocks": n,
        "mem_size_mean": round(statistics.mean(sizes), 2) if sizes else None,
        "mem_size_median": statistics.median(sizes) if sizes else None,
        "mem_size_max": max(sizes) if sizes else None,
        "mem_le_1_pct": pct(sum(1 for x in sizes if x <= 1), len(sizes)),
        "consult_1_pct": pct(cons.get(1, 0), tot_c),
        "consult_hist": dict(sorted(cons.items())),
    }
    d = s["distinct_inputs"]
    out["discovery"][name] = {
        "cells": s["cells"],
        "cells_with_block": s["cells_with_block"],
        "cells_that_stored": len(d),
        "distinct_inputs_mean": round(statistics.mean(d), 2) if d else None,
        "distinct_inputs_max": max(d) if d else None,
        "cells_multi_input": s["cells_multi_input"],
        "cells_multi_input_pct": pct(s["cells_multi_input"], len(d)),
        "inputs_total": s["inputs_total"],
        "inputs_beyond_given": s["inputs_beyond_given"],
        "inputs_beyond_given_pct": pct(s["inputs_beyond_given"], s["inputs_total"]),
    }
    out["bucket"][name] = {
        "logged": s["bucket_logged"],
        "hit_pct": pct(s["bucket_hit"], s["bucket_logged"]),
        "hit_refuted_pct": pct(s["bucket_hit_refuted"], s["bucket_logged"]),
    }

OUT.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out["cost_per_round"], indent=2))
