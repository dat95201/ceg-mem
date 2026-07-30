"""A3 - load a QuixBugs Python program and generate inputs for it.

Covers all 40 QuixBugs Python tasks. Each ships a Hypothesis strategy that
draws a JSON-safe args list, because args cross into the sandbox subprocess
(src/sandbox.py) as JSON over stdin - no live Python objects survive that
trip.

31 tasks take only ints/floats/strings/lists, so their strategy's output is
handed straight to the task's own top-level function. The other 9
(breadth_first_search, depth_first_search, detect_cycle, reverse_linked_list,
shortest_path_length, topological_ordering, minimum_spanning_tree,
shortest_path_lengths, shortest_paths - QuixBugs' own `graph_based` list, see
external/QuixBugs/tester.py) need either a `Node` object graph or a dict
keyed by tuples, neither of which JSON can carry. For those, the strategy
instead draws a serializable spec (adjacency lists / edge triples), and a
small `__ceg_entry__` wrapper - appended to the program source as `harness`,
never shipped to a model - rebuilds the real objects inside the sandboxed
subprocess, calls the task function, and flattens the result back to JSON.

Hypothesis-driven differential testing against the reference implementation
lives in src/oracle.py; this module only produces (task, strategy, harness).
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any, Callable

from dotenv import load_dotenv
from hypothesis import strategies as st

from src.sandbox import DEFAULT_TIMEOUT, Outcome, run_call

load_dotenv()

QUIXBUGS_ROOT = pathlib.Path(os.environ.get("QUIXBUGS_ROOT", "external/QuixBugs"))
BUGGY_DIR = QUIXBUGS_ROOT / "python_programs"
CORRECT_DIR = QUIXBUGS_ROOT / "correct_python_programs"
TESTCASE_DIR = QUIXBUGS_ROOT / "json_testcases"

SearchStrategy = st.SearchStrategy


@dataclasses.dataclass(frozen=True)
class Task:
    """Everything the oracle needs to run one QuixBugs program on live input."""

    name: str
    entry_point: str          # function name run_call should invoke
    harness: str              # source appended after the program; "" if none
    strategy: SearchStrategy  # draws a JSON-safe args list for entry_point


# ── Node harness ──────────────────────────────────────────────────────────
# Same shape as external/QuixBugs/python_testcases/node.py, but with None
# defaults instead of mutable-default-argument lists: every harness below
# builds many Node()s per call, and QuixBugs' shared-list default would alias
# successors/predecessors across all of them.
_NODE_CLASS = """
class Node:
    def __init__(self, value=None, successor=None, successors=None,
                 predecessors=None, incoming_nodes=None, outgoing_nodes=None):
        self.value = value
        self.successor = successor
        self.successors = list(successors) if successors else []
        self.predecessors = list(predecessors) if predecessors else []
        self.incoming_nodes = list(incoming_nodes) if incoming_nodes else []
        self.outgoing_nodes = list(outgoing_nodes) if outgoing_nodes else []
"""

_BFS_HARNESS = _NODE_CLASS + """
def __ceg_entry__(adj, start_idx, goal_idx):
    nodes = [Node(i) for i in range(len(adj))]
    for i, succs in enumerate(adj):
        nodes[i].successors = [nodes[j] for j in succs]
    return breadth_first_search(nodes[start_idx], nodes[goal_idx])
"""

_DFS_HARNESS = _NODE_CLASS + """
def __ceg_entry__(adj, start_idx, goal_idx):
    nodes = [Node(i) for i in range(len(adj))]
    for i, succs in enumerate(adj):
        nodes[i].successors = [nodes[j] for j in succs]
    return depth_first_search(nodes[start_idx], nodes[goal_idx])
"""

_DETECT_CYCLE_HARNESS = _NODE_CLASS + """
def __ceg_entry__(values, cycle_to):
    nodes = [Node(v) for v in values]
    for i in range(len(nodes) - 1):
        nodes[i].successor = nodes[i + 1]
    if nodes and cycle_to is not None:
        nodes[-1].successor = nodes[cycle_to]
    return detect_cycle(nodes[0])
"""

_REVERSE_LINKED_LIST_HARNESS = _NODE_CLASS + """
def __ceg_entry__(values):
    nodes = [Node(v) for v in values]
    for i in range(len(nodes) - 1):
        nodes[i].successor = nodes[i + 1]
    head = nodes[0] if nodes else None
    result = reverse_linked_list(head)
    out = []
    seen = 0
    while result is not None and seen <= len(values) + 1:
        out.append(result.value)
        result = result.successor
        seen += 1
    return out
