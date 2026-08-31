"""A6 - the four memory stores.

Common interface: `store(attempt)`, `guard(candidate_source, buggy_source)`,
and `.history` (the `list[Attempt]` src.proposer already consumes to build
evidence_block/exclusion_block - see that module's docstring for how steering
happens). This module owns the other half of Section 3.3: **store + guard**
(Eq. (2)). `UntypedMemory.guard` must genuinely re-run every stored
counterexample; that cost is the subject of Proposition 4.5. Do not optimise
it away - the whole point of comparing it against `TypedMemory.guard` is that
one is Theta(m) and the other is O(1) in expectation.

`TranscriptMemory` is the fourth store and it is a *baseline*, not one of the
paper's three arms. It guards exactly as UntypedMemory does; the only thing it
changes is that src.proposer shows its history to the model. See that module's
docstring for why the paper's `untyped` arm must not do that, and DESIGN.md
for why the transcript condition is nonetheless worth running - it is what
ChatRepair and every reflective agent actually do, and a reviewer who reads
`untyped` as a straw man is reading it correctly unless this arm is reported
next to it.
"""
from __future__ import annotations

import dataclasses
import random

from src.adapter import TASKS
from src.oracle import outputs_equal
from src.proposer import Attempt
from src.sandbox import run_program
from src.typer import WHOLE_PROGRAM, edit_location

# `transcript` (the ChatRepair/E6 arm) was REMOVED on 2026-08-29. It tested no
# surviving claim - Thm 4.2(i), 4.3(a), 4.3(b) and Prop 4.5 all live inside the
# no_memory/untyped/typed triangle - and the one claim it was built for, that a
# typed index is flat in the round index where a transcript grows linearly, is
# already falsified by the typed arm alone: typed grows 80.7 tokens/round against
# untyped's 3.5. Five orphan rounds from one pilot episode remain in
# data/episodes.jsonl and are dropped at load. See docs/DIAGNOSIS.md.
MODES = ("no_memory", "untyped", "typed")


@dataclasses.dataclass(frozen=True)
class GuardResult:
    """Outcome of one guard check."""

    blocked: bool
    evaluations: int  # stored counterexamples actually re-run - the Prop. 4.5 cost
    # The stored attempt whose counterexample fired. Carried out so src.loop can
    # log its failure type, which is what makes "redundant attempt" ONE
    # definition across arms instead of two (DESIGN.md SS6's first open item):
    # the typed guard blocks on a type-indexed bucket and the untyped guard on a
    # flat replay, but both block *because a known counterexample still
    # refutes*, and that counterexample has a type in either arm - Attempt
    # carries coarse_type/fine_type from theta_both regardless of the mode that
    # stored it. Without this the column counted type repeats for one arm and
    # guard firings for the other while Theorem 4.3(b) assigned both the same R.
    blocked_by: "Attempt | None" = None


def _case_key(attempt: Attempt) -> tuple[str, str] | None:
    """(task, counterexample case name) - the identity of what a guard check
    actually *runs*.

    Two stored attempts sharing this key return the same verdict against any
    candidate: `_still_refutes` re-runs the same shipped case against the same
    reference value, and that is deterministic. Deduplicating on it is what
    keeps the fallback scan below O(distinct counterexamples) instead of O(m).

    None when the attempt carries no runnable counterexample. Those are never
    deduplicated against each other - "cannot be checked" is not the same fact
    as "already checked", and collapsing them would silently drop a check.
    """
    ft = attempt.fine_type or attempt.coarse_type
    ref = attempt.result.reference
    if ft is None or ref is None or not ref.ok or not attempt.result.args:
        return None
    return (ft.task, attempt.result.args[0])


def _still_refutes(attempt: Attempt, candidate_source: str) -> bool:
    """Re-run attempt's stored counterexample against a *new* candidate.

    One sandboxed run of one test case, not a fresh sample of the whole pool -
    this is what keeps a guard check cheap relative to a full oracle round
    (Eq. (2)). The stored counterexample is a case *name* (src/oracle.py), so
    the input text is looked back up from the task here.
    """
    ft = attempt.fine_type or attempt.coarse_type
    if ft is None:
        return False  # accepted attempts are never stored as refutations
    task = TASKS[ft.task]
    ref = attempt.result.reference
    if ref is None or not ref.ok or not attempt.result.args:
        return False  # the stored counterexample was never a valid comparison point
    case = task.case(attempt.result.args[0])
    if case is None:
        return False  # test data went away under us; do not block on a guess
    cand = run_program(candidate_source, case.input_text)
    if cand.timed_out or not cand.ok:
        return True
    return not outputs_equal(cand.value, ref.value)


