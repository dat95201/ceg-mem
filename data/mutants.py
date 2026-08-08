"""Planted mutants for oracle validation: 3 per program, one per fault type
from proposal section 3.5's illustrative episode (main_proposal.pdf):

  off_by_one       - tau_1: boundary arithmetic error (loop/slice bound, index
                     arithmetic, an accumulator's step size)
  wrong_comparison - tau_2: wrong relational/equality/membership operator, in
                     particular a strict/non-strict boundary swap
  missing_guard    - tau_3: an edge-case guard (empty/zero/visited/early
                     return) is neutralised, so the correct answer is produced
                     everywhere except that edge case

Every mutant is a single targeted edit against the *correct* source
(`correctVersion.py`), never against `faultyVersion.py` - that one already
carries ConDefects' own real fault, which is a separate thing from these
self-written, categorised mutants.

Why this is generated rather than a hand-written table
------------------------------------------------------
The QuixBugs version of this file held 120 substring anchors typed out by
hand, one triple per named algorithm. That does not transfer: a ConDefects
program is an anonymous AtCoder submission drawn from a pool of 2,864, and the
corpus is not known until *after* the mutant stage has run - the whole point
of the stage is to decide which programs to keep. So the hand-written thing
here is the *operator*, not the anchor: three families of edit, each of which
locates its own site in any given program.

An operator finds its site with `ast`, but applies the edit by splicing the
source *text* over the node's byte range. Nothing is unparsed, so every byte
outside the edit is identical to what AtCoder accepted, and a mutant can never
be "caught" because of a round-trip through `ast.unparse` rather than because
of the planted fault.

Each fault type yields an ordered list of candidate sites, most-preferred
first, and `build_mutants(..., attempt=k)` takes the k-th. That is what lets
scripts/validate_oracle.py retry: a generated mutant can land on a site no
test reaches, which makes it an *equivalent mutant* - undetectable in
principle, and a known artefact of mutation testing rather than a weakness of
the oracle under test. Skipping to the next site is what a human writing these
by hand does anyway when their first idea turns out not to break anything.

MUTANT_OVERRIDES is the escape hatch: a hand-written edit for a specific
program wins over anything the operators would pick.
"""
from __future__ import annotations

import ast
import dataclasses
import difflib
import re

FAULT_TYPES = ("off_by_one", "wrong_comparison", "missing_guard")


@dataclasses.dataclass(frozen=True)
class Mutant:
    task: str
    fault_type: str
    note: str          # what the planted fault does, for the audit record
    source: str
    site: str          # "L12:C4" in the correct source, or "override"
    diff: str          # unified diff against the correct source


@dataclasses.dataclass(frozen=True)
class _Edit:
    """One candidate splice: replace source[start:end] (byte offsets) with `text`."""

    rank: int          # lower = preferred; ties broken by position
    start: int
    end: int
    text: str
    note: str
    site: str


# ── source <-> AST position plumbing ──────────────────────────────────────
# ast reports col_offset as a *byte* offset into the UTF-8 encoding of the
# line, so every span is computed over the encoded source and decoded back at
# the end. Contest submissions are usually ASCII, but not always - a stray
# full-width character in a comment would otherwise shift every edit on its
# line.

def _line_starts(data: bytes) -> list[int]:
    starts, pos = [0], 0
    for line in data.splitlines(keepends=True):
        pos += len(line)
        starts.append(pos)
    return starts


def _span(starts: list[int], node: ast.AST) -> tuple[int, int] | None:
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    if lineno is None or end_lineno is None or end_lineno > len(starts):
        return None
    start = starts[lineno - 1] + node.col_offset
    end = starts[end_lineno - 1] + node.end_col_offset
    return (start, end) if end > start else None


def _site(node: ast.AST) -> str:
    return f"L{node.lineno}:C{node.col_offset}"


def _text(data: bytes, span: tuple[int, int]) -> str:
    return data[span[0]:span[1]].decode("utf-8", "replace")


