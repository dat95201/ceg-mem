"""A4 - differential-testing counterexample oracle.

Generate inputs with Hypothesis, run the patch and the reference side by
side, return the first divergence as the counterexample. Accept if none is
found. The reference implementation is visible here only - never place it in
a model prompt (see src/adapter.py's module docstring and the README).
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any

from hypothesis import HealthCheck, given, reject, seed as hyp_seed, settings

from src.adapter import QuixBugsProgram, Task
from src.sandbox import DEFAULT_TIMEOUT, Outcome, run_call

DEFAULT_MAX_EXAMPLES = 100


def values_equal(a: Any, b: Any, *, rel_tol: float = 1e-6, abs_tol: float = 1e-9) -> bool:
    """Structural equality, tolerant of float rounding (e.g. sqrt, rpn_eval)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
        except TypeError:
            return a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_equal(x, y, rel_tol=rel_tol, abs_tol=abs_tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            values_equal(a[k], b[k], rel_tol=rel_tol, abs_tol=abs_tol) for k in a
        )
    return a == b


@dataclasses.dataclass(frozen=True)
class OracleResult:
    accept: bool
    args: list | None = None
    candidate: Outcome | None = None
    reference: Outcome | None = None
    reason: str | None = None       # human-readable divergence description
    examples_tried: int = 0


def _combined_source(task: Task, program_source: str) -> str:
    return program_source + task.harness


def differential_test(
    task: Task,
    candidate_source: str,
    reference_source: str,
    *,
    max_examples: int = DEFAULT_MAX_EXAMPLES,
    seed: int = 0,
    timeout: float = DEFAULT_TIMEOUT,
) -> OracleResult:
    """Run candidate and reference on Hypothesis-drawn inputs for `task`.

    Returns the first (shrunk) counterexample where they disagree, or
    accept=True if none turns up within `max_examples` valid draws. An input
    the reference itself fails on is out of the task's domain (a strategy
    that draws one too often is a strategy bug, not a counterexample) and is
    rejected rather than compared.
    """
    candidate_full = _combined_source(task, candidate_source)
    reference_full = _combined_source(task, reference_source)

    found: dict[str, Any] = {}
    tried = {"n": 0}

    @settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=list(HealthCheck),
        database=None,
    )
    @hyp_seed(seed)
    @given(task.strategy)
    def _run(args):
        tried["n"] += 1
        ref = run_call(reference_full, task.entry_point, args, timeout=timeout)
        if not ref.ok:
            reject()

        cand = run_call(candidate_full, task.entry_point, args, timeout=timeout)
        if cand.timed_out:
            reason = f"candidate timed out after {timeout}s (reference returned {ref.value!r})"
        elif not cand.ok:
            reason = f"candidate raised {cand.error_type}: {cand.error_message}"
        elif not values_equal(cand.value, ref.value):
            reason = f"expected {ref.value!r}, got {cand.value!r}"
        else:
            return

        found["args"] = args
        found["candidate"] = cand
        found["reference"] = ref
        found["reason"] = reason
        raise AssertionError(reason)

    try:
        _run()
    except AssertionError:
        pass

    if "args" in found:
        return OracleResult(
            accept=False,
            args=found["args"],
            candidate=found["candidate"],
            reference=found["reference"],
            reason=found["reason"],
            examples_tried=tried["n"],
        )
    return OracleResult(accept=True, examples_tried=tried["n"])


def is_truly_correct(
    task: Task,
    program: QuixBugsProgram,
    candidate_source: str,
    *,
    big_n: int = 2000,
    seed: int = 1_000_000_007,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Stronger correctness check for a patch the (sampling-based) oracle
    already accepted - used only for the E2 overfitting audit, never as the
    repair loop's acceptance criterion (that stays differential_test with
    max_examples=100, the oracle Algorithm 1 actually calls).

    Two independent checks, both must pass:
      1. Re-run differential_test with a much larger example budget and a
         different seed, to catch inputs the original narrower search missed.
      2. Every QuixBugs-shipped fixed testcase for this task (program.testcases()),
         checked exactly - an independent, human-curated check. 9 of the 40
         tasks (graph/linked-list) ship none, so step 1 alone covers those.
    """
    big = differential_test(
        task, candidate_source, program.correct_source,
        max_examples=big_n, seed=seed, timeout=timeout,
    )
    if not big.accept:
        return False

    full_source = candidate_source + task.harness
    for args, expected in program.testcases():
        outcome = run_call(full_source, task.entry_point, list(args), timeout=timeout)
        if not outcome.ok or not values_equal(outcome.value, expected):
            return False
    return True


if __name__ == "__main__":
    from src.adapter import TASKS, load

    for name in ("gcd", "mergesort", "detect_cycle", "topological_ordering"):
        task = TASKS[name]
        program = load(name)
        result = differential_test(task, program.buggy_source, program.correct_source, max_examples=50)
        status = "accept (no divergence found)" if result.accept else f"REJECT: {result.reason}"
        print(f"{name}: {status} ({result.examples_tried} examples)")
