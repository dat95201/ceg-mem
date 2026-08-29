"""Per-arm summary straight off the round log. No corpus freeze required.

This is the *look at it now* tool, not the artifact tool. The reporting chain
is scripts/freeze_results.py -> analyze.py -> fit_theory.py, and its first step
is a completeness gate: it reads the frozen data/tasks.json and refuses unless
every cell of the declared grid is present. That gate is the right behaviour
for a number that goes in the paper and the wrong behaviour for a smoke run on
three programs, which is why this exists beside it rather than inside it.

What it shares with the real chain is the only thing that matters: every number
here comes from src.metrics.summarize_episode, the same function
freeze_results, analyze, fit_theory and check_consistency all call. So "oracle
calls to repair" means the same thing here as in Table 2 - rounds after the
first accept truncated out, guarded rounds not counted as oracle calls - and a
smoke number and a real number are comparable quantities measured on
incomparable samples.

It writes nothing frozen and nothing downstream reads it. Deliberately: an
artifact produced without the completeness gate must not be able to reach the
manuscript by accident.

Episodes are grouped by *arm* - the mode plus every knob that makes a different
experiment (guard, steer, max_examples, typing_noise_c, force_full_budget) -
because a single log routinely holds several. scripts/analyze.py's _is_main_grid
exists for the same reason; pooling arms is the easiest way to report a number
that means nothing.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds, summarize_episode

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"


def arm_of(summary: dict) -> tuple:
    """Everything that makes this episode a different experiment."""
    return (
        summary["mode"],
        summary["guard_on"], summary["steer_on"],
        summary["max_examples"], summary["typing_noise_c"],
        summary.get("granularity", "fine"),
        summary.get("force_full_budget", False),
        # Each of these makes an episode a different experiment, so without them
        # an audited cell and an unaudited one would average together in this
        # table under one label.
        summary.get("audit_guarded", False),
        summary.get("reasoning_effort"),
    )


def arm_label(arm: tuple) -> str:
    mode, guard, steer, k, c, gran, full, audit, effort = arm
    bits = [mode]
    if not guard:
        bits.append("steer-only")      # guard off, steering on (E3)
    if not steer:
        bits.append("guard-only")      # steering off, guard on (E3)
    if k != 100:
        bits.append(f"k={k}")          # E4
    if c != 1.0:
        bits.append(f"c={c}")          # E5
    if gran != "fine":
        bits.append(gran)
    if full:
        bits.append("full-budget")     # E1
    if audit:
        bits.append("audit")           # E8 --audit-guarded
    if effort:
        bits.append(f"eff={effort}")   # o-series reasoning effort
    return " ".join(bits)


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def _fmt(value, spec=".2f"):
    return "-" if value is None else format(value, spec)


def aggregate(summaries: list[dict]) -> list[dict]:
    """One row per arm. Means are over episodes, unweighted by task."""
    by_arm: dict[tuple, list[dict]] = collections.defaultdict(list)
    for summary in summaries:
        by_arm[arm_of(summary)].append(summary)

    rows = []
    for arm, group in sorted(by_arm.items(), key=lambda kv: arm_label(kv[0])):
        accepted = [s for s in group if s["accepted"]]
        rows.append({
            "arm": arm_label(arm),
            "mode": arm[0], "guard_on": arm[1], "steer_on": arm[2],
            "max_examples": arm[3], "typing_noise_c": arm[4],
            "granularity": arm[5], "force_full_budget": arm[6],
            "audit_guarded": arm[7], "reasoning_effort": arm[8],
            "n_episodes": len(group),
            "n_tasks": len({s["task"] for s in group}),
            "n_seeds": len({s["seed"] for s in group}),
            # Corollary 4.4: repaired within budget, as a rate over episodes.
            "success_at_b": _mean([s["success_at_b"] for s in group]),
            # Theorem 4.3(a). Defined only on episodes that were repaired, so
            # it is reported next to the rate above and never instead of it -
            # an arm that repairs one easy task in one call looks best on this
            # column alone.
            "oracle_calls_to_accept": _mean([s["oracle_calls_to_accept"] for s in accepted]),
            "n_oracle_calls": _mean([s["n_oracle_calls"] for s in group]),
            # Theorem 4.3(b).
            "redundant_attempts": _mean([s["redundant_attempts"] for s in group]),
            "n_guarded": _mean([s["n_guarded"] for s in group]),
            # Proposition 4.5: the cost of guarding, Theta(m) untyped vs O(1) typed.
            "guard_evaluations": _mean([s["guard_evaluations"] for s in group]),
            "proposals": _mean([s["proposals"] for s in group]),
            "n_inconclusive": sum(s["n_inconclusive"] for s in group),
            # The arm-neutral redundancy count (DESIGN.md SS6): rounds blocked
            # because they provably reproduced a stored counterexample. Unlike
            # redundant_attempts it means the same thing in the flat and the
            # typed arm, so it is the one to compare across arms.
            "blocked_known_counterexample": _mean(
                [s.get("blocked_known_counterexample") for s in group]),
            # What the run cost. None when the freeze predates the ledger join.
            "tokens_in": _mean([s.get("tokens_in") for s in group]),
            "tokens_out": _mean([s.get("tokens_out") for s in group]),
            "wall_sec": _mean([s.get("wall_sec") for s in group]),
        })
    return rows


def print_table(rows: list[dict]) -> None:
    head = (f"{'arm':32s} {'n':>3s} {'succ@B':>7s} {'oracle→ok':>10s} {'oracle':>7s} "
            f"{'redund':>7s} {'blkKnwn':>7s} {'guarded':>7s} {'gEval':>6s} {'tok_in':>8s}")
    print(head)
    print("-" * len(head))
    for row in rows:
        print(f"{row['arm']:32s} {row['n_episodes']:>3d} "
              f"{_fmt(row['success_at_b']):>7s} "
              f"{_fmt(row['oracle_calls_to_accept']):>10s} "
              f"{_fmt(row['n_oracle_calls']):>7s} "
              f"{_fmt(row['redundant_attempts']):>7s} "
              f"{_fmt(row['blocked_known_counterexample']):>7s} "
              f"{_fmt(row['n_guarded']):>7s} "
              f"{_fmt(row['guard_evaluations']):>6s} "
              f"{_fmt(row['tokens_in'], '.0f'):>8s}")


def print_by_task(summaries: list[dict]) -> None:
    by: dict[tuple, list[dict]] = collections.defaultdict(list)
    for summary in summaries:
        by[(summary["task"], arm_label(arm_of(summary)))].append(summary)
    print(f"\n{'task':26s} {'arm':32s} {'n':>3s} {'succ@B':>7s} {'oracle→ok':>10s}")
    for (task, arm), group in sorted(by.items()):
        accepted = [s for s in group if s["accepted"]]
        print(f"{task[:26]:26s} {arm:32s} {len(group):>3d} "
              f"{_fmt(_mean([s['success_at_b'] for s in group])):>7s} "
              f"{_fmt(_mean([s['oracle_calls_to_accept'] for s in accepted])):>10s}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--programs", nargs="+", default=None,
                        help="restrict to these tasks (default: everything in the log)")
    parser.add_argument("--mode", nargs="+", default=None, help="restrict to these modes")
    parser.add_argument("--by-task", action="store_true", help="also break each arm down per task")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="also write the rows as JSON (not a frozen artifact)")
    args = parser.parse_args()

    rows = load_rounds(args.episodes_path)
    if not rows:
        raise SystemExit(f"no rounds in {args.episodes_path} - run scripts/run_eval.py first")

    summaries = [summarize_episode(ep) for ep in group_by_episode(rows).values()]
    if args.programs:
        wanted = set(args.programs)
        summaries = [s for s in summaries if s["task"] in wanted]
    if args.mode:
        summaries = [s for s in summaries if s["mode"] in args.mode]
    if not summaries:
        raise SystemExit("no episodes matched the filters")

    # The provenance line. Every one of these is a reason a number below is not
    # a result: one seed has no variance to report, a short budget truncates
    # Theorem 4.3(a)'s tail, and pi is a property of the model - a smoke model's
    # repair rate says nothing about the reported one's.
    seeds = sorted({s["seed"] for s in summaries})
    models = sorted({str(r.get("model")) for r in rows})
    budget = max(r["round_index"] for r in rows)
    print(f"{len(rows)} rounds | {len(summaries)} episodes | "
          f"{len({s['task'] for s in summaries})} tasks | seeds {seeds} | "
          f"max round seen {budget} | model {', '.join(models)}")
    if len(seeds) < 2:
        print("NOTE: one seed - every difference below is a single episode, and no "
              "interval can be computed from it.")
    print()

    aggregated = aggregate(summaries)
    print_table(aggregated)
    if args.by_task:
        print_by_task(summaries)

    inconclusive = sum(row["n_inconclusive"] for row in aggregated)
    if inconclusive:
        print(f"\n{inconclusive} rounds were inconclusive (oracle or proposer error) - "
              f"they spent a proposal but refuted nothing.")

    if args.out:
        args.out.write_text(json.dumps({
            "episodes_path": str(args.episodes_path),
            "frozen": False,     # never; see this module's docstring
            "n_rounds": len(rows), "n_episodes": len(summaries),
            "seeds": seeds, "models": models,
            "arms": aggregated,
        }, indent=2) + "\n")
        print(f"\nwrote {args.out} (not frozen - scripts/freeze_results.py owns the artifact)")


if __name__ == "__main__":
    main()
