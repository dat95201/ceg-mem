"""E11 - the self-generated-test pre-filter (the CodeT family, ICLR '23).

One cached model call per (task, model) asks for a handful of test cases -
input text plus the output the model *believes* is correct - and every
proposal must pass them before the oracle is paid. This is the one baseline
that attacks oracle cost the same way the guard does, by blocking calls, but
with a-priori model knowledge instead of accumulated refutations. The two can
also compose (preset E11b): self-tests catch a-priori failures, the guard
catches repeats.

Three properties the experiment depends on:

  * The verdicts are NOT oracle-grade. A generated expected output can simply
    be wrong, and then the filter blocks correct patches - that risk is the
    experiment, not a bug, and `filter precision` is measured downstream by
    joining blocked rounds against E8-style audits. Nothing a self-test decides
    is ever stored in memory or shown to the proposer.
  * The generation is deterministic and cached: the nonce is a pure function of
    the task, so every seed, round and shard replays one draw
    (src.llm.complete's cache), and a re-run costs no model calls.
  * Failure to parse is an INERT filter, not a failed episode: cases=[] and
    every check passes. The episode row carries selftest_cases=0, so an arm
    where generation failed is visible rather than silently identical to
    no_memory.

The knowledge budget is deliberately the proposer's own: the prompt shows the
worked examples (spec_note) and the buggy source, both of which every arm's
proposer already sees. It never sees the reference solution or the shipped
test pool - that would smuggle the oracle into the filter.
"""
from __future__ import annotations

import dataclasses
import json
import re

from src.llm import complete
from src.oracle import outputs_equal
from src.sandbox import run_program

# One generation per (task, model, n) per process. The response cache already
# makes the call free across processes; this just skips the round trip.
_FILTERS: dict[tuple, "SelfTestFilter"] = {}

# A generated case bigger than this is almost certainly the model rambling, and
# feeding it to run_program would bill the filter for a mistake the parser
# should have caught.
_MAX_CASE_CHARS = 4000

_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class SelfTestVerdict:
    blocked: bool
    runs: int                 # sandbox executions this check actually cost
    n_cases: int              # usable cases the filter holds (0 = inert)
    failed_case: str | None = None


class SelfTestFilter:
    """Holds the parsed cases; `check` runs a candidate against all of them."""

    def __init__(self, cases: list[tuple[str, str, str]], *, parse_error: str | None = None):
        # (name, input_text, expected_output)
        self.cases = cases
        self.parse_error = parse_error

    def check(self, candidate_source: str) -> SelfTestVerdict:
        runs = 0
        for name, stdin_text, expected in self.cases:
            out = run_program(candidate_source, stdin_text)
            runs += 1
            # A crash or timeout on a well-formed input is a failure like any
            # other; on a MALFORMED generated input it wrongly blocks - which is
            # the filter being wrong, i.e. the measured risk, not a harness bug.
            if out.timed_out or not out.ok or not outputs_equal(out.value, expected):
                return SelfTestVerdict(blocked=True, runs=runs,
                                       n_cases=len(self.cases), failed_case=name)
        return SelfTestVerdict(blocked=False, runs=runs, n_cases=len(self.cases))


def _parse_cases(text: str, n_cases: int) -> list[tuple[str, str, str]]:
    """[{"input": ..., "output": ...}, ...] out of the reply, defensively."""
    m = _JSON_FENCE.search(text)
    raw = m.group(1) if m else text
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        raise ValueError("no JSON array in the reply")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("top-level JSON is not a list")
    cases: list[tuple[str, str, str]] = []
    for i, item in enumerate(data[:n_cases]):
        if not isinstance(item, dict):
            continue
        stdin_text, expected = item.get("input"), item.get("output")
        if not isinstance(stdin_text, str) or not isinstance(expected, str):
            continue
        if not stdin_text.strip() or not expected.strip():
            continue
        if len(stdin_text) > _MAX_CASE_CHARS or len(expected) > _MAX_CASE_CHARS:
            continue
        if not stdin_text.endswith("\n"):
            stdin_text += "\n"
        cases.append((f"selftest{i + 1}", stdin_text, expected))
    if not cases:
        raise ValueError("the array held no usable {input, output} pairs")
    return cases


def generate_selftests(task_name: str, spec_note: str, buggy_source: str | None = None,
                       *, model: str | None = None, n_cases: int = 5) -> SelfTestFilter:
    """One deterministic, cached model call -> a SelfTestFilter.

    The nonce is a pure function of (task, n): every seed, round and shard
    replays the identical draw, so the generation is paid for exactly once per
    task per model. Unparseable replies come back as an inert filter.
    """
    sections = [
        "You are writing test cases for a competitive-programming task. The "
        "program reads from standard input and writes to standard output.",
    ]
    if spec_note:
        sections.append(spec_note)
    if buggy_source:
        sections.append(
            "A (possibly buggy) submission for the task, for context about the "
            f"input format only:\n```python\n{buggy_source}\n```")
    sections.append(
        f"Write {n_cases} NEW small test cases you are confident about. Reply "
        "with ONLY a JSON array in a ```json fence, each element "
        '{"input": "<stdin text>", "output": "<exact expected stdout>"}. '
        "Keep every input under 20 lines. No prose.")
    prompt = "\n\n".join(sections)
    try:
        text = complete(prompt, model=model, max_tokens=2000,
                        nonce=f"selftest|{task_name}|n{n_cases}")
        cases = _parse_cases(text, n_cases)
        return SelfTestFilter(cases)
    except (ValueError, json.JSONDecodeError) as exc:
        # Inert, visible, and cheap: the row carries selftest_cases=0 and the
        # arm degrades to no_memory rather than dying mid-shard.
        print(f"    selftest: unusable generation for {task_name} ({exc}) - filter inert",
              flush=True)
        return SelfTestFilter([], parse_error=str(exc))


def make_filter(task_name: str, spec_note: str, *, model: str | None = None,
                n_cases: int = 5, buggy_source: str | None = None) -> SelfTestFilter:
    """Memoised per (task, model, n) for the life of the process."""
    key = (task_name, model, n_cases)
    if key not in _FILTERS:
        _FILTERS[key] = generate_selftests(task_name, spec_note or "", buggy_source,
                                           model=model, n_cases=n_cases)
    return _FILTERS[key]
