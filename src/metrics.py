"""Data schema for the repair loop (src/loop.py).

One RoundRecord is written per round - one proposal plus, if it wasn't
guarded away, one oracle call - not per episode, so an interrupted run still
leaves usable rows. Appended as JSON Lines, same convention as
data/calls.jsonl in src/llm.py.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

DEFAULT_METRICS_LOG = pathlib.Path("data/episodes.jsonl")


@dataclasses.dataclass(frozen=True)
class RoundRecord:
    """One round within one episode of the repair loop."""

    episode_id: str
    task: str
    mode: str            # "no_memory" | "untyped" | "typed"
    round_index: int      # 1-based
    patch: str
    accept: bool
    counterexample_args: list | None
    reason: str | None
    examples_tried: int
    coarse_type: str | None   # FailureType.key, or None once accept=True
    fine_type: str | None
    model: str | None
    seed: int = 0
    guarded: bool = False        # True: candidate blocked before the oracle ran (Eq. (2))
    guard_evaluations: int = 0   # stored counterexamples re-run this round (Proposition 4.5 cost)
    guard_on: bool = True        # ablation (E3): guard disabled entirely when False
    steer_on: bool = True        # ablation (E3): exclusion_block suppressed when False
    max_examples: int = 100      # oracle informativeness sweep (E4)
    typing_noise_c: float = 1.0  # typing coherence sweep (E5)

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def append_round(record: RoundRecord, path: pathlib.Path = DEFAULT_METRICS_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record.to_json()) + "\n")


def load_rounds(path: pathlib.Path = DEFAULT_METRICS_LOG) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def group_by_episode(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_episode: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_episode.setdefault(row["episode_id"], []).append(row)
    return by_episode


def summarize_episode(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up one episode's RoundRecord rows into the per-episode summary
    shared by scripts/freeze_results.py, scripts/analyze.py,
    scripts/fit_theory.py and scripts/check_consistency.py - one
    implementation so every consumer agrees on what "oracle calls to repair"
    and "redundant attempt" mean (Section 4/5 of the paper).

    Two failure-taxonomy quantities (Table 6 in the paper) fall out directly:
      - every guarded round is, by construction, a redundant attempt: its
        type matched a bucket memory had already eliminated (that's the only
        way to be guarded), so the guard did its job of avoiding an oracle
        call for it.
      - a "guard miss" is a round that reached the oracle anyway (not
        guarded) but whose type turns out (post-hoc, from theta()) to match
        a type an *earlier* round in this same episode already had refuted -
        i.e. a round the guard should have caught but its cheap a-priori
        type guess (src.typer.edit_location) missed.
    redundant_attempts = n_guarded + guard_miss follows from that directly.
    """
    rows = sorted(rows, key=lambda r: r["round_index"])
    oracle_rows = [r for r in rows if not r.get("guarded", False)]
    accepted_rows = [r for r in oracle_rows if r["accept"]]
    accepted = bool(accepted_rows)
    first_accept_round = accepted_rows[0]["round_index"] if accepted else None

    oracle_calls_to_accept = None
    if accepted:
        oracle_calls_to_accept = sum(1 for r in oracle_rows if r["round_index"] <= first_accept_round)

    guard_miss = 0
    eliminated_before: set[str] = set()
    for r in oracle_rows:
        if r["accept"]:
            continue
        tkey = r.get("fine_type")
        if tkey is None:
            continue
        if tkey in eliminated_before:
            guard_miss += 1
        eliminated_before.add(tkey)

    n_guarded = len(rows) - len(oracle_rows)
    first = rows[0]
    return {
        "episode_id": first["episode_id"], "task": first["task"], "mode": first["mode"],
        "seed": first.get("seed", 0),
        "guard_on": first.get("guard_on", True), "steer_on": first.get("steer_on", True),
        "max_examples": first.get("max_examples", 100), "typing_noise_c": first.get("typing_noise_c", 1.0),
        "n_rounds": len(rows), "n_oracle_calls": len(oracle_rows), "n_guarded": n_guarded,
        "guard_evaluations": sum(r.get("guard_evaluations", 0) for r in rows),
        "accepted": accepted, "first_accept_round": first_accept_round,
        "oracle_calls_to_accept": oracle_calls_to_accept,
        "guard_miss": guard_miss, "redundant_attempts": n_guarded + guard_miss,
    }


if __name__ == "__main__":
    demo = RoundRecord(
        episode_id="demo", task="gcd", mode="typed", round_index=1,
        patch="def gcd(a, b):\n    return a\n", accept=False,
        counterexample_args=[10, 4], reason="expected 2, got 10",
        examples_tried=3, coarse_type=None, fine_type=None, model=None,
    )
    print(json.dumps(demo.to_json(), indent=2))
