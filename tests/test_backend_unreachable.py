"""A connection blip must cost one round, not the shard.

Written because openai.APIConnectionError was uncaught for the entire study.
The SDK retries MAX_RETRIES times inside one call and then raises; nothing above
it caught the result, so one blip killed the shard - and under a fleet, every
shard at once, since they share one server. It surfaced as

    openai.APIConnectionError: Connection error.
    shard exited with status 1

No openai package and no model server needed: the failure is injected at
src.loop's own seam.
"""
import sys, types, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import src.loop as loop
from src.llm import BackendUnreachable

class FakeGuard:
    blocked, evaluations, blocked_by = False, 0, None
class FakeMemory:
    history = []
    def guard(self, *a): return FakeGuard()
    def store(self, a): return a
class FakeResult:
    accept = True; args = ["c1"]; reason = "ok"; examples_tried = 3; oracle_error = None
class FakeAttempt:
    coarse_type = fine_type = None
    @staticmethod
    def from_result(*a, **k): return FakeAttempt()
    def failure_type(self, g): return None

def run(fail_rounds, budget=8):
    rows = []
    def propose(*a, **k):
        r = int(k["nonce"].rsplit("|", 1)[-1])
        if r in fail_rounds:
            raise BackendUnreachable("APIConnectionError: Connection error.")
        return f"p{r}"
    loop.propose = propose
    loop.build_memory = lambda *a, **k: FakeMemory()
    loop.differential_test = lambda *a, **k: FakeResult()
    loop.Attempt = FakeAttempt
    loop.append_round = lambda rec, **k: rows.append(rec.to_json())
    loop.load = lambda n: types.SimpleNamespace(buggy_source="x", correct_source="y")
    loop.TASKS = {"t": types.SimpleNamespace(name="t", spec_note=None)}
    loop.proposal_nonce = lambda t, s, r: f"n|{r}"
    try:
        res = loop.run_episode("t", "typed", budget=budget)
        return rows, res, None
    except BackendUnreachable as exc:
        return rows, None, exc

# 1. isolated blips: recorded, episode carries on and still succeeds
rows, res, exc = run({1, 2})
errs = [r for r in rows if r.get("proposal_error") == "backend_unreachable"]
assert exc is None, f"a pair of blips should not stop the episode: {exc}"
assert len(errs) == 2, errs
assert res.accepted_patch == "p3", res.accepted_patch
print(f"  2 isolated blips -> {len(errs)} rounds recorded, episode still repaired "
      f"at {res.accepted_patch}")

# 2. a run of them: stops cleanly, every failure on the record
cap = loop.MAX_CONSECUTIVE_BACKEND_FAILURES
rows, res, exc = run(set(range(1, cap + 3)), budget=20)
errs = [r for r in rows if r.get("proposal_error") == "backend_unreachable"]
assert exc is not None, "a dead server must stop the episode"
assert len(errs) == cap, f"expected {cap} recorded failures, got {len(errs)}"
print(f"  {cap} consecutive -> BackendUnreachable raised, {len(errs)} rounds on the record")

# 3. the counter resets: cap-1 failures, a success, then cap-1 more
rows, res, exc = run(set(range(1, cap)) | set(range(cap + 1, 2 * cap)), budget=20)
assert exc is None, "failures either side of a success must not accumulate"
print(f"  {cap-1} + success + {cap-1} -> no stop; the counter resets")
print("\nOK: a blip costs one round, a dead server stops the sweep cleanly.")