"""

_SHORTEST_PATH_LENGTH_HARNESS = _NODE_CLASS + """
def __ceg_entry__(n, edges, start_idx, goal_idx):
    nodes = [Node(i) for i in range(n)]
    adj = {i: [] for i in range(n)}
    for a, b, _w in edges:
        adj[a].append(b)
    for i in range(n):
        nodes[i].successors = [nodes[j] for j in adj[i]]
    length_by_edge = {(nodes[a], nodes[b]): w for a, b, w in edges}
    return shortest_path_length(length_by_edge, nodes[start_idx], nodes[goal_idx])
"""

_TOPOLOGICAL_ORDERING_HARNESS = _NODE_CLASS + """
def __ceg_entry__(n, edges):
    nodes = [Node(i) for i in range(n)]
    for a, b in edges:
        nodes[a].outgoing_nodes.append(nodes[b])
        nodes[b].incoming_nodes.append(nodes[a])
    result = topological_ordering(nodes)
    return [node.value for node in result]
"""

# These three are QuixBugs "graph_based" too, but their signature is a dict
# keyed by (node, node) tuples where a node is any hashable - no Node object
# needed, just a wrapper to get past "JSON object keys must be strings".
_MINIMUM_SPANNING_TREE_HARNESS = """
def __ceg_entry__(edges):
    weight_by_edge = {(a, b): w for a, b, w in edges}
    result = minimum_spanning_tree(weight_by_edge)
    return sorted(list(e) for e in result)
"""

_SHORTEST_PATH_LENGTHS_HARNESS = """
def __ceg_entry__(n, edges):
    weight_by_edge = {(a, b): w for a, b, w in edges}
    result = shortest_path_lengths(n, weight_by_edge)
    return sorted([a, b, w] for (a, b), w in result.items())
"""

_SHORTEST_PATHS_HARNESS = """
def __ceg_entry__(source, edges):
    weight_by_edge = {(a, b): w for a, b, w in edges}
    result = shortest_paths(source, weight_by_edge)
    return sorted([k, v] for k, v in result.items())
