"""A3 - load one ConDefects faulty program and the test pool that judges it.

ConDefects (https://github.com/appmlk/ConDefects) is a corpus of real faults
from AtCoder submissions. Every fault ships three things this project needs:

  faultyVersion.py    the program to repair - a whole script, ~50 lines,
                      reading stdin and writing stdout
  correctVersion.py   the same author's accepted submission (the reference;
                      visible to the oracle only, never to a model)
  faultLocation.txt   1-indexed line numbers of the faulty statements

and the contest's own test data, shipped separately as Test.zip:

  Test/<contest>/<LETTER>/in/<case>     the input handed to the program
  Test/<contest>/<LETTER>/out/<case>    the output AtCoder accepted

So unlike a property-based setup there are no input *strategies* to write: the
inputs already exist, and each comes with its expected output, which is what
the counterexample oracle (src/oracle.py) diffs a candidate against. A
counterexample is therefore a concrete test case, identified by name - the
input text itself never has to travel through the metrics log.

Task identity here is one *fault*, `"<task_id>/<program_id>"`: several
submissions can fail the same coding task, and they are different programs
with different bugs. scripts/validate_oracle.py keeps at most one per task_id
so the frozen corpus stays independent.

The reference implementation is visible to the oracle only. Never place
`correct_source` in a model prompt - see src/proposer.py and the README.
"""
from __future__ import annotations

import dataclasses
import functools
import os
import pathlib

from dotenv import load_dotenv

from src.sandbox import DEFAULT_TIMEOUT, Outcome, run_program

load_dotenv()

CONDEFECTS_ROOT = pathlib.Path(os.environ.get("CONDEFECTS_ROOT", "external/ConDefects"))
CODE_DIR = CONDEFECTS_ROOT / "Code"
# Normally <root>/Test. Overridable because Test.zip is a 6.4 GB out-of-band
# download that can arrive incomplete: a partial tree can be salvaged to a
# separate directory and pointed at from here, which keeps Test/ meaning "the
# complete archive" and keeps a truncated download from being frozen into a
# corpus by accident (see scripts/fetch_condefects.py).
TEST_DIR = pathlib.Path(os.environ.get("CONDEFECTS_TEST_DIR", "")) or CONDEFECTS_ROOT / "Test"
LANGUAGE_DIR = "Python"

_SETUP_HINT = (
    f"{CONDEFECTS_ROOT} is missing or incomplete - run "
    "`python3 scripts/fetch_condefects.py` (it clones the code and tells you "
    "where to get Test.zip, which is not in the git repository)."
)

# Worked examples put in front of the proposer (Task.spec_note). Contest
# problems always show a couple of sample cases, and a submission stripped of
# its problem statement is genuinely ambiguous without them - a patch would get
# refuted for printing "Yes" where the judge wanted "YES", which is a
# disagreement about the specification, not a bug. Applied identically in all
# three memory conditions, so it cannot confound the comparison. The examples
# stay in the oracle's pool: failing one the proposer was shown is still a
# legitimate refutation.
N_SPEC_EXAMPLES = 2
SPEC_MAX_INPUT_CHARS = 400
SPEC_MAX_OUTPUT_CHARS = 200


@dataclasses.dataclass(frozen=True)
class TestCase:
    """One shipped contest test case."""

    name: str
    input_text: str
    expected_output: str | None   # None if Test/ has no matching out/ file


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_dir_for(task_id: str) -> pathlib.Path | None:
    """Test/<contest>/<LETTER> for a task id like "abc221_f".

    Mirrors the toolkit's own lookup (ConDefects Tool/RunTest.py): the letter
    after the underscore, uppercased, with "Ex" as the fallback - AtCoder names
    a Beginner Contest's eighth problem "Ex" rather than "H".
    """
    contest, _, suffix = task_id.partition("_")
    for letter in (suffix.upper(), "Ex"):
        candidate = TEST_DIR / contest / letter
        if (candidate / "in").is_dir():
            return candidate
    return None


