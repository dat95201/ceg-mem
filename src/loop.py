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
one, so pi_hat isn't biased by early stopping"). It is part of the cell
identity below, because it changes what a round means; src.metrics then
truncates the extra rounds back out when summarizing, so the no-memory arm
stays comparable to the memory arms it is tabulated against.

Everything random about an episode is derived from its cell: the episode id,
the typing-noise RNG, and each round's model-call nonce. Re-running a cell
therefore reproduces it exactly and replays from cache for free.
"""
from __future__ import annotations

import dataclasses
import hashlib
import random

from src.adapter import TASKS, load
from src.memory import build_memory
from src.metrics import RoundRecord, append_round
from src.oracle import differential_test
from src.proposer import Attempt, TruncatedResponse, propose

MODES = ("no_memory", "untyped", "typed")


def cell_signature(
    task_name: str,
    mode: str,
    *,
    seed: int,
    granularity: str,
    max_examples: int,
    typing_noise_c: float,
    guard_on: bool,
    steer_on: bool,
    budget: int,
    force_full_budget: bool,
) -> str:
    """Canonical string identity of one experiment cell.

    Every knob that changes what the episode *is* goes in, so two runs of the
    same cell agree and two different cells never collide. Used for both the
    episode id and the typing-noise RNG below.

    `budget` is accepted and deliberately *not* used. A B-round episode is the
    prefix of a longer one - same task, same seed, same nonces, so round k is
    the identical draw at either budget - which is why scripts/run_eval.py
    excludes budget from its resume key too. Including it here gave the same
    cell two episode ids, so src.metrics.load_rounds (which collapses on
    (episode_id, round_index), last write wins) could not recognise the rerun
    as a rewrite: topping a cell up from 12 rounds to 20 appended a second
    episode instead, and every estimator that averages over rounds - pi_hat in
    scripts/fit_theory.py above all - counted the first 12 draws twice. The
    parameter stays in the signature because callers pass it by keyword.
    """
    del budget
    return "|".join((
        task_name, mode, f"seed={seed}", f"gran={granularity}",
        f"max_examples={max_examples}", f"c={typing_noise_c!r}",
        f"guard={guard_on}", f"steer={steer_on}",
        f"full={force_full_budget}",
    ))


def proposal_nonce(task_name: str, seed: int, round_index: int) -> str:
    """Cache nonce for one proposal draw (src.llm.complete).

    Keyed on (task, seed, round) and deliberately *not* on mode or the ablation
    flags. Two conditions whose prompt is byte-identical - every arm's round 1,
    where the history is still empty - then share one draw, which pairs the arms
    on common random numbers instead of buying the same completion three times.
    The moment the prompts diverge (evidence/exclusion blocks appear) the prompt
    itself separates the cache keys, so nothing is shared that shouldn't be.

    Deterministic, so re-running a cell replays from cache for free; distinct per
    round and per seed, so rounds meant to be independent draws are not served
    the same cached completion - which is what happens with no memory, whose
    prompt is the same every single round.
    """
    return f"{task_name}|seed{seed}|r{round_index}"


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

    cell = cell_signature(
        task_name, mode, seed=seed, granularity=granularity, max_examples=max_examples,
        typing_noise_c=typing_noise_c, guard_on=guard_on, steer_on=steer_on,
        budget=budget, force_full_budget=force_full_budget,
    )
    # Deterministic, not uuid4: a cell that died halfway and is re-run rewrites
    # its own rounds in data/episodes.jsonl (src.metrics.load_rounds collapses on
    # (episode_id, round_index), last write wins) instead of appending a second,
    # truncated episode that downstream means would silently average in.
    episode_id = hashlib.sha256(cell.encode()).hexdigest()[:12]
    # Seeded from the same identity: TypedMemory's mistyping coin (Def. 3.1's c)
    # is the E5 sweep's independent variable, so an unseeded Random() would make
    # that sweep unreproducible - and scripts/check_consistency.py fail on it.
    if rng is None:
        rng = random.Random("typing-noise|" + cell)
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
            granularity=granularity, force_full_budget=force_full_budget,
        )
        base.update(overrides)
        return RoundRecord(**base)

    for round_index in range(1, budget + 1):
        try:
            patch = propose(
                task_name, program.buggy_source, task.name,
                mode, memory.history, model=model, granularity=granularity,
                disable_exclusion=not steer_on,
                nonce=proposal_nonce(task_name, seed, round_index),
                spec_note=task.spec_note,
            )
        except TruncatedResponse as exc:
            # The harness failed, not the proposal. Log the round - it did spend
            # a model call - and move on without touching memory, so a reply the
            # response budget cut short can never enter the evidence block or an
            # eliminated bucket (paper SS VI-D-a).
            append_round(_record(
                round_index=round_index, patch="", accept=False,
                counterexample_args=None, reason=f"proposal unusable: {exc}",
                examples_tried=0, coarse_type=None, fine_type=None,
                proposal_error="truncated_response",
            ), **kwargs)
            continue

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

        if result.oracle_error is not None:
            # The search failed, not the patch (src.oracle): no counterexample to
            # store, no type to steer with. Log it - the round did spend a
            # proposal - and move on without touching memory, so an inconclusive
            # round can never enter the evidence block or an eliminated bucket.
            append_round(_record(
                round_index=round_index, patch=patch, accept=False,
                counterexample_args=None, reason=result.reason,
                examples_tried=result.examples_tried,
                coarse_type=None, fine_type=None,
                guarded=False, guard_evaluations=guard_evaluations,
                oracle_error=result.oracle_error,
            ), **kwargs)
            continue

        attempt = Attempt.from_result(task_name, program.buggy_source, patch, result)

        # Store first, then log: TypedMemory may file the attempt under a
        # different location than its true one (Def. 3.1's coherence c), and the
        # record has to carry both. coarse_type/fine_type stay the *true* types;
        # stored_type is what memory believes, and the gap between them is the
        # mistyping that scripts/measure_anchoring.py looks for.
        stored = memory.store(attempt)
        stored_ft = stored.failure_type(granularity)

        append_round(_record(
            round_index=round_index, patch=patch, accept=result.accept,
            counterexample_args=result.args, reason=result.reason,
            examples_tried=result.examples_tried,
            coarse_type=attempt.coarse_type.key if attempt.coarse_type else None,
            fine_type=attempt.fine_type.key if attempt.fine_type else None,
            stored_type=stored_ft.key if stored_ft else None,
            guarded=False, guard_evaluations=guard_evaluations,
        ), **kwargs)

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

    from src.adapter import SUPPORTED_PROGRAMS

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")
    task_name = sys.argv[1] if len(sys.argv) > 1 else SUPPORTED_PROGRAMS[0]
    mode = sys.argv[2] if len(sys.argv) > 2 else "typed"
    result = run_episode(task_name, mode, budget=5)
    status = f"repaired in {result.rounds} rounds" if result.accepted_patch else "budget exhausted"
    print(f"{task_name} [{mode}]: {status} (guard evaluations: {result.guard_evaluations})")
