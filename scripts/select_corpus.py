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

from src.paths import DATA_DIR, ROOT, announce  # noqa: E402

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

# A band below this is a hole in the design, not a small cell: the primary
# comparison is per-band, and three of the paper's four theoretical results are
# stated as varying WITH the band, so a band of four tasks cannot carry the
# claim it is there to test. Enforced as an error rather than the warning
# `short` used to print, because a corpus that under-fills silently is one the
# analysis will happily run on and quietly under-power.
MIN_PER_BAND = 10

# Where the previous corpus lives, and therefore which tasks already have paid
# episodes and a warm LLM cache. Under RUN_DIR this is the BASE corpus (plain
# data/tasks.json), not the new run's - which is the point: a named run
# re-selects against the corpus whose calls have already been bought.
DEFAULT_PIN = ROOT / "data" / "tasks.json"


def read_task_list(path: pathlib.Path) -> list[str]:
    """Task names from a tasks.json freeze, or one name per line.

    Accepts both so a pin can be an earlier corpus (the usual case) or a list
    written by hand.
    """
    text = path.read_text()
    if text.lstrip().startswith("{"):
        blob = json.loads(text)
        return [t["name"] for t in blob.get("tasks", [])]
    return [ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.startswith("#")]


# Representative pi per band, for the cost projection only. Never used to
# select anything - a task's band comes from its own measured pi_hat.
BAND_PI = {"dead": 0.01, "hard": 0.05, "medium": 0.13, "easy": 0.26, "too_easy": 0.60}