@functools.lru_cache(maxsize=None)
def test_cases_for(task_id: str) -> tuple[TestCase, ...]:
    """Every shipped case for a coding task, in a deterministic order.

    Cached per task_id rather than per fault: several faulty programs share one
    coding task, and re-reading a few hundred input files for each of them
    would dominate the oracle's cost.
    """
    root = test_dir_for(task_id)
    if root is None:
        return ()
    out_dir = root / "out"
    cases = []
    for in_path in sorted((root / "in").iterdir()):
        if not in_path.is_file() or in_path.name.startswith("."):
            continue
        out_path = out_dir / in_path.name
        cases.append(TestCase(
            name=in_path.name,
            input_text=_read(in_path),
            expected_output=_read(out_path) if out_path.is_file() else None,
        ))
    return tuple(cases)


def _render_examples(cases: tuple[TestCase, ...]) -> str:
    if not cases:
        return ""
    blocks = [
        f"Example {i}\ninput:\n{c.input_text.rstrip()}\n"
        f"expected output:\n{(c.expected_output or '').rstrip()}"
        for i, c in enumerate(cases, 1)
    ]
    return (
        "The program reads from standard input and writes to standard output. "
        "Worked examples of the intended behaviour:\n\n" + "\n\n".join(blocks)
    )


@dataclasses.dataclass(frozen=True)
class Task:
    """Everything the oracle needs to judge one ConDefects fault."""

    name: str          # "<task_id>/<program_id>" - the identity used everywhere
    task_id: str       # AtCoder coding task, e.g. "abc221_f"
    program_id: str    # submission id, e.g. "40898300"

    @property
    def dir(self) -> pathlib.Path:
        return CODE_DIR / self.task_id / LANGUAGE_DIR / self.program_id

    @property
    def test_cases(self) -> tuple[TestCase, ...]:
        return test_cases_for(self.task_id)

    def case(self, name: str) -> TestCase | None:
        """Look one case up by name - how a stored counterexample is replayed."""
        for case in self.test_cases:
            if case.name == name:
                return case
        return None

    @property
    def spec_note(self) -> str:
        """Sample input/output pairs shown to the proposer. See N_SPEC_EXAMPLES."""
        small = [
            c for c in self.test_cases
            if c.expected_output is not None
            and len(c.input_text) <= SPEC_MAX_INPUT_CHARS
            and len(c.expected_output) <= SPEC_MAX_OUTPUT_CHARS
        ]
        # AtCoder ships the problem statement's own samples under names like
        # "sample_01"/"00_example_00"; prefer those, they are the ones a human
        # solver would have seen. Otherwise fall back to the smallest inputs.
        named = [c for c in small if "sample" in c.name.lower() or "example" in c.name.lower()]
        chosen = named or sorted(small, key=lambda c: len(c.input_text))
        return _render_examples(tuple(chosen[:N_SPEC_EXAMPLES]))


def discover() -> dict[str, Task]:
    """Scan the checkout for faults. TASKS below is this, evaluated at import;
    scripts/fetch_condefects.py calls it again after downloading, when the
    import-time snapshot is stale by construction."""
    if not CODE_DIR.is_dir():
        return {}
    tasks = {}
    for faulty in CODE_DIR.glob(f"*/{LANGUAGE_DIR}/*/faultyVersion.py"):
        program_dir = faulty.parent
        if not (program_dir / "correctVersion.py").is_file():
            continue
        task_id = program_dir.parent.parent.name
        name = f"{task_id}/{program_dir.name}"
        tasks[name] = Task(name=name, task_id=task_id, program_id=program_dir.name)
    return dict(sorted(tasks.items()))


TASKS: dict[str, Task] = discover()
SUPPORTED_PROGRAMS: tuple[str, ...] = tuple(TASKS)


# ── Corpus metadata (root date.txt / difficulty.txt) ──────────────────────
# "abc221_a 2021-10-02" and "abc221_a 10", one coding task per line. The dates
# are what makes a contamination-controlled split possible - the whole reason
# for preferring ConDefects to an older benchmark - and the difficulties are
# reported alongside the measured pi_hat strata.

def _metadata(filename: str) -> dict[str, str]:
    path = CONDEFECTS_ROOT / filename
    if not path.is_file():
        return {}
    out = {}
    for line in _read(path).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


