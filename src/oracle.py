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


# ── #22 regression rate: the F2P / P2P split ────────────────────────────────
# ConDefects ships one stdin->stdout program per fault, so there is no
# pass-to-pass suite to borrow the way a repo-level benchmark has one. The
# shipped pool supplies the same partition anyway, and supplies it *measured*
# rather than declared: run the FAULTY version over the pool once and the cases
# separate into
#
#   F2P  the cases it fails  - the fault's observable footprint, and the only
#        thing the repair loop ever looks at. differential_test needs just one
#        of these to refute, so it stops at the first.
#   P2P  the cases it passes - behaviour that already worked and that a repair
#        is not licensed to break. NOTHING in the loop reads these, which is
#        precisely why a patch can trade them away and still be accepted.
#
# That asymmetry is the finding this measures. `accept` is a single bit over a
# sample of F2P; a patch that repairs the fault and breaks three P2P cases is
# indistinguishable from a clean repair until someone runs the other half.
#
# The split is a property of the faulty program, not of any patch, so it is
# computed once and memoised: every accept, in every mode and every seed, is
# graded against the same denominator. A per-patch split would quietly grade
# two patches on two different partitions.


@dataclasses.dataclass(frozen=True)
class PoolSplit:
    """How the faulty version divides the shipped pool. Case names, not inputs."""

    f2p: tuple[str, ...]
    p2p: tuple[str, ...]
    unusable: tuple[str, ...]    # the reference could not answer these
    cases: tuple[str, ...]       # the cases actually run, in run order
    cap: int | None              # requested cap; None = the whole pool
    seed: int

    @property
    def n_scoreable(self) -> int:
        return len(self.f2p) + len(self.p2p)


@dataclasses.dataclass(frozen=True)
class RegressionResult:
    """One patch, scored on both halves. No short-circuit anywhere."""

    f2p_total: int
    f2p_fixed: int
    p2p_total: int
    p2p_broken: int
    broken_cases: tuple[str, ...]        # P2P cases this patch newly fails
    still_failing_cases: tuple[str, ...]  # F2P cases it did not repair
    truly_correct: bool                  # failed no scoreable case
    inconclusive: bool                   # nothing was scoreable at all
    cap: int | None
    seed: int

    @property
    def regression_rate(self) -> float | None:
        """Share of already-working cases this patch broke. None if no P2P case
        exists - a fault whose footprint is the whole pool cannot regress."""
        return (self.p2p_broken / self.p2p_total) if self.p2p_total else None

    @property
    def fix_rate(self) -> float | None:
        return (self.f2p_fixed / self.f2p_total) if self.f2p_total else None

    def as_dict(self) -> dict:
        """Flat, JSON-safe, and prefixed - these ride along in the audit log
        beside the overfit verdict, so the names have to stay unambiguous."""
        return {
            "f2p_total": self.f2p_total,
            "f2p_fixed": self.f2p_fixed,
            "fix_rate": self.fix_rate,
            "p2p_total": self.p2p_total,
            "p2p_broken": self.p2p_broken,
            "regression_rate": self.regression_rate,
            "regression_broken_cases": list(self.broken_cases),
            "regression_still_failing": list(self.still_failing_cases),
            "regression_inconclusive": self.inconclusive,
            # Reported, never implied. A capped audit is a different measurement
            # from an uncapped one and the number alone does not say which.
            "regression_cap": self.cap,
            "regression_seed": self.seed,
        }


# Keyed on everything that can change a verdict. `timeout` is in the key because
# a timeout IS a failure here, exactly as it is in differential_test - the same
# case can land in F2P at 10s and P2P at 30s, and two runs that disagree about
# that must not share a cached answer.
_SPLIT_CACHE: dict[tuple, PoolSplit] = {}

REGRESSION_SEED = 22_000_022


