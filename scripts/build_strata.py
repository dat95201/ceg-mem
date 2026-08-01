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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pi-pilot-path", type=pathlib.Path, default=DATA_DIR / "pi_pilot.json")
    parser.add_argument("--force", action="store_true", help="overwrite an already-frozen data/strata.json")
    args = parser.parse_args()

    out_path = DATA_DIR / "strata.json"
    if out_path.exists() and not args.force:
        existing = json.loads(out_path.read_text())
        if existing.get("frozen"):
            raise SystemExit(f"{out_path} is already frozen - pass --force to overwrite")

    if not args.pi_pilot_path.exists():
        raise SystemExit(
            f"{args.pi_pilot_path} missing - run "
            "`python3 scripts/measure_pi.py --programs all` first"
        )
    pilot = json.loads(args.pi_pilot_path.read_text())
    pi_by_task = {name: p["pi_hat"] for name, p in pilot["per_program"].items()}

    expected = set(_frozen_task_names())
    missing = expected - set(pi_by_task)
    if missing:
        raise SystemExit(
            f"{args.pi_pilot_path} covers {len(pi_by_task)}/{len(expected)} frozen tasks, "
            f"missing: {sorted(missing)} - re-run measure_pi.py --programs all"
        )

    tasks = stratify({name: pi_by_task[name] for name in expected})
    counts = {label: sum(1 for t in tasks if t["stratum"] == label) for label in ("easy", "medium", "hard")}

    report = {
        "frozen": True,
        "source": str(args.pi_pilot_path),
        "seed": pilot.get("seed"),
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
