"""Where the LLM is called: builds the repair prompt for all three memory
conditions and extracts the patch from the response.

There is exactly one prompt-building function, `build_prompt`. The three
memory conditions - no_memory, untyped, typed - never branch the overall
template; they only change two sections of it:

  evidence_block   - what past refuted attempts are shown, if any
    no_memory: nothing (a fresh, independent proposal every time)
    untyped:   the full flat transcript of every past attempt - patch source
               plus the counterexample that refuted it - exactly the "prior
               patches and error messages concatenated into the prompt"
               transcript this project's memory is contrasted against
    typed:     one representative counterexample per distinct failure type
               seen so far (src.typer.theta), not the raw patches - this is
               the "type-matched evidence" a typed guard would retrieve

  exclusion_block  - an explicit instruction to avoid certain failure classes
    no_memory / untyped: empty - a flat transcript has no notion of a class
               to exclude, which is exactly the point Section 3.3 makes
               (untyped memory can guard but cannot steer)
    typed:     one line per eliminated failure type, telling the proposer
               not to repeat that (edit location, violated property) pair -
               the prompted analogue of Eq. (3)'s steering

Never place the reference implementation (task's correct_source) here - see
src/adapter.py's module docstring.
"""
from __future__ import annotations

import dataclasses
import re

from src.llm import complete
from src.oracle import OracleResult
from src.sandbox import Outcome
from src.typer import FailureType, theta_both

MODES = ("no_memory", "untyped", "typed")
GRANULARITIES = ("coarse", "fine")

_CODE_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class Attempt:
    """One past (proposal, verification) pair, typed at both granularities."""

    patch: str
    result: OracleResult
    coarse_type: FailureType | None
    fine_type: FailureType | None

    @classmethod
    def from_result(cls, task_name: str, buggy_source: str, patch: str, result: OracleResult) -> "Attempt":
        coarse, fine = theta_both(task_name, buggy_source, patch, result)
        return cls(patch=patch, result=result, coarse_type=coarse, fine_type=fine)

    def failure_type(self, granularity: str) -> FailureType | None:
        return self.coarse_type if granularity == "coarse" else self.fine_type


def _describe_outcome(outcome: Outcome, reference: Outcome | None) -> str:
    if outcome.timed_out:
        return "timed out"
    if not outcome.ok:
        return f"raised {outcome.error_type}: {outcome.error_message}"
    ref_desc = f", reference returned {reference.value!r}" if reference and reference.ok else ""
    return f"returned {outcome.value!r}{ref_desc}"


def _format_counterexample(attempt: Attempt) -> str:
    r = attempt.result
    return f"input {r.args!r} -> {_describe_outcome(r.candidate, r.reference)}"


def _refuted(history: list[Attempt]) -> list[Attempt]:
    return [a for a in history if not a.result.accept]


def _evidence_block(mode: str, history: list[Attempt], granularity: str) -> str:
    refuted = _refuted(history)
    if mode == "no_memory" or not refuted:
        return ""

    if mode == "untyped":
        lines = ["Past attempts that were tried and refuted (do not blindly repeat them):"]
        for i, a in enumerate(refuted, 1):
            lines.append(
                f"\nAttempt {i}:\n```python\n{a.patch}\n```\nCounterexample: {_format_counterexample(a)}"
            )
        return "\n".join(lines)

    if mode == "typed":
        by_type: dict[str, Attempt] = {}
        for a in refuted:
            ft = a.failure_type(granularity)
            if ft is not None:
                by_type.setdefault(ft.key, a)
        if not by_type:
            return ""
        lines = ["Failure classes already ruled out, each with one representative counterexample:"]
        for a in by_type.values():
            ft = a.failure_type(granularity)
            lines.append(f"- [{ft.location} / {ft.property}] {_format_counterexample(a)}")
        return "\n".join(lines)

    raise ValueError(f"mode must be one of {MODES}, got {mode!r}")


def _exclusion_block(mode: str, history: list[Attempt], granularity: str) -> str:
    if mode != "typed":
        return ""
    eliminated: dict[str, FailureType] = {}
    for a in _refuted(history):
        ft = a.failure_type(granularity)
        if ft is not None:
            eliminated[ft.key] = ft
    if not eliminated:
        return ""
    lines = ["Do not propose a patch that falls into any of these already-eliminated failure classes:"]
    for ft in eliminated.values():
        lines.append(f"- edit location {ft.location}, violates {ft.property}")
    return "\n".join(lines)


def build_prompt(
    task_name: str,
    buggy_source: str,
    entry_point: str,
    mode: str,
    history: list[Attempt],
    *,
    granularity: str = "fine",
) -> str:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}, got {granularity!r}")

    evidence_block = _evidence_block(mode, history, granularity)
    exclusion_block = _exclusion_block(mode, history, granularity)

    sections = [
        "You are repairing a single buggy Python function.",
        f"Function to fix: `{entry_point}`",
        f"Current source:\n```python\n{buggy_source}\n```",
    ]
    if evidence_block:
        sections.append(evidence_block)
    if exclusion_block:
        sections.append(exclusion_block)
    sections.append(
        "Return the corrected source for the whole module and nothing else: "
        "a single ```python fenced code block, no prose before or after it."
    )
    return "\n\n".join(sections)


def _extract_code(text: str) -> str:
    match = _CODE_FENCE.search(text)
    return (match.group(1) if match else text).strip() + "\n"


def propose(
    task_name: str,
    buggy_source: str,
    entry_point: str,
    mode: str,
    history: list[Attempt],
    *,
    model: str | None = None,
    granularity: str = "fine",
    max_tokens: int = 1024,
) -> str:
    """Build the mode-appropriate prompt, call the LLM, and extract the patch source."""
    prompt = build_prompt(task_name, buggy_source, entry_point, mode, history, granularity=granularity)
    text = complete(prompt, model=model, max_tokens=max_tokens)
    return _extract_code(text)


if __name__ == "__main__":
    from src.adapter import TASKS, load

    task = TASKS["gcd"]
    program = load("gcd")
    print(build_prompt("gcd", program.buggy_source, task.entry_point, "no_memory", []))
