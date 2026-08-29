"""Where the LLM is called: builds the repair prompt for all three memory
conditions and extracts the patch from the response.

There is exactly one prompt-building function, `build_prompt`. The three
memory conditions - no_memory, untyped, typed - never branch the overall
template; they only change two sections of it:

  evidence_block   - what past refuted attempts are shown, if any
    no_memory: nothing (a fresh, independent proposal every time)
    untyped:   nothing either - see "What steering means" below
    typed:     one representative counterexample per distinct failure type
               seen so far (src.typer.theta), not the raw patches - this is
               the "type-matched evidence" a typed guard would retrieve

  exclusion_block  - an explicit instruction to avoid certain failure classes
    no_memory / untyped: empty - a flat log has no notion of a class
               to exclude, which is exactly the point Section 3.3 makes
               (untyped memory can guard but cannot steer)
    typed:     one line per eliminated failure type, telling the proposer
               not to repeat that (edit location, violated property) pair -
               the prompted analogue of Eq. (3)'s steering

What steering means, and why the untyped arm shows the proposer nothing
-----------------------------------------------------------------------
Steering in the paper is a property of the proposal DISTRIBUTION, not of the
wording of an instruction: Algorithm 1 line 3 draws p_t ~ G(.|E) over the
not-yet-eliminated types, and E is empty without types, so an untyped agent
draws from the unconditional G - the same distribution no memory draws from.
Section 5 says so in as many words ("a flat counterexample log that guards by
re-running all stored counterexamples but *cannot steer the proposer*... we
grant it a validation guard, making it a strong, not straw, baseline"), and
Section 3.3 repeats it ("untyped/flat memory can guard but cannot steer,
because it has no class to remove from the proposal").

Anything placed in the prompt conditions the distribution. An earlier version
of this module put the full transcript - every refuted patch's source plus its
counterexample - into the untyped arm's prompt, reading "cannot steer" as
"gets no exclusion instruction". That is not the same arm the theory is about,
and the measured consequence was large: on the first 30 tasks the transcript
arm's proposer re-emitted a near-copy of the patch it had just been shown, the
guard blocked 16-19 of its 20 rounds, and budgeted success fell BELOW the
no-memory baseline (0.50 vs 0.63) - where the paper has the two tied
("untyped collapses to 0.68, indistinguishable from no memory", Section 6).

The same reading error applied to the E3 ablation: Table 4 reports Guard-only
at budgeted success 0.68, identical to no memory, which is only possible if
guard-only's proposals are unconditioned too. So `disable_steering` now
suppresses the evidence block as well as the exclusion block.

The consequence to know when re-running: with no evidence in the prompt, the
untyped and guard-only arms build byte-identical prompts to no_memory's, under
the same draw nonce - so they share src.llm's cache entries with E1 and cost no
model calls at all. They also give a free soundness test: their success@B must
equal no_memory's exactly, because a guard can only block a candidate that
provably fails a stored counterexample, and a correct patch fails none. Any
gap is a guard-soundness bug (src.memory._still_refutes blocks on timeout too),
not a result.

The transcript condition remains a legitimate question about real LLM agents -
just not this paper's untyped baseline. To ask it, add it as its own mode rather
than by relabelling this one.

It briefly existed as `transcript` and was REMOVED on 2026-08-29, unrun. Two
reasons, both from the data rather than from cost. It tested no surviving claim:
Thm 4.2(i), Thm 4.3(a), Thm 4.3(b) and Prop 4.5 are all internal to the
no_memory/untyped/typed triangle. And the one claim it was built to support -
that a typed index is flat in the round index where a transcript grows linearly
- is already falsified without it, by the typed arm's own numbers: measured
prompt growth is 0.1 tokens/round for no_memory, 3.5 for untyped and 80.7 for
typed. The typed arm is the steepest of the three, not the flat one, so no
transcript measurement can rescue that sentence; it could only establish by how
much a transcript is worse.

If it is ever needed again, the cheap version answers most of it with no model
calls at all: build_prompt() below is separable from propose(), so a transcript
prompt can be reconstructed from the Attempts already logged in
data/episodes.jsonl and tokenised, which gives the context-growth comparison for
free. Only "does transcript steering beat typed steering on success" needs a
real run.

Never place the reference implementation (task's correct_source) here - see
src/adapter.py's module docstring.
"""
from __future__ import annotations

