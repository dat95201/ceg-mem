"""--corpus-size auto, and telling a short walk from a failed oracle.

The gate refuses to freeze unless len(cohort) == corpus_size exactly. That check
is right - it stops a short walk from silently shrinking the denominator its
pass rate is measured against - but it means the operator must know how many
faults will survive stage 1 before running stage 1. The slow-task filter just
changed that number: the previous freeze used 315 and after the filter it is
304, which nobody can know without running the gate.

`auto` removes the guess without weakening anything. The pre-registered quantity
was never N - it is CORPUS_PASS_FRACTION - and N was already data-determined
(315 = however many turned out eligible). Auto makes N *all* of them, the one
choice with no freedom left to tune a flattering denominator out of.

These tests pin the four properties that make that true:

  * auto takes every eligible fault into the cohort, and the gate is then
    decided on that population;
  * a declared size still behaves exactly as it did, short walk and all;
  * the report says WHICH mode ran, so two freezes are comparable;
  * cohort_short_by separates "the walk came up short" from "the oracle failed",
    because the two need opposite responses and wore one message until now.

No sandbox, no benchmark: check_usable and check_mutants are replaced in the
module namespace, so only run()'s bookkeeping is under test.
"""
import sys
import types

sys.path.insert(0, '.')
import scripts.validate_oracle as vo

BAND = "hard"          # what --select hard labels every fault


def install(n_faults, *, n_pass=None, n_slow=0):
    """`n_faults` candidates; the first n_slow are dropped by the filter and the
    rest are usable, of which n_pass catch enough natural mutants."""
    n_pass = n_faults - n_slow if n_pass is None else n_pass
    names = [f"t{i:03d}/p" for i in range(n_faults)]
    order = {n: i for i, n in enumerate(names)}

    def check_usable(name, **kw):
        i = order[name]
        slow = i < n_slow
        return {"task_id": f"t{i:03d}", "program_id": "p", "usable": not slow,
                "slow": slow, "reference_sec_max": 99.0 if slow else 0.1,
                "reason": "reference needs >10s" if slow else None,
                "difficulty": 1700, "date": "2023-01-01", "n_test_cases": 20,
                "loc": 40, "fault_lines": [1], "reference_ok": not slow,
                "fault_exposed": not slow, "examples_tried": 1,
                "counterexample": "x"}

    def check_mutants(name, **kw):
        i = order[name]
        passes = (i - n_slow) < n_pass
        return {"mutants_caught": 2 if passes else 0, "mutants_scored": 3,
                "n_missed": 0 if passes else 3, "n_equivalent": 0,
                "passes": passes, "per_mutant": {}}

    vo.check_usable = check_usable
    vo.check_mutants = check_mutants
    vo.sibling_faults = lambda n: ["sib"]
    vo.TASKS = {n: types.SimpleNamespace(task_id=n.split("/")[0]) for n in names}
    return names


def go(names, corpus_size):
    return vo.run(names, corpus_size=corpus_size, max_examples=10, mutant_examples=10,
                  reference_cases=5, seed=1, timeout=5.0, full_pool_cap=10,
                  top_up=True, jobs=1, band_of=None, bands=vo.STRATA,
                  per_stratum=0, unstratified_label=BAND)


def test_auto_takes_every_eligible_fault():
    names = install(40, n_slow=6)                 # 34 usable
    r = go(names, None)
    assert r["corpus_size_mode"] == "auto"
    assert r["corpus_size"] == 34, r["corpus_size"]
    assert r["n_cohort"] == 34
    assert r["cohort_short_by"] == 0
    assert r["corpus_gate_ok"] is True


def test_auto_still_fails_when_the_oracle_does():
    """Auto changes the denominator, not the bar. Half passing is below 75%."""
    names = install(40, n_pass=20)
    r = go(names, None)
    assert r["corpus_size"] == 40 and r["n_cohort_passing"] == 20
    assert r["cohort_short_by"] == 0, "nothing was short - this is an oracle failure"
    assert r["corpus_gate_ok"] is False


def test_a_declared_size_caps_the_cohort_as_before():
    names = install(40)
    r = go(names, 25)
    assert r["corpus_size_mode"] == "declared"
    assert r["n_cohort"] == 25, "the cap stopped applying"
    assert r["corpus_gate_ok"] is True


def test_a_declared_size_the_walk_cannot_reach_is_a_SHORT_walk():
    """The case that sent the last freeze round the houses: 315 declared, 304
    eligible. Not a verdict on the oracle - everything it found passed."""
    names = install(40, n_slow=6)                 # 34 eligible, 40 asked for
    r = go(names, 40)
    assert r["n_cohort"] == 34
    assert r["cohort_short_by"] == 6, r["cohort_short_by"]
    assert r["corpus_gate_ok"] is False
    assert r["n_cohort_passing"] == r["n_cohort"], "every fault it did find passed"


def test_short_and_failed_are_distinguishable():
    """One field has to separate them, because the fixes are opposite: a short
    walk wants a smaller --corpus-size, a low pass rate wants a better oracle."""
    short = go(install(40, n_slow=6), 40)
    failed = go(install(40, n_pass=20), 40)
    assert short["cohort_short_by"] > 0 and failed["cohort_short_by"] == 0
    assert not short["corpus_gate_ok"] and not failed["corpus_gate_ok"]


def test_the_threshold_follows_the_effective_size():
    names = install(40, n_slow=6)
    r = go(names, None)
    assert r["corpus_pass_threshold"] == f">= {vo.corpus_threshold(34)}/34 programs passing"


def test_the_filter_is_visible_in_the_auto_report():
    """A cohort that shrank because of the slow-task filter must say so, or two
    freezes with different corpus_size look like a mystery."""
    r = go(install(40, n_slow=6), None)
    assert r["n_slow"] == 6
    assert len(r["slow_tasks"]) == 6


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  ok  {name}"); n += 1
    print(f"{n} passed")
