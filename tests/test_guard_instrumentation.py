"""Coherence measured in situ, and a memo that may not change a verdict.

Two additions to src.memory, both aimed at the paper's Proposition 4.5:

  bucket_hit / bucket_hit_refuted - the guard already knows whether theta's
  guess landed on a non-empty eliminated bucket and whether anything in it
  actually still refuted the candidate. Their ratio IS Definition 3.1's
  coherence, and Prop. 4.5's O(1) guard is only sound if it is 1.0. Recording
  it costs nothing. Measured on the frozen log it is 0.930, and the 7% shortfall
  is not harmless: 88 of those 230 candidates were patches the oracle ACCEPTS,
  so a lookup-only guard would lose 20.7% of all repairs.

  memo_guard - running the same program on the same input twice is a pure
  function, and duplicate_patch_rate is 0.217. Default OFF, because it lowers
  sandbox_runs and the completed E2 grid was measured without it.

No LLM, no sandbox: run_program, TASKS and edit_location are replaced in the
module namespace, so only the guard's own bookkeeping is under test.
"""
import sys, types
sys.path.insert(0, '.')
import src.memory as memory
from src.memory import TypedMemory, UntypedMemory

TASK = "t"
EXPECTED = {"c1": "1", "c2": "2"}
FAILS = {"at_A": {"c1"}, "at_B": {"c1"}, "at_C": set()}
LOC = {"at_A": "A", "at_B": "B", "at_C": "C"}


class Ref:
    def __init__(self, v): self.ok, self.value = True, v


class Result:
    def __init__(self, case, accept=False):
        self.accept, self.args = accept, [case]
        self.reference = Ref(EXPECTED[case])


class FT:
    def __init__(self, loc): self.task, self.location = TASK, loc


class Att:
    def __init__(self, loc, case, accept=False):
        self.coarse_type = None
        self.fine_type = None if accept else FT(loc)
        self.result = Result(case, accept)
    def failure_type(self, g): return self.fine_type


def install():
    calls = []
    memory.TASKS = {TASK: types.SimpleNamespace(
        case=lambda n: types.SimpleNamespace(input_text=n))}
    memory.outputs_equal = lambda a, b: a == b
    memory.edit_location = lambda buggy, cand: LOC[cand]
    def run_program(src, input_text, **kw):
        calls.append((src, input_text))
        wrong = input_text in FAILS[src]
        return types.SimpleNamespace(timed_out=False, ok=True,
                                     value="WRONG" if wrong else EXPECTED[input_text])
    memory.run_program = run_program
    return calls


def test_bucket_hit_is_recorded_when_the_index_fires():
    install()
    m = TypedMemory()
    m.store(Att("A", "c1"))
    g = m.guard("at_A", buggy_source="b")          # location A - bucket is non-empty
    assert g.blocked and g.bucket_hit and g.bucket_hit_refuted


def test_a_miss_on_an_empty_bucket_is_not_a_hit():
    install()
    m = TypedMemory()
    m.store(Att("A", "c1"))
    g = m.guard("at_B", buggy_source="b")          # location B - never indexed
    assert g.blocked, "the fallback should still catch c1"
    assert g.bucket_hit is False, "an empty bucket is not a hit"
    assert g.bucket_hit_refuted is False


def test_a_hit_whose_bucket_does_not_refute_is_the_incoherent_case():
    """theta says 'been here', the counterexample says otherwise. This is the
    7% that a lookup-only guard would block wrongly."""
    install()
    m = TypedMemory()
    m.store(Att("C", "c1"))                        # bucket C holds a c1 refutation
    g = m.guard("at_C", buggy_source="b")          # at_C passes c1
    assert g.bucket_hit is True, "bucket C is non-empty"
    assert g.blocked is False and g.bucket_hit_refuted is False


def test_memo_is_off_by_default_and_reruns():
    calls = install()
    m = TypedMemory()
    m.store(Att("A", "c1"))
    for _ in range(3):
        m.guard("at_A", buggy_source="b")
    assert len(calls) == 3, f"{len(calls)} runs; default must not memoize"


def test_memo_on_runs_each_case_patch_pair_once():
    calls = install()
    m = TypedMemory(memo_guard=True)
    m.store(Att("A", "c1"))
    verdicts = [m.guard("at_A", buggy_source="b").blocked for _ in range(3)]
    assert verdicts == [True, True, True], "the memo changed a verdict"
    assert len(calls) == 1, f"{len(calls)} runs; the memo should leave 1"


def test_memo_never_changes_a_block_decision():
    """Same stores, same probes, memo on vs off - identical verdicts."""
    for probe in ("at_A", "at_B", "at_C"):
        install(); off = TypedMemory()
        install(); on = TypedMemory(memo_guard=True)
        for mem in (off, on):
            mem.store(Att("A", "c1")); mem.store(Att("C", "c1"))
        a = [off.guard(probe, buggy_source="b").blocked for _ in range(3)]
        b = [on.guard(probe, buggy_source="b").blocked for _ in range(3)]
        assert a == b, f"{probe}: memo off {a} vs on {b}"


def test_sandbox_runs_tracks_executions_not_consultations():
    calls = install()
    m = TypedMemory(memo_guard=True)
    m.store(Att("A", "c1"))
    first = m.guard("at_A", buggy_source="b")
    second = m.guard("at_A", buggy_source="b")
    assert first.evaluations == second.evaluations == 1, "Prop 4.5's count must not move"
    assert first.sandbox_runs == 1 and second.sandbox_runs == 0
    assert len(calls) == 1


def test_untyped_reports_runs_equal_to_evaluations():
    install()
    m = UntypedMemory()
    m.store(Att("A", "c1")); m.store(Att("C", "c2"))
    g = m.guard("at_C", buggy_source="b")
    assert g.sandbox_runs == g.evaluations, "untyped has no memo; the two must agree"


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  ok  {name}"); n += 1
    print(f"{n} passed")
