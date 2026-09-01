"""The slow-task filter: one clock, on criterion 2's own run.

A coding task whose CORRECT solution cannot answer one of its own shipped
inputs inside --reference-timeout is dropped before stage 2. What these tests
pin is not the threshold - that is a budget dial and it moves - but the four
properties that let it be a threshold at all:

  * it fires on the FIRST overrun, so a slow task costs one timeout to find
    rather than twenty, and never reaches stage 2's mutant judging;
  * it is distinguishable in the record (`slow`) from a fault that failed the
    gate on its merits, so a corpus can report what cost dropped versus what
    correctness dropped;
  * the measured seconds are recorded on EVERY record, passing or failing, so
    the corpus can be re-filtered at another threshold from the frozen artifact
    instead of by re-running the gate;
  * it never looks at anything an arm produced. The inputs are the reference
    solution and the problem's own test data, both fixed before the first
    proposal is drawn.

Measured on all 526 Stage-0 candidates at the shipped 10s: 34 candidates
dropped, 11 of the 315-task gated pool, 3 of the 106-task corpus. The curve
either side is 0 of 106 at 30s and 7 of 106 at 5s - there is no cliff to
discover, which is exactly why the number is declared rather than derived.

No LLM, no network: run_program and the task table are replaced in the module
namespace, so only the filter's control flow is under test.
"""
import sys
import types

sys.path.insert(0, '.')
import scripts.validate_oracle as vo

FAST, SLOW = 0.05, 99.0


def install(case_secs, *, timeout):
    """Replace the sandbox. `case_secs` is the wall clock each case would take;
    anything at or above `timeout` times out, as the real sandbox would."""
    runs = []

    def run_program(src, input_text, timeout=timeout, **kw):
        secs = case_secs[int(input_text)]
        runs.append(input_text)
        if secs >= timeout:
            return types.SimpleNamespace(timed_out=True, ok=False, value=None,
                                         error_type="Timeout", duration=float(timeout))
        return types.SimpleNamespace(timed_out=False, ok=True, value="OK",
                                     error_type=None, duration=secs)

    vo.run_program = run_program
    vo.outputs_equal = lambda a, b: True
    cases = [types.SimpleNamespace(name=f"c{i}", input_text=str(i), expected_output="OK")
             for i in range(len(case_secs))]
    task = types.SimpleNamespace(test_cases=cases)
    program = types.SimpleNamespace(correct_source="REF", buggy_source="BUG", fault_lines=[1])
    return runs, task, program


def test_a_slow_case_is_caught_and_labelled_slow():
    runs, task, program = install([FAST, FAST, SLOW, FAST], timeout=10.0)
    ok, why, stats = vo._check_reference(program, task, n_cases=20, timeout=10.0)
    assert ok is False
    assert stats["slow"] is True, "a cost drop must be distinguishable from a gate failure"
    assert ">10s" in why and "c2" in why, why


def test_it_stops_at_the_first_overrun():
    """THE saving. Twenty cases behind a slow one must not be run."""
    runs, task, program = install([FAST, SLOW] + [FAST] * 18, timeout=10.0)
    vo._check_reference(program, task, n_cases=20, timeout=10.0)
    assert len(runs) == 2, f"ran {len(runs)} cases; the filter must stop at the first overrun"


def test_a_fast_task_passes_and_runs_every_case():
    runs, task, program = install([FAST] * 20, timeout=10.0)
    ok, why, stats = vo._check_reference(program, task, n_cases=20, timeout=10.0)
    assert ok is True and why == ""
    assert stats["slow"] is False
    assert len(runs) == 20


def test_the_cost_is_recorded_on_a_passing_record_too():
    """So the threshold can be moved later without re-running the gate."""
    runs, task, program = install([0.1, 0.2, 0.3], timeout=10.0)
    ok, _, stats = vo._check_reference(program, task, n_cases=20, timeout=10.0)
    assert ok is True
    assert abs(stats["reference_sec_total"] - 0.6) < 1e-6, stats
    assert abs(stats["reference_sec_max"] - 0.3) < 1e-6, stats
    assert stats["reference_cases_run"] == 3
    assert stats["reference_timeout"] == 10.0, "the threshold in force must be on the record"


def test_the_threshold_is_the_only_thing_that_moves_the_verdict():
    """Same task, two thresholds: kept at 30s, dropped at 10s. Nothing else
    about the task changed, which is what makes this a declared dial."""
    secs = [FAST, 12.0, FAST]
    for timeout, expect_slow in ((30.0, False), (10.0, True)):
        _, task, program = install(secs, timeout=timeout)
        ok, _, stats = vo._check_reference(program, task, n_cases=20, timeout=timeout)
        assert stats["slow"] is expect_slow, f"at {timeout}s: slow={stats['slow']}"
        if not expect_slow:
            assert ok is True


def test_a_wrong_reference_is_not_reported_as_slow():
    """Criterion 2 can still fail on correctness. The two reasons must not be
    conflated - one says the benchmark data is unusable, the other says the
    task is expensive."""
    _, task, program = install([FAST, FAST], timeout=10.0)
    vo.outputs_equal = lambda a, b: False
    ok, why, stats = vo._check_reference(program, task, n_cases=20, timeout=10.0)
    assert ok is False and stats["slow"] is False
    assert "disagrees" in why, why


def test_check_usable_never_reaches_the_oracle_for_a_slow_task():
    """Stage 1 pays one timeout; stage 2's mutant judging - the expensive half
    - is never entered. differential_test raises if it is called at all."""
    _, task, program = install([SLOW], timeout=10.0)
    vo.TASKS = {"t": types.SimpleNamespace(task_id="t", program_id="p", test_cases=task.test_cases)}
    vo.load = lambda n: program
    vo.task_dates = lambda: {}
    vo.task_difficulties = lambda: {}

    def boom(*a, **k):
        raise AssertionError("the oracle was called for a task the filter dropped")

    vo.differential_test = boom
    rec = vo.check_usable("t", max_examples=80, reference_cases=20, seed=1,
                          timeout=30.0, reference_timeout=10.0)
    assert rec["usable"] is False and rec["slow"] is True
    assert rec["reference_sec_max"] == 10.0


def test_the_default_is_the_one_that_was_measured():
    assert vo.DEFAULT_REFERENCE_TIMEOUT == 10.0, (
        "the docstring's 34/526, 11/315 and 3/106 were measured at 10s; "
        "moving the default means re-measuring them")


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  ok  {name}"); n += 1
    print(f"{n} passed")