"""


# ── Strategies: 31 flat tasks ─────────────────────────────────────────────
_ints = lambda lo, hi: st.integers(min_value=lo, max_value=hi)
_small_ints = _ints(-100, 100)


def _sorted_list_and_x():
    arr = st.lists(_ints(-200, 200), max_size=30).map(sorted)
    return arr.flatmap(lambda a: st.tuples(st.just(a), _ints(-200, 200))).map(list)


def _bucketsort_args():
    return _ints(1, 30).flatmap(
        lambda k: st.tuples(st.lists(_ints(0, k - 1), max_size=30), st.just(k))
    ).map(list)


def _flatten_args():
    leaves = _ints(-50, 50)
    nested = st.recursive(leaves, lambda children: st.lists(children, max_size=5), max_leaves=20)
    return st.lists(nested, max_size=8).map(lambda arr: [arr])


def _kth_args():
    return st.lists(_ints(-50, 50), min_size=1, max_size=15).flatmap(
        lambda arr: st.tuples(st.just(arr), _ints(0, len(arr) - 1))
    ).map(list)


def _knapsack_args():
    items = st.lists(st.tuples(_ints(1, 15), _ints(0, 50)), max_size=8)
    return st.tuples(_ints(0, 30), items).map(list)


def _next_permutation_args():
    return _ints(0, 7).flatmap(lambda n: st.permutations(list(range(n)))).map(lambda perm: [perm])


def _possible_change_args():
    coins = st.lists(_ints(1, 25), min_size=0, max_size=4)
    return st.tuples(coins, _ints(0, 20)).map(list)


def _rpn_eval_args():
    floats = st.floats(min_value=1, max_value=50, allow_nan=False, allow_infinity=False).map(lambda x: round(x, 2))
    ops = st.sampled_from(["+", "-", "*"])

    def _combine(children):
        return st.one_of(
            floats.map(lambda v: [v]),
            st.tuples(children, children, ops).map(lambda t: t[0] + t[1] + [t[2]]),
        )

    expr = st.recursive(floats.map(lambda v: [v]), _combine, max_leaves=6)
    return expr.map(lambda toks: [toks])


def _shunting_yard_args():
    tokens = st.one_of(_ints(-20, 20), st.sampled_from(["+", "-", "*", "/"]))
    return st.lists(tokens, max_size=20).map(lambda toks: [toks])


def _subsequences_args():
    return _ints(0, 10).flatmap(
        lambda a: _ints(a, a + 8).flatmap(
            lambda b: st.tuples(st.just(a), st.just(b), _ints(0, b - a + 1))
        )
    ).map(list)


_FLAT_STRATEGIES: dict[str, SearchStrategy] = {
    "bitcount": _ints(0, 10_000).map(lambda n: [n]),
    "bucketsort": _bucketsort_args(),
    "find_first_in_sorted": _sorted_list_and_x(),
    "find_in_sorted": _sorted_list_and_x(),
    "flatten": _flatten_args(),
    "gcd": st.tuples(_ints(0, 1000), _ints(0, 1000)).map(list),
    "get_factors": _ints(1, 10_000).map(lambda n: [n]),
    "hanoi": _ints(0, 10).map(lambda h: [h]),
    "is_valid_parenthesization": st.text(alphabet="()", max_size=30).map(lambda s: [s]),
    "kheapsort": st.tuples(st.lists(_ints(-100, 100), max_size=25), _ints(0, 20)).map(list),
    "knapsack": _knapsack_args(),
    "kth": _kth_args(),
    "lcs_length": st.tuples(
        st.text(alphabet="abcd", max_size=15), st.text(alphabet="abcd", max_size=15)
    ).map(list),
    # Both unbounded-recursion, no-memo implementations: exponential in
    # string length, so keep these short or the sandbox timeout dominates.
    "levenshtein": st.tuples(
        st.text(alphabet="ab", max_size=7), st.text(alphabet="ab", max_size=7)
    ).map(list),
    "lis": st.lists(_ints(-50, 50), max_size=20).map(lambda arr: [arr]),
    "longest_common_subsequence": st.tuples(
        st.text(alphabet="ab", max_size=7), st.text(alphabet="ab", max_size=7)
    ).map(list),
    "max_sublist_sum": st.lists(_ints(-100, 100), max_size=30).map(lambda arr: [arr]),
    "mergesort": st.lists(_small_ints, max_size=30).map(lambda arr: [arr]),
    "next_palindrome": st.lists(_ints(0, 9), min_size=1, max_size=10).map(lambda ds: [ds]),
    "next_permutation": _next_permutation_args(),
    "pascal": _ints(0, 15).map(lambda n: [n]),
    "possible_change": _possible_change_args(),
    "powerset": st.lists(_ints(-20, 20), max_size=10).map(lambda arr: [arr]),
    "quicksort": st.lists(_small_ints, max_size=30).map(lambda arr: [arr]),
    "rpn_eval": _rpn_eval_args(),
    "shunting_yard": _shunting_yard_args(),
    "sieve": _ints(0, 100).map(lambda n: [n]),
    "sqrt": st.tuples(
        st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
        st.floats(min_value=1e-3, max_value=1.0, allow_nan=False, allow_infinity=False),
    ).map(list),
    "subsequences": _subsequences_args(),
    "to_base": st.tuples(_ints(1, 100_000), _ints(2, 36)).map(list),
    "wrap": st.tuples(
        st.text(alphabet="abcde ", max_size=60), _ints(1, 20)
    ).map(list),
}


# ── Strategies: 9 graph/linked-list tasks (need a harness) ────────────────
def _adjacency_args(max_nodes: int = 8):
    return _ints(1, max_nodes).flatmap(lambda n: st.tuples(
        st.lists(st.lists(_ints(0, n - 1), max_size=n, unique=True), min_size=n, max_size=n),
        _ints(0, n - 1),
        _ints(0, n - 1),
    )).map(list)


def _detect_cycle_args():
    return _ints(1, 8).flatmap(lambda n: st.tuples(
        st.just(list(range(n))), st.one_of(st.none(), _ints(0, n - 1))
    )).map(list)


def _weighted_edges(n_lo, n_hi, weight_lo, weight_hi, *, allow_self=False):
    def edges_for(n):
        edge = st.tuples(_ints(0, n - 1), _ints(0, n - 1), _ints(weight_lo, weight_hi))
        if not allow_self:
            edge = edge.filter(lambda e: e[0] != e[1])
        return st.lists(edge, max_size=n * 2, unique_by=lambda e: (e[0], e[1]))
    return _ints(n_lo, n_hi), edges_for


def _shortest_path_length_args():
    n_strategy, edges_for = _weighted_edges(1, 7, 1, 20)
    return n_strategy.flatmap(lambda n: st.tuples(
        st.just(n), edges_for(n), _ints(0, n - 1), _ints(0, n - 1)
    )).map(list)


def _topological_ordering_args():
    return _ints(1, 7).flatmap(lambda n: st.tuples(
        st.just(n),
        st.lists(
            st.tuples(_ints(0, n - 1), _ints(0, n - 1)).filter(lambda e: e[0] != e[1]),
            max_size=n * 2, unique=True,
        ),
    )).map(list)


def _minimum_spanning_tree_args():
    return _ints(2, 7).flatmap(lambda n: st.lists(
        st.tuples(_ints(0, n - 1), _ints(0, n - 1), _ints(1, 20)).filter(lambda e: e[0] < e[1]),
        max_size=n * (n - 1) // 2, unique_by=lambda e: (e[0], e[1]),
    )).map(lambda edges: [edges])


def _shortest_path_lengths_args():
    n_strategy, edges_for = _weighted_edges(1, 6, -10, 20)
    return n_strategy.flatmap(lambda n: st.tuples(st.just(n), edges_for(n))).map(list)


def _shortest_paths_args():
    n_strategy, edges_for = _weighted_edges(2, 6, -10, 20)
    return n_strategy.flatmap(lambda n: st.tuples(_ints(0, n - 1), edges_for(n))).map(list)


_GRAPH_TASKS: dict[str, tuple[str, SearchStrategy]] = {
    "breadth_first_search": (_BFS_HARNESS, _adjacency_args()),
    "depth_first_search": (_DFS_HARNESS, _adjacency_args()),
    "detect_cycle": (_DETECT_CYCLE_HARNESS, _detect_cycle_args()),
    "reverse_linked_list": (
        _REVERSE_LINKED_LIST_HARNESS,
        st.lists(_ints(-50, 50), max_size=10).map(lambda values: [values]),
    ),
    "shortest_path_length": (_SHORTEST_PATH_LENGTH_HARNESS, _shortest_path_length_args()),
    "topological_ordering": (_TOPOLOGICAL_ORDERING_HARNESS, _topological_ordering_args()),
    "minimum_spanning_tree": (_MINIMUM_SPANNING_TREE_HARNESS, _minimum_spanning_tree_args()),
    "shortest_path_lengths": (_SHORTEST_PATH_LENGTHS_HARNESS, _shortest_path_lengths_args()),
    "shortest_paths": (_SHORTEST_PATHS_HARNESS, _shortest_paths_args()),
}


def _build_tasks() -> dict[str, Task]:
    tasks = {
        name: Task(name=name, entry_point=name, harness="", strategy=strategy)
        for name, strategy in _FLAT_STRATEGIES.items()
    }
    for name, (harness, strategy) in _GRAPH_TASKS.items():
        tasks[name] = Task(name=name, entry_point="__ceg_entry__", harness=harness, strategy=strategy)
    return tasks


TASKS: dict[str, Task] = _build_tasks()
SUPPORTED_PROGRAMS: tuple[str, ...] = tuple(sorted(TASKS))


@dataclasses.dataclass(frozen=True)
class QuixBugsProgram:
    name: str
    buggy_source: str
    correct_source: str

    def testcases(self) -> list[tuple[list, Any]]:
        """Shipped `[args, expected]` rows, args always normalised to a list.

        Only the 31 non-graph tasks ship one of these files (see
        external/QuixBugs/json_testcases); the other 9 have none.
        """
        path = TESTCASE_DIR / f"{self.name}.json"
        if not path.exists():
            return []
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
    if name not in TASKS:
        raise ValueError(f"{name!r} not in TASKS {SUPPORTED_PROGRAMS}")
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
    task = TASKS[program.name]
    return run_call(program.buggy_source + task.harness, task.entry_point, args, timeout=timeout)


def run_reference(program: QuixBugsProgram, args: list, *, timeout: float = DEFAULT_TIMEOUT) -> Outcome:
    task = TASKS[program.name]
    return run_call(program.correct_source + task.harness, task.entry_point, args, timeout=timeout)


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
    literal expected value QuixBugs ships). Tasks with no shipped testcases
    (the 9 graph/linked-list ones) yield an empty list.
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
        if not cases:
            print(f"{name}: no shipped json_testcases (graph/linked-list task)")
            continue
        n_pass = sum(c.passed for c in cases)
        print(f"{name}: {n_pass}/{len(cases)} testcases pass on the buggy version")
        for c in cases:
            if not c.passed:
                reason = c.outcome.error_message if not c.outcome.ok else f"got {c.outcome.value!r}"
                print(f"    FAIL args={c.args} expected={c.expected!r}: {reason}")
