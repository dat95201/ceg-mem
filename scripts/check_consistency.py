"""Assert every number reported in the paper equals the value in the frozen
data files, recomputed from scratch.

This does not read data/results_real.json, data/analysis.json,
data/theory_fit.json or data/failure_taxonomy.json as ground truth to check
*against itself* - it rebuilds each of them fresh from data/episodes.jsonl
(and data/strata.json) using the exact same functions scripts/freeze_results.py,
scripts/analyze.py and scripts/fit_theory.py use to write them, then deep-diffs
the recomputed structure against the frozen file on disk. A mismatch means
either the frozen files are stale (episodes.jsonl changed since they were
written - re-run the freeze/analyze/fit_theory pipeline) or someone hand-edited
a number - both are exactly what this check exists to catch.

Bootstrap resampling is seeded (scripts.analyze.bootstrap_ci's default
seed=0), so recomputing is bit-for-bit deterministic, not just "close" -
mismatches are reported at exact (tolerance 1e-9) precision.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.analyze import MODES as ANALYZE_MODES
from scripts.analyze import compare_conditions
from scripts.fit_theory import (
    estimate_pi_q,
    failure_taxonomy,
    success_at_budget,
    theory_fit,
)
from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds, summarize_episode

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
FLOAT_TOL = 1e-9


def _stratum_by_task() -> dict[str, str]:
    strata_json = DATA_DIR / "strata.json"
    if not strata_json.exists():
        return {}
    return {t["name"]: t["stratum"] for t in json.loads(strata_json.read_text()).get("tasks", [])}


def _recompute_episodes(episodes_path: pathlib.Path) -> list[dict]:
    rows = load_rounds(episodes_path)
    strata = _stratum_by_task()
    summaries = [summarize_episode(ep_rows) for ep_rows in group_by_episode(rows).values()]
    for s in summaries:
        s["stratum"] = strata.get(s["task"])
    return sorted(summaries, key=lambda s: s["episode_id"])


def _diff(path: str, frozen, fresh, mismatches: list[tuple[str, object, object]]) -> None:
    if isinstance(frozen, dict) and isinstance(fresh, dict):
        for key in sorted(set(frozen) | set(fresh)):
            _diff(f"{path}.{key}", frozen.get(key, "<missing>"), fresh.get(key, "<missing>"), mismatches)
        return
    if isinstance(frozen, list) and isinstance(fresh, list):
        if len(frozen) != len(fresh):
            mismatches.append((path, f"<list len {len(frozen)}>", f"<list len {len(fresh)}>"))
            return
        for i, (a, b) in enumerate(zip(frozen, fresh)):
            _diff(f"{path}[{i}]", a, b, mismatches)
        return
    if isinstance(frozen, (int, float)) and isinstance(fresh, (int, float)):
        if abs(frozen - fresh) > FLOAT_TOL:
            mismatches.append((path, frozen, fresh))
        return
    if frozen != fresh:
        mismatches.append((path, frozen, fresh))


def _check(name: str, frozen_path: pathlib.Path, frozen_value, fresh_value, all_ok: list[bool]) -> None:
    if not frozen_path.exists():
        print(f"SKIP  {name}: {frozen_path} does not exist yet")
        return
    # Normalize both sides through JSON first: freshly-computed values may be
    # Python tuples/sets/etc. that are equivalent to, but not `==`, the lists
    # the frozen file's json.load() produced for the same data.
    fresh_value = json.loads(json.dumps(fresh_value))
    mismatches: list[tuple[str, object, object]] = []
    _diff(name, frozen_value, fresh_value, mismatches)
    if mismatches:
        all_ok.append(False)
        print(f"FAIL  {name}: {len(mismatches)} mismatch(es)")
        for path, frozen_v, fresh_v in mismatches[:20]:
            print(f"        {path}: frozen={frozen_v!r} fresh={fresh_v!r}")
        if len(mismatches) > 20:
            print(f"        ... and {len(mismatches) - 20} more")
    else:
        all_ok.append(True)
        print(f"PASS  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--results-path", type=pathlib.Path, default=DATA_DIR / "results_real.json")
    parser.add_argument("--analysis-path", type=pathlib.Path, default=DATA_DIR / "analysis.json")
    parser.add_argument("--theory-path", type=pathlib.Path, default=DATA_DIR / "theory_fit.json")
    parser.add_argument("--taxonomy-path", type=pathlib.Path, default=DATA_DIR / "failure_taxonomy.json")
    parser.add_argument("--granularity", default="fine", choices=["coarse", "fine"])
    args = parser.parse_args()

    all_ok: list[bool] = []
    fresh_episodes = _recompute_episodes(args.episodes_path)

    if args.results_path.exists():
        frozen_episodes = sorted(json.loads(args.results_path.read_text())["episodes"], key=lambda s: s["episode_id"])
        _check("results_real.episodes", args.results_path, frozen_episodes, fresh_episodes, all_ok)
    else:
        print(f"SKIP  results_real.episodes: {args.results_path} does not exist yet")

    if args.analysis_path.exists():
        frozen_analysis = json.loads(args.analysis_path.read_text())
        fresh_analysis = {
            "results_path": frozen_analysis.get("results_path"),
            "metrics": {
                "oracle_calls_to_accept": compare_conditions(fresh_episodes, "oracle_calls_to_accept"),
                "redundant_attempts": compare_conditions(fresh_episodes, "redundant_attempts"),
            },
        }
        _check("analysis", args.analysis_path, frozen_analysis, fresh_analysis, all_ok)
    else:
        print(f"SKIP  analysis: {args.analysis_path} does not exist yet")

    rows = load_rounds(args.episodes_path)
    no_memory_by_task: dict[str, list[dict]] = {}
    for r in rows:
        if r["mode"] == "no_memory":
            no_memory_by_task.setdefault(r["task"], []).append(r)
    fresh_pi_q = estimate_pi_q(no_memory_by_task, args.granularity)
    fresh_fit = theory_fit(fresh_episodes, fresh_pi_q)

    if args.theory_path.exists():
        frozen_theory = json.loads(args.theory_path.read_text())
        fresh_theory = {"pi_q_by_task": fresh_pi_q, "fit": fresh_fit}
        _check("theory_fit", args.theory_path, frozen_theory, fresh_theory, all_ok)
    else:
        print(f"SKIP  theory_fit: {args.theory_path} does not exist yet")

    if args.taxonomy_path.exists():
        from scripts.fit_theory import _accepted_no_memory_locations

        frozen_taxonomy = json.loads(args.taxonomy_path.read_text())
        by_episode = group_by_episode(rows)
        accepted_locations = _accepted_no_memory_locations(no_memory_by_task, args.granularity)
        fresh_success = success_at_budget(fresh_episodes)
        fresh_failure = failure_taxonomy(fresh_episodes, by_episode, accepted_locations, args.granularity)
        fresh_taxonomy = {"success_at_budget": fresh_success, "failure_taxonomy": fresh_failure}
        _check("failure_taxonomy", args.taxonomy_path, frozen_taxonomy, fresh_taxonomy, all_ok)
    else:
        print(f"SKIP  failure_taxonomy: {args.taxonomy_path} does not exist yet")

    if not all_ok:
        print("\nno frozen data files found yet - nothing to check")
        return

    print(f"\n{sum(all_ok)}/{len(all_ok)} checks passed")
    if not all(all_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
