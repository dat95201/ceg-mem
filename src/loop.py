"""A7 - the repair loop (Algorithm 1).

  for t in 1..B:
      p = propose(evidence, exclusions)   <- steering acts here (src.proposer)
      if guard(p): continue               <- guard acts here (src.memory, Eq. (2))
      r = oracle(p)
      if r is accept: return p
      store(p, r.counterexample, theta(p))

Guard and steer can each be switched off independently (guard_on/steer_on)
for the E3 ablation (guard-only vs steering-only, Table 4 in the paper).
force_full_budget keeps the loop running past the first accept - needed for
E1's unbiased no-memory corpus ("run all 12 attempts even after a correct
one, so pi_hat isn't biased by early stopping").
"""
from __future__ import annotations

import dataclasses
import random
import uuid

from src.adapter import TASKS, load
from src.memory import build_memory
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
    guard_evaluations: int = 0        # total stored-counterexample re-runs this episode (Prop. 4.5)
    first_accept_round: int | None = None  # set even under force_full_budget, unlike accepted_patch's round


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
    guard_on: bool = True,
    steer_on: bool = True,
    typing_noise_c: float = 1.0,
    force_full_budget: bool = False,
    rng: random.Random | None = None,
) -> EpisodeResult:
    """propose -> guard -> oracle -> record, repeated up to `budget` rounds.

    Every round is one model call (one unit of the proposal budget); the
    oracle call is the expensive one guard exists to avoid. Writes one
    RoundRecord per round via src.metrics regardless of how the episode ends.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    task = TASKS[task_name]
    program = load(task_name)
    episode_id = uuid.uuid4().hex[:12]
    memory = build_memory(mode, granularity=granularity, typing_noise_c=typing_noise_c, rng=rng)

    accepted_patch: str | None = None
    first_accept_round: int | None = None
    total_guard_evaluations = 0
    kwargs = {} if metrics_path is None else {"path": metrics_path}

    def _record(**overrides) -> RoundRecord:
        base = dict(
            episode_id=episode_id, task=task_name, mode=mode,
            model=model, seed=seed, guard_on=guard_on, steer_on=steer_on,
            max_examples=max_examples, typing_noise_c=typing_noise_c,
        )
        base.update(overrides)
        return RoundRecord(**base)

    for round_index in range(1, budget + 1):
        patch = propose(
            task_name, program.buggy_source, task.entry_point,
            mode, memory.history, model=model, granularity=granularity,
            disable_exclusion=not steer_on,
        )

        guarded, guard_evaluations = False, 0
        if guard_on:
            guard_result = memory.guard(patch, program.buggy_source)
            guarded, guard_evaluations = guard_result.blocked, guard_result.evaluations
            total_guard_evaluations += guard_evaluations

        if guarded:
            append_round(_record(
                round_index=round_index, patch=patch, accept=False,
                counterexample_args=None,
                reason="guarded: candidate reproduces a stored counterexample",
                examples_tried=0, coarse_type=None, fine_type=None,
                guarded=True, guard_evaluations=guard_evaluations,
            ), **kwargs)
            continue  # oracle call avoided; still consumes one round of budget

        result = differential_test(
            task, patch, program.correct_source,
            max_examples=max_examples, seed=seed + round_index,
        )
        attempt = Attempt.from_result(task_name, program.buggy_source, patch, result)

        append_round(_record(
            round_index=round_index, patch=patch, accept=result.accept,
            counterexample_args=result.args, reason=result.reason,
            examples_tried=result.examples_tried,
            coarse_type=attempt.coarse_type.key if attempt.coarse_type else None,
            fine_type=attempt.fine_type.key if attempt.fine_type else None,
            guarded=False, guard_evaluations=guard_evaluations,
        ), **kwargs)
        memory.store(attempt)

        if result.accept:
            if first_accept_round is None:
                first_accept_round = round_index
            if accepted_patch is None:
                accepted_patch = patch
            if not force_full_budget:
                return EpisodeResult(
                    episode_id, task_name, mode, patch, round_index, memory.history,
                    guard_evaluations=total_guard_evaluations, first_accept_round=first_accept_round,
                )

    return EpisodeResult(
        episode_id, task_name, mode, accepted_patch, budget, memory.history,
        guard_evaluations=total_guard_evaluations, first_accept_round=first_accept_round,
    )


if __name__ == "__main__":
    import sys

    task_name = sys.argv[1] if len(sys.argv) > 1 else "gcd"
    mode = sys.argv[2] if len(sys.argv) > 2 else "typed"
    result = run_episode(task_name, mode, budget=5)
    status = f"repaired in {result.rounds} rounds" if result.accepted_patch else "budget exhausted"
    print(f"{task_name} [{mode}]: {status} (guard evaluations: {result.guard_evaluations})")
