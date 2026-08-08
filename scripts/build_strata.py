"""Stratify the 40 frozen tasks into easy/medium/hard by measured pi_hat.

Reads data/pi_pilot.json (must cover every task in the frozen data/tasks.json
- run `python3 scripts/measure_pi.py --programs all` first) and splits tasks
into terciles by pi_hat: the lowest third is "hard" (least likely the LLM
proposes a correct patch in one shot), the highest third is "easy". This
mirrors the paper's own stratification story (Easy/Medium/Hard by pi) but the
tercile boundaries here come from the *measured* pi_hat distribution, not
imposed from the synthetic study's pi ranges - those are recorded alongside
purely as a reference point, not as thresholds.

Writes data/strata.json with "frozen": true once written; the "khoa lai,
khong sua sau" (freeze, don't touch again) requirement from the plan means
re-running this script is a no-op unless --force is passed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# For context only in the output - not enforced as thresholds (see module docstring).
PAPER_REFERENCE_RANGES = {
    "easy": [0.18, 0.35],
    "medium": [0.08, 0.18],
    "hard": [0.02, 0.08],
}


def _frozen_task_names() -> list[str]:
    tasks_json = DATA_DIR / "tasks.json"
    if not tasks_json.exists():
        raise SystemExit(f"{tasks_json} missing - run scripts/validate_oracle.py first")
    data = json.loads(tasks_json.read_text())
    if not data.get("frozen"):
        raise SystemExit(f"{tasks_json} is not frozen - re-run scripts/validate_oracle.py")
    return [t["name"] for t in data["tasks"]]


def stratify(pi_by_task: dict[str, float]) -> list[dict]:
    ordered = sorted(pi_by_task.items(), key=lambda kv: kv[1])  # ascending: hardest first
    n = len(ordered)
    # split into 3 near-equal groups; remainder (n % 3) goes to the earlier groups
    base, extra = divmod(n, 3)
    sizes = [base + (1 if i < extra else 0) for i in range(3)]
    labels = ["hard", "medium", "easy"]
    out, i = [], 0
    for label, size in zip(labels, sizes):
        for name, pi_hat in ordered[i:i + size]:
            out.append({"name": name, "pi_hat": pi_hat, "stratum": label})
        i += size
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

    pi_path, pi_by_task, seed = _read_pi(args.pi_source or args.pi_pilot_path)

    expected = set(_frozen_task_names())
    missing = expected - set(pi_by_task)
    if missing:
        raise SystemExit(
            f"{pi_path} covers {len(pi_by_task)}/{len(expected)} frozen tasks, "
            f"missing: {sorted(missing)} - re-run E1 over the whole corpus "
            f"(run_eval.py --modes no_memory --force-full-budget) and "
            f"scripts/fit_theory.py"
        )

    tasks = stratify({name: pi_by_task[name] for name in expected})
    counts = {label: sum(1 for t in tasks if t["stratum"] == label) for label in ("easy", "medium", "hard")}

    report = {
        "frozen": True,
        "source": str(pi_path),
        "seed": seed,
        "n_total": len(tasks),
        "counts": counts,
        "paper_reference_ranges": PAPER_REFERENCE_RANGES,
        "tasks": tasks,
    }
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"stratified {len(tasks)} tasks: {counts}")
    for t in tasks:
        print(f"  {t['stratum']:6s} {t['name']:28s} pi_hat={t['pi_hat']:.3f}")
    print(f"wrote {out_path} (frozen)")


if __name__ == "__main__":
    main()
