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


if __name__ == "__main__":
    demo = RoundRecord(
        episode_id="demo", task="gcd", mode="typed", round_index=1,
        patch="def gcd(a, b):\n    return a\n", accept=False,
        counterexample_args=[10, 4], reason="expected 2, got 10",
        examples_tried=3, coarse_type=None, fine_type=None, model=None,
    )
    print(json.dumps(demo.to_json(), indent=2))
