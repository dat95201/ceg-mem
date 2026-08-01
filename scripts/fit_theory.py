"""E6 (failure taxonomy), E7 (closed-form theory fit), E8 (budgeted success) -
three post-hoc analyses that read already-logged data, no extra API calls.

Data sources:
  data/results_real.json  - frozen per-episode summaries (src.metrics.summarize_episode)
  data/episodes.jsonl      - raw per-round rows, needed for:
                              - E7's q_tau/K_hat (per-type frequency among no_memory rounds)
                              - E6's anchoring proxy (per-round location detail + patch source)

E7 estimates pi_hat and q_hat_tau from the *same* no_memory corpus (every
round of a no_memory episode is an i.i.d. draw from (pi, {q_tau}) under
Assumption 3 - that's exactly why E1 used force_full_budget), rather than
mixing in the separate 5-task data/pi_pilot.json, so the pi/q pair used to
compute D (Eq. (4)) always comes from one consistent source per task.

E6's "anchoring" is the proxy already flagged to the user as approximate
(cross-referencing a failed typed episode's eliminated locations against
accepted patches of the same task in the no_memory corpus - correlational,
not ground truth). "Guard miss" (src.metrics.summarize_episode) is a real,
directly-measured quantity: a non-guarded refuted round whose post-hoc type
matches an earlier refuted round's type in the same episode, i.e. the guard's
cheap a-priori location guess (src.typer.edit_location) missed a real repeat.

Known simplification, stated once here rather than faked with a made-up
split: "Residual redundant proposal" (Table 6 in the paper) is a *separate*
mechanism from "guard miss" only because the synthetic model gives the guard
its own independent coherence coin-flip from the stored type's. This
implementation's guard and steer both read the *same* (possibly noised)
stored type (src/memory.py's TypedMemory), so there is no second, independent
noise source to attribute a distinct "residual redundant proposal" count to -
this script reports guard_miss as the single measured quantity for both rows
and says so explicitly in the output, rather than inventing a fake split.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import load
from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds
from src.typer import edit_location

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
MODES = ("no_memory", "untyped", "typed")


def _is_main_grid(ep: dict) -> bool:
    return ep["guard_on"] and ep["steer_on"] and ep["max_examples"] == 100 and ep["typing_noise_c"] == 1.0


# ---------------------------------------------------------------- E7 ------

def estimate_pi_q(no_memory_rows_by_task: dict[str, list[dict]], granularity: str) -> dict[str, dict]:
    out = {}
    for task, rows in no_memory_rows_by_task.items():
        rows = [r for r in rows if not r.get("guarded", False)]
        n = len(rows)
        if n == 0:
            continue
        pi_hat = sum(1 for r in rows if r["accept"]) / n
        counts = collections.Counter(
            r[f"{granularity}_type"] for r in rows if not r["accept"] and r.get(f"{granularity}_type")
        )
        q_hat = {t: c / n for t, c in counts.items()}
        out[task] = {"pi_hat": pi_hat, "n_rounds": n, "K_hat": len(q_hat), "q_hat": q_hat}
    return out


def expected_redundant_types(pi_hat: float, q_hat: dict[str, float]) -> float:
    """D from Eq. (4): expected number of distinct wrong types drawn before
    the first correct proposal."""
    if pi_hat <= 0:
        return float("nan")
    return sum(q / (q + pi_hat) for q in q_hat.values())


def _relative_mae(observed: list[float], predicted: list[float]) -> float | None:
    if not observed:
        return None
    obs, pred = np.asarray(observed), np.asarray(predicted)
    mean_obs = obs.mean()
    return float(np.abs(obs - pred).mean() / mean_obs) if mean_obs else None


def theory_fit(results_episodes: list[dict], pi_q_by_task: dict[str, dict]) -> dict:
    """Compare predicted 1/pi (no memory) and 1+D (untyped/typed, pooled) against
    observed mean oracle_calls_to_accept per task - Figure 4 in the paper."""

    def _observed(mode_filter) -> dict[str, list[float]]:
        by_task: dict[str, list[float]] = collections.defaultdict(list)
        for ep in results_episodes:
            if not _is_main_grid(ep) or not ep["accepted"] or ep["oracle_calls_to_accept"] is None:
                continue
            if mode_filter(ep["mode"]):
                by_task[ep["task"]].append(ep["oracle_calls_to_accept"])
        return {t: float(np.mean(v)) for t, v in by_task.items()}

    obs_no_memory = _observed(lambda m: m == "no_memory")
    obs_memory = _observed(lambda m: m in ("untyped", "typed"))  # pooled: Theorem 4.3(a) says they're equivalent here

    points_no_memory, points_memory = [], []
    for task, obs in obs_no_memory.items():
        stats = pi_q_by_task.get(task)
        if stats is None or stats["pi_hat"] <= 0:
            continue
        points_no_memory.append((task, obs, 1.0 / stats["pi_hat"]))
    for task, obs in obs_memory.items():
        stats = pi_q_by_task.get(task)
        if stats is None:
            continue
        d = expected_redundant_types(stats["pi_hat"], stats["q_hat"])
        points_memory.append((task, obs, 1.0 + d))

    def _summarize(points):
        if len(points) < 2:
            return {"n_tasks": len(points), "r": None, "relative_mae": None, "points": points}
        obs = [p[1] for p in points]
        pred = [p[2] for p in points]
        r = float(np.corrcoef(obs, pred)[0, 1])
        return {"n_tasks": len(points), "r": r, "relative_mae": _relative_mae(obs, pred), "points": points}

    return {"no_memory_arm": _summarize(points_no_memory), "memory_arm": _summarize(points_memory)}


# ---------------------------------------------------------------- E8 ------

def success_at_budget(results_episodes: list[dict], budgets: tuple[int, ...] = (8, 10)) -> dict:
    out = {}
    for mode in MODES:
        cell = [e for e in results_episodes if e["mode"] == mode and _is_main_grid(e)]
        if not cell:
            continue
        out[mode] = {"n_episodes": len(cell)}
        for b in budgets:
            oracle_budget = sum(
                1 for e in cell if e["accepted"] and e["oracle_calls_to_accept"] is not None and e["oracle_calls_to_accept"] <= b
            ) / len(cell)
            proposal_budget = sum(
                1 for e in cell if e["accepted"] and e["first_accept_round"] is not None and e["first_accept_round"] <= b
            ) / len(cell)
            out[mode][f"success@{b}"] = {"oracle_budget": oracle_budget, "proposal_budget": proposal_budget}
    return out


# ---------------------------------------------------------------- E6 ------

def _accepted_no_memory_locations(no_memory_rows_by_task: dict[str, list[dict]], granularity: str) -> dict[str, set[str]]:
    """For each task, the edit locations of patches the no_memory corpus
    actually got accepted - the "did a correct fix exist near here" signal
    the anchoring proxy cross-references against."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for task, rows in no_memory_rows_by_task.items():
        buggy_source = load(task).buggy_source
        for r in rows:
            if not r["accept"]:
                continue
            out[task].add(edit_location(buggy_source, r["patch"]))
    return out


