"""A6 - the three memory stores.

Common interface: `store(attempt)`, `guard(candidate_source, buggy_source)`,
and `.history` (the `list[Attempt]` src.proposer already consumes to build
evidence_block/exclusion_block - see that module's docstring for how steering
happens). This module owns the other half of Section 3.3: **store + guard**
(Eq. (2)). `UntypedMemory.guard` must genuinely re-run every stored
counterexample; that cost is the subject of Proposition 4.5. Do not optimise
it away - the whole point of comparing it against `TypedMemory.guard` is that
one is Theta(m) and the other is O(1) in expectation.
"""
from __future__ import annotations

import dataclasses
import random

from src.adapter import TASKS
from src.oracle import values_equal
from src.proposer import Attempt
from src.sandbox import run_call
from src.typer import edit_location

MODES = ("no_memory", "untyped", "typed")


@dataclasses.dataclass(frozen=True)
class GuardResult:
    """Outcome of one guard check."""

    blocked: bool
    evaluations: int  # stored counterexamples actually re-run - the Prop. 4.5 cost


def _still_refutes(attempt: Attempt, candidate_source: str) -> bool:
    """Re-run attempt's stored counterexample input against a *new* candidate.

    One sandboxed call, not a fresh Hypothesis search - this is what keeps a
    guard check cheap relative to a full oracle round (Eq. (2)).
    """
    ft = attempt.fine_type or attempt.coarse_type
    if ft is None:
        return False  # accepted attempts are never stored as refutations
    task = TASKS[ft.task]
    ref = attempt.result.reference
    if ref is None or not ref.ok:
        return False  # the stored counterexample was never a valid comparison point
    full_source = candidate_source + task.harness
    cand = run_call(full_source, task.entry_point, attempt.result.args)
    if cand.timed_out or not cand.ok:
        return True
    return not values_equal(cand.value, ref.value)


class Memory:
    """Base class: keeps history for the proposer; never guards."""

    mode = "no_memory"

    def __init__(self) -> None:
        self.history: list[Attempt] = []

    def store(self, attempt: Attempt) -> None:
        self.history.append(attempt)

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
                return GuardResult(blocked=True, evaluations=evaluations)
        return GuardResult(blocked=False, evaluations=evaluations)


class TypedMemory(Memory):
    """Type-indexed log: guards only the type-matched bucket - O(1) in
    expectation (Proposition 4.5), because a candidate's edit location can be
    guessed from a pure diff against the buggy source (src.typer.edit_location)
    without ever running it.

    `typing_noise_c` models Def. 3.1's typing coherence c: with probability
    1-c, a stored attempt is filed under a *wrong* location bucket instead of
    its true one - used by the c-sweep (E5). c=1.0 (default) is ideal typing.
    """

    mode = "typed"

    def __init__(
        self,
        *,
        granularity: str = "fine",
        typing_noise_c: float = 1.0,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__()
        self.granularity = granularity
        self.typing_noise_c = typing_noise_c
        self._rng = rng or random.Random()
        self._by_location: dict[str, list[Attempt]] = {}
        self._seen_locations: list[str] = []

    def _true_type(self, attempt: Attempt):
        return attempt.failure_type(self.granularity)

    def store(self, attempt: Attempt) -> None:
        if attempt.result.accept:
            super().store(attempt)
            return
        ft = self._true_type(attempt)
        if ft is None:
            super().store(attempt)
            return
        location = ft.location
        if self.typing_noise_c < 1.0 and self._rng.random() >= self.typing_noise_c:
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
            attempt = dataclasses.replace(attempt, **{field: noised_ft})

        super().store(attempt)
        self._by_location.setdefault(location, []).append(attempt)

    def eliminated_locations(self) -> set[str]:
        """Snapshot of eliminated buckets E, as stored (post-noise) locations."""
        return set(self._by_location)

    def guard(self, candidate_source: str, buggy_source: str) -> GuardResult:
        # Mirror src.typer.theta's own branching exactly: coarse locations are
        # always the constant "whole_function", never a line range, so the
        # bucket guess must match that or a coarse-granularity guard would
        # look up keys that store() never uses and silently never fire.
        guess = "whole_function" if self.granularity == "coarse" else edit_location(buggy_source, candidate_source)
        bucket = self._by_location.get(guess, [])
        evaluations = 0
        for attempt in bucket:
            evaluations += 1
            if _still_refutes(attempt, candidate_source):
                return GuardResult(blocked=True, evaluations=evaluations)
        return GuardResult(blocked=False, evaluations=evaluations)


def build_memory(
    mode: str,
    *,
    granularity: str = "fine",
    typing_noise_c: float = 1.0,
    rng: random.Random | None = None,
) -> Memory:
    if mode == "no_memory":
        return NoMemory()
    if mode == "untyped":
        return UntypedMemory()
    if mode == "typed":
        return TypedMemory(granularity=granularity, typing_noise_c=typing_noise_c, rng=rng)
    raise ValueError(f"mode must be one of {MODES}, got {mode!r}")


if __name__ == "__main__":
    from src.adapter import load
    from src.oracle import differential_test
    from data.mutants import build_mutants

    name = "gcd"
    task = TASKS[name]
    program = load(name)
    mem = build_memory("typed")
    for mutant in build_mutants(name, program.correct_source):
        result = differential_test(task, mutant.source, program.correct_source, max_examples=50)
        attempt = Attempt.from_result(name, program.correct_source, mutant.source, result)
        guard = mem.guard(mutant.source, program.correct_source)
        print(f"{mutant.fault_type:18s} accept={result.accept} guard_blocked={guard.blocked} evals={guard.evaluations}")
        mem.store(attempt)
