"""A4 - counterexample oracle over the shipped contest test pool.

Run the candidate on test inputs and compare its stdout against the output
AtCoder accepted; the first case where they disagree is the counterexample.
Accept if none is found within the budget.

Two things follow from ConDefects shipping expected outputs alongside inputs
(src/adapter.py) rather than only a reference implementation:

  * a round costs *one* sandbox run per case, not two - the reference's output
    is already on disk. `reference_source` is still taken, and still never
    goes near a model prompt, but it is only executed for the rare case whose
    `out/` file is missing.

  * the counterexample is a test case *name*, not an input. That is what goes
    into OracleResult.args and therefore into data/episodes.jsonl and the
    proposer's evidence block; the input text is looked back up from the task
    when a guard replays it (src/memory.py). Contest inputs run to hundreds of
    kilobytes, so storing them inline would bloat the log and blow the prompt.

`max_examples` keeps its name and its role from the property-based version -
the oracle's informativeness knob swept in E4 - but now means "how many of the
shipped cases this round is allowed to run" rather than "how many inputs to
draw". It is a *sample*, seeded per round, not a prefix: a prefix would make
every round of an episode re-run the same first N cases and see the same
counterexample forever.
"""
from __future__ import annotations

import dataclasses
import random

from src.adapter import ConDefectsProgram, Task, TestCase
from src.sandbox import DEFAULT_TIMEOUT, Outcome, run_program

DEFAULT_MAX_EXAMPLES = 100


def normalize_output(text: str | None) -> list[str]:
    """Judge-normalised form of a program's stdout.

    Trailing whitespace on a line and trailing blank lines at the end are not
    differences a contest judge counts, and neither should the oracle: a patch
    that prints an extra newline is not a distinct failure class, it is noise
    that would otherwise get its own failure type and its own memory bucket.
    Everything else is compared exactly, matching ConDefects' own comparison
    (Tool/RunTest.py: `compare_res`).
    """
    if text is None:
        return []
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def outputs_equal(candidate: str | None, expected: str | None) -> bool:
    return normalize_output(candidate) == normalize_output(expected)


@dataclasses.dataclass(frozen=True)
class OracleResult:
    accept: bool
    args: list | None = None        # [test case name] - replayable via Task.case()
    candidate: Outcome | None = None
    reference: Outcome | None = None
    reason: str | None = None       # human-readable divergence description
    examples_tried: int = 0
    # Set when the search itself failed rather than the patch: the round is
    # inconclusive, not a refutation. accept is False (never claim correctness
    # we did not establish) but there is no counterexample, so candidate/args
    # stay None and src.typer.theta assigns no failure type.
    oracle_error: str | None = None


def _expected_outcome(
    case: TestCase,
    reference_source: str,
    *,
    timeout: float,
) -> Outcome:
    """The reference's answer for one case: the shipped out/ file if there is
    one, otherwise the reference implementation actually run."""
    if case.expected_output is not None:
        return Outcome(ok=True, value=case.expected_output)
    return run_program(reference_source, case.input_text, timeout=timeout)


def _sample(cases: tuple[TestCase, ...], max_examples: int, seed: int) -> list[TestCase]:
    if max_examples >= len(cases):
        return list(cases)
    return random.Random(seed).sample(list(cases), max_examples)


def differential_test(
    task: Task,
    candidate_source: str,
    reference_source: str,
    *,
    max_examples: int = DEFAULT_MAX_EXAMPLES,
    seed: int = 0,
    timeout: float = DEFAULT_TIMEOUT,
) -> OracleResult:
    """Run `candidate_source` on up to `max_examples` of the task's test cases.

    Returns the first case where the candidate's stdout differs from the
    expected output (or where it crashes or times out), or accept=True if none
    of the sampled cases separates them. A case whose reference answer cannot
    itself be established is skipped rather than counted against the candidate.
    """
    cases = task.test_cases
    if not cases:
        return OracleResult(
            accept=False,
            reason=f"no test data for {task.task_id} (is Test.zip unpacked?)",
            oracle_error="src.oracle.NoTestCases",
        )

    tried = 0
    for case in _sample(cases, max_examples, seed):
        ref = _expected_outcome(case, reference_source, timeout=timeout)
        if not ref.ok:
            continue  # the reference cannot answer this case; it proves nothing
        tried += 1

        cand = run_program(candidate_source, case.input_text, timeout=timeout)
        if cand.timed_out:
            reason = f"timed out after {timeout}s on test case {case.name}"
        elif not cand.ok:
            reason = f"raised {cand.error_type} on test case {case.name}: {cand.error_message}"
        elif not outputs_equal(cand.value, ref.value):
            reason = f"wrong output on test case {case.name}"
        else:
            continue

        return OracleResult(
            accept=False, args=[case.name], candidate=cand, reference=ref,
            reason=reason, examples_tried=tried,
        )

    if tried == 0:
        return OracleResult(
            accept=False,
            reason=f"no usable test case for {task.name}: the reference failed every one",
            oracle_error="src.oracle.NoUsableTestCase",
            examples_tried=0,
        )
    return OracleResult(accept=True, examples_tried=tried)


def is_truly_correct(
    task: Task,
    program: ConDefectsProgram,
    candidate_source: str,
    *,
    big_n: int | None = None,
    seed: int = 1_000_000_007,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Stronger correctness check for a patch the (sampling) oracle accepted -
    used only for the E2 overfitting audit, never as the repair loop's
    acceptance criterion (that stays differential_test with max_examples=100,
    the oracle Algorithm 1 actually calls).

    This is the plausible-vs-correct distinction the APR literature reports on
    real-fault benchmarks: the loop accepts on a *sample* of the contest's
    tests, so a patch can be accepted while still failing a case the sample
    never drew. Here the candidate is run against the **entire** shipped pool
    - the same suite AtCoder judged the original submission with.

    `big_n` caps the pool for a cheap approximation; None (the default) means
    all of it. An inconclusive run fails the check: this is the overfitting
    audit, so "could not establish" is treated as "not clean".
    """
    result = differential_test(
        task, candidate_source, program.correct_source,
        max_examples=big_n if big_n is not None else len(task.test_cases),
        seed=seed, timeout=timeout,
    )
    return result.accept


if __name__ == "__main__":
    from src.adapter import SUPPORTED_PROGRAMS, TASKS, load

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")
    for name in SUPPORTED_PROGRAMS[:4]:
        task = TASKS[name]
        program = load(name)
        result = differential_test(task, program.buggy_source, program.correct_source, max_examples=30)
        status = "accept (pool did not expose the fault)" if result.accept else f"REJECT: {result.reason}"
        print(f"{name}: {status} ({result.examples_tried} cases)")
