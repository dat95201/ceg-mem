"""Every number the paper reports about the SECOND proposer, from the two frozen runs.

The right block of Table II, RQ6 (sec:results-scale), the primary-band
supplement table and the second-proposer sentences of the abstract, the
introduction and the conclusion all come from here and from nowhere else. The
7B block of Table II stays with extract_numbers.py; this tool reads the local
run only for the pieces RQ6 compares against (the same 55 tasks under the 7B
proposer, the 7B band profile) and for the FROZEN banding.

Accounting is tables/main.tex's, read off the pipeline's results_real.json of
each run (scripts/freeze_results.py): every per-episode quantity is already
truncated at the first accept, guard replays are billed as program executions
(`sandbox_runs`), and `redundancy_paid` is the CRN-joined count. Cells are
(task, seed); the main grid is 99 tasks x 5 seeds; the steer-only arm is 3 seeds.

Banding is the frozen 7B banding (the local run's strata.json), which is what
the pre-specification register fixed for the second proposer (PRESPEC §3, §6):
the second proposer's own pi-hat banding is NOT used for any reported number.

Statistics, implemented here so the artifact needs no scipy: exact two-sided
sign tests per cell and per task (task means), and a task-clustered bootstrap
(10,000 resamples of tasks, fixed seed) for the interval on the paired mean
difference. The band-heterogeneity test is a permutation test of band labels
over per-task mean differences (20,000 permutations, fixed seed), statistic =
between-band weighted sum of squares of band means.

Usage: python3 second_proposer.py --local-run DIR --cloud-run DIR --out secondproposer.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import random

BANDS = ("dead", "hard", "medium", "easy", "too_easy")
PRIMARY = ("hard", "medium", "easy")
BOOT_N = 10000
BOOT_SEED = 20260908
PERM_N = 20000
PERM_SEED = 7
# 'lower is better' for the sign-test orientation reported as typed wins/losses
COST = {"oc", "sb", "redun", "gev", "pt", "sec"}
METRICS = ("acc20", "acc2", "oc", "sb", "redun", "gev", "pt", "sec")
METRIC_NOTE = {
    "acc20": "repair rate at the full budget B=20 (accepted within the episode)",
    "acc2": "repair rate at a matched budget of 2 oracle calls",
    "oc": "oracle calls per episode (truncated at first accept)",
    "sb": "program executions per episode (oracle cases + guard replays)",
    "redun": "paid redundant verifications per episode (CRN-joined)",
    "gev": "memory entries consulted per episode (guard evaluations)",
    "pt": "prompt tokens per episode",
    "sec": "wall-clock seconds per episode (llm + oracle + guard)",
}


# ── load ────────────────────────────────────────────────────────────────────
def arm_of(e: dict) -> str | None:
    """Main-grid arms only: default knobs, no audit, no free-guarded, no random."""
    if e.get("audit_guarded") or e.get("free_guarded_rounds") or e.get("typing_random"):
        return None
    if e.get("max_examples") != 100 or e.get("typing_noise_c") != 1.0:
        return None
    if e.get("granularity", "fine") != "fine":
        return None
    if e.get("history", "off") != "off" or e.get("selftest") or (e.get("oracle_skip_p") or 0) > 0:
        return None
    m, g, s = e["mode"], e["guard_on"], e["steer_on"]
    if m == "no_memory" and g and s and e.get("force_full_budget"):
        return "NM"
    if m == "untyped" and g and s:
        return "UN"
    if m == "typed" and g and s:
        return "TY"
    if m == "typed" and g and not s:
        return "GO"
    if m == "typed" and (not g) and s:
        return "SO"
    return None


def load_run(run: pathlib.Path) -> tuple[dict, dict]:
    data = json.loads((run / "results_real.json").read_text())
    cells: dict[tuple, dict] = {}
    dup = collections.Counter()
    for e in data["episodes"]:
        a = arm_of(e)
        if a is None:
            continue
        k = (a, e["task"], e["seed"])
        if k in cells:
            dup[a] += 1
            continue
        cells[k] = dict(
            acc=e["oracle_calls_to_accept"] if e["accepted"] else None,
            oc=e["n_oracle_calls"], sb=e["sandbox_runs"], redun=e["redundancy_paid"],
            gev=e["guard_evaluations"], pt=e["tokens_in"], sec=e["wall_sec"],
        )
    meta = {"model": data.get("model"), "n_episodes": data.get("n_episodes"),
            # every logged round of the frozen log, E1's post-accept rounds
            # included: the size the Data Availability section quotes
            "n_rounds_logged": sum(e.get("n_rounds_logged", 0) for e in data["episodes"]),
            "duplicate_cells_ignored": dict(dup)}
    return cells, meta


def load_bands(run: pathlib.Path) -> dict[str, str]:
    d = json.loads((run / "strata.json").read_text())
    return {t["name"]: t["stratum"] for t in d["tasks"]}


# ── primitives ──────────────────────────────────────────────────────────────
def cells_for(S, arms, tasks=None):
    s = None
    for a in arms:
        c = {k[1:] for k in S if k[0] == a}
        s = c if s is None else s & c
    if tasks is not None:
        s = {c for c in s if c[0] in tasks}
    return sorted(s)


def val(S, a, c, f):
    v = S[(a,) + c]
    if f == "acc20":
        return 1.0 if v["acc"] is not None else 0.0
    if f == "acc2":
        return 1.0 if (v["acc"] is not None and v["acc"] <= 2) else 0.0
    return v[f]


def mean(S, a, cs, f):
    # math.fsum: exactly rounded, so identical on every Python version and platform
    return math.fsum(val(S, a, c, f) for c in cs) / len(cs)


def sign_p(w, l):
    n = w + l
    if n == 0:
        return 1.0
    m = min(w, l)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n)


def task_means(S, a1, a2, cs, f):
    bt = collections.defaultdict(lambda: ([], []))
    for c in cs:
        bt[c[0]][0].append(val(S, a1, c, f))
        bt[c[0]][1].append(val(S, a2, c, f))
    # fsum: two arms with the same multiset of values get the SAME mean, so a
    # task-level tie is a tie on every platform (the sign tests depend on it)
    return {t: (math.fsum(x) / len(x), math.fsum(y) / len(y)) for t, (x, y) in bt.items()}


def oriented_sign(pairs, f):
    """(wins, losses, p) with wins = the FIRST arm is better on metric f."""
    lower = f in COST
    w = sum(1 for a, b in pairs if (a < b if lower else a > b))
    l = sum(1 for a, b in pairs if (a > b if lower else a < b))
    return w, l, sign_p(w, l)


def boot_ci(S, a1, a2, cs, f):
    bytask = collections.defaultdict(list)
    for c in cs:
        bytask[c[0]].append(val(S, a1, c, f) - val(S, a2, c, f))
    tasks = sorted(bytask)
    rng = random.Random(BOOT_SEED)
    out = []
    for _ in range(BOOT_N):
        s = n = 0
        for _ in range(len(tasks)):
            d = bytask[rng.choice(tasks)]
            s += math.fsum(d)
            n += len(d)
        out.append(s / n)
    out.sort()
    return out[int(0.025 * BOOT_N)], out[int(0.975 * BOOT_N)]


def contrast(S, a1, a2, cs, f):
    """a1 vs a2 on f: means, difference, oriented sign tests, clustered CI."""
    cell_pairs = [(val(S, a1, c, f), val(S, a2, c, f)) for c in cs]
    w, l, p = oriented_sign(cell_pairs, f)
    tw, tl, tp = oriented_sign(list(task_means(S, a1, a2, cs, f).values()), f)
    lo, hi = boot_ci(S, a1, a2, cs, f)
    m1, m2 = mean(S, a1, cs, f), mean(S, a2, cs, f)
    return {
        "mean_a": m1, "mean_b": m2, "diff": m1 - m2,
        "fold_b_over_a": (m2 / m1) if m1 else None,
        "cells": {"wins": w, "losses": l, "p": p},
        "tasks": {"wins": tw, "losses": tl, "p": tp},
        "ci95_diff": [lo, hi],
        "n_cells": len(cs), "n_tasks": len({c[0] for c in cs}),
        "_orientation": f"wins = {a1} better ({'lower' if f in COST else 'higher'} is better)",
    }


def interaction(S, a1, a2, cs, f, band_of, bands):
    tm = task_means(S, a1, a2, cs, f)
    tasks = [t for t in sorted(tm) if band_of.get(t) in bands]
    diffs = {t: tm[t][0] - tm[t][1] for t in tasks}
    labels = [band_of[t] for t in tasks]

    def stat(lab):
        g = collections.defaultdict(list)
        for t, b in zip(tasks, lab):
            g[b].append(diffs[t])
        gm = math.fsum(diffs.values()) / len(tasks)
        return math.fsum(len(v) * (math.fsum(v) / len(v) - gm) ** 2 for v in g.values())

    obs = stat(labels)
    rng = random.Random(PERM_SEED)
    ge = 0
    lab = labels[:]
    for _ in range(PERM_N):
        rng.shuffle(lab)
        if stat(lab) >= obs - 1e-12:
            ge += 1
    band_means = {}
    for b in bands:
        v = [diffs[t] for t in tasks if band_of[t] == b]
        band_means[b] = {"mean_diff": (math.fsum(v) / len(v)) if v else None, "n_tasks": len(v)}
    return {"p": (ge + 1) / (PERM_N + 1), "band_means": band_means,
            "n_permutations": PERM_N, "statistic": "between-band weighted SS of per-task mean differences"}


def block(S, tasks):
    cs = cells_for(S, ["NM", "UN", "TY"], tasks)
    out = {"n_cells_per_arm": len(cs), "n_tasks": len({c[0] for c in cs}), "metrics": {}}
    for f in METRICS:
        out["metrics"][f] = {
            "_what": METRIC_NOTE[f],
            "NM": mean(S, "NM", cs, f), "UN": mean(S, "UN", cs, f), "TY": mean(S, "TY", cs, f),
            "TY_vs_UN": contrast(S, "TY", "UN", cs, f),
            "TY_vs_NM": contrast(S, "TY", "NM", cs, f),
            "UN_vs_NM": contrast(S, "UN", "NM", cs, f),
        }
    cso = cells_for(S, ["NM", "SO"], tasks)
    if cso:
        out["steer_only"] = {
            "n_cells": len(cso), "n_tasks": len({c[0] for c in cso}),
            "acc20": {"SO": mean(S, "SO", cso, "acc20"), "NM": mean(S, "NM", cso, "acc20"),
                      "SO_vs_NM": contrast(S, "SO", "NM", cso, "acc20")},
            "pt": {"SO": mean(S, "SO", cso, "pt"), "NM": mean(S, "NM", cso, "pt"),
                   "ratio": mean(S, "SO", cso, "pt") / mean(S, "NM", cso, "pt")},
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local-run", type=pathlib.Path, required=True)
    ap.add_argument("--cloud-run", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()

    L, lmeta = load_run(args.local_run)
    C, cmeta = load_run(args.cloud_run)
    band = load_bands(args.local_run)          # the FROZEN banding, for both
    primary = {t for t, b in band.items() if b in PRIMARY}

    out = {
        "_provenance": {
            "local_run": str(args.local_run), "cloud_run": str(args.cloud_run),
            "local_model": lmeta["model"], "cloud_model": cmeta["model"],
            "banding": f"{args.local_run}/strata.json (frozen 7B banding; PRESPEC section 3/6)",
            "bootstrap": {"resamples": BOOT_N, "seed": BOOT_SEED, "unit": "task"},
            "permutation": {"n": PERM_N, "seed": PERM_SEED},
            "duplicate_cells_ignored": {"local": lmeta["duplicate_cells_ignored"],
                                        "cloud": cmeta["duplicate_cells_ignored"]},
        },
        "cloud": {
            "all": block(C, None),
            "primary55": block(C, primary),
        },
        "local_on_same_universes": {
            "all": block(L, None),
            "primary55": block(L, primary),
        },
        "band_profile": {},
        "criteria_F7": {},
    }
    # per-band typed - untyped and steer-only - no-memory, both proposers
    for name, S in (("cloud", C), ("local", L)):
        prof = {}
        cs = cells_for(S, ["NM", "UN", "TY"])
        for b in BANDS:
            tb = {t for t, bb in band.items() if bb == b}
            cb = [c for c in cs if c[0] in tb]
            prof[b] = {
                "n_tasks": len(tb),
                "acc20": {"NM": mean(S, "NM", cb, "acc20"), "UN": mean(S, "UN", cb, "acc20"),
                          "TY": mean(S, "TY", cb, "acc20"),
                          "TY_vs_UN": contrast(S, "TY", "UN", cb, "acc20")},
                "oc": {"TY_vs_UN": contrast(S, "TY", "UN", cb, "oc")},
            }
        cso = cells_for(S, ["NM", "SO"])
        out["band_profile"][name] = {
            "bands": prof,
            "interaction_TY_vs_UN_acc20": interaction(S, "TY", "UN", cs, "acc20", band, BANDS),
            "interaction_TY_vs_NM_acc20": interaction(S, "TY", "NM", cs, "acc20", band, BANDS),
            "interaction_SO_vs_NM_acc20": interaction(S, "SO", "NM", cso, "acc20", band, BANDS),
        }
    # the two pre-specified criteria (PRESPEC section 3, F7), on the 55 tasks
    p55 = out["cloud"]["primary55"]
    fold_un = p55["metrics"]["oc"]["NM"] / p55["metrics"]["oc"]["UN"]
    fold_ty = p55["metrics"]["oc"]["NM"] / p55["metrics"]["oc"]["TY"]
    so = p55["steer_only"]["acc20"]["SO_vs_NM"]
    out["criteria_F7"] = {
        "universe": "the 55 tasks the frozen banding places in hard/medium/easy",
        "a_guard_oracle_reduction": {
            "threshold": 3.0, "untyped_fold": fold_un, "typed_fold": fold_ty,
            "held": bool(min(fold_un, fold_ty) >= 3.0),
        },
        "b_steer_only_repair_ci_contains_zero": {
            "diff_pp": so["diff"] * 100, "ci95_pp": [so["ci95_diff"][0] * 100, so["ci95_diff"][1] * 100],
            "tasks": so["tasks"], "held": bool(so["ci95_diff"][0] <= 0 <= so["ci95_diff"][1]),
        },
    }
    # headline folds on all 99, as the abstract quotes them
    a = out["cloud"]["all"]["metrics"]
    out["headline"] = {
        "oracle_fold_untyped": a["oc"]["NM"] / a["oc"]["UN"],
        "oracle_fold_typed": a["oc"]["NM"] / a["oc"]["TY"],
        "exec_fold_untyped": a["sb"]["NM"] / a["sb"]["UN"],
        "exec_fold_typed": a["sb"]["NM"] / a["sb"]["TY"],
        "paid_fold_untyped": a["redun"]["NM"] / a["redun"]["UN"],
        "paid_fold_typed": a["redun"]["NM"] / a["redun"]["TY"],
        "typed_prompt_tokens_over_untyped": a["pt"]["TY"] / a["pt"]["UN"],
        "typed_wall_over_untyped": a["sec"]["TY"] / a["sec"]["UN"],
        "steer_only_prompt_tokens_over_no_memory": out["cloud"]["all"]["steer_only"]["pt"]["ratio"],
        "typed_prompt_tokens_over_untyped_primary55": p55["metrics"]["pt"]["TY"] / p55["metrics"]["pt"]["UN"],
        "n_cloud_episodes": cmeta["n_episodes"],
        "n_local_episodes": lmeta["n_episodes"],
        "rounds_logged": {"local": lmeta["n_rounds_logged"], "cloud": cmeta["n_rounds_logged"]},
    }
    args.out.write_text(json.dumps(out, indent=1) + "\n")
    h = out["headline"]
    print(f"wrote {args.out}")
    print(f"  cloud oracle folds x{h['oracle_fold_untyped']:.2f} (untyped) x{h['oracle_fold_typed']:.2f} (typed); "
          f"exec x{h['exec_fold_untyped']:.2f}/x{h['exec_fold_typed']:.2f}; paid x{h['paid_fold_untyped']:.1f}/x{h['paid_fold_typed']:.1f}")
    f7 = out["criteria_F7"]
    print(f"  F7(a) folds on 55: x{fold_un:.2f} / x{fold_ty:.2f} -> {'held' if f7['a_guard_oracle_reduction']['held'] else 'FIRED'}")
    print(f"  F7(b) steer-only - no-memory on 55: {f7['b_steer_only_repair_ci_contains_zero']['diff_pp']:+.1f} pp, "
          f"CI {[round(x, 1) for x in f7['b_steer_only_repair_ci_contains_zero']['ci95_pp']]} -> "
          f"{'held' if f7['b_steer_only_repair_ci_contains_zero']['held'] else 'FIRED'}")


if __name__ == "__main__":
    main()