import math

import dataclasses
import re

from src.adapter import TASKS
from src.llm import _is_reasoning, complete
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
    # What theta actually assigned, kept only when a memory has since re-filed
    # this attempt under a different location (TypedMemory's c-sweep
    # mistyping). Everything the proposer and the guard see is the BELIEVED
    # type - that is the content of Def. 3.1 - so the true one has to travel
    # separately or it is gone, and every per-type breakdown in the c-sweep
    # then compares what typed believes against what the other arms know.
    mistyped_from: tuple = ()

    @classmethod
    def from_result(cls, task_name: str, buggy_source: str, patch: str, result: OracleResult) -> "Attempt":
        coarse, fine = theta_both(task_name, buggy_source, patch, result)
        return cls(patch=patch, result=result, coarse_type=coarse, fine_type=fine)

    def failure_type(self, granularity: str) -> FailureType | None:
        return self.coarse_type if granularity == "coarse" else self.fine_type


def _snippet(text: str | None, limit: int = _MAX_SNIPPET_CHARS) -> str:
    """One-line, length-capped view of a test input or a program's output.

    Contest data is not prompt-sized: a single AtCoder input can be hundreds of
    kilobytes, and a flat log concatenates one per past attempt. Left
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
        # Nothing. Untyped memory guards; it does not reach the proposer at all
        # (Section 3.3, Section 5's baseline definition, and Algorithm 1's
        # p_t ~ G(.|E) with E empty). See this module's docstring for why
        # putting the full history here made a different arm than the theory's.
        return ""

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
    disable_steering: bool = False,
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

    disable_steering: the E3 "guard-only" ablation - the guard still runs, but
    the proposer is not conditioned on memory at all: no exclusion instruction
    AND no evidence. Both, because Table 4 puts guard-only's budgeted success
    at 0.68, exactly no memory's, which only holds if its proposals come from
    the unconditional G. Showing the evidence while withholding the
    instruction is a third condition, not the paper's ablation.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity must be one of {GRANULARITIES}, got {granularity!r}")

    evidence_block = "" if disable_steering else _evidence_block(
        mode, history, granularity)
    exclusion_block = "" if disable_steering else _exclusion_block(mode, history, granularity)

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


class TruncatedResponse(RuntimeError):
    """The reply carried no *closed* ```python fence.

    Two different causes land here and the name only names the first. A reply
    cut off mid-block does not match _CODE_FENCE, which requires the closing
    fence - but neither does a reply that answered in prose and stopped, which
    is what a weaker model does to a format instruction it ignores. The two are
    told apart by `finish_reason` in data/calls.jsonl: 'length' is a real
    truncation and the output budget is too small, 'stop' is a model that
    simply never emitted a block.

    The old fallback returned the raw reply - prose and opening fence included -
    as though it were a patch. Such a candidate always fails, and src.typer then
    assigns it a failure type, so a harness defect entered the typed memory as
    evidence and polluted exactly the mechanism under study (paper SS VI-D-a).
    Raising instead lets src.loop log the round as spent-but-inconclusive and
    leave memory untouched, the same treatment an unusable oracle already gets.
    """


def _extract_code(text: str) -> str:
    match = _CODE_FENCE.search(text)
    if match is None:
        raise TruncatedResponse(
            f"no ```python code block in a {len(text)}-char reply - either the "
            f"output budget cut it off or the model answered in prose; "
            f"finish_reason in data/calls.jsonl says which "
            f"(ends: {text[-80:]!r})"
        )
    return match.group(1).strip() + "\n"


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
# The 16000 ceiling is now the tighter of two real limits rather than the
# streaming quirk of the Anthropic SDK it was originally written against:
# gpt-4o-mini's own output cap is 16384, and the local backend is pinned to a
# 32768-token window (scripts/screen_shard.sh) that has to hold the prompt too.
# src.llm raises ContextOverflow rather than letting a backend truncate silently
# when a program's budget plus its prompt would not fit.
#
# The draw that kept any single program from reaching this cap lived in
# scripts/select_hard_tasks.py, which was deleted in the results reset (748cabc)
# and has no replacement - so the ceiling is currently enforced only here and by
# that ContextOverflow check, not at corpus-selection time.