def _is_main_guard(node: ast.If) -> bool:
    """`if __name__ == "__main__":` - a guard, but not one worth planting in."""
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


def _is_dead(node: ast.AST) -> bool:
    """Already a constant test - mutating it plants nothing."""
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


# ── tau_1: off_by_one ─────────────────────────────────────────────────────

def _off_by_one_edits(tree: ast.AST, data: bytes, starts: list[int]) -> list[_Edit]:
    edits: list[_Edit] = []

    def add(rank, node, new_text, note):
        span = _span(starts, node)
        if span:
            edits.append(_Edit(rank, span[0], span[1], new_text, note, _site(node)))

    for node in ast.walk(tree):
        # range(...) - the loop bound. The single most common home for a
        # boundary error in competitive code.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "range" and node.args and not node.keywords):
            bound = node.args[-1]
            span = _span(starts, bound)
            if span:
                add(0, bound, f"({_text(data, span)}) - 1",
                    "narrows a range() bound by one, dropping the last iteration")

        # Slice bounds: a[:k] loses an element, a[i:] skips one.
        elif isinstance(node, ast.Slice):
            if node.upper is not None:
                span = _span(starts, node.upper)
                if span:
                    add(1, node.upper, f"({_text(data, span)}) - 1",
                        "narrows a slice's upper bound by one")
            elif node.lower is not None:
                span = _span(starts, node.lower)
                if span:
                    add(1, node.lower, f"({_text(data, span)}) + 1",
                        "advances a slice's lower bound by one")

        # Accumulator step: total += x becomes total += x + 1.
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, (ast.Add, ast.Sub)):
            span = _span(starts, node.value)
            if span:
                add(2, node.value, f"({_text(data, span)}) + 1",
                    "inflates an accumulator's step by one on every update")

        # An integer literal already taking part in index arithmetic.
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            for operand in (node.left, node.right):
                if isinstance(operand, ast.Constant) and isinstance(operand.value, int) \
                        and not isinstance(operand.value, bool):
                    add(3, operand, str(operand.value + 1),
                        f"shifts the constant {operand.value} in an index expression by one")

        # Last resort: shift a subscript index.
        elif isinstance(node, ast.Subscript) and not isinstance(node.slice, ast.Slice):
            span = _span(starts, node.slice)
            if span:
                add(4, node.slice, f"({_text(data, span)}) - 1",
                    "shifts a subscript index down by one")

    return edits


# ── tau_2: wrong_comparison ───────────────────────────────────────────────
# Comparison operators carry no position of their own in the AST, so the
# operator token is found by regex inside the gap between the left operand's
# end and the right operand's start. That gap holds only whitespace, closing
# and opening parens, line continuations and the operator itself - never a
# string literal - so a token-level regex is safe there.

_COMPARISON_SWAPS: dict[type, tuple[str, str, int, str]] = {
    # ast op type: (regex for the token, replacement, rank, note)
    ast.Lt:    (r"<(?![=>])",            "<=", 0, "relaxes a strict `<` to `<=`, admitting the boundary case"),
    ast.Gt:    (r">(?![=])",             ">=", 0, "relaxes a strict `>` to `>=`, admitting the boundary case"),
    ast.LtE:   (r"<=",                   "<",  0, "tightens `<=` to `<`, excluding the boundary case"),
    ast.GtE:   (r">=",                   ">",  0, "tightens `>=` to `>`, excluding the boundary case"),
    ast.Eq:    (r"==",                   "!=", 1, "inverts an equality test"),
    ast.NotEq: (r"!=",                   "==", 1, "inverts an inequality test"),
    ast.In:    (r"\bin\b",               "not in", 2, "inverts a membership test"),
    ast.NotIn: (r"\bnot\s+in\b",         "in", 2, "inverts a negated membership test"),
    ast.Is:    (r"\bis\b(?!\s+not\b)",   "is not", 3, "inverts an identity test"),
    ast.IsNot: (r"\bis\s+not\b",         "is", 3, "inverts a negated identity test"),
}