class Memory:
    """Base class: keeps history for the proposer; never guards."""

    mode = "no_memory"

    def __init__(self) -> None:
        self.history: list[Attempt] = []

    def store(self, attempt: Attempt) -> Attempt:
        """File `attempt` and return it *as filed*.

        The return value matters only for TypedMemory, which may re-file an
        attempt under a different location than its true one (Def. 3.1's
        coherence c). Returning it lets src.loop log what memory actually
        believes alongside what was actually true, which is the only way to
        measure the anchoring rate without re-deriving the typing-noise RNG
        outside the loop that owns it.
        """
        self.history.append(attempt)
        return attempt

    def guard(self, candidate_source: str, buggy_source: str) -> GuardResult:
        return GuardResult(blocked=False, evaluations=0)


class NoMemory(Memory):
    """Trivial baseline: no record of past attempts, no guard."""

    mode = "no_memory"


class UntypedMemory(Memory):
    """Flat counterexample log: guards by re-running *every* stored
    counterexample against the new candidate - an O(m) scan, m = live memory
    size. This is the faithful abstraction of transcript/verbal memory: it
    can guard but, having no notion of a class, cannot steer.
    """

    mode = "untyped"

    def guard(self, candidate_source: str, buggy_source: str) -> GuardResult:
        evaluations = 0
        for attempt in self.history:
            if attempt.result.accept:
                continue
            evaluations += 1
            if _still_refutes(attempt, candidate_source):
                return GuardResult(blocked=True, evaluations=evaluations, blocked_by=attempt)
        return GuardResult(blocked=False, evaluations=evaluations)