# Priced for gpt-4o-mini ($0.15/$0.60 per Mtok) from the token profile measured
# over 4,778 logged calls: median 620 in / 831 out for a no-memory call, ~900
# more input tokens for a memory-arm call. Output is still ~85% of cost, so the
# memory arms - which add evidence to the *input* - run ~25% dearer, not 2x.
#
# The token counts are measured; the dollar figures are that profile repriced,
# not yet re-measured on gpt-4o-mini. Re-derive them from data/calls.jsonl once
# the screen has run - the previous rate card was for claude-haiku-4-5 at
# $1/$5, which made this projection read ~9x high.
USD_PER_CALL_NO_MEMORY = 0.00059
USD_PER_CALL_MEMORY = 0.00073


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
    announce('select_corpus')
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pool", type=pathlib.Path, default=DATA_DIR / "pool" / "tasks.json",
                        help="candidate-pool freeze from validate_oracle.py --data-dir data/pool")
    parser.add_argument("--screen", type=pathlib.Path, nargs="+", default=None,
                        help="one or more measure_pi.py reports; deepest screen per task "
                             "wins. OPTIONAL: without it the corpus is inherited from "
                             "--pin (the previous corpus, minus whatever the gate and the "
                             "slow-task filter removed), keeping each task's frozen "
                             "screen_pi_hat and stratum. That is the official path - see "
                             "the `inherited` note below")
    parser.add_argument("--quota", action="append", default=[], metavar="BAND=N",
                        help=f"override a band quota (default: {DEFAULT_QUOTAS})")
    parser.add_argument("--min-calls", type=int, default=20,
                        help="a candidate screened with fewer draws than this is not placed")
    parser.add_argument("--pin", type=pathlib.Path, action="append", default=None,
                        metavar="PATH",
                        help="a corpus (tasks.json) or task list whose members are taken "
                             f"FIRST within their band. Default: {DEFAULT_PIN} if it exists. "
                             "This is the cache-reuse mechanism - see --no-pin")
    parser.add_argument("--no-pin", action="store_true",
                        help="select purely by pool order, ignoring the previous corpus. "
                             "Every task that changes places loses its paid episodes")
    parser.add_argument("--min-per-band", type=int, default=MIN_PER_BAND,
                        help=f"a band below this fails the selection (default {MIN_PER_BAND}); "
                             "0 to allow any size")
    parser.add_argument("--allow-unpinned", action="store_true",
                        help="downgrade a BAND-FULL eviction from an error to a warning. "
                             "Only that: a pinned task the gate rejected or the slow-task "
                             "filter dropped is reported and the run continues either way, "
                             "because no quota brings those back")
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
    inherited = args.screen is None
    screens = {} if inherited else merge_screens(list(args.screen))

    models = {s["model"] for s in screens.values() if s.get("model")}
    if len(models) > 1:
        raise SystemExit(f"screens disagree on the model: {sorted(models)} - "
                         f"pi_hat is model-specific, so they cannot be merged")

    # ── the pin: which tasks already have paid episodes ─────────────────────
    #
    # THIS is the mechanism that makes a re-selection reuse the cache, and it is
    # worth being precise about what it does and does not guarantee.
    #
    # What makes a cached episode replay for free is src.loop.cell_signature -
    # task name, mode, seed, granularity, max_examples, typing noise, the two
    # memory flags, the model. The BAND is not in it, and neither is the corpus
    # a task was selected into. So a pinned task keeps every paid episode even
    # if the re-screen moves it from `hard` to `medium`; the one thing that
    # loses them is the task falling out of the corpus altogether.
    #
    # A task falls out for exactly one avoidable reason: its band filled up with
    # other tasks before the walk reached it. Pool order is fixed, so that is a
    # real risk whenever the quotas or the screen change. The pin removes it by
    # walking twice - pinned tasks first, everyone else into what is left. Both
    # passes are in pool order, so the result is still deterministic given the
    # pool freeze and the screen.
    #
    # The unavoidable reasons are checked and reported, not worked around: a
    # pinned task that no longer passes the oracle gate, was not screened deeply
    # enough, or was dropped by the slow-task filter is genuinely gone, and
    # keeping it would mean overriding the gate to protect a cache.
    pin_paths = args.pin
    if pin_paths is None:
        pin_paths = [DEFAULT_PIN] if DEFAULT_PIN.exists() else []
    if args.no_pin:
        pin_paths = []
    # Pinning the file we are about to write is a no-op that would freeze the
    # corpus against itself forever - and it is the DEFAULT when RUN_DIR is
    # unset, because then DEFAULT_PIN and --out are the same file. Say so
    # rather than doing it.
    kept_pins = []
    for p in pin_paths:
        if p.resolve() == args.out.resolve():
            print(f"pin: {p} is this run's own output - ignored "
                  f"(set RUN_DIR, or pass --pin explicitly, to pin a different corpus)")
        else:
            kept_pins.append(p)
    pin_paths = kept_pins

    pinned: list[str] = []
    for p in pin_paths:
        if not p.exists():
            raise SystemExit(f"--pin {p} does not exist")
        pinned.extend(read_task_list(p))
    pinned_set = set(pinned)
    if pinned_set:
        print(f"pin: {len(pinned_set)} tasks from {', '.join(str(p) for p in pin_paths)}")

    # ── no screen: the corpus IS the previous one, minus what the gate took ──
    #
    # Every field the screen would supply is already frozen on the pinned tasks
    # - screen_pi_hat, screen_successes, screen_calls and the stratum derived
    # from them - so re-measuring pi_hat would buy nothing the corpus does not
    # already carry. What the gate DOES change is fresher: whether the fault is
    # still usable, how its natural mutants scored, and (new on this branch)
    # what its reference costs. So each task is the gate's current record with
    # the pin's four screen fields laid over it.
    #
    # No quotas here, and that is the point rather than an omission: quotas
    # backfill an under-full band from the pool, and a backfilled task has no
    # paid episodes. The requirement this path exists to meet is that the corpus
    # be a SUBSET of the previous one, so nothing is ever added.
    inherit_fields = ("stratum", "screen_pi_hat", "screen_successes", "screen_calls")
    if inherited:
        if not pinned:
            raise SystemExit(
                "--screen was not given, so the corpus has to be inherited from a "
                "previous one - but no pin was found.\n"
                f"Expected {DEFAULT_PIN}. Pass --pin PATH, or pass --screen to select "
                "from a screen the way a first-ever corpus has to be built.")
        pool_by_name = {e["name"]: e for e in pool}
        pin_rows = {}
        for p in pin_paths:
            blob = json.loads(p.read_text()) if p.read_text().lstrip().startswith("{") else {}
            for row in blob.get("tasks", []):
                pin_rows.setdefault(row["name"], row)
        missing_fields = [n for n in pinned
                          if n in pool_by_name
                          and not all(f in pin_rows.get(n, {}) for f in inherit_fields)]
        if missing_fields:
            raise SystemExit(
                f"{len(missing_fields)} pinned task(s) carry no frozen stratum/screen_pi_hat, "
                f"e.g. {missing_fields[0]} - the pin is not a corpus this can be inherited "
                f"from. Pass --screen and select from a measured screen instead.")

        selected, audit = [], []
        for name in pinned:                       # the pin's own order is kept
            entry = pool_by_name.get(name)
            if entry is None:
                audit.append({"name": name, "disposition": "not_in_pool", "pinned": True})
                continue
            row = pin_rows[name]
            merged = {**entry, **{f: row[f] for f in inherit_fields}, "pinned": True}
            selected.append(merged)
            audit.append({"name": name, "band": row["stratum"], "pi_hat": row["screen_pi_hat"],
                          "calls": row["screen_calls"], "successes": row["screen_successes"],
                          "disposition": "selected", "selected_in": "inherited", "pinned": True})
        # Report order stays band-major, matching what the screen path produces,
        # so eval_shard.sh's interleave sees the same shape either way.
        order = {b: i for i, (b, _, _) in enumerate(BANDS)}
        selected.sort(key=lambda t: order[t["stratum"]])
        counts = {b: sum(1 for t in selected if t["stratum"] == b) for b in quotas}
        short = {}
        audit_by_name = {a["name"]: a for a in audit}
        taken = {b: [t for t in selected if t["stratum"] == b] for b in quotas}

    # ── the walk: pinned first, then pool order ─────────────────────────────
    taken_screen: dict[str, list[dict]] = {b: [] for b in quotas}
    audit_by_name: dict[str, dict] = {}
    audit = []

    def consider(entry: dict, *, is_pin_pass: bool) -> None:
        name = entry["name"]
        screen = screens.get(name)
        if screen is None:
            audit_by_name[name] = {"name": name, "disposition": "not_screened"}
            return
        if screen["calls"] < args.min_calls:
            audit_by_name[name] = {"name": name, "disposition": "under_screened",
                                   "pi_hat": screen["pi_hat"], "calls": screen["calls"]}
            return
        band = band_of(screen["pi_hat"])
        row = {"name": name, "pi_hat": screen["pi_hat"], "calls": screen["calls"],
               "successes": screen["successes"], "band": band,
               "pinned": name in pinned_set}
        if len(taken_screen[band]) < quotas[band]:
            taken_screen[band].append({**entry, "stratum": band,
                                "screen_pi_hat": screen["pi_hat"],
                                "screen_successes": screen["successes"],
                                "screen_calls": screen["calls"],
                                "pinned": name in pinned_set})
            audit_by_name[name] = {**row, "disposition": "selected",
                                   "selected_in": "pin_pass" if is_pin_pass else "pool_pass"}
        else:
            audit_by_name[name] = {**row, "disposition": "band_full"}

    if not inherited:
        taken = taken_screen
        for entry in pool:                               # pass 1: the paid corpus
            if entry["name"] in pinned_set:
                consider(entry, is_pin_pass=True)
        for entry in pool:                               # pass 2: everyone else
            if entry["name"] not in pinned_set:
                consider(entry, is_pin_pass=False)
        audit = [audit_by_name[e["name"]] for e in pool if e["name"] in audit_by_name]

        selected = [t for band, _, _ in BANDS for t in taken[band]]
        counts = {b: len(v) for b, v in taken.items()}
        short = {b: quotas[b] - counts[b] for b in quotas if counts[b] < quotas[b]}

    # ── did every pinned task survive? ──────────────────────────────────────
    #
    # Two kinds of loss, and they need opposite responses.
    #
    #   RECOVERABLE  band_full - nothing about the task changed, the walk just
    #                reached the quota first. Raising that band's quota gets it
    #                back, and the episodes are still valid, so re-running them
    #                would be paying twice for the same measurement. An error.
    #
    #   SETTLED      the gate rejected it, the slow-task filter dropped it, or
    #                it was not screened deeply enough. It is gone on its merits
    #                and no quota brings it back. Reported, then we continue -
    #                this is the "except those the slow filter drops" case, and
    #                making it fatal would mean every run of this pipeline needs
    #                --allow-unpinned, which would in turn silence the case
    #                above. That is exactly backwards.
    in_pool = {e["name"] for e in pool}
    chosen = {t["name"] for t in selected}

    # Why a task left the pool, from the gate's own report when it is beside the
    # pool file. Worth the lookup: "the slow-task filter dropped it, reference
    # needs >10s" is actionable and "not in the pool" is not.
    gate_reason: dict[str, str] = {}
    gate_report = args.pool.parent / "oracle_validation.json"
    if gate_report.exists():
        try:
            faults = json.loads(gate_report.read_text()).get("faults", {})
        except (ValueError, OSError):
            faults = {}
        for name, rec in faults.items():
            if rec.get("slow"):
                gate_reason[name] = (f"dropped by the slow-task filter "
                                     f"(reference needs >{rec.get('reference_timeout')}s "
                                     f"on one of its own cases)")
            elif not rec.get("usable"):
                gate_reason[name] = f"failed the oracle gate: {rec.get('reason')}"
            elif rec.get("eligible") is False:
                gate_reason[name] = "no sibling submission to validate the oracle against"
            elif not rec.get("passes"):
                gate_reason[name] = "did not catch enough of its natural mutants"

    recoverable, settled = [], []
    for name in pinned:
        if name in chosen:
            continue
        disp = audit_by_name.get(name, {}).get("disposition")
        if name in in_pool and disp == "band_full":
            recoverable.append((name, audit_by_name[name]["band"]))
        elif name not in in_pool:
            settled.append((name, gate_reason.get(name, "no longer in the gated pool")))
        else:
            settled.append((name, disp or "not reached by the walk"))

    kept = len(pinned_set & chosen)
    if pinned_set:
        print(f"pin: {kept}/{len(pinned_set)} kept - "
              f"{kept} tasks' episodes and cached calls stay valid")
    if settled:
        print(f"pin: {len(settled)} pinned task(s) are gone for good - no quota brings "
              f"them back, and their episodes are spent:")
        for name, why in settled[:40]:
            print(f"     {name:30s} {why}")
        if len(settled) > 40:
            print(f"     ... and {len(settled) - 40} more")
    if recoverable:
        crowded = sorted({b for _, b in recoverable})
        msg = [f"{len(recoverable)} pinned task(s) were evicted by a FULL BAND, which is "
               f"the one loss this pin exists to prevent:"]
        msg += [f"  {n:30s} band {b} filled first" for n, b in recoverable[:40]]
        if len(recoverable) > 40:
            msg.append(f"  ... and {len(recoverable) - 40} more")
        msg.append("")
        msg.append(f"Nothing is wrong with these tasks - the walk simply reached the quota "
                   f"before it reached them, and their episodes are still valid. Raise the "
                   f"quota: " + "  ".join(f"--quota {b.upper()}=N" for b in crowded))
        msg.append("Re-running them instead costs real calls.")
        text = "\n".join(msg)
        if args.allow_unpinned:
            print("WARNING: " + text)
        else:
            raise SystemExit(text + "\n\nPass --allow-unpinned to accept the loss.")

    # ── every band must be able to carry its own claim ──────────────────────
    if args.min_per_band:
        thin = {b: counts[b] for b in counts if counts[b] < args.min_per_band}
        if thin:
            raise SystemExit(
                f"{thin} - every band needs at least {args.min_per_band} tasks "
                f"(5 bands x {args.min_per_band} = {5 * args.min_per_band} minimum corpus).\n"
                f"The primary comparison is per-band and three of the paper's four\n"
                f"theoretical results are stated as varying WITH the band, so a band\n"
                f"this thin cannot carry the claim it is there to test.\n"
                f"Screen more candidates and re-run - the walk is deterministic, so\n"
                f"already-selected tasks keep their places. Or pass --min-per-band 0.")

    corpus = {
        "frozen": True,
        "benchmark": pool_blob.get("benchmark", "ConDefects (Python)"),
        "test_dir": pool_blob.get("test_dir"),
        "model": sorted(models)[0] if models else None,
        "selection": {
            "mode": "inherited" if inherited else "screen",
            "stage": (
                # Not re-running a measurement is not the same as never having made
                # one. This corpus WAS selected on a measured pi_hat, in 2026-08;
                # what changed is that the screen no longer re-runs, because every
                # field it produced is frozen on the tasks being inherited.
                "inherited from a previously screened corpus, minus what the gate "
                "and the slow-task filter removed. screen_pi_hat and stratum are the "
                "frozen 2026-08 values; the REPORTED banding comes from E1 via "
                "scripts/build_strata.py (paper SS VI-A-c's second sample)"
                if inherited else
                "measured pi_hat (paper SS VI-A-b course 2, conditions of SS VI-A-c)"),
            "pool": str(args.pool),
            "pool_size": len(pool),
            "screened": len(screens),
            "screens": [str(p) for p in (args.screen or [])],
            "min_calls": args.min_calls,
            "bands": {n: [lo, hi] for n, lo, hi in BANDS},
            "quotas": quotas,
            "counts": counts,
            "short": short,
            "primary_bands": list(PRIMARY_BANDS),
            "min_per_band": args.min_per_band,
            # Provenance for the cache-reuse claim: which corpus was pinned,
            # how many of its tasks survived, and what became of the rest.
            "pin": {
                "sources": [str(p) for p in pin_paths],
                "n_pinned": len(pinned_set),
                "n_kept": kept,
                "lost_recoverable": [{"name": n, "band": b} for n, b in recoverable],
                "lost_settled": [{"name": n, "why": w} for n, w in settled],
                "note": "pinned tasks are taken first within their band so a "
                        "re-selection does not evict a task whose episodes are "
                        "already paid for. Band membership is not in "
                        "src.loop.cell_signature, so a kept task replays its "
                        "cache even if the re-screen moves it to another band",
            },
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
    if inherited:
        # No quota was applied and nothing was screened in this run, so printing
        # "screened 0 · taken 19/20" would name two numbers that mean nothing
        # here. What the reader wants is what each band kept and what it lost.
        print(f"pool {len(pool)} · inherited from {len(pinned_set)} pinned tasks "
              f"· kept {len(selected)}")
        before = {b: sum(1 for n in pinned if pin_rows.get(n, {}).get("stratum") == b)
                  for b in quotas}
        for name, lo, hi in BANDS:
            star = "  *primary" if name in PRIMARY_BANDS else "   control"
            gone = before[name] - counts[name]
            note = f"  (-{gone} to the gate)" if gone else ""
            print(f"  {name:9s} [{lo:.2f},{hi:.2f})  kept {counts[name]:3d}/"
                  f"{before[name]:<3d}{star}{note}")
    else:
        print(f"pool {len(pool)} · screened {len(screens)} · placed {len(placed)}")
        for name, lo, hi in BANDS:
            n_in_band = sum(1 for a in placed if a["band"] == name)
            star = "  *primary" if name in PRIMARY_BANDS else "   control"
            print(f"  {name:9s} [{lo:.2f},{hi:.2f})  screened {n_in_band:4d}  "
                  f"taken {counts[name]:3d}/{quotas[name]:<3d}{star}")
    n_primary = sum(counts[b] for b in PRIMARY_BANDS)
    print(f"\ncorpus {len(selected)} tasks · primary comparison rests on {n_primary}")
    if inherited:
        print("  the bands above are the frozen 2026-08 screen_pi_hat, and they are used\n"
              "  only to interleave shards evenly. The banding results are REPORTED on\n"
              "  comes from E1, via scripts/build_strata.py -> data/strata.json.")
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