def _wrong_comparison_edits(tree: ast.AST, data: bytes, starts: list[int]) -> list[_Edit]:
    edits: list[_Edit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        # Chained comparisons (`0 <= i < n`, idiomatic in contest code and too
        # common to skip) hold one operator per adjacent operand pair, so each
        # op is located in the gap between the operands that bracket it.
        operands = [node.left, *node.comparators]
        for i, op in enumerate(node.ops):
            swap = _COMPARISON_SWAPS.get(type(op))
            if swap is None:
                continue
            pattern, replacement, rank, note = swap
            left = _span(starts, operands[i])
            right = _span(starts, operands[i + 1])
            if not left or not right or right[0] <= left[1]:
                continue
            gap = data[left[1]:right[0]].decode("utf-8", "replace")
            match = re.search(pattern, gap)
            if match is None:
                continue
            start = left[1] + len(gap[:match.start()].encode("utf-8"))
            end = left[1] + len(gap[:match.end()].encode("utf-8"))
            edits.append(_Edit(rank, start, end, replacement, note, _site(node)))
    return edits


# ── tau_3: missing_guard ──────────────────────────────────────────────────

def _missing_guard_edits(tree: ast.AST, data: bytes, starts: list[int]) -> list[_Edit]:
    edits: list[_Edit] = []

    def add(rank, node, new_text, note):
        span = _span(starts, node.test)
        if span and not _is_dead(node.test):
            edits.append(_Edit(rank, span[0], span[1], new_text, note, _site(node)))

    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            if _is_main_guard(node):
                continue
            early_exit = (
                len(node.body) == 1
                and isinstance(node.body[0], (ast.Return, ast.Continue, ast.Break, ast.Raise))
            )
            if early_exit and not node.orelse:
                add(0, node, "False",
                    "neutralises an early-exit guard, so the special case falls "
                    "through into the general path")
            elif not node.orelse:
                add(1, node, "False", "neutralises a guard, skipping its special-case handling")
            else:
                add(2, node, "True",
                    "forces a guarded branch to always be taken, so its else-case never runs")
        elif isinstance(node, ast.IfExp) and not _is_dead(node.test):
            span = _span(starts, node.test)
            if span:
                edits.append(_Edit(
                    3, span[0], span[1], "False",
                    "collapses a conditional expression onto its else-branch", _site(node),
                ))
        elif isinstance(node, ast.While) and not _is_dead(node.test):
            # Last resort, and only in the False direction: `while True:` would
            # hand the sandbox a program it has to kill on every single case,
            # which costs the whole timeout per run and types as Timeout rather
            # than as the wrong answer the guard was protecting against.
            span = _span(starts, node.test)
            if span:
                edits.append(_Edit(
                    4, span[0], span[1], "False",
                    "neutralises a loop's entry condition, so its body never runs", _site(node),
                ))
    return edits


_GENERATORS = {
    "off_by_one": _off_by_one_edits,
    "wrong_comparison": _wrong_comparison_edits,
    "missing_guard": _missing_guard_edits,
}


# ── hand-written overrides ────────────────────────────────────────────────
# name -> fault_type -> (find, replace, note), where `find` must occur exactly
# once in the correct source. Takes precedence over the generated site. Use
# this when a generated mutant is degenerate for a particular program and a
# human wants to plant a better one; every entry should say why in its note.

MUTANT_OVERRIDES: dict[str, dict[str, tuple[str, str, str]]] = {}


def _apply_override(task_name: str, correct_source: str, fault_type: str) -> Mutant | None:
    entry = MUTANT_OVERRIDES.get(task_name, {}).get(fault_type)
    if entry is None:
        return None
    find, replace, note = entry
    count = correct_source.count(find)
    if count != 1:
        raise ValueError(
            f"{task_name}/{fault_type}: expected exactly one occurrence of {find!r}, found {count}"
        )
    mutated = correct_source.replace(find, replace)
    return Mutant(
        task=task_name, fault_type=fault_type, note=note, source=mutated,
        site="override", diff=unified_diff(correct_source, mutated),
    )


# ── public API ────────────────────────────────────────────────────────────

def unified_diff(before: str, after: str, *, context: int = 1) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="correctVersion.py", tofile="mutant.py", n=context,
    ))


