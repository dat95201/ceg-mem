"""A5 - failure-type function theta(p).

(edit location) x (violated property), at two granularities:
  coarse: whole program x exception class
  fine:   exact line    x exception class + divergence shape

A patch's edit location is found by diffing it against the *faulty* source it
started from (never the reference - see src/adapter.py's module docstring).
When the diff cannot be pinned to a small, contiguous region - a wholesale
rewrite rather than a targeted edit - the fine-grained location falls back to
the "wholesale" tag instead of a fabricated line number; coarse is unaffected
since it never carries a location finer than "whole_program".

The property half reads a ConDefects program's *stdout* rather than a return
value (src/oracle.py), and a timeout means something weaker here than it did
on a property-based benchmark: a contest solution can exceed the wall clock by
being correct but too slow, not only by looping forever. "Timeout" is still
one property class, but it is not evidence of a wrong answer.
"""
from __future__ import annotations

import dataclasses
import difflib
from typing import Any

from src.oracle import OracleResult, normalize_output
from src.sandbox import Outcome

GRANULARITIES = ("coarse", "fine")
WHOLESALE = "wholesale"
WHOLE_PROGRAM = "whole_program"   # the only location a coarse type ever carries

# A fine-grained location degrades to WHOLESALE once the edit stops looking
# like a targeted fix: too much of the function changed, or the change is
# scattered across too many separate regions to name one location.
_MAX_HUNK_FRACTION = 0.4
_MAX_HUNKS = 3


@dataclasses.dataclass(frozen=True)
class FailureType:
    """tau: an (edit location) x (violated property) signature."""

    granularity: str   # "coarse" | "fine"
    task: str
    location: str      # "whole_program" (coarse) | "L{a}"/"L{a}-L{b}" | "wholesale" (fine)
    property: str       # exception class, "Timeout", "WrongValue", or "wrong_value:<shape>"

    @property
    def key(self) -> str:
        """Canonical string identity, safe to use as a dict/set key."""
        return f"{self.granularity}::{self.task}::{self.location}::{self.property}"


def _changed_hunks(buggy_source: str, candidate_source: str) -> list[tuple[int, int]]:
    """1-indexed, inclusive [start, end] line ranges in candidate_source that differ from buggy_source."""
    buggy_lines = buggy_source.splitlines()
    cand_lines = candidate_source.splitlines()
    matcher = difflib.SequenceMatcher(a=buggy_lines, b=cand_lines, autojunk=False)
    return [
        (j1 + 1, j2)
        for tag, _i1, _i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal" and j2 > j1
    ]


def edit_location(buggy_source: str, candidate_source: str) -> str:
    """Where a candidate edits the faulty source - the location half of theta.

    Execution-free (pure diff), so a guard can call this on a fresh candidate
    to guess its type *before* paying for a sandbox run (src.memory.TypedMemory).
    """
    hunks = _changed_hunks(buggy_source, candidate_source)
    if not hunks:
        return WHOLESALE  # no diff at all; shouldn't happen for a refuted patch, but don't fabricate a line
    changed = sum(b - a + 1 for a, b in hunks)
    total = max(len(candidate_source.splitlines()), 1)
    if len(hunks) > _MAX_HUNKS or changed / total > _MAX_HUNK_FRACTION:
        return WHOLESALE
    start, end = (hunks[0][0], hunks[-1][1]) if len(hunks) > 1 else hunks[0]
    return f"L{start}" if start == end else f"L{start}-L{end}"


_edit_location = edit_location  # internal alias, kept so the rest of this module reads unchanged


def _as_number(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _divergence_shape(candidate: Any, reference: Any) -> str:
    """A coarse guess at *how* the wrong output diverges from the expected one.

    Both sides are program stdout (src/oracle.py), so the comparison is over
    whitespace-separated tokens after judge normalisation - contest output is
    almost always a number, a word, or a sequence of them, and the token view
    is what makes "printed one line too many" and "off by one" distinguishable
    classes rather than one undifferentiated "wrong output".
    """
    cand_tokens = " ".join(normalize_output(candidate)).split()
    ref_tokens = " ".join(normalize_output(reference)).split()

    if not cand_tokens:
        return "no_output"
    if len(cand_tokens) != len(ref_tokens):
        return "extra_elements" if len(cand_tokens) > len(ref_tokens) else "missing_elements"
    if sorted(cand_tokens) != sorted(ref_tokens):
        differing = [(c, r) for c, r in zip(cand_tokens, ref_tokens) if c != r]
        if len(differing) == 1:
            got, want = (_as_number(t) for t in differing[0])
            if got is not None and want is not None:
                diff = got - want
                if 0 < diff <= 3:
                    return "off_by_small_high"
                if -3 <= diff < 0:
                    return "off_by_small_low"
            return "single_token_mismatch"
        return "value_mismatch"
    # Same multiset of tokens in a different order - or the same tokens laid
    # out over different lines, which normalize_output already flattened away.
    return "reordered" if cand_tokens != ref_tokens else "value_mismatch"


def _property(outcome: Outcome, reference: Outcome | None, *, granularity: str) -> str:
    if outcome.timed_out:
        return "Timeout"
    if not outcome.ok:
        return outcome.error_type or "UnknownError"
    if granularity == "coarse":
        return "WrongValue"
    if reference is not None and reference.ok:
        return f"wrong_value:{_divergence_shape(outcome.value, reference.value)}"
    return "wrong_value:unknown"


def theta(
    task_name: str,
    buggy_source: str,
    candidate_source: str,
    result: OracleResult,
    *,
    granularity: str = "coarse",
) -> FailureType | None:
    """tau(p): the failure type of a refuted candidate; None (top) if result.accept."""
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}, got {granularity!r}")
    if result.accept:
        return None
    if result.candidate is None:
        # Inconclusive round (src.oracle.OracleResult.oracle_error): the search
        # failed, so no failure of the *patch* was observed and there is nothing
        # to type. Same top element as accept - a memory must not file this.
        return None

    location = WHOLE_PROGRAM if granularity == "coarse" else _edit_location(buggy_source, candidate_source)
    prop = _property(result.candidate, result.reference, granularity=granularity)
    return FailureType(granularity=granularity, task=task_name, location=location, property=prop)


def theta_both(
    task_name: str,
    buggy_source: str,
    candidate_source: str,
    result: OracleResult,
) -> tuple[FailureType | None, FailureType | None]:
    """(coarse, fine) failure types in one call - both None if result.accept."""
    return (
        theta(task_name, buggy_source, candidate_source, result, granularity="coarse"),
        theta(task_name, buggy_source, candidate_source, result, granularity="fine"),
    )


if __name__ == "__main__":
    from src.adapter import SUPPORTED_PROGRAMS, TASKS, load
    from src.oracle import differential_test

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")
    # Type the real fault: run the shipped faulty version against its own test
    # pool and read off theta(p) for the counterexample that comes back.
    for name in SUPPORTED_PROGRAMS[:5]:
        task = TASKS[name]
        program = load(name)
        result = differential_test(task, program.buggy_source, program.correct_source, max_examples=30)
        coarse, fine = theta_both(name, program.correct_source, program.buggy_source, result)
        status = "accept (pool did not expose it)" if result.accept else f"tau={fine.key if fine else None}"
        print(f"{name:28s} coarse={(coarse.key if coarse else 'None'):48s} {status}")