class TypedMemory(Memory):
    """Type-indexed log: guards only the type-matched bucket - O(1) in
    expectation (Proposition 4.5), because a candidate's edit location can be
    guessed from a pure diff against the buggy source (src.typer.edit_location)
    without ever running it.

    `typing_noise_c` models Def. 3.1's typing coherence c: with probability
    1-c, a stored attempt is filed under a *wrong* location bucket instead of
    its true one - used by the c-sweep (E5). c=1.0 (default) is ideal typing.

    `typing_random` is the c axis's missing null, and it is NOT c=0.0. Two
    things stop the sweep reaching random assignment: `store` only ever noises
    the *location* half of the type, and the first store of an episode has no
    other location to move to, so it can never mistype. c=0.0 is therefore a
    lower bound on the damage, not a null. With `typing_random` a stored
    attempt is filed under a location drawn uniformly from every location seen
    so far INCLUDING its own - so memory still partitions the evidence and
    still guards O(1), but the partition carries no information about failure
    type. Without this arm, "typing helps because the classes are right" is not
    separable from "any partition of the evidence helps".
    """

    mode = "typed"

    def __init__(
        self,
        *,
        granularity: str = "fine",
        typing_noise_c: float = 1.0,
        typing_random: bool = False,
        guard_fallback: bool = True,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__()
        self.granularity = granularity
        self.typing_noise_c = typing_noise_c
        self.typing_random = typing_random
        # False reproduces the pre-2026-08-31 guard exactly: bucket only, no
        # fallback. Kept reachable so the partition and the index can be run as
        # a paired ablation rather than argued about - see `guard` below.
        self.guard_fallback = guard_fallback
        self._rng = rng or random.Random()
        self._by_location: dict[str, list[Attempt]] = {}
        self._seen_locations: list[str] = []

    def _true_type(self, attempt: Attempt):
        return attempt.failure_type(self.granularity)

    def store(self, attempt: Attempt) -> Attempt:
        if attempt.result.accept:
            return super().store(attempt)
        ft = self._true_type(attempt)
        if ft is None:
            return super().store(attempt)
        location = ft.location
        if self.typing_random:
            # Uniform over every location seen so far, its own included: a
            # partition with no information, not a guaranteed wrong answer.
            pool = self._seen_locations or [location]
            if location not in pool:
                pool = pool + [location]
            location = self._rng.choice(pool)
        elif self.typing_noise_c < 1.0 and self._rng.random() >= self.typing_noise_c:
            # Mistype: file this counterexample under a different location than
            # its true one - the memory "correctly attributes" with prob. c.
            other = [loc for loc in self._seen_locations if loc != location]
            if other:
                location = self._rng.choice(other)
        if location not in self._seen_locations:
            self._seen_locations.append(location)

        if location != ft.location:
            # Both steer (proposer.py reads Attempt.failure_type from memory.history)
            # and guard (self._by_location below) must see the *same* mistyping - a
            # single shared attribution, per Def. 3.1 - so replace the attempt's own
            # type before it goes into history, not just the guard's bucket key.
            noised_ft = dataclasses.replace(ft, location=location)
            field = "coarse_type" if self.granularity == "coarse" else "fine_type"
            # mistyped_from keeps theta's own verdict. Carrying it is what lets
            # a per-type breakdown in the c-sweep be read against the other
            # arms at all: without it the typed arm reports what memory
            # believes and every other arm reports the truth, and the two are
            # silently plotted on one axis.
            attempt = dataclasses.replace(
                attempt, mistyped_from=(attempt.coarse_type, attempt.fine_type),
                **{field: noised_ft})

        super().store(attempt)
        self._by_location.setdefault(location, []).append(attempt)
        return attempt

    def eliminated_locations(self) -> set[str]:
        """Snapshot of eliminated buckets E, as stored (post-noise) locations."""
        return set(self._by_location)

    def guard(self, candidate_source: str, buggy_source: str) -> GuardResult:
        """Type-indexed bucket first, then the rest of memory.

        The index says where to look FIRST. It must not say where to STOP.

        Until 2026-08-31 this method searched the bucket and nothing else, which
        made it a *partition* rather than an index - and a partition is only
        sound as a stopping rule if theta's key is what decides refutation. It is
        not. The key is where the candidate EDITS (`edit_location`, a pure diff
        against the buggy source); what decides refutation is which INPUT the
        candidate gets wrong. Those are close to independent, and the frozen log
        prices the mistake exactly (docs/TYPED-VS-UNTYPED.md SS1): of 7,349 typed
        rounds that reached the oracle and were refuted, **5,060 - 68.9% - came
        back with a counterexample already sitting in memory**, every one of them
        a round the untyped flat scan would certainly have blocked. Same figure
        for untyped: 1 round in 1,004.

        Widening the search cannot cost soundness. `_still_refutes` genuinely
        re-runs the stored case, so a block is always a verified refutation and
        never a type-match guess; more searching can only find more TRUE
        refutations. That is why this does not wait on the cross-refutation rate
        `scripts/measure_coherence.py` has yet to produce.

        Proposition 4.5 survives in a form that is actually testable: the index
        reduces expected evaluations *at equal recall*. Phase 1 alone answers the
        rounds where theta guesses right (25.4% of them on the current log); the
        rest pay for a deduplicated scan they were paying an entire oracle call
        for before.
        """
        # Mirror src.typer.theta's own branching exactly: coarse locations are
        # always the constant WHOLE_PROGRAM, never a line range, so the bucket
        # guess must match that or a coarse-granularity guard would look up
        # keys that store() never uses and silently never fire.
        guess = WHOLE_PROGRAM if self.granularity == "coarse" else edit_location(buggy_source, candidate_source)
        bucket = self._by_location.get(guess, [])
        evaluations = 0
        seen_cases: set[tuple[str, str]] = set()

        # Phase 1 - the type-indexed bucket. Proposition 4.5's O(1) expected hit.
        for attempt in bucket:
            key = _case_key(attempt)
            if key is not None:
                if key in seen_cases:
                    continue
                seen_cases.add(key)
            evaluations += 1
            if _still_refutes(attempt, candidate_source):
                return GuardResult(blocked=True, evaluations=evaluations, blocked_by=attempt)

        if not self.guard_fallback:
            return GuardResult(blocked=False, evaluations=evaluations)

        # Phase 2 - everything else, one run per distinct counterexample. Skipped
        # by identity for what phase 1 already tried, so an attempt is never run
        # twice in one guard call and the Prop. 4.5 count stays honest.
        in_bucket = {id(a) for a in bucket}
        for attempt in self.history:
            if attempt.result.accept or id(attempt) in in_bucket:
                continue
            key = _case_key(attempt)
            if key is not None:
                if key in seen_cases:
                    continue
                seen_cases.add(key)
            evaluations += 1
            if _still_refutes(attempt, candidate_source):
                return GuardResult(blocked=True, evaluations=evaluations, blocked_by=attempt)

        return GuardResult(blocked=False, evaluations=evaluations)


def build_memory(
    mode: str,
    *,
    granularity: str = "fine",
    typing_noise_c: float = 1.0,
    typing_random: bool = False,
    guard_fallback: bool = True,
    rng: random.Random | None = None,
) -> Memory:
    if mode == "no_memory":
        return NoMemory()
    if mode == "untyped":
        return UntypedMemory()
    if mode == "typed":
        return TypedMemory(granularity=granularity, typing_noise_c=typing_noise_c,
                           typing_random=typing_random, guard_fallback=guard_fallback,
                           rng=rng)
    raise ValueError(f"mode must be one of {MODES}, got {mode!r}")


if __name__ == "__main__":
    from src.adapter import SUPPORTED_PROGRAMS, load
    from src.oracle import differential_test

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")
    # Store the real fault's own refutation, then check the guard blocks a
    # candidate that reproduces it - here, the faulty program re-proposed.
    name = SUPPORTED_PROGRAMS[0]
    task = TASKS[name]
    program = load(name)
    mem = build_memory("typed")
    result = differential_test(task, program.buggy_source, program.correct_source, max_examples=30)
    attempt = Attempt.from_result(name, program.buggy_source, program.buggy_source, result)
    print(f"{name}: accept={result.accept} reason={result.reason}")
    mem.store(attempt)
    guard = mem.guard(program.buggy_source, program.buggy_source)
    print(f"re-proposing the same patch: blocked={guard.blocked} evals={guard.evaluations}")