def candidate_mutants(task_name: str, correct_source: str, fault_type: str) -> list[Mutant]:
    """Every mutant of one fault type this program admits, most-preferred first.

    Ordering is (operator rank, position in the file), so it is a pure function
    of the source: re-running the validator reproduces the same mutants.
    """
    if fault_type not in FAULT_TYPES:
        raise ValueError(f"unknown fault type {fault_type!r}; expected one of {FAULT_TYPES}")

    override = _apply_override(task_name, correct_source, fault_type)
    if override is not None:
        return [override]

    try:
        tree = ast.parse(correct_source)
    except SyntaxError:
        return []

    data = correct_source.encode("utf-8")
    starts = _line_starts(data)
    edits = sorted(_GENERATORS[fault_type](tree, data, starts), key=lambda e: (e.rank, e.start))

    mutants, seen = [], set()
    for edit in edits:
        mutated_bytes = data[:edit.start] + edit.text.encode("utf-8") + data[edit.end:]
        try:
            mutated = mutated_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if mutated == correct_source or mutated in seen:
            continue
        try:
            compile(mutated, "<mutant>", "exec")
        except SyntaxError:
            continue        # e.g. an edit inside an f-string expression
        seen.add(mutated)
        mutants.append(Mutant(
            task=task_name, fault_type=fault_type, note=edit.note, source=mutated,
            site=edit.site, diff=unified_diff(correct_source, mutated),
        ))
    return mutants


def build_mutants(task_name: str, correct_source: str, *, attempt: int = 0) -> list[Mutant]:
    """One mutant per fault type - the `attempt`-th candidate of each.

    Returns fewer than three when a program admits no site for some fault type
    (a program with no comparison in it has no tau_2 mutant); the caller
    records that rather than pretending the mutant existed and was missed.
    """
    out = []
    for fault_type in FAULT_TYPES:
        candidates = candidate_mutants(task_name, correct_source, fault_type)
        if attempt < len(candidates):
            out.append(candidates[attempt])
    return out


def mutant_capacity(task_name: str, correct_source: str) -> dict[str, int]:
    """How many candidate sites each fault type has - the retry budget."""
    return {
        fault_type: len(candidate_mutants(task_name, correct_source, fault_type))
        for fault_type in FAULT_TYPES
    }


if __name__ == "__main__":
    import collections
    import json
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    from src.adapter import SUPPORTED_PROGRAMS, load

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - see scripts/fetch_condefects.py")

    tasks_json = pathlib.Path(__file__).resolve().parent / "tasks.json"
    names = None
    if tasks_json.exists():
        frozen = json.loads(tasks_json.read_text())
        names = [t["name"] for t in frozen.get("tasks", []) if t["name"] in SUPPORTED_PROGRAMS]
    names = names or list(SUPPORTED_PROGRAMS[:40])

    totals: collections.Counter[str] = collections.Counter()
    complete = 0
    for name in names:
        capacity = mutant_capacity(name, load(name).correct_source)
        for fault_type, n in capacity.items():
            totals[fault_type] += bool(n)
        complete += all(capacity.values())
        missing = [f for f, n in capacity.items() if not n]
        if missing:
            print(f"{name:34s} no site for: {', '.join(missing)}")

    print(f"\n{complete}/{len(names)} programs admit all three fault types")
    for fault_type in FAULT_TYPES:
        print(f"  {fault_type:18s} {totals[fault_type]:4d}/{len(names)} programs")
