"""Control-flow tests for the three baseline arms (E10/E11/E12): no LLM, no sandbox.

What is pinned, per arm:

  E10-chat    round 1's transcript block is EMPTY - so its prompt inputs are
              byte-identical to no_memory's and the CRN round-1 pairing holds -
              the block then grows with the refuted history, and the ChatRepair
              restart policy clears it after N straight fails, with the restart
              count on every row.
  E11         a proposal the self-test filter rejects never reaches the oracle,
              charges one unit of budget, stores nothing in memory, and carries
              the filter's own cost on its row.
  E12         skips are drawn from a cell-seeded RNG - p=1.0 blocks every call,
              p=0.0 none, and two runs of the same cell skip identically.
  identity    cell_signature without the new knobs is byte-identical to what it
              produced before they existed, so no existing episode id moves.
"""
import random
import sys
import types

sys.path.insert(0, '.')
import src.loop as loop


class FakeOutcome:
    def __init__(self, ok=True, value="1\n"):
        self.ok, self.value, self.timed_out = ok, value, False
        self.error_type = self.error_message = None


class FakeResult:
    def __init__(self, accept):
        self.accept = accept
        self.args = ["case1"]
        self.reason = "ok" if accept else "wrong answer"
        self.examples_tried = 4
        self.oracle_error = None
        self.candidate = FakeOutcome(value="2\n")
        self.reference = FakeOutcome(value="1\n")


class FakeType:
    key = "loc:prop"
    location, property, task = "loc", "prop", "t"


class FakeAttempt:
    def __init__(self, patch, result):
        self.patch, self.result = patch, result
        self.coarse_type = self.fine_type = None if result.accept else FakeType()
        self.mistyped_from = ()

    @classmethod
    def from_result(cls, task_name, buggy, patch, result):
        return cls(patch, result)

    def failure_type(self, granularity):
        return self.fine_type


class FakeVerdict:
    def __init__(self, blocked, runs=3, n_cases=5, failed_case="selftest1"):
        self.blocked, self.runs, self.n_cases = blocked, runs, n_cases
        self.failed_case = failed_case if blocked else None


class FakeSelfTestFilter:
    def __init__(self, block_patches):
        self.block_patches = set(block_patches)

    def check(self, patch):
        return FakeVerdict(patch in self.block_patches)


def harness(accept_at=None, selftest_blocks=(), transcripts=None):
    """Wire the fakes; returns (rows, run) where run(**episode_kwargs)."""
    rows = []
    loop.propose = lambda *a, **k: (
        (transcripts.append(k.get("transcript_block", "")) if transcripts is not None else None)
        or f"p{k['nonce'].rsplit('r', 1)[-1]}")
    loop.differential_test = lambda task, patch, ref, **k: FakeResult(
        accept=(accept_at is not None and int(patch[1:]) == accept_at))
    loop.Attempt = FakeAttempt
    loop.append_round = lambda rec, **k: rows.append(rec.to_json())
    loop.load = lambda n: types.SimpleNamespace(buggy_source="x", correct_source="y")
    loop.TASKS = {"t": types.SimpleNamespace(name="t", spec_note="")}
    loop.make_filter = lambda *a, **k: FakeSelfTestFilter(selftest_blocks)

    def run(**kw):
        rows.clear()
        return rows, loop.run_episode("t", kw.pop("mode", "no_memory"), budget=kw.pop("budget", 6),
                                      seed=kw.pop("seed", 1), **kw)
    return run


def test_e10_round1_is_no_memory_and_restarts_fire():
    transcripts = []
    run = harness(accept_at=None, transcripts=transcripts)
    rows, res = run(history="chat", history_restart_after=3, budget=8)
    assert transcripts[0] == "", "round 1 must be the no_memory prompt (CRN identity)"
    assert transcripts[1] != "", "round 2 must carry the failed attempt"
    assert "p1" in transcripts[1] and "FAILED" in transcripts[1]
    # after 3 straight fails the conversation restarts: the next block is empty
    restart_rounds = [i + 1 for i, t in enumerate(transcripts) if i and t == ""]
    assert restart_rounds, "no restart ever fired at history_restart_after=3"
    assert rows[-1]["history_restarts"] >= 1
    assert all(r["history"] == "chat" for r in rows)
    # and the identical run without history never builds a block
    transcripts2 = []
    run2 = harness(accept_at=None, transcripts=transcripts2)
    run2(budget=8)
    assert all(t == "" for t in transcripts2)
    print("  ok  E10: round-1 empty, transcript grows, restart clears it")


