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
import time
import random

from src.adapter import TASKS, load
from src.memory import build_memory
from src.metrics import RoundRecord, append_round
from src.oracle import differential_test
from src.llm import ContextOverflow
from src.proposer import Attempt, TruncatedResponse, propose

MODES = ("no_memory", "untyped", "typed", "transcript")


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
    transcript_window: int = 0,
    audit_guarded: bool = False,
    reasoning_effort: str | None = None,
    typing_random: bool = False,
    model: str | None = None,
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
    parts = [
        task_name, mode, f"seed={seed}", f"gran={granularity}",
        f"max_examples={max_examples}", f"c={typing_noise_c!r}",
        f"guard={guard_on}", f"steer={steer_on}",
        f"full={force_full_budget}",
    ]
    # Appended only when set, so an ordinary cell keeps the signature - and
    # therefore the episode id - it had before these three knobs existed. The
    # same reason src.llm.cache_key appends reasoning_effort conditionally:
    # adding an always-present field would rename every cell at once.
    if transcript_window:
        parts.append(f"tw={transcript_window}")
    if audit_guarded:
        parts.append("audit=True")
    if reasoning_effort:
        parts.append(f"effort={reasoning_effort}")
    if typing_random:
        parts.append("typing=random")
    # model belongs here for the reason every other knob does, and its absence
    # was a real hazard: episode_id was model-independent, so the same
    # task/mode/seed run under two proposers collided in
    # src.metrics.load_rounds, which collapses on (episode_id, round_index)
    # last-write-wins - one model's rounds silently overwrote the other's.
    # scripts/freeze_results.py and scripts/consolidate_evals.py both hard-stop
    # on a mixed-model log, so it was contained rather than fatal, but the
    # containment was two files away from the collision.
    if model:
        parts.append(f"model={model}")
    return "|".join(parts)


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
    typing_random: bool = False,
    force_full_budget: bool = False,
    transcript_window: int = 0,
    audit_guarded: bool = False,
    reasoning_effort: str | None = None,
    rng: random.Random | None = None,
) -> EpisodeResult:
    """propose -> guard -> oracle -> record, repeated up to `budget` rounds.

    Every round is one model call (one unit of the proposal budget); the
    oracle call is the expensive one guard exists to avoid. Writes one
    RoundRecord per round via src.metrics regardless of how the episode ends.

    audit_guarded: run the oracle on guarded rounds too, purely to record what
    the candidate's failure type *was*. The verdict does not reach memory and
    does not change the loop - it only fills in coarse_type/fine_type on a row
    that would otherwise carry none. Without it, guarded rounds are censored
    from every type-based redundancy count, so an arm that guards often looks
    less redundant than one that guards never, for procedural reasons rather
    than real ones. It costs sandbox time and no model calls, and it defeats
    the very saving E2 measures, so run it as its own cell on a subset - never
    over the reported grid.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    task = TASKS[task_name]
    program = load(task_name)

    cell = cell_signature(
        task_name, mode, seed=seed, granularity=granularity, max_examples=max_examples,
        typing_noise_c=typing_noise_c, guard_on=guard_on, steer_on=steer_on,
        budget=budget, force_full_budget=force_full_budget,
        transcript_window=transcript_window, audit_guarded=audit_guarded,
        reasoning_effort=reasoning_effort, typing_random=typing_random, model=model,
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
    memory = build_memory(mode, granularity=granularity, typing_noise_c=typing_noise_c,
                          typing_random=typing_random, rng=rng)

    accepted_patch: str | None = None
    first_accept_round: int | None = None
    total_guard_evaluations = 0
    kwargs = {} if metrics_path is None else {"path": metrics_path}

    def _record(**overrides) -> RoundRecord:
        base = dict(
            episode_id=episode_id, task=task_name, mode=mode,
            model=model, seed=seed, guard_on=guard_on, steer_on=steer_on,
            max_examples=max_examples, typing_noise_c=typing_noise_c,
            typing_random=typing_random,
            granularity=granularity, force_full_budget=force_full_budget,
            transcript_window=transcript_window, audit_guarded=audit_guarded,
            reasoning_effort=reasoning_effort,
        )
        base.update(overrides)
        return RoundRecord(**base)

    def _cost(meta: dict) -> dict:
        """The subset of a call's meta that belongs on a RoundRecord."""
        return {
            "cache_key": meta.get("cache_key"),
            "tokens_method": meta.get("tokens_method"),
            "prompt_tokens": meta.get("prompt_tokens"),
            "completion_tokens": meta.get("completion_tokens"),
            "reasoning_tokens": meta.get("reasoning_tokens"),
            "llm_sec": meta.get("llm_sec"),
        }

    for round_index in range(1, budget + 1):
        call_meta: dict = {}
        try:
            patch = propose(
                task_name, program.buggy_source, task.name,
                mode, memory.history, model=model, granularity=granularity,
                disable_steering=not steer_on,
                nonce=proposal_nonce(task_name, seed, round_index),
                spec_note=task.spec_note,
                transcript_window=transcript_window,
                reasoning_effort=reasoning_effort,
                meta=call_meta,
            )
        except (TruncatedResponse, ContextOverflow) as exc:
            # The harness failed, not the proposal. Log the round - it did spend
            # a model call - and move on without touching memory, so a reply the
            # response budget cut short can never enter the evidence block or an
            # eliminated bucket (paper SS VI-D-a).
            #
            # ContextOverflow gets the same treatment for a different reason.
            # src.llm raises it rather than let the server silently crop the
            # prompt, and the arms that reach it are the memory arms deep into a
            # never-accepting episode, where twenty rounds of evidence have
            # accumulated. Uncaught it would abort the whole shard mid-grid -
            # and because the prompt is deterministic, every re-run would abort
            # at the identical round, so the shard could never get past that
            # task. Recorded as a spent round with no refutation instead, which
            # is what it is; the count is a threat-to-validity number, not a
            # crash. RUNBOOK.md.
            kind = ("context_overflow" if isinstance(exc, ContextOverflow)
                    else "truncated_response")
            append_round(_record(
                round_index=round_index, patch="", accept=False,
                counterexample_args=None, reason=f"proposal unusable: {exc}",
                examples_tried=0, coarse_type=None, fine_type=None,
                proposal_error=kind, **_cost(call_meta),
            ), **kwargs)
            continue

        guarded, guard_evaluations, blocked_by = False, 0, None
        guard_sec = 0.0
        if guard_on:
            t_guard = time.perf_counter()
            guard_result = memory.guard(patch, program.buggy_source)
            guard_sec = round(time.perf_counter() - t_guard, 3)
            guarded, guard_evaluations = guard_result.blocked, guard_result.evaluations
            blocked_by = guard_result.blocked_by
            total_guard_evaluations += guard_evaluations

        if guarded:
            # The type of the counterexample that fired - the arm-neutral label
            # for "this round repeated something memory already had". Available
            # in every arm, because Attempt carries theta_both's verdict no
            # matter which memory stored it.
            blocked_ft = blocked_by.failure_type(granularity) if blocked_by else None
            # ... and the label theta gave it, which differs only where memory
            # mistyped. mistyped_from is empty unless TypedMemory re-filed it.
            blocked_true = blocked_ft
            if blocked_by is not None and blocked_by.mistyped_from:
                true_coarse, true_fine = blocked_by.mistyped_from
                blocked_true = true_coarse if granularity == "coarse" else true_fine
            # --audit-guarded: pay the oracle anyway, only to fill in what this
            # candidate's own type was. Deliberately not stored and deliberately
            # not allowed to end the episode - an accept here would mean the
            # guard blocked a correct patch, which src.memory._still_refutes
            # cannot do, so it is logged as the soundness violation it would be
            # rather than acted on.
            audit_coarse = audit_fine = None
            audit_sec = 0.0
            audit_examples = 0
            reason = "guarded: candidate reproduces a stored counterexample"
            if audit_guarded:
                t_audit = time.perf_counter()
                audited = differential_test(
                    task, patch, program.correct_source,
                    max_examples=max_examples, seed=seed + round_index,
                )
                audit_sec = round(time.perf_counter() - t_audit, 3)
                audit_examples = audited.examples_tried
                audit_attempt = Attempt.from_result(
                    task_name, program.buggy_source, patch, audited)
                audit_coarse = audit_attempt.coarse_type.key if audit_attempt.coarse_type else None
                audit_fine = audit_attempt.fine_type.key if audit_attempt.fine_type else None
                if audited.accept:
                    # A guard blocking a candidate the oracle accepts is
                    # impossible by construction - _still_refutes blocks only on
                    # a counterexample that provably still fails - so this is a
                    # guard-soundness bug, not a result. It goes in the ROW, not
                    # only on stdout: a warning in a multi-day shard log is a
                    # warning nobody reads, and the row is what the analysis
                    # sees. accept stays False so the episode does not terminate
                    # on a verdict the loop was never allowed to act on.
                    reason = ("GUARD SOUNDNESS VIOLATION: guarded a candidate the "
                              "oracle accepts - see src.memory._still_refutes")
                    print(f"    {reason} ({task_name} seed={seed} r{round_index})", flush=True)
            append_round(_record(
                round_index=round_index, patch=patch, accept=False,
                counterexample_args=None,
                reason=reason,
                examples_tried=audit_examples,
                coarse_type=audit_coarse, fine_type=audit_fine,
                guarded=True, guard_evaluations=guard_evaluations,
                blocked_by_type=blocked_ft.key if blocked_ft else None,
                blocked_by_type_true=blocked_true.key if blocked_true else None,
                guard_sec=guard_sec, oracle_sec=audit_sec or None,
                **_cost(call_meta),
            ), **kwargs)
            continue  # oracle call avoided; still consumes one round of budget

        t_oracle = time.perf_counter()
        result = differential_test(
            task, patch, program.correct_source,
            max_examples=max_examples, seed=seed + round_index,
        )
        oracle_sec = round(time.perf_counter() - t_oracle, 3)

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
                guard_sec=guard_sec, oracle_sec=oracle_sec, **_cost(call_meta),
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
            guard_sec=guard_sec, oracle_sec=oracle_sec, **_cost(call_meta),
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
