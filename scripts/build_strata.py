"""Stratify the frozen corpus into the paper's pi bands, and audit the drift.

The bands are **absolute** - the proposal's own Easy [0.18, 0.35], Medium
[0.08, 0.18), Hard [0.02, 0.08), plus the two control bands outside the
analysis range (scripts/select_corpus.py holds the same table). They are not
terciles of whatever happens to be on disk.

This used to split the corpus into terciles of the measured pi_hat. That is
wrong for the thing SS VI-A of the paper does with the strata: Table II reads
informativeness off *absolute* pi, so a tercile boundary that floats with the
corpus renames the bands every time the corpus changes and makes "the effect
is predicted in the middle band" untestable. On a bimodal pi_hat distribution
- which is what SS V-F measured - terciles are worse than uninformative: they
put pi_hat = 0.00 and pi_hat = 0.05 in different strata and pi_hat = 0.30 and
pi_hat = 1.00 in the same one.

Two pi_hat's, deliberately, and the distinction is the whole of SS VI-A-c:

  selection-time pi_hat  (`screen_pi_hat`, from scripts/measure_pi.py) fixes
      the stratum. Measured before any treatment, so stratifying on it is a
      controlled factor, not conditioning on an outcome.
  reported pi_hat        (from E1 via data/theory_fit.json) is what results
      tables print. An independent sample, so regression to the mean cannot
      inflate it.

Tasks migrate between the two - that is expected, and the migration matrix is
written out rather than hidden, because its size *is* the regression-to-the-
mean term SS VI-A-c warns about.

Writes data/strata.json with "frozen": true; re-running is a no-op unless
--force is passed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

from scripts.select_corpus import BANDS, PRIMARY_BANDS, band_of  # noqa: E402

# Enforced as thresholds (see module docstring), not decoration.
PAPER_REFERENCE_RANGES = {name: [lo, hi] for name, lo, hi in BANDS}


def _frozen_corpus() -> list[dict]:
    tasks_json = DATA_DIR / "tasks.json"
    if not tasks_json.exists():
        raise SystemExit(f"{tasks_json} missing - run scripts/select_corpus.py first")
    data = json.loads(tasks_json.read_text())
    if not data.get("frozen"):
        raise SystemExit(f"{tasks_json} is not frozen - re-run scripts/select_corpus.py")
    return data["tasks"]


PROXY_LABELS = ("proxy_low", "proxy_mid", "proxy_high")


def stratify_by_proxy(corpus: list[dict], reported_pi: dict[str, float]) -> tuple[list[dict], dict]:
    """Terciles of the corpus's own AtCoder rating, for a corpus selected by a
    rating floor rather than by measured pi.

    Deliberately *not* called easy/medium/hard. Those names denote pi bands in
    the paper, and this is not one: SS V-G measures Spearman(rating, pi_hat) =
    -0.350 on 60 tasks - monotone in the intended direction, moderately
    predictive, nowhere near sharp enough to name a pi band. Borrowing the names
    would assert a calibration nobody measured.

    What it is good for is the one thing a stratum has to do: be fixed before
    any treatment is applied, so that stratifying on it is a controlled factor
    rather than conditioning on an outcome. The rating is a property of the
    contest, decided years before this experiment.

    The measured pi_hat from E1 is attached alongside once it exists, as a
    descriptive column. Binning *on* it would be stratifying on the no-memory
    arm's own outcome, which is why it is reported and not used as the stratum.
    """
    rated = [e for e in corpus if e.get("difficulty") is not None]
    if len(rated) < len(corpus):
        missing = [e["name"] for e in corpus if e.get("difficulty") is None]
        raise SystemExit(f"{len(missing)} tasks carry no AtCoder rating, so a proxy "
                         f"stratum cannot be assigned: {missing[:3]}")
    ordered = sorted(rated, key=lambda e: (e["difficulty"], e["name"]))
    n = len(ordered)
    base, extra = divmod(n, 3)
    sizes = [base + (1 if i < extra else 0) for i in range(3)]

    out, i = [], 0
    cuts = {}
    for label, size in zip(PROXY_LABELS, sizes):
        chunk = ordered[i:i + size]
        cuts[label] = [chunk[0]["difficulty"], chunk[-1]["difficulty"]] if chunk else None
        for entry in chunk:
            row = {"name": entry["name"], "stratum": label, "difficulty": entry["difficulty"]}
            if entry["name"] in reported_pi:
                row["reported_pi_hat"] = reported_pi[entry["name"]]
            out.append(row)
        i += size
    return out, cuts


def stratify(corpus: list[dict], reported_pi: dict[str, float]) -> list[dict]:
    """One row per task: the stratum it was selected into, and where the
    independent measurement would have put it."""
    out = []
    for entry in corpus:
        name = entry["name"]
        selected_pi = entry["screen_pi_hat"]
        row = {
            "name": name,
            "stratum": entry.get("stratum") or band_of(selected_pi),
            "screen_pi_hat": selected_pi,
            "screen_calls": entry.get("screen_calls"),
        }
        if name in reported_pi:
            row["reported_pi_hat"] = reported_pi[name]
            row["reported_band"] = band_of(reported_pi[name])
            row["moved"] = row["reported_band"] != row["stratum"]
        out.append(row)
    return out


def _read_pi(explicit: pathlib.Path | None) -> tuple[pathlib.Path, dict[str, float], int | None]:
    """(path, task -> pi_hat, seed) from whichever measurement is on disk.

    Two shapes, because pi_hat has two producers and only one of them is still
    part of the pipeline:

      data/theory_fit.json   `pi_q_by_task[task]["pi_hat"]` - estimated by
                             scripts/fit_theory.py from E1, the no-memory arm
                             run with --force-full-budget. This is the one to
                             use: every round of that arm is an independent
                             draw, the arm is needed for RQ1 anyway, and it
                             yields q_hat_tau alongside pi_hat.
      data/pi_pilot.json     `per_program[task]["pi_hat"]` - scripts/measure_pi.py.
                             A standalone pilot that buys pi_hat and nothing
                             else, at the price of a whole extra arm's worth of
                             model calls. Kept readable for the calibration runs
                             that used it; not part of the flow.

    Preferred order rather than a required flag: a corpus that has run E1 should
    never be stratified from an older pilot just because the file is still there.
    """
    candidates = [explicit] if explicit else [DATA_DIR / "theory_fit.json",
                                              DATA_DIR / "pi_pilot.json"]
    for path in candidates:
        if path is None or not path.exists():
            continue
        blob = json.loads(path.read_text())
        if "pi_q_by_task" in blob:
            return path, {t: v["pi_hat"] for t, v in blob["pi_q_by_task"].items()}, None
        if "per_program" in blob:
            return path, {t: v["pi_hat"] for t, v in blob["per_program"].items()}, blob.get("seed")
        raise SystemExit(f"{path}: no pi_hat in it (expected pi_q_by_task or per_program)")
    raise SystemExit(
        "no measured pi_hat on disk. Run E1 and fit_theory:\n"
        "  python3 scripts/run_eval.py --modes no_memory --force-full-budget\n"
        "  python3 scripts/fit_theory.py"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pi-source", type=pathlib.Path, default=None,
                        help="where measured pi_hat comes from. Default: "
                             "data/theory_fit.json (E1's no-memory corpus) if it "
                             "exists, else data/pi_pilot.json (the retired "
                             "measure_pi.py pilot)")
    parser.add_argument("--pi-pilot-path", type=pathlib.Path, default=None,
                        help="deprecated alias for --pi-source")
    parser.add_argument("--force", action="store_true", help="overwrite an already-frozen data/strata.json")
    args = parser.parse_args()

    out_path = DATA_DIR / "strata.json"
    if out_path.exists() and not args.force:
        existing = json.loads(out_path.read_text())
        if existing.get("frozen"):
            raise SystemExit(f"{out_path} is already frozen - pass --force to overwrite")

    corpus = _frozen_corpus()

    # The reported pi_hat is optional here: strata come from the pre-treatment
    # screen, so this script is runnable before E1 and gains the drift audit
    # once E1 and fit_theory have run.
    try:
        pi_path, reported_pi, seed = _read_pi(args.pi_source or args.pi_pilot_path)
    except SystemExit:
        pi_path, reported_pi, seed = None, {}, None

    # Two corpora shapes, two stratifications. A corpus frozen by
    # scripts/select_corpus.py carries screen_pi_hat and is banded on it; a
    # corpus frozen by a rating floor (validate_oracle.py --select hard) has no
    # pre-treatment pi measurement at all, and its only pre-treatment ordering
    # is the contest rating.
    by_pi = all("screen_pi_hat" in e for e in corpus)
    cuts = None
    if by_pi:
        tasks = stratify(corpus, reported_pi)
        labels = [name for name, _, _ in BANDS]
        primary = list(PRIMARY_BANDS)
    else:
        tasks, cuts = stratify_by_proxy(corpus, reported_pi)
        labels = list(PROXY_LABELS)
        primary = list(PROXY_LABELS)   # all three carry the same prediction

    counts = {name: sum(1 for t in tasks if t["stratum"] == name) for name in labels}
    n_primary = sum(counts[b] for b in primary)

    moved = [t for t in tasks if t.get("moved")]
    migration: dict[str, dict[str, int]] = {}
    for t in tasks:
        if "reported_band" in t:
            migration.setdefault(t["stratum"], {}).setdefault(t["reported_band"], 0)
            migration[t["stratum"]][t["reported_band"]] += 1

    report = {
        "frozen": True,
        "stratified_by": "measured pi_hat (pre-treatment screen)" if by_pi
                         else "AtCoder rating terciles - a PROXY, not a pi band",
        "proxy_warning": None if by_pi else (
            "these strata are terciles of the corpus's own contest rating, not "
            "the paper's pi bands. SS V-G measures Spearman(rating, pi_hat) = "
            "-0.350: monotone but only moderately predictive. Report them as a "
            "difficulty proxy and use the E1-measured pi_hat for any claim about pi."),
        "rating_cuts": cuts,
        "bands": PAPER_REFERENCE_RANGES if by_pi else None,
        "primary_bands": primary,
        "selection_source": ("data/tasks.json screen_pi_hat (pre-treatment)" if by_pi
                             else "data/tasks.json difficulty (contest rating, pre-treatment)"),
        "reported_source": str(pi_path) if pi_path else None,
        "seed": seed,
        "n_total": len(tasks),
        "n_primary": n_primary,
        "counts": counts,
        "n_moved": len(moved),
        "migration_selected_to_reported": migration,
        "tasks": tasks,
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    if by_pi:
        print(f"stratified {len(tasks)} tasks into absolute pi bands:")
        for name, lo, hi in BANDS:
            role = "primary" if name in primary else "control"
            print(f"  {name:9s} [{lo:.2f},{hi:.2f})  {counts[name]:3d}   {role}")
    else:
        print(f"stratified {len(tasks)} tasks into rating terciles (PROXY, not pi bands):")
        for name in labels:
            lo, hi = cuts[name]
            print(f"  {name:10s} rating [{lo}, {hi}]  {counts[name]:3d}")
        print("  NOTE: Spearman(rating, pi_hat) = -0.350 (SS V-G) - a difficulty")
        print("        proxy only. Any claim about pi must use E1's measured pi_hat.")
    print(f"primary comparison rests on {n_primary} tasks")
    if reported_pi:
        print(f"drift vs the independent E1 measurement: {len(moved)}/{len(tasks)} "
              f"tasks change band (regression to the mean, SS VI-A-c)")
    else:
        print("no reported pi_hat yet - re-run after E1 + fit_theory for the drift audit")
    print(f"wrote {out_path} (frozen)")


if __name__ == "__main__":
    main()
