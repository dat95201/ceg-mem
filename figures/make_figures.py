"""Regenerate the paper's figures from frozen, already-analyzed data - no
extra API calls, no re-running experiments. Mirrors the three synthetic-study
figures with the real-data equivalents:

  fig2_oracle_calls.pdf       oracle calls to repair, by stratum x mode  (Fig. 2a)
  fig2_redundant_attempts.pdf redundant attempts, by stratum x mode      (Fig. 2b)
  fig4_theory_fit.pdf         simulated vs. predicted oracle calls (1/pi, 1+D) (Fig. 4)

Reads data/analysis.json (scripts/analyze.py) and data/theory_fit.json
(scripts/fit_theory.py). Both must exist first.

Print-safe: every mode/arm is distinguished by hatch pattern or marker shape,
not color alone, so the figures still read correctly in black-and-white.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.paths import DATA_DIR, announce  # noqa: E402
OUT_DIR = pathlib.Path(__file__).resolve().parent
STRATA = ("easy", "medium", "hard")
MODES = ("no_memory", "untyped", "typed")
HATCHES = {"no_memory": "", "untyped": "//", "typed": "xx"}
MARKERS = {"no_memory_arm": "o", "memory_arm": "s"}


def _bar_chart(analysis: dict, metric: str, ylabel: str, out_path: pathlib.Path) -> None:
    data = analysis["metrics"][metric]["strata"]
    x = np.arange(len(STRATA))
    width = 0.25

    fig, ax = plt.subplots(figsize=(5, 3.5))
    for i, mode in enumerate(MODES):
        means, los, his = [], [], []
        for stratum in STRATA:
            summary = data[stratum]["summary"][mode]
            means.append(summary["mean"] if summary["mean"] is not None else 0)
            los.append(summary["mean"] - summary["lo"] if summary["mean"] is not None and summary["lo"] is not None else 0)
            his.append(summary["hi"] - summary["mean"] if summary["mean"] is not None and summary["hi"] is not None else 0)
        ax.bar(
            x + (i - 1) * width, means, width, label=mode, hatch=HATCHES[mode],
            edgecolor="black", facecolor="white", yerr=[los, his], capsize=3,
        )
    ax.set_xticks(x)
    ax.set_xticklabels([s.capitalize() for s in STRATA])
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    print(f"wrote {out_path}")


def _theory_fit_scatter(theory: dict, out_path: pathlib.Path) -> None:
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    all_vals = []
    for arm, label in (("no_memory_arm", "no memory (1/pi)"), ("memory_arm", "memory (1+D)")):
        points = theory["fit"][arm]["points"]
        if not points:
            continue
        obs = [p[1] for p in points]
        pred = [p[2] for p in points]
        all_vals.extend(obs + pred)
        ax.scatter(pred, obs, marker=MARKERS[arm], facecolors="none", edgecolors="black", label=label)

    if all_vals:
        lo, hi = min(all_vals), max(all_vals)
        ax.plot([lo, hi], [lo, hi], linestyle="--", color="black", linewidth=1, label="y = x")

    ax.set_xlabel("predicted expected oracle calls")
    ax.set_ylabel("observed mean oracle calls")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    print(f"wrote {out_path}")


def main() -> None:
    announce('make_figures')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--analysis-path", type=pathlib.Path, default=DATA_DIR / "analysis.json")
    parser.add_argument("--theory-path", type=pathlib.Path, default=DATA_DIR / "theory_fit.json")
    parser.add_argument("--out-dir", type=pathlib.Path, default=OUT_DIR)
    args = parser.parse_args()

    if not args.analysis_path.exists():
        raise SystemExit(f"{args.analysis_path} missing - run scripts/analyze.py first")
    if not args.theory_path.exists():
        raise SystemExit(f"{args.theory_path} missing - run scripts/fit_theory.py first")

    analysis = json.loads(args.analysis_path.read_text())
    theory = json.loads(args.theory_path.read_text())

    args.out_dir.mkdir(parents=True, exist_ok=True)
    _bar_chart(analysis, "oracle_calls_to_accept", "oracle calls to repair", args.out_dir / "fig2_oracle_calls.pdf")
    _bar_chart(analysis, "redundant_attempts", "redundant attempts", args.out_dir / "fig2_redundant_attempts.pdf")
    _theory_fit_scatter(theory, args.out_dir / "fig4_theory_fit.pdf")


if __name__ == "__main__":
    main()
