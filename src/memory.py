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
from src.oracle import outputs_equal
from src.proposer import Attempt
from src.sandbox import run_program
from src.typer import WHOLE_PROGRAM, edit_location

MODES = ("no_memory", "untyped", "typed")


@dataclasses.dataclass(frozen=True)
class GuardResult:
    """Outcome of one guard check."""

    blocked: bool
    evaluations: int  # stored counterexamples actually re-run - the Prop. 4.5 cost


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

    def store(self, attempt: Attempt) -> Attempt:
        if attempt.result.accept:
            return super().store(attempt)
        ft = self._true_type(attempt)
        if ft is None:
            return super().store(attempt)
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
        return attempt

    def eliminated_locations(self) -> set[str]:
        """Snapshot of eliminated buckets E, as stored (post-noise) locations."""
        return set(self._by_location)

    def guard(self, candidate_source: str, buggy_source: str) -> GuardResult:
        # Mirror src.typer.theta's own branching exactly: coarse locations are
        # always the constant WHOLE_PROGRAM, never a line range, so the bucket
        # guess must match that or a coarse-granularity guard would look up
        # keys that store() never uses and silently never fire.
        guess = WHOLE_PROGRAM if self.granularity == "coarse" else edit_location(buggy_source, candidate_source)
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
