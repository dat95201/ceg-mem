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

import math

import dataclasses
import re

from src.adapter import TASKS
from src.llm import complete
from src.oracle import OracleResult
from src.sandbox import Outcome
from src.typer import FailureType, theta_both

MODES = ("no_memory", "untyped", "typed")
GRANULARITIES = ("coarse", "fine")

# Cap on any single piece of contest data quoted into a prompt. See _snippet.
_MAX_SNIPPET_CHARS = 240

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


def _snippet(text: str | None, limit: int = _MAX_SNIPPET_CHARS) -> str:
    """One-line, length-capped view of a test input or a program's output.

    Contest data is not prompt-sized: a single AtCoder input can be hundreds of
    kilobytes, and an untyped transcript concatenates one per past attempt. Left
    whole they would blow the context window - and would do it *only* in the
    untyped arm, turning a memory comparison into a truncation artefact. Every
    counterexample shown to a model goes through here, in all three conditions.
    """
    if not text:
        return "<no output>"
    joined = " / ".join(line.strip() for line in text.strip().splitlines())
    if len(joined) > limit:
        return f"{joined[:limit]}... [truncated, {len(text)} chars total]"
    return joined


def _case_input(attempt: Attempt) -> str | None:
    """The stored counterexample's input text, looked up by case name."""
    ft = attempt.fine_type or attempt.coarse_type
    if ft is None or not attempt.result.args:
        return None
    task = TASKS.get(ft.task)
    case = task.case(attempt.result.args[0]) if task else None
    return case.input_text if case else None


def _describe_outcome(outcome: Outcome, reference: Outcome | None) -> str:
    if outcome.timed_out:
        return "exceeded the time limit"
    if not outcome.ok:
        return f"crashed with {outcome.error_type}: {outcome.error_message}"
    expected = f", but the expected output is `{_snippet(reference.value)}`" if reference and reference.ok else ""
    return f"printed `{_snippet(outcome.value)}`{expected}"


def _format_counterexample(attempt: Attempt) -> str:
    r = attempt.result
    case_name = r.args[0] if r.args else "?"
    stdin_text = _case_input(attempt)
    on_input = f" (stdin: `{_snippet(stdin_text)}`)" if stdin_text is not None else ""
    return f"test case {case_name}{on_input} -> {_describe_outcome(r.candidate, r.reference)}"