def _passes(case: TestCase, source: str, expected: Outcome, *, timeout: float) -> bool:
    """One case, one verdict, by differential_test's rules exactly: a timeout
    and a crash are failures, and the comparison is judge-normalised."""
    got = run_program(source, case.input_text, timeout=timeout)
    if got.timed_out or not got.ok:
        return False
    return outputs_equal(got.value, expected.value)


def split_pool(
    task: Task,
    program: ConDefectsProgram,
    *,
    cap: int | None = None,
    seed: int = REGRESSION_SEED,
    timeout: float = DEFAULT_TIMEOUT,
) -> PoolSplit:
    """Partition the shipped pool by what the faulty version already does.

    `cap` samples the pool instead of walking all of it, seeded, and the chosen
    subset is recorded on the result so a patch is scored on *those* cases and
    no others - a split taken over one subset and a patch scored over another
    would produce a regression rate over a denominator that was never measured.
    """
    key = (task.name, cap, seed, timeout)
    cached = _SPLIT_CACHE.get(key)
    if cached is not None:
        return cached

    chosen = _sample(task.test_cases, len(task.test_cases) if cap is None else cap, seed)
    f2p: list[str] = []
    p2p: list[str] = []
    unusable: list[str] = []
    for case in chosen:
        ref = _expected_outcome(case, program.correct_source, timeout=timeout)
        if not ref.ok:
            unusable.append(case.name)   # proves nothing either way
            continue
        target = p2p if _passes(case, program.buggy_source, ref, timeout=timeout) else f2p
        target.append(case.name)

    split = PoolSplit(
        f2p=tuple(f2p), p2p=tuple(p2p), unusable=tuple(unusable),
        cases=tuple(c.name for c in chosen), cap=cap, seed=seed,
    )
    _SPLIT_CACHE[key] = split
    return split


def regression_report(
    task: Task,
    program: ConDefectsProgram,
    candidate_source: str,
    *,
    split: PoolSplit | None = None,
    cap: int | None = None,
    seed: int = REGRESSION_SEED,
    timeout: float = DEFAULT_TIMEOUT,
) -> RegressionResult:
    """Score one accepted patch on both halves of `split`.

    Every scoreable case runs. differential_test stops at the first
    counterexample because one is all a refutation needs; here the count is the
    point, so a patch that breaks three P2P cases has to come out as three
    rather than as "rejected".

    `truly_correct` on the result is the same criterion is_truly_correct
    applies - failed no scoreable case - so an UNCAPPED report subsumes that
    check and the caller need not pay for a second full pass. Under a cap it is
    the weaker statement "failed none of the sampled cases", which is why the
    cap travels with the number in as_dict().
    """
    if split is None:
        split = split_pool(task, program, cap=cap, seed=seed, timeout=timeout)

    by_name = {c.name: c for c in task.test_cases}
    scoreable = set(split.f2p) | set(split.p2p)
    if not scoreable:
        return RegressionResult(
            f2p_total=0, f2p_fixed=0, p2p_total=0, p2p_broken=0,
            broken_cases=(), still_failing_cases=(), truly_correct=False,
            inconclusive=True, cap=split.cap, seed=split.seed,
        )

    failed: set[str] = set()
    for name in split.cases:
        if name not in scoreable:
            continue
        case = by_name[name]
        ref = _expected_outcome(case, program.correct_source, timeout=timeout)
        if not ref.ok:
            continue   # became unusable between the two passes; not the patch's fault
        if not _passes(case, candidate_source, ref, timeout=timeout):
            failed.add(name)

    broken = tuple(n for n in split.p2p if n in failed)
    still_failing = tuple(n for n in split.f2p if n in failed)
    return RegressionResult(
        f2p_total=len(split.f2p),
        f2p_fixed=len(split.f2p) - len(still_failing),
        p2p_total=len(split.p2p),
        p2p_broken=len(broken),
        broken_cases=broken,
        still_failing_cases=still_failing,
        truly_correct=not failed,
        inconclusive=False,
        cap=split.cap,
        seed=split.seed,
    )

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