def test_e10_cap_restarts_too():
    transcripts = []
    run = harness(accept_at=None, transcripts=transcripts)
    # cap of 0 tokens: every non-empty transcript restarts before shipping
    rows, _ = run(history="chat", history_cap_tokens=0, history_restart_after=99, budget=5)
    assert all(t == "" for t in transcripts), "cap=0 must never ship a transcript"
    assert rows[-1]["history_restarts"] >= 1
    print("  ok  E10: token cap forces a restart before the block ships")


def test_e11_blocked_rounds_never_reach_the_oracle():
    oracle_calls = []
    run = harness(accept_at=3, selftest_blocks={"p1", "p2"})
    real = loop.differential_test
    loop.differential_test = lambda *a, **k: (oracle_calls.append(1), real(*a, **k))[1]
    rows, res = run(selftest=True, budget=6)
    blocked = [r for r in rows if r["selftest_blocked"]]
    assert [r["round_index"] for r in blocked] == [1, 2]
    assert len(oracle_calls) == 1 and res.accepted_patch == "p3"
    assert all(r["attempt_index"] is not None for r in blocked), "blocked rounds still charge budget"
    assert all(r["sandbox_runs"] == 3 for r in blocked), "the filter's own runs are billed"
    assert all(r["examples_tried"] == 0 for r in blocked)
    assert all(r["selftest"] for r in rows)
    accepted = [r for r in rows if r["accept"]]
    assert accepted and accepted[0]["selftest_runs"] == 3, "a passing check costs runs too"
    print("  ok  E11: filter blocks before the oracle, charges budget, bills its runs")


def test_e12_skip_probability_and_determinism():
    run = harness(accept_at=None)
    rows, _ = run(oracle_skip_p=1.0, budget=5)
    assert all(r["oracle_skipped"] for r in rows), "p=1.0 must skip every oracle call"
    assert all(r["examples_tried"] == 0 for r in rows)
    first = [r["reason"] for r in rows]
    rows2, _ = run(oracle_skip_p=1.0, budget=5)
    assert [r["reason"] for r in rows2] == first, "same cell must replay the same skips"
    rows3, _ = run(oracle_skip_p=0.0, budget=5)
    assert not any(r.get("oracle_skipped") for r in rows3)
    print("  ok  E12: p=1 skips all, p=0 none, and the skip sequence is cell-deterministic")


def test_cell_signature_backwards_identity():
    base = dict(seed=1, granularity="fine", max_examples=100, typing_noise_c=1.0,
                guard_on=True, steer_on=True, budget=20, force_full_budget=False)
    old = loop.cell_signature("t/p", "typed", **base)
    assert "history=" not in old and "selftest=" not in old and "skip=" not in old, \
        "default knobs must leave the signature - and every episode id - unchanged"
    assert loop.cell_signature("t/p", "typed", **base, history="chat") != old
    assert loop.cell_signature("t/p", "typed", **base, selftest=True) != old
    assert loop.cell_signature("t/p", "typed", **base, oracle_skip_p=0.37) != old
    print("  ok  identity: old cells keep their episode ids; each knob is its own cell")


if __name__ == "__main__":
    for name in ("test_e10_round1_is_no_memory_and_restarts_fire",
                 "test_e10_cap_restarts_too",
                 "test_e11_blocked_rounds_never_reach_the_oracle",
                 "test_e12_skip_probability_and_determinism",
                 "test_cell_signature_backwards_identity"):
        globals()[name]()
    print("\nOK: E10/E11/E12 control flow pinned, existing cell identities untouched.")