def _refuted(history: list[Attempt]) -> list[Attempt]:
    """Past attempts the oracle actually refuted.

    `result.candidate is None` marks an inconclusive round - the oracle itself
    errored (src.oracle.OracleResult.oracle_error) rather than observing the
    patch fail - so there is no counterexample to show and nothing to steer
    away from. src.loop already declines to store those; filtering here too
    keeps a hand-built history from putting a `None` into the prompt.
    """
    return [a for a in history if not a.result.accept and a.result.candidate is not None]


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
    program_label: str,
    mode: str,
    history: list[Attempt],
    *,
    granularity: str = "fine",
    disable_exclusion: bool = False,
    spec_note: str = "",
) -> str:
    """program_label: how the fault is named to the model. ConDefects programs
    are whole scripts with no single function under repair, so this is the
    fault id, not an entry point - there is nothing for the model to rename.

    spec_note: worked input/output examples for the coding task (see
    src.adapter.Task.spec_note). A submission carries no problem statement, so
    without them the intended output format is underdetermined and a patch can
    be refuted for a defensible reading rather than for a bug. Applied
    identically in all three modes, so it cannot confound the comparison.

    disable_exclusion: steering ablation (E3, "guard-only") - keep the typed
    evidence_block but suppress exclusion_block, so the proposer sees what was
    refuted without being told to avoid it.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}, got {granularity!r}")

    evidence_block = _evidence_block(mode, history, granularity)
    exclusion_block = "" if disable_exclusion else _exclusion_block(mode, history, granularity)

    sections = [
        "You are repairing a single buggy Python program. It reads its input "
        "from standard input and writes its answer to standard output.",
        f"Program: `{program_label}`",
        f"Current source:\n```python\n{buggy_source}\n```",
    ]
    if spec_note:
        sections.append(spec_note)
    if evidence_block:
        sections.append(evidence_block)
    if exclusion_block:
        sections.append(exclusion_block)
    sections.append(
        "Return the corrected source for the whole program and nothing else: "
        "a single ```python fenced code block, no prose before or after it."
    )
    return "\n\n".join(sections)


def _extract_code(text: str) -> str:
    match = _CODE_FENCE.search(text)
    return (match.group(1) if match else text).strip() + "\n"


# The proposer rewrites a whole program, so its output budget has to cover one.
# A reply cut off mid-block is a SyntaxError, and a SyntaxError refutes the
# *harness*, not the proposal: the episode records a failure the model never
# made. A flat 2048 did exactly that on the 8 corpus programs longer than about
# 7 KB - one of them, a 68 KB submission carrying a precomputed table, needs
# 22k.
#
# Sized per program rather than raised globally, for one reason: max_tokens is
# part of src.llm's cache key. A global raise would invalidate every cached call
# - including the pi already paid for on four corpus tasks - while a per-program
# budget changes the key only for the programs whose budget actually moves. 112
# of the 120 keep the 2048 floor and their cache with it.
#
# 3.5 chars/token is measured on this corpus's Python; the 15% margin covers the
# model's own formatting.
#
# The ceiling is the SDK's, not the model's. claude-haiku-4-5 will emit 64k
# output tokens, but only on a streamed request - the SDK refuses a
# non-streaming call whose max_tokens it estimates will outrun the HTTP timeout,
# and src.llm does not stream. Rather than add streaming for one outlier, the
# draw refuses to select a program that would need more (MAX_SOURCE_CHARS in
# scripts/select_hard_tasks.py, derived from this number), so nothing in the
# corpus can reach the cap.

_CHARS_PER_TOKEN = 3.5
_BUDGET_MARGIN = 1.15
_MIN_BUDGET = 2048
_MAX_BUDGET = 16000


def budget_for_source(source: str) -> int:
    """Output tokens needed to rewrite `source`, floored at _MIN_BUDGET."""
    estimate = math.ceil(len(source) / _CHARS_PER_TOKEN * _BUDGET_MARGIN)
    return max(_MIN_BUDGET, min(estimate, _MAX_BUDGET))


def propose(
    task_name: str,
    buggy_source: str,
    program_label: str,
    mode: str,
    history: list[Attempt],
    *,
    model: str | None = None,
    granularity: str = "fine",
    max_tokens: int | None = None,
    disable_exclusion: bool = False,
    nonce: str = "",
    temperature: float | None = None,
    spec_note: str = "",
) -> str:
    """Build the mode-appropriate prompt, call the LLM, and extract the patch source.

    `nonce` separates this draw from any other call that happens to build the
    same prompt - mandatory for no_memory, whose prompt never changes from round
    to round. src.loop.proposal_nonce is where the experiment's nonces come from.

    max_tokens defaults to a budget sized for *this* program (see
    budget_for_source). Pass a value only to override that.
    """
    prompt = build_prompt(
        task_name, buggy_source, program_label, mode, history,
        granularity=granularity, disable_exclusion=disable_exclusion,
        spec_note=spec_note,
    )
    if max_tokens is None:
        max_tokens = budget_for_source(buggy_source)
    text = complete(prompt, model=model, max_tokens=max_tokens, nonce=nonce, temperature=temperature)
    return _extract_code(text)


if __name__ == "__main__":
    from src.adapter import SUPPORTED_PROGRAMS, load

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")
    name = SUPPORTED_PROGRAMS[0]
    task = TASKS[name]
    program = load(name)
    print(build_prompt(name, program.buggy_source, name, "no_memory", [], spec_note=task.spec_note))
