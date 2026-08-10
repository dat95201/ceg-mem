"""Select the reported corpus from a screened candidate pool, by measured pi_hat.

This is the paper's SS VI-A-b course 2 ("re-select on measured difficulty"),
executed under the three conditions SS VI-A-c attaches to it:

  1. taken now, with the data fixed in advance - the quotas and the band edges
     are arguments, written down before the screen is read;
  2. the full pilot distribution reported including everything excluded -
     data/screening.json carries every candidate screened, its pi_hat, its
     band and why it was or was not taken;
  3. the pi_hat reported in results drawn independently of the pi_hat used to
     select - the screen's draws carry nonce `pi-pilot|...` and are spent here;
     the reported pi_hat comes from E1, whose draws carry `proposal|...`. Two
     independent samples, so conditioning on this one does not bias that one,
     and regression to the mean cannot inflate what results tables print.

Why select at all. SS VI-A-a: at budget B each round of the no-memory arm is an
independent Bernoulli draw, so pi alone fixes both Pr[accept within B] and
E[rounds | accept]. Above the top band nothing is ever refuted, so no memory is
written and the three conditions coincide; below the bottom band the episode
usually exhausts B, and the primary metric (SS VII-a) is averaged over accepted
episodes only, so the task contributes no datum to it. Selecting on that -
a property of the *no-memory arm, before any treatment* - is dose-range choice,
not outcome selection (SS VI-A-c).

Bands are the proposal's own, absolute, not terciles of whatever was screened
(scripts/build_strata.py holds the same table). The two control bands are kept
deliberately rather than discarded, which is SS VI-A-b course 3: an effect that
appears only where the mechanism has room to operate is stronger evidence than
a uniform one, and `dead`/`hard` carry the long episodes that Proposition 4.5's
Theta(m)-versus-O(1) guard-cost claim is measured on (SS I, SS III).

Writes data/tasks.json (the frozen corpus) and data/screening.json (the audit).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# Paper SS VI-A / scripts/build_strata.py. Half-open upward except the top of
# `easy`, so the bands tile [0, 1] with no gap and no overlap.
BANDS: tuple[tuple[str, float, float], ...] = (
    ("dead",     0.00, 0.02),   # control: below the analysis range entirely
    ("hard",     0.02, 0.08),   # proposal's Hard
    ("medium",   0.08, 0.18),   # proposal's Medium
    ("easy",     0.18, 0.3501),  # proposal's Easy (inclusive of 0.35)
    ("too_easy", 0.3501, 1.01),  # control: above the analysis range entirely
)
PRIMARY_BANDS = ("hard", "medium", "easy")

# Unequal on purpose, for two reasons.
#
# Supply: SS V-F measured the pi_hat distribution and it is bimodal - of 60
# tasks, 25 landed above 0.35 and 16 below 0.02, leaving 4 in Easy, 4 in Medium
# and 11 in Hard. `medium` is the trough between the two modes, so a quota of 30
# there would demand roughly 450 screened candidates on its own.
#
# Demand: the proposal's own simulation puts the *largest* predicted effect at
# the low-pi end - Vargha-Delaney A12 of 0.83 / 0.96 / 1.00 for Easy / Medium /
# Hard, oracle calls 23.07 -> 6.50 on Hard against 2.6x overall, and 16.62
# redundant attempts on Hard. Proposition 4.5's guard-cost gap is stated to
# "grow with task difficulty (smaller pi accumulates more refuted types before a
# repair)", and Corollary 4.4's budgeted-success advantage holds only "whenever
# B binds", which is exactly the low-pi regime. So `hard` gets a full quota and
# `dead` is generous: those are the cells where three of the four theoretical
# results are most visible, not spare capacity.
DEFAULT_QUOTAS = {"easy": 30, "medium": 20, "hard": 30, "too_easy": 15, "dead": 20}


# Representative pi per band, for the cost projection only. Never used to
# select anything - a task's band comes from its own measured pi_hat.
BAND_PI = {"dead": 0.01, "hard": 0.05, "medium": 0.13, "easy": 0.26, "too_easy": 0.60}

# Measured over 4,778 logged calls at claude-haiku-4-5 ($1/$5 per Mtok):
# mean $0.00513, median 620 in / 831 out tokens. Output is ~87% of cost, so the
# memory arms - which add evidence to the *input* - run ~15-20% dearer, not 2x.
USD_PER_CALL_NO_MEMORY = 0.0051
USD_PER_CALL_MEMORY = 0.0060


def expected_rounds(pi: float, budget: int) -> float:
    """E[rounds] for an early-stopping episode at one-shot rate `pi`.

    Truncated geometric: (1 - (1-pi)^B) / pi. Deliberately evaluated at pi
    rather than at the per-round rate q the memory arms actually achieve. Since
    q >= pi whenever steering helps at all, this is an upper bound on the memory
    arms' cost and an exact figure for no_memory.
    """
    return (1.0 - (1.0 - pi) ** budget) / pi


def band_of(pi_hat: float) -> str:
    for name, lo, hi in BANDS:
        if lo <= pi_hat < hi:
            return name
    raise ValueError(f"pi_hat={pi_hat!r} falls outside [0, 1]")


def merge_screens(paths: list[pathlib.Path]) -> dict[str, dict]:
    """task -> {pi_hat, successes, calls, source}, keeping the deepest screen.

    A two-stage screen writes one file per stage. src.llm's cache is keyed on
    the per-draw nonce, so stage B re-runs stage A's draws for free and its
    report is a strict superset for the tasks it covers - hence "most calls
    wins" rather than any averaging, which would double-count them.
    """
    merged: dict[str, dict] = {}
    for path in paths:
        blob = json.loads(path.read_text())
        if "per_program" not in blob:
            raise SystemExit(f"{path}: not a measure_pi.py report (no per_program)")
        for name, rec in blob["per_program"].items():
            prev = merged.get(name)
            if prev is None or rec["calls"] > prev["calls"]:
                merged[name] = {
                    "pi_hat": rec["pi_hat"], "successes": rec["successes"],
                    "calls": rec["calls"], "source": str(path),
                    "model": blob.get("model"),
                }
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pool", type=pathlib.Path, default=DATA_DIR / "pool" / "tasks.json",
                        help="candidate-pool freeze from validate_oracle.py --data-dir data/pool")
    parser.add_argument("--screen", type=pathlib.Path, nargs="+", required=True,
                        help="one or more measure_pi.py reports; deepest screen per task wins")
    parser.add_argument("--quota", action="append", default=[], metavar="BAND=N",
                        help=f"override a band quota (default: {DEFAULT_QUOTAS})")
    parser.add_argument("--min-calls", type=int, default=20,
                        help="a candidate screened with fewer draws than this is not placed")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "tasks.json")
    parser.add_argument("--audit-out", type=pathlib.Path, default=DATA_DIR / "screening.json")
    parser.add_argument("--force", action="store_true", help="overwrite an already-frozen corpus")
    parser.add_argument("--budget", type=int, default=20, help="B, for the cost projection")
    parser.add_argument("--seeds-main", type=int, default=5, help="seeds in E1/E2, for the cost projection")
    parser.add_argument("--seeds-abl", type=int, default=3, help="seeds in E3-E5, for the cost projection")
    parser.add_argument("--sweep-size", type=int, default=30, help="E4/E5 subset, for the cost projection")
    args = parser.parse_args()

    quotas = dict(DEFAULT_QUOTAS)
    for spec in args.quota:
        band, _, n = spec.partition("=")
        if band not in quotas:
            raise SystemExit(f"unknown band {band!r}; known: {sorted(quotas)}")
        quotas[band] = int(n)

    if args.out.exists() and not args.force:
        if json.loads(args.out.read_text()).get("frozen"):
            raise SystemExit(f"{args.out} is already frozen - pass --force to overwrite")

    pool_blob = json.loads(args.pool.read_text())
    if not pool_blob.get("frozen"):
        raise SystemExit(f"{args.pool} is not frozen - re-run scripts/validate_oracle.py")
    pool = pool_blob["tasks"]
    screens = merge_screens(list(args.screen))

    models = {s["model"] for s in screens.values() if s.get("model")}
    if len(models) > 1:
        raise SystemExit(f"screens disagree on the model: {sorted(models)} - "
                         f"pi_hat is model-specific, so they cannot be merged")

    # The walk: pool order (the seeded candidate order validate_oracle.py froze),
    # first-come per band. Deterministic given the pool freeze and the screen.
    taken: dict[str, list[dict]] = {b: [] for b in quotas}
    audit = []
    for entry in pool:
        name = entry["name"]
        screen = screens.get(name)
        if screen is None:
            audit.append({"name": name, "disposition": "not_screened"})
            continue
        if screen["calls"] < args.min_calls:
            audit.append({"name": name, "disposition": "under_screened",
                          "pi_hat": screen["pi_hat"], "calls": screen["calls"]})
            continue
        band = band_of(screen["pi_hat"])
        row = {"name": name, "pi_hat": screen["pi_hat"], "calls": screen["calls"],
               "successes": screen["successes"], "band": band}
        if len(taken[band]) < quotas[band]:
            taken[band].append({**entry, "stratum": band,
                                "screen_pi_hat": screen["pi_hat"],
                                "screen_successes": screen["successes"],
                                "screen_calls": screen["calls"]})
            audit.append({**row, "disposition": "selected"})
        else:
            audit.append({**row, "disposition": "band_full"})

    selected = [t for band, _, _ in BANDS for t in taken[band]]
    counts = {b: len(v) for b, v in taken.items()}
    short = {b: quotas[b] - counts[b] for b in quotas if counts[b] < quotas[b]}

    corpus = {
        "frozen": True,
        "benchmark": pool_blob.get("benchmark", "ConDefects (Python)"),
        "test_dir": pool_blob.get("test_dir"),
        "model": sorted(models)[0] if models else None,
        "selection": {
            "stage": "measured pi_hat (paper SS VI-A-b course 2, conditions of SS VI-A-c)",
            "pool": str(args.pool),
            "pool_size": len(pool),
            "screened": len(screens),
            "screens": [str(p) for p in args.screen],
            "min_calls": args.min_calls,
            "bands": {n: [lo, hi] for n, lo, hi in BANDS},
            "quotas": quotas,
            "counts": counts,
            "short": short,
            "primary_bands": list(PRIMARY_BANDS),
            "note": "stratum is fixed here, before any treatment; the pi_hat "
                    "reported in results comes from E1, an independent sample",
        },
        "n_selected": len(selected),
        "candidate_order_sha256": hashlib.sha256(
            "\n".join(t["name"] for t in pool).encode()).hexdigest(),
        "selected_sha256": hashlib.sha256(
            "\n".join(t["name"] for t in selected).encode()).hexdigest(),
        "tasks": selected,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corpus, indent=2) + "\n")

    audit_blob = {
        "note": "every candidate in the pool and what became of it - the full "
                "screened distribution SS VI-A-c requires to be reported, "
                "including everything excluded",
        "model": sorted(models)[0] if models else None,
        "bands": {n: [lo, hi] for n, lo, hi in BANDS},
        "quotas": quotas,
        "counts": counts,
        "candidates": audit,
    }
    args.audit_out.write_text(json.dumps(audit_blob, indent=2) + "\n")

    placed = [a for a in audit if "band" in a]
    print(f"pool {len(pool)} · screened {len(screens)} · placed {len(placed)}")
    for name, lo, hi in BANDS:
        n_in_band = sum(1 for a in placed if a["band"] == name)
        star = "  *primary" if name in PRIMARY_BANDS else "   control"
        print(f"  {name:9s} [{lo:.2f},{hi:.2f})  screened {n_in_band:4d}  "
              f"taken {counts[name]:3d}/{quotas[name]:<3d}{star}")
    n_primary = sum(counts[b] for b in PRIMARY_BANDS)
    print(f"\ncorpus {len(selected)} tasks · primary comparison rests on {n_primary}")
    if short:
        print(f"SHORT: {short} - screen more candidates and re-run; the walk is "
              f"deterministic, so already-selected tasks keep their places")

    # The grid's cost, from the counts that just came out - not from the quotas.
    # A band that under-fills makes the corpus smaller and the run cheaper, and
    # this is the first moment either number is knowable.
    B = args.budget
    e1 = sum(counts[b] * args.seeds_main * B for b in counts)
    e2 = sum(counts[b] * args.seeds_main * 2 * expected_rounds(BAND_PI[b], B) for b in counts)
    e3 = sum(counts[b] * args.seeds_abl * 2 * expected_rounds(BAND_PI[b], B) for b in counts)
    n_sweep = min(args.sweep_size, len(selected))
    sweep_pi = sum(counts[b] * BAND_PI[b] for b in counts) / max(1, len(selected))
    e45 = 2 * 3 * n_sweep * args.seeds_abl * expected_rounds(sweep_pi, B)

    lines = [
        ("screening (already spent by this point)", None, None),
        (f"E1  no_memory, {len(selected)} x {args.seeds_main} x {B} full budget", e1, USD_PER_CALL_NO_MEMORY),
        (f"E2  untyped+typed, {len(selected)} x {args.seeds_main} x 2, early stop", e2, USD_PER_CALL_MEMORY),
        (f"E3  ablations, {len(selected)} x {args.seeds_abl} x 2", e3, USD_PER_CALL_MEMORY),
        (f"E4+E5  2 sweeps x 3 levels x {n_sweep} x {args.seeds_abl}", e45, USD_PER_CALL_MEMORY),
    ]
    total_calls = total_usd = 0.0
    print(f"\nprojected grid cost at B={B} (upper bound: E[rounds] evaluated at pi, not q)")
    for label, calls, rate in lines:
        if calls is None:
            print(f"  {label}")
            continue
        total_calls += calls
        total_usd += calls * rate
        print(f"  {label:56s} {calls:8,.0f} calls  ${calls * rate:7,.0f}")
    print(f"  {'TOTAL':56s} {total_calls:8,.0f} calls  ${total_usd:7,.0f}")
    print(f"  set BUDGET_USD_CAP above ${total_usd:,.0f} + whatever data/calls.jsonl "
          f"already carries (llm.spent() sums the whole file)")

    print(f"\nwrote {args.out} (frozen) and {args.audit_out}")


if __name__ == "__main__":
    main()
