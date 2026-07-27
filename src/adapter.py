"""A3 - load a QuixBugs Python program and run it through the sandbox.

Five programs to start, chosen for having no extra graph/Node scaffolding
(see external/QuixBugs/tester.py's `graph_based` list, which we deliberately
avoid): bitcount, gcd, is_valid_parenthesization, kheapsort, mergesort.

Each ships a fixed JSON-lines file of shape `[args, expected]` at
external/QuixBugs/json_testcases/<name>.json (one JSON array per line, not one
big array - see external/QuixBugs/tester.py). We use those as fixed smoke-test
inputs here. Hypothesis-driven input generation and the differential oracle
against the reference implementation are out of scope for this module; that
lands in src/oracle.py.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any

from dotenv import load_dotenv

from src.sandbox import DEFAULT_TIMEOUT, Outcome, run_call

load_dotenv()

QUIXBUGS_ROOT = pathlib.Path(os.environ.get("QUIXBUGS_ROOT", "external/QuixBugs"))
BUGGY_DIR = QUIXBUGS_ROOT / "python_programs"
CORRECT_DIR = QUIXBUGS_ROOT / "correct_python_programs"
TESTCASE_DIR = QUIXBUGS_ROOT / "json_testcases"

# Graph-based programs (breadth_first_search, detect_cycle, ...) need a Node
# class threaded through their inputs; every name below instead takes plain
# ints/strs/lists, so a program's source text alone is enough to run it.
SUPPORTED_PROGRAMS = (
    "bitcount",
    "gcd",
    "is_valid_parenthesization",
    "kheapsort",
    "mergesort",
)


@dataclasses.dataclass(frozen=True)
class QuixBugsProgram:
    name: str
    buggy_source: str
    correct_source: str

    def testcases(self) -> list[tuple[list, Any]]:
        """Shipped `[args, expected]` rows, args always normalised to a list."""
        path = TESTCASE_DIR / f"{self.name}.json"
        rows = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            args, expected = json.loads(line)
            if not isinstance(args, list):
                args = [args]
            rows.append((args, expected))
        return rows


def load(name: str) -> QuixBugsProgram:
    """Read the buggy and reference sources for one QuixBugs program."""
    if name not in SUPPORTED_PROGRAMS:
        raise ValueError(f"{name!r} not in SUPPORTED_PROGRAMS {SUPPORTED_PROGRAMS}")
    buggy_path = BUGGY_DIR / f"{name}.py"
    correct_path = CORRECT_DIR / f"{name}.py"
    if not buggy_path.exists() or not correct_path.exists():
        raise FileNotFoundError(
            f"{buggy_path} or {correct_path} missing - did you "
            "`git clone https://github.com/jkoppel/QuixBugs external/QuixBugs`?"
        )
    return QuixBugsProgram(
        name=name,
        buggy_source=buggy_path.read_text(),
        correct_source=correct_path.read_text(),
    )


def load_all() -> list[QuixBugsProgram]:
    return [load(name) for name in SUPPORTED_PROGRAMS]


def run_buggy(program: QuixBugsProgram, args: list, *, timeout: float = DEFAULT_TIMEOUT) -> Outcome:
    return run_call(program.buggy_source, program.name, args, timeout=timeout)


def run_reference(program: QuixBugsProgram, args: list, *, timeout: float = DEFAULT_TIMEOUT) -> Outcome:
    return run_call(program.correct_source, program.name, args, timeout=timeout)


@dataclasses.dataclass(frozen=True)
class CaseResult:
    args: list
    expected: Any
    outcome: Outcome
    passed: bool


def smoke_test(name: str, *, timeout: float = DEFAULT_TIMEOUT) -> list[CaseResult]:
    """Run every shipped testcase for `name` against the *buggy* program.

    This only exercises loading + sandboxed execution; it is not the
    counterexample oracle (no Hypothesis, no reference diffing beyond the
    literal expected value QuixBugs ships).
    """
    program = load(name)
    results = []
    for args, expected in program.testcases():
        outcome = run_buggy(program, list(args), timeout=timeout)
        passed = outcome.ok and outcome.value == expected
        results.append(CaseResult(args=args, expected=expected, outcome=outcome, passed=passed))
    return results


if __name__ == "__main__":
    for name in SUPPORTED_PROGRAMS:
        cases = smoke_test(name)
        n_pass = sum(c.passed for c in cases)
        print(f"{name}: {n_pass}/{len(cases)} testcases pass on the buggy version")
        for c in cases:
            if not c.passed:
                reason = c.outcome.error_message if not c.outcome.ok else f"got {c.outcome.value!r}"
                print(f"    FAIL args={c.args} expected={c.expected!r}: {reason}")