_CHARS_PER_TOKEN = 3.5
_BUDGET_MARGIN = 1.15
_MIN_BUDGET = 2048
_MAX_BUDGET = 16000

# A reasoning model spends hidden tokens *out of this same budget* before it
# writes a character of the answer, so the chat-model ceiling would cut the
# program off mid-rewrite - and a program cut off mid-rewrite is a SyntaxError
# recorded as a harness failure, on exactly the longest corpus tasks. o4-mini
# allows 100k completion tokens; the headroom below is the ceiling plus enough
# for a medium-effort reasoning trace, not the model's maximum.
#
# Applied ONLY when the model is an o-series id. max_tokens is in src.llm's
# cache key, so raising it unconditionally would re-key every cached call for
# the 8 corpus programs whose budget exceeds 2048 - including draws the pi
# screen has already paid for.
_MAX_BUDGET_REASONING = 48000
_REASONING_HEADROOM = 24000


def budget_for_source(source: str, model: str | None = None) -> int:
    """Output tokens needed to rewrite `source`, floored at _MIN_BUDGET.

    `model` only ever *raises* the ceiling, and only for a reasoning model - a
    chat model gets the identical number it got before this parameter existed,
    which is what keeps the response cache valid across the change.
    """
    estimate = math.ceil(len(source) / _CHARS_PER_TOKEN * _BUDGET_MARGIN)
    if model and _is_reasoning(model):
        return max(_MIN_BUDGET, min(estimate + _REASONING_HEADROOM, _MAX_BUDGET_REASONING))
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
    disable_steering: bool = False,
    nonce: str = "",
    temperature: float | None = None,
    spec_note: str = "",
    reasoning_effort: str | None = None,
    meta: dict | None = None,
) -> str:
    """Build the mode-appropriate prompt, call the LLM, and extract the patch source.

    `nonce` separates this draw from any other call that happens to build the
    same prompt - mandatory for no_memory, whose prompt never changes from round
    to round. src.loop.proposal_nonce is where the experiment's nonces come from.

    max_tokens defaults to a budget sized for *this* program (see
    budget_for_source). Pass a value only to override that.

    `meta`, when given, comes back filled with what the call cost - see
    src.llm.complete. src.loop puts those numbers on the RoundRecord, which is
    the only join between a round and data/calls.jsonl.
    """
    prompt = build_prompt(
        task_name, buggy_source, program_label, mode, history,
        granularity=granularity, disable_steering=disable_steering,
        spec_note=spec_note,
    )
    if max_tokens is None:
        max_tokens = budget_for_source(buggy_source, model=model)
    text = complete(prompt, model=model, max_tokens=max_tokens, nonce=nonce,
                    temperature=temperature, reasoning_effort=reasoning_effort, meta=meta)
    return _extract_code(text)


if __name__ == "__main__":
    from src.adapter import SUPPORTED_PROGRAMS, load

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")
    name = SUPPORTED_PROGRAMS[0]
    task = TASKS[name]
    program = load(name)
    print(build_prompt(name, program.buggy_source, name, "no_memory", [], spec_note=task.spec_note))