@functools.lru_cache(maxsize=1)
def task_dates() -> dict[str, str]:
    """task_id -> contest date, "YYYY-MM-DD"."""
    return _metadata("date.txt")


@functools.lru_cache(maxsize=1)
def task_difficulties() -> dict[str, int]:
    """task_id -> AtCoder difficulty rating."""
    return {k: int(v) for k, v in _metadata("difficulty.txt").items() if v.lstrip("-").isdigit()}


@dataclasses.dataclass(frozen=True)
class ConDefectsProgram:
    name: str
    buggy_source: str
    correct_source: str
    fault_lines: tuple[int, ...]   # 1-indexed, from faultLocation.txt

    def testcases(self) -> tuple[TestCase, ...]:
        return TASKS[self.name].test_cases


def load(name: str) -> ConDefectsProgram:
    """Read the faulty and reference sources for one ConDefects fault."""
    if name not in TASKS:
        if not TASKS:
            raise FileNotFoundError(_SETUP_HINT)
        raise ValueError(f"{name!r} is not a known fault ({len(TASKS)} available)")
    task = TASKS[name]
    faulty = task.dir / "faultyVersion.py"
    correct = task.dir / "correctVersion.py"
    if not faulty.is_file() or not correct.is_file():
        raise FileNotFoundError(f"{faulty} or {correct} missing - {_SETUP_HINT}")

    location = task.dir / "faultLocation.txt"
    fault_lines: tuple[int, ...] = ()
    if location.is_file():
        fault_lines = tuple(int(t) for t in _read(location).split() if t.lstrip("-").isdigit())

    return ConDefectsProgram(
        name=name,
        buggy_source=_read(faulty),
        correct_source=_read(correct),
        fault_lines=fault_lines,
    )


def load_all() -> list[ConDefectsProgram]:
    return [load(name) for name in SUPPORTED_PROGRAMS]


def run_buggy(program: ConDefectsProgram, case: TestCase, *, timeout: float = DEFAULT_TIMEOUT) -> Outcome:
    return run_program(program.buggy_source, case.input_text, timeout=timeout)


def run_reference(program: ConDefectsProgram, case: TestCase, *, timeout: float = DEFAULT_TIMEOUT) -> Outcome:
    return run_program(program.correct_source, case.input_text, timeout=timeout)


@dataclasses.dataclass(frozen=True)
class CaseResult:
    case: TestCase
    outcome: Outcome
    passed: bool


def smoke_test(name: str, *, limit: int | None = 10, timeout: float = DEFAULT_TIMEOUT) -> list[CaseResult]:
    """Run the first `limit` shipped cases against the *faulty* program.

    This only exercises loading + sandboxed execution; it is not the
    counterexample oracle (src/oracle.py owns that). A fault none of whose
    cases fail here is one the shipped pool does not expose -
    scripts/validate_oracle.py drops those from the corpus.
    """
    from src.oracle import outputs_equal   # local: src.oracle imports this module

    program = load(name)
    cases = program.testcases()
    results = []
    for case in (cases[:limit] if limit else cases):
        outcome = run_buggy(program, case, timeout=timeout)
        passed = (
            outcome.ok
            and case.expected_output is not None
            and outputs_equal(outcome.value, case.expected_output)
        )
        results.append(CaseResult(case=case, outcome=outcome, passed=passed))
    return results


if __name__ == "__main__":
    print(f"{len(TASKS)} ConDefects Python faults under {CONDEFECTS_ROOT}")
    if not TASKS:
        raise SystemExit(_SETUP_HINT)
    for name in SUPPORTED_PROGRAMS[:5]:
        task = TASKS[name]
        if not task.test_cases:
            print(f"{name}: no test data (Test.zip not unpacked for {task.task_id}?)")
            continue
        cases = smoke_test(name, limit=5)
        n_pass = sum(c.passed for c in cases)
        print(f"{name}: {n_pass}/{len(cases)} of the first cases pass on the faulty version "
              f"({len(task.test_cases)} cases total, "
              f"difficulty {task_difficulties().get(task.task_id, '?')})")
