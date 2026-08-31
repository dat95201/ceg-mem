"""The typed guard must not stop at its own bucket.

Regression test for the defect priced in docs/TYPED-VS-UNTYPED.md: until
2026-08-31 `TypedMemory.guard` searched only the type-indexed bucket, which made
it a partition rather than an index. Because theta's key is where the candidate
*edits* and refutation is decided by which *input* it gets wrong, 5,060 of 7,349
refuting typed oracle rounds (68.9%) returned a counterexample already held in
memory - rounds the untyped flat scan would certainly have blocked.

No LLM, no sandbox: `run_program`, `TASKS` and `edit_location` are replaced in
the module namespace so the guard's search order is the only thing under test.
"""
import sys, types
sys.path.insert(0, '.')
import src.memory as memory
from src.memory import TypedMemory

TASK = "t"
EXPECTED = {"c1": "1", "c2": "2", "c3": "3"}
# which cases each candidate patch gets wrong
FAILS = {"edits_A": {"c1"}, "edits_B": {"c1"}, "edits_D": {"c1"}, "clean": set()}
# what edit_location() reports for each candidate
LOC = {"edits_A": "A", "edits_B": "B", "edits_D": "D", "clean": "A"}


class Ref:
    def __init__(self, value): self.ok, self.value = True, value


class Result:
    def __init__(self, case, accept=False):
        self.accept, self.args = accept, [case]
        self.reference = Ref(EXPECTED[case])


class FT:
    def __init__(self, location): self.task, self.location = TASK, location


class Att:
    """Duck-typed src.proposer.Attempt - only what guard/store touch."""
    def __init__(self, location, case, accept=False):
        self.coarse_type = None
        self.fine_type = None if accept else FT(location)
        self.result = Result(case, accept)

    def failure_type(self, granularity): return self.fine_type


def _install(monkey_calls):
    """Patch the module namespace; return a list that records each sandbox run."""
    memory.TASKS = {TASK: types.SimpleNamespace(
        case=lambda name: types.SimpleNamespace(input_text=name))}
    memory.outputs_equal = lambda a, b: a == b
    memory.edit_location = lambda buggy, cand: LOC[cand]

    def run_program(src, input_text, **kw):
        monkey_calls.append((src, input_text))
        wrong = input_text in FAILS[src]
        return types.SimpleNamespace(timed_out=False, ok=True,
                                     value="WRONG" if wrong else EXPECTED[input_text])
    memory.run_program = run_program
    return monkey_calls


def fresh(**kw):
    mem = TypedMemory(**kw)
    return mem


def test_index_does_not_stop_the_search():
    """THE regression. A counterexample filed under A must still block a
    candidate that edits B."""
    _install([])
    mem = fresh()
    mem.store(Att("A", "c1"))
    got = mem.guard("edits_B", buggy_source="buggy")
    assert got.blocked, "guard missed a counterexample it was holding"
    assert got.blocked_by is not None

    partition = fresh(guard_fallback=False)
    partition.store(Att("A", "c1"))
    assert not partition.guard("edits_B", buggy_source="buggy").blocked, \
        "guard_fallback=False no longer reproduces the old partition guard"


def test_bucket_is_probed_first():
    """Prop. 4.5: when theta guesses right, phase 1 is the whole cost."""
    _install([])
    mem = fresh()
    mem.store(Att("A", "c1"))
    mem.store(Att("B", "c2"))
    mem.store(Att("C", "c3"))
    got = mem.guard("edits_A", buggy_source="buggy")
    assert got.blocked
    assert got.evaluations == 1, f"probed {got.evaluations} times, expected 1"


def test_one_run_per_distinct_counterexample():
    """Three attempts, one case: the fallback scan runs it once, not three times."""
    calls = _install([])
    mem = fresh()
    for loc in ("A", "B", "C"):
        mem.store(Att(loc, "c1"))
    got = mem.guard("clean", buggy_source="buggy")   # bucket A, passes c1
    assert not got.blocked
    assert got.evaluations == 1, f"ran {got.evaluations} checks for 1 distinct case"
    assert len(calls) == 1


def test_never_blocks_a_candidate_no_stored_case_refutes():
    """Soundness: widening the search must not invent a block."""
    _install([])
    mem = fresh()
    mem.store(Att("A", "c1"))
    mem.store(Att("B", "c2"))
    got = mem.guard("clean", buggy_source="buggy")
    assert not got.blocked
    assert got.blocked_by is None


def test_accepted_attempts_are_never_re_run():
    _install([])
    mem = fresh()
    mem.store(Att("A", "c1", accept=True))
    got = mem.guard("edits_D", buggy_source="buggy")
    assert got.evaluations == 0
    assert not got.blocked


def test_fallback_reaches_a_bucket_never_indexed():
    """The candidate's location was never stored at all - the bucket is empty,
    which on the old guard meant an automatic pass to the oracle."""
    _install([])
    mem = fresh()
    mem.store(Att("A", "c1"))
    mem.store(Att("B", "c2"))
    got = mem.guard("edits_D", buggy_source="buggy")   # bucket "D" is empty
    assert got.blocked, "empty bucket still short-circuits the search"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"{passed} passed")
