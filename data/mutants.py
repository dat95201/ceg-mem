"""Hand-authored mutants: 3 per task, one per fault type from proposal
section 3.5's illustrative episode (main_proposal.pdf):

  off_by_one       - tau_1: boundary arithmetic error (loop/array bound,
                     index arithmetic, an accumulator's step size)
  wrong_comparison - tau_2: wrong relational/equality/membership operator,
                     or swapped operands of a non-commutative operator
  missing_guard    - tau_3: an edge-case guard (empty/None/visited/zero)
                     is omitted or trivialised, so the correct answer is
                     produced everywhere except that edge case

Each mutant is a single targeted text edit against the *correct*
(external/QuixBugs/correct_python_programs) source, not the shipped buggy
version - the buggy version already carries QuixBugs' own single bug, which
is a separate thing from these self-written, categorised mutants. Applying
a mutant is a plain, asserted-unique substring replace: if the anchor text
ever drifts (QuixBugs updates, a typo), `_mutate` raises immediately instead
of silently mutating the wrong spot or a stray duplicate.
"""
from __future__ import annotations

import dataclasses

FAULT_TYPES = ("off_by_one", "wrong_comparison", "missing_guard")


def _mutate(source: str, find: str, replace: str) -> str:
    count = source.count(find)
    if count != 1:
        raise ValueError(f"expected exactly one occurrence of {find!r}, found {count}")
    return source.replace(find, replace)


def _strip_trailing_docstring(source: str) -> str:
    """Drop a trailing module-level triple-quoted block.

    19 of the 40 correct_python_programs files append one after the real
    function: either a plain description, or QuixBugs' own commented-out
    alternate-but-equivalent implementations. Either way it is inert text
    that would otherwise duplicate the very substrings `_mutate` needs to
    find exactly once (e.g. breadth_first_search's "queue.popleft()").
    """
    marker = source.find('\n"""')
    return source if marker == -1 else source[:marker]


@dataclasses.dataclass(frozen=True)
class Mutant:
    task: str
    fault_type: str
    note: str
    source: str


