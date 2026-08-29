"""Control-flow test for free_guarded_rounds: no LLM, no sandbox."""
import sys, types, pathlib, tempfile, json, itertools
sys.path.insert(0, '.')
import src.loop as loop

BUDGET = 3
# round k proposes patch "p{k}"; the guard blocks every patch except p5,
# which the oracle accepts. Under charged rounds the episode dies at k=6
# without ever reaching p5's attempt; under free rounds it reaches it.
blocked_until = 5

class FakeGuardResult:
    def __init__(self, blocked): self.blocked, self.evaluations, self.blocked_by = blocked, 2, None
class FakeMemory:
    history = []
    def guard(self, cand, buggy):
        n = int(cand[1:])
        return FakeGuardResult(n < blocked_until)
    def store(self, a): return a
class FakeResult:
    accept = True; args = ["c1"]; reason = "ok"; examples_tried = 7; oracle_error = None
class FakeAttempt:
    coarse_type = fine_type = None
    @staticmethod
    def from_result(*a, **k): return FakeAttempt()
    def failure_type(self, g): return None

def run(free):
    rows = []
    loop.propose = lambda *a, **k: f"p{k['nonce'].rsplit('|',1)[-1]}"
    loop.build_memory = lambda *a, **k: FakeMemory()
    loop.differential_test = lambda *a, **k: FakeResult()
    loop.Attempt = FakeAttempt
    loop.append_round = lambda rec, **k: rows.append(rec.to_json())
    loop.load = lambda n: types.SimpleNamespace(buggy_source="x", correct_source="y")
    loop.TASKS = {"t": types.SimpleNamespace(name="t", spec_note=None)}
    loop.proposal_nonce = lambda t, s, r: f"n|{r}"
    res = loop.run_episode("t", "typed", budget=BUDGET, free_guarded_rounds=free)
    return rows, res

for free in (False, True):
    rows, res = run(free)
    charged = [r for r in rows if r["attempt_index"] is not None]
    guarded = [r for r in rows if r["guarded"]]
    print(f"free_guarded_rounds={free!s:5s} draws={len(rows):2d} charged={len(charged):2d} "
          f"guarded={len(guarded):2d} accepted={res.accepted_patch!r} "
          f"runs={sum(r['sandbox_runs'] for r in rows)}")
    assert len(charged) <= BUDGET
    assert [r["round_index"] for r in rows] == list(range(1, len(rows)+1))
    ai = [r["attempt_index"] for r in charged]
    assert ai == list(range(1, len(ai)+1)), ai
    if free:
        assert all(r["attempt_index"] is None for r in guarded), "guarded round charged"
        assert res.accepted_patch is not None, "free rounds should reach the accepting draw"
    else:
        assert all(r["attempt_index"] is not None for r in rows), "charged mode left a free round"
        assert res.accepted_patch is None, "charged mode should starve at B=3"
print("\nOK: attempt_index is contiguous, guarded rounds are free only when asked,")
print("    round_index still counts every draw, and the guard now changes the outcome.")
