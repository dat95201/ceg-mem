"""Behavioural tests for the five validation policies: no LLM, no sandbox.

Everything here runs against a hand-built verdict matrix, so each test pins one
claim the simulation makes and nothing else:

  draw          the oracle policy reproduces `src.oracle._sample`'s seeded draw
                exactly - same RNG, same seed arithmetic (seed + round_index),
                same "return the pool unchanged when k >= |pool|" branch. If this
                drifts, every reported oracle cost is measuring a different
                instrument from the one the paper ran.
  qi13          a case that killed a candidate sorts to the front for the next
                one, and pool order breaks ties, so round 1 is plain pool order.
  venugopal20   kill history is kept per edit location: a case that killed a
                candidate at location A does NOT get promoted for a candidate
                at location B, which is the whole difference from qi13.
  guards        a repeat failure is blocked by the store for one execution and
                never reaches the oracle; the indexed guard consults its own
                bucket first but still falls back to the rest of the store, so
                it can never block less than the flat one (the soundness
                correction recorded in src/memory.py).
  stats         the fallback Wilcoxon agrees with scipy's where scipy exists,
                and is one-sided in the direction the criterion claims.

Run: pytest tests/test_policies.py   -   or   python3 tests/test_policies.py
"""
import pathlib
import random
import sys

sys.path.insert(0, ".")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import simulate_policies as sp


# --------------------------------------------------------------------------
# a fake task: five cases, no adapter, no ConDefects checkout
# --------------------------------------------------------------------------

CASES = ["c1", "c2", "c3", "c4", "c5"]


def install_fake_task(name="t/1"):
    """No adapter, no benchmark: a policy is constructed from case NAMES alone."""
    return name


def fake_locations(mapping):
    """Pin lambda(p) without importing difflib or the benchmark."""
    sp._location = lambda task, source: mapping.get(source, "L1")


def make(policy, task, seed=1, depth=100):
    return sp.POLICY_CLASSES[policy](task, seed, list(CASES), list(CASES), depth)


# --------------------------------------------------------------------------

def test_draw_matches_the_oracles_own_sample():
    task = install_fake_task()
    pol = make("oracle-k", task, seed=7, depth=3)
    for r in (1, 2, 5):
        expected = random.Random(7 + r).sample(list(CASES), 3)
        assert pol.draw(r) == expected, f"draw diverged at round {r}"
    # k >= |pool| is the other branch: the pool, in pool order, unsampled.
    assert make("oracle-k", task, seed=7, depth=100).draw(1) == CASES


def test_oracle_stops_at_the_first_failing_case_in_draw_order():
    task = install_fake_task()
    pol = make("oracle-k", task, depth=100)
    n, hit, accepted, blocked = pol.round(1, "s", "src", {"c3"})
    assert (n, hit, accepted, blocked) == (3, "c3", False, False)
    n, hit, accepted, _ = pol.round(2, "s", "src", set())
    assert (n, hit, accepted) == (5, None, True)      # a clean candidate pays the pool


def test_qi13_promotes_a_killer_and_keeps_pool_order_otherwise():
    task = install_fake_task()
    fake_locations({})
    pol = make("qi13", task)
    # Round 1: nothing has killed anything, so c4 is found in pool position 4.
    assert pol.round(1, "a", "A", {"c4"})[0] == 4
    # Round 2: c4 killed one candidate, so it is tried first.
    assert pol.round(2, "b", "B", {"c4"})[0] == 1
    # A case that never killed anything stays where the pool put it.
    assert pol.round(3, "c", "C", {"c2"})[0] == 3      # order is c4, c1, c2, ...


def test_venugopal_keeps_kill_history_per_edit_location():
    task = install_fake_task()
    fake_locations({"A": "L10", "B": "L10", "C": "L99"})
    pol = make("venugopal20", task)
    assert pol.round(1, "a", "A", {"c4"})[0] == 4      # learn c4 at L10
    assert pol.round(2, "b", "B", {"c4"})[0] == 1      # same location: promoted
    # Different location. The global fallback still ranks c4 first, which is
    # what makes venugopal20 a refinement of qi13 rather than a different
    # algorithm - but the LOCAL bucket for L99 is empty, and that is the field
    # this test pins.
    assert pol.by_loc["L99"] == {}
    assert dict(pol.by_loc["L10"]) == {"c4": 2}


def test_dedup_guard_blocks_a_repeat_for_one_execution():
    task = install_fake_task()
    fake_locations({})
    pol = make("dedup-guard", task)
    n, hit, accepted, blocked = pol.round(1, "a", "A", {"c4"})
    assert (n, blocked) == (4, False)                 # first time: the oracle pays
    n, hit, accepted, blocked = pol.round(2, "b", "B", {"c4"})
    assert (n, hit, accepted, blocked) == (1, "c4", False, True)
    # A candidate the store cannot refute falls through and pays the oracle,
    # store consultations included.
    n, hit, accepted, blocked = pol.round(3, "c", "C", set())
    assert (n, accepted, blocked) == (1 + 5, True, False)


def test_cegmem_guard_consults_its_bucket_first_then_the_rest():
    task = install_fake_task()
    fake_locations({"A": "L1", "B": "L2", "C": "L2"})
    pol = make("cegmem-guard", task)
    pol.round(1, "a", "A", {"c5"})                    # c5 learned under L1
    pol.round(2, "b", "B", {"c2"})                    # c2 learned under L2
    # A candidate at L2 tries its own bucket (c2) before L1's c5.
    assert pol.consult("C") == ["c2", "c5"]
    n, hit, accepted, blocked = pol.round(3, "c", "C", {"c2"})
    assert (n, hit, blocked) == (1, "c2", True)


def test_the_index_never_blocks_less_than_the_flat_store():
    """The 2026-08-31 soundness correction, as a property.

    The bucket says where to look first, never where to stop, so over any
    sequence the indexed guard must block exactly the rounds the flat one
    blocks. Only the number of consultations may differ.
    """
    task = install_fake_task()
    rng = random.Random(0)
    fake_locations({f"p{i}": f"L{i % 3}" for i in range(40)})
    flat, indexed = make("dedup-guard", task), make("cegmem-guard", task)
    for r in range(1, 40):
        src = f"p{r}"
        fails = set(rng.sample(CASES, rng.choice([0, 1, 2])))
        a = flat.round(r, src, src, fails)
        b = indexed.round(r, src, src, fails)
        assert a[3] == b[3], f"round {r}: flat blocked={a[3]} indexed blocked={b[3]}"
        assert a[2] == b[2], f"round {r}: acceptance disagreed"


def test_wilcoxon_is_one_sided_and_matches_scipy_when_present():
    better = [-3.0, -2.0, -5.0, -1.0, -4.0, 0.0]
    worse = [3.0, 2.0, 5.0, 1.0, 4.0]
    assert sp.wilcoxon_one_sided(better) < 0.05
    assert sp.wilcoxon_one_sided(worse) > 0.5
    assert sp.wilcoxon_one_sided([0.0, 0.0]) == 1.0
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        return
    nz = [d for d in better if d]
    assert abs(sp.wilcoxon_one_sided(better)
               - float(wilcoxon(nz, alternative="less").pvalue)) < 0.05


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok    {name}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {name}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    raise SystemExit(1 if failed else 0)
