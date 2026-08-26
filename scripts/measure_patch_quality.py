"""Patch verbosity and the correct/plausible ratio - what memory does to the
*quality* of the patch, not just to how many attempts it took.

**No model calls.** Both quantities come from data/episodes.jsonl plus the
reference source already on disk; the overfit half additionally reads
data/overfit_checks.jsonl, which the grid writes under --check-overfit.

Patch verbosity
---------------
The edit an accepted patch makes, against the edit the author's own fix makes:

  patch_loc / gold_loc      lines changed, candidate vs reference
  patch_hunks / gold_hunks  contiguous changed regions

Both are diffs against the *faulty* source (never the reference - see
src/adapter.py), computed with the same src.typer._changed_hunks the type
function uses, so "how big is this edit" means the same thing here as it does
where theta decides an edit location.

This is a metric CEGMem plausibly wins by construction rather than by luck:
steering cuts the refine-then-refine-again loop that inflates patches, and
RECAP measured that loop adding +121% to the change size. It is reported for
every arm and is only meaningful on episodes that accepted.

Correct/plausible ratio (overfitting)
-------------------------------------
plausible = the sampled oracle accepted. correct = is_truly_correct agreed
over the whole pool. The ratio is correct/plausible per arm.

This one is a **guardrail, not a win to hope for**. Steering pushes the
proposer away from failure classes it has seen, and nothing guarantees the
direction it pushes toward is the correct class rather than the
plausible-but-wrong one. If typed memory has a worse ratio than untyped, that
is a real cost of the method and belongs in the paper next to the savings.
DESIGN.md already warns the audit is near-vacuous wherever the sampled oracle
is already the full pool - 109 of 120 tasks ship 80 cases or fewer against
max_examples=100 - so the report carries how many episodes the audit could
actually discriminate on, and a ratio computed over none of them is None
rather than a reassuring 1.00.

Writes data/patch_quality.json.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import TASKS, load  # noqa: E402
from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds  # noqa: E402
from src.typer import _changed_hunks  # noqa: E402

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DEFAULT_OVERFIT_LOG = DATA_DIR / "overfit_checks.jsonl"
MODES = ("no_memory", "untyped", "typed", "transcript")


def edit_size(buggy_source: str, candidate_source: str) -> tuple[int, int]:
    """(lines changed, contiguous hunks) of candidate against the faulty source."""
    hunks = _changed_hunks(buggy_source, candidate_source)
    return sum(b - a + 1 for a, b in hunks), len(hunks)


def _mean(values: list) -> float | None:
    vals = [v for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(vals)) if vals else None


def _regression_summary(eps: list[dict]) -> dict:
    """#22 rolled up over one arm's accepted episodes.

    Two separate reasons a rate can be absent, kept apart on purpose:
    the audit never ran (`regression_rate` is None), or it ran and the program
    had no already-passing case to break (`p2p_total` is 0). Only the second is
    evidence; the first is a gap.
    """
    scored = [e for e in eps if e.get("regression_rate") is not None]
    if not scored:
        return {"n_regression_audited": 0, "regression_rate": None,
                "n_episodes_with_regression": None, "p2p_cases_broken": None,
                "regression_cap": None}
    broken = [e for e in scored if (e.get("p2p_broken") or 0) > 0]
    caps = {e.get("regression_cap") for e in scored}
    return {
        "n_regression_audited": len(scored),
        # Pooled over cases, not a mean of per-episode rates: an episode with
        # 200 P2P cases and one with 3 are not one vote each.
        "regression_rate": (sum(e["p2p_broken"] for e in scored)
                            / sum(e["p2p_total"] for e in scored)),
        "n_episodes_with_regression": len(broken),
        "p2p_cases_broken": sum(e["p2p_broken"] for e in scored),
        "mean_fix_rate": _mean([e["fix_rate"] for e in scored
                                if e.get("fix_rate") is not None]),
        # A mixed set of caps is a mixed measurement - say so rather than
        # picking one to print.
        "regression_cap": (caps.pop() if len(caps) == 1 else sorted(
            str(c) for c in caps)),
    }


def _is_main_grid(r: dict) -> bool:
    """The E2 comparison grid, by the same predicate analyze.py applies.

    This script had no filter at all, so arms["typed"] pooled E2 with
    E3-guard-only, E3-steer-only, E4-k20/k8/k3 and every E5 level - which made
    the headline correct/plausible ratio partly the c=0.0 arm's.
    """
    return (r.get("guard_on", True) and r.get("steer_on", True)
            and r.get("max_examples", 100) == 100
            and r.get("typing_noise_c", 1.0) == 1.0
            and r.get("force_full_budget", False) == (r["mode"] == "no_memory")
            and not r.get("audit_guarded", False)
            and not r.get("typing_random", False)
            and r.get("transcript_window", 0) == 0)


def measure(rows: list[dict], overfit: dict[str, dict],
            main_grid_only: bool = True) -> list[dict]:
    """One record per accepted episode."""
    out: list[dict] = []
    gold_cache: dict[str, tuple[int, int]] = {}

    for _eid, ep in group_by_episode(rows).items():
        ep = sorted(ep, key=lambda r: r["round_index"])
        if main_grid_only and not _is_main_grid(ep[0]):
            continue
        accepted = next((r for r in ep if r["accept"] and not r.get("guarded", False)), None)
        if accepted is None or not accepted.get("patch"):
            continue
        task_name = accepted["task"]
        if task_name not in TASKS:
            continue   # a log from a different corpus; skip rather than guess
        program = load(task_name)
        if task_name not in gold_cache:
            gold_cache[task_name] = edit_size(program.buggy_source, program.correct_source)
        gold_loc, gold_hunks = gold_cache[task_name]
        loc, hunks = edit_size(program.buggy_source, accepted["patch"])

        first = ep[0]
        audit = overfit.get(first["episode_id"])
        out.append({
            "episode_id": first["episode_id"], "task": task_name,
            "mode": first["mode"], "seed": first.get("seed", 0),
            "first_accept_round": accepted["round_index"],
            "patch_loc": loc, "patch_hunks": hunks,
            "gold_loc": gold_loc, "gold_hunks": gold_hunks,
            # None, not inf, when the reference itself is a wholesale rewrite
            # with no measurable hunk - json.dumps writes a bare Infinity that
            # only Python reads back.
            "loc_ratio": (loc / gold_loc) if gold_loc else None,
            "hunk_ratio": (hunks / gold_hunks) if gold_hunks else None,
            "truly_correct": audit["truly_correct"] if audit else None,
            # #22. None means the audit did not run, which is not the same as
            # zero - an arm that never ran --check-regression must not read as
            # an arm that regressed nothing.
            "regression_rate": audit.get("regression_rate") if audit else None,
            "p2p_total": audit.get("p2p_total") if audit else None,
            "p2p_broken": audit.get("p2p_broken") if audit else None,
            "fix_rate": audit.get("fix_rate") if audit else None,
            "regression_cap": audit.get("regression_cap") if audit else None,
        })
    return out


def _load_overfit(path: pathlib.Path) -> dict[str, dict]:
    """episode_id -> audit row.

    Keyed on episode_id, which is unique by construction
    (src.loop.cell_signature). (task, mode, seed) is NOT: E2 and E4-k20/k8/k3
    all run mode="typed" over the sweep subset at seeds 1-3, so four legitimate
    audits collapsed onto one arbitrary survivor and it was then attributed to
    all four episodes. E6 with a window does the same.
    """
    if not path.exists():
        return {}
    by_key: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        # Last write wins, matching src.metrics.load_rounds: a re-run cell
        # re-audits its own accept rather than adding a second verdict.
        if "episode_id" in row:
            by_key[row["episode_id"]] = row
    return by_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--overfit-path", type=pathlib.Path, default=DEFAULT_OVERFIT_LOG)
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "patch_quality.json")
    args = parser.parse_args()

    rows = load_rounds(args.episodes_path)
    if not rows:
        raise SystemExit(f"{args.episodes_path} is empty or missing - run the grid first")
    overfit = _load_overfit(args.overfit_path)
    if not overfit:
        print(f"NOTE: {args.overfit_path} missing or empty - the correct/plausible "
              f"ratio needs a grid run with --check-overfit. Verbosity is unaffected.",
              flush=True)

    per_episode = measure(rows, overfit)

    arms: dict = {}
    for mode in MODES:
        eps = [e for e in per_episode if e["mode"] == mode]
        if not eps:
            continue
        audited = [e for e in eps if e["truly_correct"] is not None]
        correct = sum(1 for e in audited if e["truly_correct"])
        arms[mode] = {
            "n_accepted_episodes": len(eps),
            # #23 verbosity, against the author's own fix.
            "loc_ratio": _mean([e["loc_ratio"] for e in eps]),
            "hunk_ratio": _mean([e["hunk_ratio"] for e in eps]),
            "patch_loc": _mean([float(e["patch_loc"]) for e in eps]),
            "gold_loc": _mean([float(e["gold_loc"]) for e in eps]),
            # #21 correct/plausible. None when nothing was audited - saying
            # "not measured" beats printing a 1.00 that means "never checked".
            "n_audited": len(audited),
            "n_truly_correct": correct,
            "correct_over_plausible": (correct / len(audited)) if audited else None,
            "overfit_rate": (1 - correct / len(audited)) if audited else None,
            # #22 regression. Denominator is episodes whose audit actually
            # scored a P2P case: a fault whose footprint is the whole pool has
            # nothing to regress, and averaging its None as a 0 would dilute
            # the rate towards a reassuring number nobody measured.
            **_regression_summary(eps),
        }

    report = {
        "episodes_path": str(args.episodes_path),
        "overfit_path": str(args.overfit_path),
        "n_accepted_episodes": len(per_episode),
        "arms": arms,
        "per_episode": per_episode,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    head = (f"{'arm':12s} {'acc':>4s} {'loc/gold':>9s} {'hunk/gold':>10s} "
            f"{'audited':>8s} {'correct/plaus':>14s} {'regr':>8s} {'regr@ep':>8s}")
    print("\n" + head)
    print("-" * len(head))
    for mode, a in arms.items():
        def f(x, spec=".3f"):
            return format(x, spec) if isinstance(x, float) else "-"
        n_reg = a["n_regression_audited"]
        at_ep = (f"{a['n_episodes_with_regression']}/{n_reg}" if n_reg else "-")
        print(f"{mode:12s} {a['n_accepted_episodes']:>4d} {f(a['loc_ratio']):>9s} "
              f"{f(a['hunk_ratio']):>10s} {a['n_audited']:>8d} "
              f"{f(a['correct_over_plausible']):>14s} "
              f"{f(a['regression_rate']):>8s} {at_ep:>8s}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