def failure_taxonomy(
    results_episodes: list[dict],
    raw_rows_by_episode: dict[str, list[dict]],
    accepted_locations_by_task: dict[str, set[str]],
    granularity: str,
) -> dict:
    typed_main = [e for e in results_episodes if e["mode"] == "typed" and _is_main_grid(e)]
    if not typed_main:
        return {"n_typed_episodes": 0}

    budget_exhaustion_rate = sum(1 for e in typed_main if not e["accepted"]) / len(typed_main)
    guard_miss_mean = float(np.mean([e["guard_miss"] for e in typed_main]))

    failed = [e for e in typed_main if not e["accepted"]]
    anchoring_suspected = []
    for e in failed:
        rows = raw_rows_by_episode.get(e["episode_id"], [])
        eliminated = {
            r[f"{granularity}_type"].split("::")[2]
            for r in rows
            if not r["accept"] and not r.get("guarded", False) and r.get(f"{granularity}_type")
        }
        if eliminated & accepted_locations_by_task.get(e["task"], set()):
            anchoring_suspected.append(e["episode_id"])

    return {
        "n_typed_episodes": len(typed_main),
        "n_failed": len(failed),
        "budget_exhaustion_rate": budget_exhaustion_rate,
        "guard_miss_mean_per_episode": guard_miss_mean,
        "residual_redundant_proposal": (
            "not separable from guard_miss with this implementation's instrumentation - "
            "see module docstring; reporting guard_miss for both taxonomy rows"
        ),
        "anchoring_suspected_rate_among_failed": (len(anchoring_suspected) / len(failed)) if failed else None,
        "anchoring_suspected_episode_ids": anchoring_suspected,
        "anchoring_note": (
            "PROXY, not ground truth: flags a failed typed episode if the no_memory corpus "
            "of the same task ever accepted a patch touching a location this episode had "
            "already eliminated. Correlational evidence, not a causal trace of this episode's "
            "own proposer - reviewed manually before citing in the paper."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-path", type=pathlib.Path, default=DATA_DIR / "results_real.json")
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--granularity", default="fine", choices=["coarse", "fine"])
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "failure_taxonomy.json")
    parser.add_argument("--theory-out", type=pathlib.Path, default=DATA_DIR / "theory_fit.json")
    args = parser.parse_args()

    if not args.results_path.exists():
        raise SystemExit(f"{args.results_path} missing - run scripts/freeze_results.py first")
    results_episodes = json.loads(args.results_path.read_text())["episodes"]

    raw_rows = load_rounds(args.episodes_path)
    raw_by_episode = group_by_episode(raw_rows)
    no_memory_rows_by_task: dict[str, list[dict]] = collections.defaultdict(list)
    for r in raw_rows:
        if r["mode"] == "no_memory":
            no_memory_rows_by_task[r["task"]].append(r)

    pi_q = estimate_pi_q(no_memory_rows_by_task, args.granularity)
    fit = theory_fit(results_episodes, pi_q)
    print("=== E7: theory fit ===")
    for arm, data in fit.items():
        print(f"  {arm}: n_tasks={data['n_tasks']} r={data['r']} relative_mae={data['relative_mae']}")
    args.theory_out.write_text(json.dumps({"pi_q_by_task": pi_q, "fit": fit}, indent=2) + "\n")
    print(f"wrote {args.theory_out}")

    success = success_at_budget(results_episodes)
    print("\n=== E8: success@budget ===")
    print(json.dumps(success, indent=2))

    accepted_locations = _accepted_no_memory_locations(no_memory_rows_by_task, args.granularity)
    taxonomy = failure_taxonomy(results_episodes, raw_by_episode, accepted_locations, args.granularity)
    print("\n=== E6: failure taxonomy (typed memory) ===")
    print(json.dumps(taxonomy, indent=2))

    out = {"success_at_budget": success, "failure_taxonomy": taxonomy}
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