# name -> fault_type -> (find, replace, note)
MUTANT_EDITS: dict[str, dict[str, tuple[str, str, str]]] = {
    "bitcount": {
        "off_by_one": ("count += 1", "count += 2",
                       "double-counts every cleared bit instead of counting it once"),
        "wrong_comparison": ("while n:", "while n > 1:",
                             "stops one iteration early, undercounting the last bit"),
        "missing_guard": ("while n:", "while True:",
                          "drops the loop-termination guard (hangs once n hits 0)"),
    },
    "breadth_first_search": {
        "off_by_one": ("queue.popleft()", "queue.pop()",
                       "pops from the wrong end, turning BFS order into DFS order"),
        "wrong_comparison": ("if node is goalnode:", "if node is not goalnode:",
                             "inverts the goal-match check"),
        "missing_guard": ("if node not in nodesseen)", "if True)",
                          "drops the seen-node dedup guard"),
    },
    "depth_first_search": {
        "off_by_one": ("for nextnode in node.successors", "for nextnode in node.successors[:-1]",
                       "skips the last successor of every node"),
        "wrong_comparison": ("elif node is goalnode:", "elif node is not goalnode:",
                             "inverts the goal-match check"),
        "missing_guard": ("if node in nodesvisited:", "if False:",
                          "drops the visited guard (infinite recursion on any cycle)"),
    },
    "bucketsort": {
        "off_by_one": ("counts = [0] * k", "counts = [0] * (k - 1)",
                       "undersizes the count array by one bucket"),
        "wrong_comparison": ("counts[x] += 1", "counts[x] += (1 if x < k - 1 else 0)",
                             "silently drops counts for the last bucket"),
        "missing_guard": ("for i, count in enumerate(counts):", "for i, count in enumerate(counts[:-1]):",
                          "drops the last bucket from the output entirely"),
    },
    "detect_cycle": {
        "off_by_one": ("hare = hare.successor.successor", "hare = hare.successor",
                       "hare no longer advances at twice tortoise's pace"),
        "wrong_comparison": ("if hare is tortoise:", "if hare is not tortoise:",
                             "inverts the cycle-detection check"),
        "missing_guard": ("if hare is None or hare.successor is None:", "if False:",
                          "drops the null-successor guard (crashes on any acyclic list)"),
    },
    "find_first_in_sorted": {
        "off_by_one": ("hi = mid", "hi = mid - 1",
                       "narrows one index too far, can skip the target"),
        "wrong_comparison": ("elif x <= arr[mid]:", "elif x < arr[mid]:",
                             "mishandles the exact-match boundary"),
        "missing_guard": ("if x == arr[mid] and (mid == 0 or x != arr[mid - 1]):", "if x == arr[mid]:",
                          "drops the first-occurrence guard, may return a later duplicate"),
    },
    "find_in_sorted": {
        "off_by_one": ("return binsearch(mid + 1, end)", "return binsearch(mid, end)",
                       "drops the +1 advance, can recurse without shrinking"),
        "wrong_comparison": ("if x < arr[mid]:", "if x <= arr[mid]:",
                             "misroutes exact matches into the left half"),
        "missing_guard": ("if start == end:", "if False:",
                          "drops the empty-range base case (infinite recursion)"),
    },
    "flatten": {
        "off_by_one": ("for x in arr:", "for x in arr[:-1]:",
                       "drops the last top-level element"),
        "wrong_comparison": ("if isinstance(x, list):", "if not isinstance(x, list):",
                             "inverts which elements get recursed into"),
        "missing_guard": ("if isinstance(x, list):", "if False:",
                          "never recurses into nested lists"),
    },
    "gcd": {
        "off_by_one": ("return gcd(b, a % b)", "return gcd(b, (a % b) + 1)",
                       "adds one to the remainder passed down each step"),
        "wrong_comparison": ("if b == 0:", "if b != 0:",
                             "inverts the base case"),
        "missing_guard": ("if b == 0:", "if False:",
                          "drops the base case (infinite recursion for every input)"),
    },
    "get_factors": {
        "off_by_one": ("range(2, int(n ** 0.5) + 1)", "range(2, int(n ** 0.5))",
                       "never tests the exact integer-sqrt factor"),
        "wrong_comparison": ("if n % i == 0:", "if n % i != 0:",
                             "inverts the divisibility check"),
        "missing_guard": ("if n == 1:", "if False:",
                          "drops the n==1 base case"),
    },
    "hanoi": {
        "off_by_one": ("steps.extend(hanoi(height - 1, start, helper))",
                       "steps.extend(hanoi(height - 2, start, helper))",
                       "skips a disk level on the first recursive call"),
        "wrong_comparison": ("if height > 0:", "if height > 1:",
                             "drops the single-disk base move"),
        "missing_guard": ("if height > 0:", "if True:",
                          "drops the recursion base case (infinite recursion)"),
    },
    "is_valid_parenthesization": {
        "off_by_one": ("depth -= 1", "depth -= 2",
                       "miscounts each closing paren by one extra"),
        "wrong_comparison": ("if depth < 0:", "if depth <= 0:",
                             "flags balanced prefixes like '()' as invalid"),
        "missing_guard": ("if depth < 0:", "if False:",
                          "drops the negative-depth guard, accepts e.g. ')('"),
    },
    "kheapsort": {
        "off_by_one": ("heap = arr[:k]", "heap = arr[:k - 1]",
                       "undersizes the initial heap window by one"),
        "wrong_comparison": ("while heap:", "while len(heap) > 1:",
                             "leaves the last element undrained"),
        "missing_guard": ("while heap:", "while True:",
                          "drops the drain-loop termination guard"),
    },
    "knapsack": {
        "off_by_one": ("for j in range(1, capacity + 1):", "for j in range(1, capacity):",
                       "never fills the exact-capacity column"),
        "wrong_comparison": ("if weight <= j:", "if weight < j:",
                             "excludes items that exactly fit the remaining capacity"),
        "missing_guard": ("if weight <= j:", "if True:",
                          "drops the fits-in-capacity guard"),
    },
    "kth": {
        "off_by_one": ("return kth(above, k - num_lessoreq)", "return kth(above, k - num_lessoreq - 1)",
                       "shifts the recursive rank index by one"),
        "wrong_comparison": ("elif k >= num_lessoreq:", "elif k > num_lessoreq:",
                             "mishandles the exact rank boundary"),
        "missing_guard": ("if k < num_less:", "if False:",
                          "never recurses into the below-pivot partition"),
    },
    "lcs_length": {
        "off_by_one": ("dp[i, j] = dp[i - 1, j - 1] + 1", "dp[i, j] = dp[i - 1, j - 1] + 2",
                       "doubles the length increment on every match"),
        "wrong_comparison": ("if s[i] == t[j]:", "if s[i] != t[j]:",
                             "inverts the character-match check"),
        "missing_guard": ("return max(dp.values()) if dp else 0", "return max(dp.values())",
                          "drops the empty-input guard (crashes instead of returning 0)"),
    },
    "levenshtein": {
        "off_by_one": ("return 1 + min(", "return 2 + min(",
                       "overcounts every edit step by one"),
        "wrong_comparison": ("elif source[0] == target[0]:", "elif source[0] != target[0]:",
                             "inverts the matching-prefix shortcut"),
        "missing_guard": ("if source == '' or target == '':", "if False:",
                          "drops the empty-string base case (infinite recursion)"),
    },
    "lis": {
        "off_by_one": ("range(1, longest + 1)", "range(1, longest)",
                       "never checks the current longest-known chain"),
        "wrong_comparison": ("if arr[ends[j]] < val]", "if arr[ends[j]] <= val]",
                             "lets equal values extend a chain, overcounting with duplicates"),
        "missing_guard": ("if length == longest or val < arr[ends[length + 1]]:", "if length == longest:",
                          "drops the smaller-tail chain-improvement branch"),
    },
    "longest_common_subsequence": {
        "off_by_one": ("return a[0] + longest_common_subsequence(a[1:], b[1:])",
                       "return a[0] + longest_common_subsequence(a[2:], b[1:])",
                       "skips an extra character of `a` on every match"),
        "wrong_comparison": ("elif a[0] == b[0]:", "elif a[0] != b[0]:",
                             "inverts the matching-prefix check"),
        "missing_guard": ("if not a or not b:", "if False:",
                          "drops the empty-sequence base case (infinite recursion)"),
    },
    "max_sublist_sum": {
        "off_by_one": ("for x in arr:", "for x in arr[:-1]:",
                       "ignores the last element of the list"),
        "wrong_comparison": ("max_ending_here = max(0, max_ending_here + x)",
                             "max_ending_here = max(-1, max_ending_here + x)",
                             "changes the reset floor from 0, corrupting sums near zero"),
        "missing_guard": ("max_so_far = max(max_so_far, max_ending_here)", "max_so_far = max_ending_here",
                          "drops the running-best guard, forgets prior maxima"),
    },
    "mergesort": {
        "off_by_one": ("middle = len(arr) // 2", "middle = len(arr) // 2 + 1",
                       "shifts the split point, can recurse without shrinking"),
        "wrong_comparison": ("while i < len(left) and j < len(right):", "while i < len(left) or j < len(right):",
                             "keeps indexing past the end of the shorter half"),
        "missing_guard": ("if len(arr) <= 1:", "if False:",
                          "drops the base case (infinite recursion on empty input)"),
    },
    "minimum_spanning_tree": {
        "off_by_one": ("for edge in sorted(weight_by_edge, key=weight_by_edge.__getitem__):",
                       "for edge in sorted(weight_by_edge, key=weight_by_edge.__getitem__)[:-1]:",
                       "drops the heaviest edge from consideration entirely"),
        "wrong_comparison": ("if group_by_node.setdefault(u, {u}) != group_by_node.setdefault(v, {v}):",
                             "if group_by_node.setdefault(u, {u}) == group_by_node.setdefault(v, {v}):",
                             "inverts the union-find cycle check"),
        "missing_guard": ("if group_by_node.setdefault(u, {u}) != group_by_node.setdefault(v, {v}):",
                          "if True:",
                          "drops the cycle guard, admits every edge"),
    },
    "next_palindrome": {
        "off_by_one": ("low_mid = (len(digit_list) - 1) // 2", "low_mid = (len(digit_list) - 2) // 2",
                       "shifts the low mirror index by one"),
        "wrong_comparison": ("while high_mid < len(digit_list) and low_mid >= 0:",
                             "while high_mid <= len(digit_list) and low_mid >= 0:",
                             "reads one past the end of digit_list"),
        "missing_guard": ("if low_mid != high_mid:", "if True:",
                          "double-increments the middle digit of odd-length palindromes"),
    },
    "next_permutation": {
        "off_by_one": ("for i in range(len(perm) - 2, -1, -1):", "for i in range(len(perm) - 3, -1, -1):",
                       "never checks the first possible pivot position"),
        "wrong_comparison": ("if perm[i] < perm[i + 1]:", "if perm[i] > perm[i + 1]:",
                             "searches for a descending pivot instead of ascending"),
        "missing_guard": ("if perm[i] < perm[j]:", "if True:",
                          "drops the smallest-greater-element guard"),
    },
    "pascal": {
        "off_by_one": ("for c in range(0, r + 1):", "for c in range(0, r):",
                       "drops the last column of every row"),
        "wrong_comparison": ("upright = rows[r - 1][c] if c < r else 0", "upright = rows[r - 1][c] if c <= r else 0",
                             "reads one past the end of the previous row"),
        "missing_guard": ("upleft = rows[r - 1][c - 1] if c > 0 else 0", "upleft = rows[r - 1][c - 1]",
                          "drops the first-column guard (wraps to the last element instead of 0)"),
    },
    "possible_change": {
        "off_by_one": ("if total == 0:", "if total == 1:",
                       "shifts the total==0 base case by one"),
        "wrong_comparison": ("if total == 0:", "if total <= 0:",
                             "treats negative totals as a valid solution"),
        "missing_guard": ("if total < 0 or not coins:", "if total < 0:",
                          "drops the no-coins-left guard (crashes on empty coins)"),
    },
    "powerset": {
        "off_by_one": ("return rest_subsets + [[first] + subset for subset in rest_subsets]",
                       "return rest_subsets[:-1] + [[first] + subset for subset in rest_subsets]",
                       "drops one subset from the without-first half"),
        "wrong_comparison": ("if arr:", "if not arr:",
                             "inverts the base-case check"),
        "missing_guard": ("if arr:", "if False:",
                          "always returns the empty powerset regardless of input"),
    },
    "quicksort": {
        "off_by_one": ("lesser = quicksort([x for x in arr[1:] if x < pivot])",
                       "lesser = quicksort([x for x in arr[2:] if x < pivot])",
                       "skips checking the second element against the pivot"),
        "wrong_comparison": ("greater = quicksort([x for x in arr[1:] if x >= pivot])",
                             "greater = quicksort([x for x in arr[1:] if x > pivot])",
                             "drops elements equal to the pivot from the output"),
        "missing_guard": ("if not arr:", "if False:",
                          "drops the empty-list base case (crashes on empty input)"),
    },
    "reverse_linked_list": {
        "off_by_one": ("node = nextnode", "node = nextnode.successor if nextnode else None",
                       "skips every other node while walking the list"),
        "wrong_comparison": ("while node:", "while not node:",
                             "loop body never runs for any non-empty list"),
        "missing_guard": ("while node:", "while True:",
                          "drops the loop-termination guard (crashes past the tail)"),
    },
    "rpn_eval": {
        "off_by_one": ("stack.append(token)", "stack.append(token + 1)",
                       "corrupts every literal operand by one"),
        "wrong_comparison": ("op(token, b, a)", "op(token, a, b)",
                             "swaps operand order for non-commutative operators"),
        "missing_guard": ("if isinstance(token, float):", "if isinstance(token, str):",
                          "corrupts the value-vs-operator dispatch guard"),
    },
    "shortest_path_length": {
        "off_by_one": ("distance + length_by_edge[node, nextnode]", "distance + length_by_edge[node, nextnode] + 1",
                       "adds a constant penalty to every edge traversal"),
        "wrong_comparison": ("if node is goalnode:", "if node is not goalnode:",
                             "inverts the goal-reached check"),
        "missing_guard": ("if nextnode in visited_nodes:", "if False:",
                          "drops the visited guard"),
    },
    "shortest_path_lengths": {
        "off_by_one": ("length_by_path[i, k] + length_by_path[k, j]",
                       "length_by_path[i, k] + length_by_path[k, j] + 1",
                       "adds a constant penalty per intermediate hop"),
        "wrong_comparison": ("length_by_path[i, j] = min(", "length_by_path[i, j] = max(",
                             "turns the shortest-path relaxation into a longest-path one"),
        "missing_guard": ("length_by_path.update({(i, i): 0 for i in range(n)})", "pass",
                          "drops the zero-self-distance initialization"),
    },
    "shortest_paths": {
        "off_by_one": ("for i in range(len(weight_by_node) - 1):", "for i in range(len(weight_by_node) - 2):",
                       "runs one fewer Bellman-Ford relaxation pass"),
        "wrong_comparison": ("weight_by_node[v] = min(", "weight_by_node[v] = max(",
                             "turns the relaxation step into a longest-path one"),
        "missing_guard": ("v: float('inf') for u, v in weight_by_edge", "v: 0 for u, v in weight_by_edge",
                          "drops the unvisited-is-infinity guard"),
    },
    "shunting_yard": {
        "off_by_one": ("while opstack:", "while len(opstack) > 1:",
                       "leaves the last operator undrained"),
        "wrong_comparison": ("while opstack and precedence[token] <= precedence[opstack[-1]]:",
                             "while opstack and precedence[token] < precedence[opstack[-1]]:",
                             "mishandles equal-precedence operators"),
        "missing_guard": ("if isinstance(token, int):", "if isinstance(token, str):",
                          "corrupts the value-vs-operator dispatch guard"),
    },
    "sieve": {
        "off_by_one": ("for n in range(2, max + 1):", "for n in range(2, max):",
                       "never tests `max` itself"),
        "wrong_comparison": ("if all(n % p > 0 for p in primes):", "if all(n % p >= 0 for p in primes):",
                             "the new condition is always true, so every n is kept"),
        "missing_guard": ("if all(n % p > 0 for p in primes):", "if all(n % p > 0 for p in primes[:-1]):",
                          "never checks against the most-recently-found prime"),
    },
    "sqrt": {
        "off_by_one": ("abs(x - approx ** 2)", "abs(x - approx ** 3)",
                       "checks convergence against the wrong power"),
        "wrong_comparison": ("while abs(x - approx ** 2) > epsilon:", "while abs(x - approx ** 2) < epsilon:",
                             "inverts the convergence check, so it barely iterates"),
        "missing_guard": ("while abs(x - approx ** 2) > epsilon:", "while True:",
                          "drops the convergence guard (hangs)"),
    },
    "subsequences": {
        "off_by_one": ("for i in range(a, b + 1 - k):", "for i in range(a, b - k):",
                       "drops the last valid starting index"),
        "wrong_comparison": ("if k == 0:", "if k == 1:",
                             "shifts the k==0 base case by one"),
        "missing_guard": ("if k == 0:", "if False:",
                          "drops the k==0 base case"),
    },
    "to_base": {
        "off_by_one": ("i = num % b", "i = num % b + 1",
                       "shifts every digit's alphabet index by one"),
        "wrong_comparison": ("while num > 0:", "while num >= 0:",
                             "adds a spurious final iteration once num hits 0 (hangs)"),
        "missing_guard": ("while num > 0:", "while True:",
                          "drops the loop-termination guard (hangs)"),
    },
    "topological_ordering": {
        "off_by_one": ("ordered_nodes = [node for node in nodes if not node.incoming_nodes]",
                       "ordered_nodes = [node for node in nodes if not node.incoming_nodes][:-1]",
                       "drops one root node from the initial frontier"),
        "wrong_comparison": ("and nextnode not in ordered_nodes:", "or nextnode not in ordered_nodes:",
                             "admits nodes before all their prerequisites are ready"),
        "missing_guard": ("if set(ordered_nodes).issuperset(nextnode.incoming_nodes) and nextnode not in ordered_nodes:",
                          "if nextnode not in ordered_nodes:",
                          "drops the all-prerequisites-ready guard"),
    },
    "wrap": {
        "off_by_one": ("end = text.rfind(' ', 0, cols + 1)", "end = text.rfind(' ', 0, cols)",
                       "narrows the break-point search window by one column"),
        "wrong_comparison": ("while len(text) > cols:", "while len(text) >= cols:",
                             "splits one line earlier than necessary"),
        "missing_guard": ("if end == -1:", "if False:",
                          "drops the no-space-found fallback (silently drops a character)"),
    },
}


