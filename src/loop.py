"""A7 - the repair loop (Algorithm 1).

  for t in 1..B:
      p = propose(evidence, exclusions)   <- steering acts here
      if guard(p): continue               <- guard acts here
      r = oracle(p)
      if r is accept: return p
      store(p, r.counterexample, theta(p))

Week-3 scope: guard is not implemented yet, so every proposal is verified
(no `continue` branch below). Steering already happens for the "typed" mode
through src.proposer's exclusion_block - only the *runtime* guard that
short-circuits verification is deferred to a later week.
"""
from __future__ import annotations

import dataclasses
import uuid

from src.adapter import TASKS, load
from src.metrics import RoundRecord, append_round
from src.oracle import differential_test
from src.proposer import Attempt, propose

MODES = ("no_memory", "untyped", "typed")


@dataclasses.dataclass(frozen=True)
class EpisodeResult:
    episode_id: str
    task: str
    mode: str
    accepted_patch: str | None   # None if the budget ran out first
    rounds: int
    history: list[Attempt]


def run_episode(
    task_name: str,
    mode: str,
    *,
    budget: int = 10,
    model: str | None = None,
    granularity: str = "fine",
    max_examples: int = 100,
    seed: int = 0,
    metrics_path=None,
) -> EpisodeResult:
    """propose -> oracle -> record, repeated up to `budget` rounds.

    Every mode is verified every round; only the prompt (src.proposer) varies
    with `mode`. Writes one RoundRecord per round via src.metrics regardless
    of how the episode ends.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    task = TASKS[task_name]
    program = load(task_name)
    episode_id = uuid.uuid4().hex[:12]
    history: list[Attempt] = []

    for round_index in range(1, budget + 1):
        patch = propose(
            task_name, program.buggy_source, task.entry_point,
            mode, history, model=model, granularity=granularity,
        )
        result = differential_test(
            task, patch, program.correct_source,
            max_examples=max_examples, seed=seed + round_index,
        )
        attempt = Attempt.from_result(task_name, program.buggy_source, patch, result)

        record = RoundRecord(
            episode_id=episode_id, task=task_name, mode=mode, round_index=round_index,
            patch=patch, accept=result.accept,
            counterexample_args=result.args, reason=result.reason,
            examples_tried=result.examples_tried,
            coarse_type=attempt.coarse_type.key if attempt.coarse_type else None,
            fine_type=attempt.fine_type.key if attempt.fine_type else None,
            model=model,
        )
        append_round(record, **({} if metrics_path is None else {"path": metrics_path}))
        history.append(attempt)

        if result.accept:
            return EpisodeResult(episode_id, task_name, mode, patch, round_index, history)

    return EpisodeResult(episode_id, task_name, mode, None, budget, history)


if __name__ == "__main__":
    import sys

    task_name = sys.argv[1] if len(sys.argv) > 1 else "gcd"
    mode = sys.argv[2] if len(sys.argv) > 2 else "typed"
    result = run_episode(task_name, mode, budget=5)
    status = f"repaired in {result.rounds} rounds" if result.accepted_patch else "budget exhausted"
    print(f"{task_name} [{mode}]: {status}")
