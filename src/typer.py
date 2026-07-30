"""A5 - failure-type function theta(p).

(edit location) x (violated property), at two granularities:
  coarse: whole function x exception class
  fine:   exact line   x exception class + divergence shape

A patch's edit location is found by diffing it against the *buggy* source it
started from (never the reference - see src/adapter.py's module docstring).
When the diff cannot be pinned to a small, contiguous region - a wholesale
rewrite rather than a targeted edit - the fine-grained location falls back to
the "wholesale" tag instead of a fabricated line number; coarse is unaffected
since it never carries a location finer than "whole_function".
"""
from __future__ import annotations

import dataclasses
import difflib
from typing import Any

from src.oracle import OracleResult
from src.sandbox import Outcome

GRANULARITIES = ("coarse", "fine")
WHOLESALE = "wholesale"

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
    location: str      # "whole_function" (coarse) | "L{a}"/"L{a}-L{b}" | "wholesale" (fine)
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


def _edit_location(buggy_source: str, candidate_source: str) -> str:
    hunks = _changed_hunks(buggy_source, candidate_source)
    if not hunks:
        return WHOLESALE  # no diff at all; shouldn't happen for a refuted patch, but don't fabricate a line
    changed = sum(b - a + 1 for a, b in hunks)
    total = max(len(candidate_source.splitlines()), 1)
    if len(hunks) > _MAX_HUNKS or changed / total > _MAX_HUNK_FRACTION:
        return WHOLESALE
    start, end = (hunks[0][0], hunks[-1][1]) if len(hunks) > 1 else hunks[0]
    return f"L{start}" if start == end else f"L{start}-L{end}"


def _divergence_shape(candidate: Any, reference: Any) -> str:
    """A coarse guess at *how* the wrong value diverges from the reference."""
    if type(candidate) is not type(reference):
        return "type_mismatch"
    if isinstance(candidate, bool):
        return "value_mismatch"
    if isinstance(candidate, (int, float)):
        diff = candidate - reference
        if diff == 0:
            return "value_mismatch"  # equal despite failing values_equal(), e.g. NaN
        return "off_by_small_high" if 0 < diff <= 3 else "off_by_small_low" if -3 <= diff < 0 else "value_mismatch"
    if isinstance(candidate, (list, tuple)):
        if len(candidate) != len(reference):
            return "extra_elements" if len(candidate) > len(reference) else "missing_elements"
        if sorted(map(repr, candidate)) == sorted(map(repr, reference)):
            return "reordered"
        return "value_mismatch"
    return "value_mismatch"


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

    location = "whole_function" if granularity == "coarse" else _edit_location(buggy_source, candidate_source)
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
    from src.adapter import TASKS, load
    from src.oracle import differential_test
    from data.mutants import build_mutants

    for name in ("gcd", "bucketsort", "mergesort"):
        task = TASKS[name]
        program = load(name)
        for mutant in build_mutants(name, program.correct_source):
            result = differential_test(task, mutant.source, program.correct_source, max_examples=50)
            coarse, fine = theta_both(name, program.correct_source, mutant.source, result)
            coarse_key = coarse.key if coarse else "None"
            status = "accept" if result.accept else f"tau={fine.key if fine else None}"
            print(f"{name:12s} {mutant.fault_type:18s} coarse={coarse_key:40s} {status}")