def build_mutants(task_name: str, correct_source: str) -> list[Mutant]:
    edits = MUTANT_EDITS[task_name]
    correct_source = _strip_trailing_docstring(correct_source)
    return [
        Mutant(
            task=task_name,
            fault_type=fault_type,
            note=edits[fault_type][2],
            source=_mutate(correct_source, edits[fault_type][0], edits[fault_type][1]),
        )
        for fault_type in FAULT_TYPES
    ]


def build_all_mutants() -> dict[str, list[Mutant]]:
    from src.adapter import SUPPORTED_PROGRAMS, load

    return {name: build_mutants(name, load(name).correct_source) for name in SUPPORTED_PROGRAMS}


if __name__ == "__main__":
    from src.adapter import SUPPORTED_PROGRAMS

    missing = [name for name in SUPPORTED_PROGRAMS if name not in MUTANT_EDITS]
    if missing:
        raise SystemExit(f"no mutant edits defined for: {missing}")

    all_mutants = build_all_mutants()
    total = sum(len(m) for m in all_mutants.values())
    print(f"built {total} mutants across {len(all_mutants)} tasks (expected {3 * len(SUPPORTED_PROGRAMS)})")
    for name, mutants in all_mutants.items():
        for m in mutants:
            assert m.fault_type in FAULT_TYPES
