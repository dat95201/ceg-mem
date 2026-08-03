"""Validate the oracle against planted mutants and freeze the task list.

Two stages, in this order, because each answers a different question.

Stage 1 - is this fault *usable* as an experiment cell? Four conditions:

  1. test data is present for the coding task (Test.zip unpacked);
  2. the reference (correctVersion.py) passes every case it is run on - if it
     does not, it is not a reference and no divergence from it means anything;
  3. the faulty version fails at least one case - otherwise the shipped pool
     does not expose this fault and the oracle can never refute a patch, so
     an episode would accept the unmodified program in round 1;
  4. neither version times out, so a repair round is not dominated by a
     candidate the sandbox has to kill.

Stage 1.5 - can the program host the fault taxonomy? Some accepted AtCoder
submissions are branch-free: no `if`, no comparison, a comprehension and a
print. There is no site in them for tau_2 or tau_3, so they are excluded here
rather than scored - charging the oracle for a mutant that could never have
existed would make the stage-2 rate mean something else.

Stage 2 - does the oracle actually *catch* bugs it has never seen? Three
mutants are planted in the reference, one per fault type of proposal section
3.5 (data/mutants.py), and the sampling oracle the repair loop calls is asked
to refute each. A mutant it refutes is `caught`. One it accepts gets a second
opinion from the whole shipped pool, which splits the failure in two:

  missed      the pool refutes the mutant but the sample did not draw the case
              that separates them. This is a real limit of the oracle at this
              `max_examples`, and it is what the stage is here to measure.
  equivalent  the pool does not refute it either. No oracle can catch this; it
              is an equivalent mutant, a known artefact of mutation testing and
              not evidence about the oracle. The generator retries on its next
              candidate site (--mutant-retries) before giving up on the type.

A program passes stage 2 when >= 2 of its 3 mutants are caught. The corpus is
frozen only if >= 30 of the 40-program cohort pass. Both thresholds come from
the proposal; neither is inferred from the data.

The cohort and the corpus are deliberately not the same set. The gate is
measured on the first `--corpus-size` candidates so it stays an unbiased
estimate of the pass rate, and the corpus is then topped up past the cohort
until it holds `--corpus-size` *passing* programs. Reporting a pass rate over
a set that was itself filtered on passing would make the gate vacuous.

Selection (--stratify difficulty, the default)
----------------------------------------------
The corpus is balanced across the paper's three levels, an equal quota each,
rather than taken as one unstratified sample that might land 45 easy programs
and 3 hard ones. The levels are bands of pi, which is *measured* and therefore
unknown at selection time, so the balance is struck on the AtCoder difficulty
rating ConDefects ships instead - see the STRATA comment below for why that
substitution is sound and where it stops being sound.

Reproducibility. Everything the seed touches is a pure function evaluated
before any program is run:

  1. filter the ~2,900 Python faults to those whose coding task ships test
     data (~931 here) - a fault without test data can never clear stage 1, and
     leaving it in would drag the strata boundaries towards a population that
     cannot be selected from;
  2. cut that pool's difficulty ratings into terciles - a function of
     difficulty.txt and the pool, with no randomness in it at all;
  3. shuffle with random.Random(--seed), the single source of randomness;
  4. interleave the three strata round-robin, so all three fill at the same
     rate instead of spending the whole candidate budget on "easy" before
     discovering whether "hard" can be filled.

Same seed and same test tree therefore give the same candidate order, and the
walk down that order is deterministic. --jobs only changes how many candidates
are in flight at once, never which ones are chosen.

Writes:
    data/oracle_validation.json - full per-fault detail and every mutant, for audit
    data/tasks.json             - the frozen task list
"""
from __future__ import annotations

import argparse
import concurrent.futures
import itertools
import json
import math
import os
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.mutants import FAULT_TYPES, candidate_mutants, mutant_capacity
from src.adapter import (TEST_DIR, SUPPORTED_PROGRAMS, TASKS, load, task_dates,
                         task_difficulties, test_dir_for)
from src.oracle import differential_test, outputs_equal
from src.sandbox import DEFAULT_TIMEOUT, run_program

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_CORPUS_SIZE = 40
DEFAULT_REFERENCE_CASES = 20   # cases the reference must pass to count as one
MUTANTS_TO_CATCH = 2           # of 3, for a program to pass stage 2
# The proposal's corpus gate is 30 of 40. Held as a fraction so that a smaller
# --corpus-size (a smoke run) is gated at the same strictness rather than at a
# threshold it could never reach; at the default 40 it is exactly 30.
CORPUS_PASS_FRACTION = 30 / DEFAULT_CORPUS_SIZE


def corpus_threshold(corpus_size: int) -> int:
    return math.ceil(CORPUS_PASS_FRACTION * corpus_size)


# Both stages are dominated by `python3` subprocesses that run one candidate on
# one test input, and src.sandbox.run_program keeps every one of them in its own
# temporary directory with its own environment. Nothing is shared, so the work
# parallelises across threads - the GIL is released for the whole subprocess.
DEFAULT_JOBS = max(1, (os.cpu_count() or 4) - 2)


# ── selection strata: the paper's three difficulty levels ────────────────
# The paper's Easy/Medium/Hard are bands of pi = P[the proposer gets it right
# in one shot], and scripts/build_strata.py assigns them from *measured*
# pi_hat. That measurement costs a model call per sample, so it cannot drive
# selection: pi_hat is only knowable for faults already in the corpus, and
# paying for it on candidates that go on to fail the mutation gate would burn
# budget on programs nobody keeps.
#
# So selection balances on a free a-priori proxy instead - the AtCoder
# difficulty rating ConDefects ships in difficulty.txt - and the proxy's bands
# are terciles of the *candidate pool's own* rating distribution rather than
# thresholds imposed from outside. A harder contest problem is harder to
# repair in one shot, so the rating runs opposite to pi: the lowest tercile is
# "easy" (high pi), the highest is "hard" (low pi).
#
# This is a proxy and is labelled as one. It buys a corpus with 20 programs in
# each band instead of one that happens to land 45 easy and 3 hard;
# build_strata.py remains the authority on the strata the paper reports, and
# the agreement between the two is itself worth reporting.

STRATA = ("easy", "medium", "hard")   # ascending AtCoder rating


def stratify_by_difficulty(names: list[str]) -> tuple[dict[str, str], dict]:
    """name -> stratum, plus the tercile cuts, as a pure function of the pool."""
    difficulties = task_difficulties()
    rated = sorted({TASKS[n].task_id for n in names} & set(difficulties))
    if len(rated) < len(STRATA):
        return {}, {}
    ratings = sorted(difficulties[t] for t in rated)
    cuts = [ratings[len(ratings) * i // len(STRATA)] for i in range(1, len(STRATA))]

    def band(rating: int) -> str:
        for i, cut in enumerate(cuts):
            if rating < cut:
                return STRATA[i]
        return STRATA[-1]

    band_of = {
        n: band(difficulties[TASKS[n].task_id])
        for n in names if TASKS[n].task_id in difficulties
    }
    counts = {b: sum(v == b for v in band_of.values()) for b in STRATA}
    return band_of, {
        "proxy": "atcoder_difficulty_tercile",
        "note": ("terciles of the candidate pool's difficulty.txt ratings; a proxy "
                 "for the paper's pi bands, not a measurement of pi - see "
                 "scripts/build_strata.py"),
        "cuts": cuts,
        "n_rated_tasks": len(rated),
        "candidates_per_stratum": counts,
    }


# ── stage 1: is the fault usable? ─────────────────────────────────────────

def _check_reference(program, task, *, n_cases: int, timeout: float) -> tuple[bool, str]:
    """Criterion 2 + half of 4: does correctVersion.py answer its own tests?"""
    cases = task.test_cases[:n_cases]
    for case in cases:
        if case.expected_output is None:
            continue
        outcome = run_program(program.correct_source, case.input_text, timeout=timeout)
        if outcome.timed_out:
            return False, f"reference timed out on {case.name}"
        if not outcome.ok:
            return False, f"reference raised {outcome.error_type} on {case.name}"
        if not outputs_equal(outcome.value, case.expected_output):
            return False, f"reference disagrees with the shipped output on {case.name}"
    return True, ""


def check_usable(name: str, *, max_examples: int, reference_cases: int, seed: int,
                 timeout: float) -> dict:
    task = TASKS[name]
    dates, difficulties = task_dates(), task_difficulties()
    record = {
        "task_id": task.task_id,
        "program_id": task.program_id,
        "date": dates.get(task.task_id),
        "difficulty": difficulties.get(task.task_id),
        "n_test_cases": len(task.test_cases),
        "loc": None,
        "fault_lines": None,
        "fault_exposed": False,
        "reference_ok": False,
        "reason": None,
        "usable": False,
    }

    if not task.test_cases:
        record["reason"] = "no test data for this coding task"
        return record

    program = load(name)
    record["loc"] = len(program.buggy_source.splitlines())
    record["fault_lines"] = list(program.fault_lines)

    reference_ok, why = _check_reference(program, task, n_cases=reference_cases, timeout=timeout)
    record["reference_ok"] = reference_ok
    if not reference_ok:
        record["reason"] = why
        return record

    # Criterion 3: run the *faulty* version through the same oracle the repair
    # loop uses. A refutation here is the fault being exposed.
    result = differential_test(
        task, program.buggy_source, program.correct_source,
        max_examples=max_examples, seed=seed, timeout=timeout,
    )
    record["fault_exposed"] = not result.accept and result.oracle_error is None
    record["examples_tried"] = result.examples_tried
    record["counterexample"] = result.args[0] if result.args else None
    record["reason"] = result.reason if not record["fault_exposed"] else None
    record["usable"] = record["fault_exposed"]
    return record


# ── stage 2: does the oracle catch planted mutants? ───────────────────────

def _judge_mutant(task, program, mutant, *, max_examples: int, seed: int, timeout: float,
                  full_pool_cap: int) -> tuple[str, dict]:
    """caught | missed | equivalent | inconclusive, plus an audit record."""
    sampled = differential_test(
        task, mutant.source, program.correct_source,
        max_examples=max_examples, seed=seed, timeout=timeout,
    )
    detail = {
        "site": mutant.site,
        "note": mutant.note,
        "diff": mutant.diff,
        "examples_tried": sampled.examples_tried,
        "counterexample": sampled.args[0] if sampled.args else None,
        "reason": sampled.reason,
    }
    if sampled.oracle_error is not None:
        return "inconclusive", detail
    if not sampled.accept:
        return "caught", detail

    # The sample accepted it. Ask the whole pool whether anything separates
    # them at all - that is what tells a weak sample from a non-bug.
    full = differential_test(
        task, mutant.source, program.correct_source,
        max_examples=full_pool_cap, seed=seed + 1, timeout=timeout,
    )
    detail["full_pool_examples_tried"] = full.examples_tried
    detail["full_pool_counterexample"] = full.args[0] if full.args else None
    if full.oracle_error is not None:
        return "inconclusive", detail
    return ("missed", detail) if not full.accept else ("equivalent", detail)


def check_mutants(name: str, *, max_examples: int, seed: int, timeout: float,
                  full_pool_cap: int, retries: int) -> dict:
    """Plant one mutant per fault type and ask the oracle to refute each.

    An `equivalent` verdict is retried on the fault type's next candidate site,
    up to `retries` attempts, because it says nothing about the oracle.
    """
    task, program = TASKS[name], load(name)
    per_type: dict[str, dict] = {}

    for fault_type in FAULT_TYPES:
        candidates = candidate_mutants(name, program.correct_source, fault_type)
        if not candidates:
            per_type[fault_type] = {"verdict": "unavailable", "attempts": 0,
                                    "reason": "the program admits no site for this fault type"}
            continue
        for attempt, mutant in enumerate(candidates[:max(retries, 1)], 1):
            verdict, detail = _judge_mutant(
                task, program, mutant, max_examples=max_examples, seed=seed,
                timeout=timeout, full_pool_cap=full_pool_cap,
            )
            if verdict != "equivalent":
                break
        per_type[fault_type] = {"verdict": verdict, "attempts": attempt, **detail}

    caught = [f for f, r in per_type.items() if r["verdict"] == "caught"]
    return {
        "mutants_caught": len(caught),
        "mutants_total": len(FAULT_TYPES),
        "fault_types_caught": caught,
        "n_missed": sum(r["verdict"] == "missed" for r in per_type.values()),
        "n_equivalent": sum(r["verdict"] == "equivalent" for r in per_type.values()),
        "n_unavailable": sum(r["verdict"] == "unavailable" for r in per_type.values()),
        "passes": len(caught) >= MUTANTS_TO_CATCH,
        "mutants": per_type,
    }


# ── driver ────────────────────────────────────────────────────────────────

def run(names: list[str], *, corpus_size: int, max_examples: int, mutant_examples: int,
        reference_cases: int, seed: int, timeout: float, full_pool_cap: int,
        retries: int, top_up: bool, jobs: int,
        band_of: dict[str, str] | None = None, per_stratum: int = 0) -> dict:
    """Stage 1 then stage 2, walking `names` until the corpus is full.

    Stage 2 is the expensive half (3+ mutants x a sampled oracle run each), so
    it is only ever paid for faults that already cleared stage 1, and the walk
    stops as soon as there are enough passing programs.

    Candidates are evaluated a batch at a time, `jobs` of them at once, and the
    results folded back **in candidate order**. Which faults reach the cohort
    and the corpus is therefore a function of the seeded candidate order alone,
    exactly as at jobs=1: the concurrency buys wall time and changes nothing
    about the selection.

    One caveat, recorded in the report rather than hidden. A candidate sitting
    near the sandbox's wall-clock limit can time out under parallel load when it
    would have finished serially, and a timeout is a verdict here, not a retry.
    Re-run the freeze that goes in the paper with --jobs 1 if that matters.
    """
    faults: dict[str, dict] = {}
    seen_task_ids: set[str] = set()
    # One quota per stratum when stratifying, otherwise a single "all" bucket
    # holding the whole corpus - the rest of the loop does not branch on which.
    bands = STRATA if band_of else ("all",)
    quota = per_stratum if band_of else corpus_size
    cohort_by: dict[str, list[str]] = {b: [] for b in bands}
    passing_by: dict[str, list[str]] = {b: [] for b in bands}
    t0 = time.monotonic()
    batch_size = max(jobs * 3, 1)
    examined = 0
    done = False

    def band(name: str) -> str:
        return band_of.get(name, STRATA[-1]) if band_of else "all"

    def band_full(b: str) -> bool:
        return len(cohort_by[b]) >= quota and (not top_up or len(passing_by[b]) >= quota)

    def _in_parallel(fn, items):
        if not items:
            return []
        if jobs == 1:
            return [fn(item) for item in items]
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(fn, items))

    for start in range(0, len(names), batch_size):
        if done:
            break
        batch = [n for n in names[start:start + batch_size] if not band_full(band(n))]
        if not batch:
            continue

        records = _in_parallel(
            lambda n: check_usable(n, max_examples=max_examples,
                                   reference_cases=reference_cases,
                                   seed=seed, timeout=timeout),
            batch,
        )

        # Fold stage 1 in order, and decide who is worth paying stage 2 for.
        todo: list[str] = []
        for name, record in zip(batch, records):
            examined += 1
            faults[name] = record
            tag = f"[{examined:4d}/{len(names)}] {name:30s}"

            if not record["usable"]:
                print(f"{tag} SKIP  {record['reason']}")
                continue
            # At most one fault per coding task: several submissions can fail
            # the same AtCoder problem, and treating them as separate tasks
            # would put near-duplicate programs in the corpus and correlate
            # cells that the analysis assumes are independent.
            if record["task_id"] in seen_task_ids:
                record["reason"] = "another fault from this coding task is already in the corpus"
                print(f"{tag} DUP   {record['task_id']}")
                continue
            # Stage 1.5: can this program host the fault taxonomy at all? An
            # accepted AtCoder submission is sometimes branch-free - no `if`,
            # no comparison, just a comprehension and a print - and then there
            # is no site for tau_2 or tau_3 to occupy. Scoring such a program
            # "1/3 caught" would charge the oracle for a property of the
            # program, so it is excluded here the way an unusable fault is.
            capacity = mutant_capacity(name, load(name).correct_source)
            record["mutant_capacity"] = capacity
            missing = [f for f, n in capacity.items() if not n]
            if missing:
                record["eligible"] = False
                record["reason"] = f"program admits no mutation site for: {', '.join(missing)}"
                print(f"{tag} INEL  {record['reason']}")
                continue
            record["eligible"] = True
            # Claimed only now: an ineligible program never enters the corpus,
            # so it must not lock out another submission to the same task.
            seen_task_ids.add(record["task_id"])
            record["_tag"] = tag
            todo.append(name)

        mutations = _in_parallel(
            lambda n: check_mutants(n, max_examples=mutant_examples, seed=seed,
                                    timeout=timeout, full_pool_cap=full_pool_cap,
                                    retries=retries),
            todo,
        )

        for name, mutation in zip(todo, mutations):
            record = faults[name]
            b = band(name)
            record["stratum"] = b
            record.update(mutation)
            record["in_cohort"] = len(cohort_by[b]) < quota
            if record["in_cohort"]:
                cohort_by[b].append(name)
            if mutation["passes"]:
                passing_by[b].append(name)

            status = "PASS" if mutation["passes"] else "FAIL"
            extra = "" if record["in_cohort"] else " (top-up)"
            label = f" {b}" if band_of else ""
            print(f"{record.pop('_tag')} {status}  "
                  f"{mutation['mutants_caught']}/3 caught "
                  f"[{'+'.join(mutation['fault_types_caught']) or 'none'}]"
                  f"{label}{extra}")

            if all(band_full(x) for x in bands):
                done = True

    cohort = [n for b in bands for n in cohort_by[b]]
    passing = [n for b in bands for n in passing_by[b]]
    cohort_passing = [n for n in cohort if faults[n].get("passes")]
    return {
        "usability_criterion": (
            "test data present; reference passes its own cases; the faulty "
            "version is refuted by the shipped pool; neither version times out"
        ),
        "task_pass_threshold": f">= {MUTANTS_TO_CATCH}/{len(FAULT_TYPES)} mutants caught",
        "corpus_pass_threshold": f">= {corpus_threshold(corpus_size)}/{corpus_size} programs passing",
        # Provenance. A corpus frozen against a salvaged partial test tree is
        # not the corpus the paper reports, and the only way to tell after the
        # fact is to have written down which tree it was read from.
        "test_dir": str(TEST_DIR),
        "jobs": jobs,
        "n_examined": len(faults),
        "n_usable": sum(r["usable"] for r in faults.values()),
        "n_ineligible": sum(r.get("eligible") is False for r in faults.values()),
        "strata": {b: {
            "cohort": cohort_by[b],
            "n_cohort": len(cohort_by[b]),
            "n_passing": len(passing_by[b]),
            "quota": quota,
        } for b in bands},
        "per_stratum": quota if band_of else None,
        "cohort": cohort,
        "n_cohort": len(cohort),
        "n_cohort_passing": len(cohort_passing),
        "corpus_gate_ok": len(cohort_passing) >= corpus_threshold(corpus_size) and len(cohort) == corpus_size,
        "n_passing": len(passing),
        "max_examples": max_examples,
        "mutant_examples": mutant_examples,
        "full_pool_cap": full_pool_cap,
        "mutant_retries": retries,
        "reference_cases": reference_cases,
        "timeout_sec": timeout,
        "seed": seed,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "faults": faults,
    }


def write_tasks_json(report: dict, selected: list[str], *, corpus_size: int, seed: int,
                     data_dir: pathlib.Path = DATA_DIR) -> None:
    frozen = report["corpus_gate_ok"] and bool(selected)
    tasks_json = {
        "frozen": frozen,
        "benchmark": "ConDefects (Python)",
        "test_dir": report["test_dir"],
        "strata_selection": report.get("strata_selection"),
        "criterion": (
            f"a program is selected if it catches {report['task_pass_threshold']}; "
            f"the corpus is frozen only if {report['corpus_pass_threshold']}"
        ),
        "usability_criterion": report["usability_criterion"],
        "selection": (
            f"at most one fault per coding task; the gate is measured on the "
            f"first {report['n_cohort']} stage-1 survivors "
            f"({report['n_cohort_passing']} passed), and the corpus is topped up "
            f"past that cohort until {corpus_size} passing programs are held"
        ),
        "n_selected": len(selected),
        "n_cohort": report["n_cohort"],
        "n_cohort_passing": report["n_cohort_passing"],
        "n_examined": report["n_examined"],
        "seed": seed,
        "tasks": [
            {
                "name": name,
                "task_id": report["faults"][name]["task_id"],
                "program_id": report["faults"][name]["program_id"],
                "date": report["faults"][name]["date"],
                "difficulty": report["faults"][name]["difficulty"],
                "loc": report["faults"][name]["loc"],
                "fault_lines": report["faults"][name]["fault_lines"],
                "n_test_cases": report["faults"][name]["n_test_cases"],
                "counterexample": report["faults"][name]["counterexample"],
                "stratum": report["faults"][name].get("stratum"),
                "mutants_caught": report["faults"][name]["mutants_caught"],
                "fault_types_caught": report["faults"][name]["fault_types_caught"],
            }
            for name in selected
        ],
    }
    (data_dir / "tasks.json").write_text(json.dumps(tasks_json, indent=2) + "\n")


def _candidates(args) -> tuple[list[str], dict[str, str], dict]:
    """(ordered candidates, name -> stratum, strata metadata)."""
    if args.programs:
        return list(args.programs), {}, {}

    names = list(SUPPORTED_PROGRAMS)
    dates, difficulties = task_dates(), task_difficulties()
    if args.since:
        names = [n for n in names if (dates.get(TASKS[n].task_id) or "") >= args.since]
    if args.until:
        names = [n for n in names if (dates.get(TASKS[n].task_id) or "9999") <= args.until]
    if args.min_difficulty is not None:
        names = [n for n in names if (difficulties.get(TASKS[n].task_id, -1)) >= args.min_difficulty]
    if args.max_difficulty is not None:
        names = [n for n in names if (difficulties.get(TASKS[n].task_id, 10**9)) <= args.max_difficulty]

    # Drop faults whose coding task ships no test data before doing anything
    # else. They can never clear stage 1, so leaving them in would spend the
    # --max-candidates budget on SKIP lines - and, worse, they carry difficulty
    # ratings, so they would drag the strata boundaries below towards a
    # population that cannot be selected from. test_dir_for only stats a
    # directory; test_cases_for would read every input file on disk.
    names = [n for n in names if test_dir_for(TASKS[n].task_id) is not None]

    # Validating all ~2900 faults costs real wall time and the corpus only
    # needs a few dozen, so shuffle first and stop once enough have passed.
    # The shuffle is the only randomness in selection: same seed, same corpus.
    random.Random(args.seed).shuffle(names)
    if args.stratify != "difficulty":
        return names, {}, {}

    band_of, meta = stratify_by_difficulty(names)
    if not band_of:
        raise SystemExit("no difficulty ratings available - cannot stratify; use --stratify none")
    # Interleave the strata round-robin so all three fill at the same rate.
    # Walking them one after another would spend the whole candidate budget on
    # "easy" before ever testing whether "hard" can be filled at all.
    per_band = [[n for n in names if band_of.get(n) == b] for b in STRATA]
    interleaved = [n for row in itertools.zip_longest(*per_band) for n in row if n is not None]
    return interleaved, band_of, meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--programs", nargs="+", default=None, help="validate exactly these faults")
    parser.add_argument("--corpus-size", type=int, default=DEFAULT_CORPUS_SIZE,
                        help="cohort size for the gate, and how many passing faults to freeze")
    parser.add_argument("--max-examples", type=int, default=80,
                        help="test cases the oracle may run per fault in stage 1")
    parser.add_argument("--mutant-examples", type=int, default=None,
                        help="test cases the oracle may run per mutant (default: --max-examples)")
    parser.add_argument("--full-pool-cap", type=int, default=300,
                        help="cases the equivalent-mutant second opinion may run")
    parser.add_argument("--mutant-retries", type=int, default=3,
                        help="candidate sites to try per fault type before conceding equivalence")
    parser.add_argument("--reference-cases", type=int, default=DEFAULT_REFERENCE_CASES,
                        help="cases the reference must answer correctly")
    parser.add_argument("--no-top-up", action="store_true",
                        help="freeze only the passing members of the cohort, do not look further")
    parser.add_argument("--stratify", choices=("difficulty", "none"), default="difficulty",
                        help="balance the corpus across the paper's three levels using "
                             "AtCoder difficulty terciles as an a-priori proxy for pi "
                             "(default), or take one unstratified sample")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help=f"candidates to evaluate concurrently (default: {DEFAULT_JOBS}); "
                             "1 is the serial path, immune to timeout jitter under load")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--since", default=None, help="keep coding tasks on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None, help="keep coding tasks on/before this date (YYYY-MM-DD)")
    parser.add_argument("--min-difficulty", type=int, default=None)
    parser.add_argument("--max-difficulty", type=int, default=None)
    parser.add_argument("--max-candidates", type=int, default=None,
                        help="stop after examining this many faults (default: 12x corpus-size)")
    parser.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR,
                        help="where to write tasks.json and oracle_validation.json; point a "
                             "smoke run somewhere else so it cannot overwrite a real freeze")
    args = parser.parse_args()

    if not SUPPORTED_PROGRAMS:
        raise SystemExit("no ConDefects faults found - run scripts/fetch_condefects.py first")

    stratified = args.stratify == "difficulty" and args.programs is None
    if stratified and args.corpus_size % len(STRATA):
        raise SystemExit(
            f"--corpus-size {args.corpus_size} is not divisible by {len(STRATA)}; a "
            f"stratified corpus holds an equal quota per level (try "
            f"{args.corpus_size - args.corpus_size % len(STRATA)}) or pass --stratify none"
        )

    names, band_of, strata_meta = _candidates(args)
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        raise SystemExit(f"unknown fault(s): {unknown}")
    if args.programs is None:
        names = names[:args.max_candidates or (max(args.corpus_size, 1) * 12)]
        band_of = {n: b for n, b in band_of.items() if n in set(names)}

    report = run(
        names,
        corpus_size=args.corpus_size,
        max_examples=args.max_examples,
        mutant_examples=args.mutant_examples or args.max_examples,
        reference_cases=args.reference_cases,
        seed=args.seed,
        timeout=args.timeout,
        full_pool_cap=args.full_pool_cap,
        retries=args.mutant_retries,
        top_up=not args.no_top_up,
        jobs=max(1, args.jobs),
        band_of=band_of or None,
        per_stratum=args.corpus_size // len(STRATA) if stratified else 0,
    )
    report["strata_selection"] = strata_meta or None
    if band_of:
        # Equal quota per level, each in candidate order, easy -> medium -> hard.
        by_band: dict[str, list[str]] = {b: [] for b in STRATA}
        for n, r in report["faults"].items():
            if r.get("passes"):
                by_band[r["stratum"]].append(n)
        quota = args.corpus_size // len(STRATA)
        selected = [n for b in STRATA for n in by_band[b][:quota]]
    else:
        selected = [n for n, r in report["faults"].items() if r.get("passes")][:args.corpus_size]

    args.data_dir.mkdir(parents=True, exist_ok=True)
    (args.data_dir / "oracle_validation.json").write_text(json.dumps(report, indent=2) + "\n")
    write_tasks_json(report, selected, corpus_size=args.corpus_size, seed=args.seed,
                     data_dir=args.data_dir)

    n_missed = sum(r.get("n_missed", 0) for r in report["faults"].values())
    n_equiv = sum(r.get("n_equivalent", 0) for r in report["faults"].values())
    print()
    print(f"stage 1: {report['n_usable']}/{report['n_examined']} faults usable, "
          f"{report['n_ineligible']} excluded as branch-free (no site for all three fault types)")
    print(f"stage 2: {report['n_cohort_passing']}/{report['n_cohort']} of the cohort caught "
          f">= {MUTANTS_TO_CATCH}/3 mutants "
          f"(threshold {corpus_threshold(args.corpus_size)}/{args.corpus_size}) "
          f"-- {'MET' if report['corpus_gate_ok'] else 'NOT MET'}")
    print(f"         {n_missed} mutants missed by the sample, {n_equiv} conceded equivalent")
    if report.get("strata_selection"):
        cuts = report["strata_selection"]["cuts"]
        per = {b: report["strata"][b]["n_passing"] for b in STRATA}
        print(f"strata:  AtCoder difficulty terciles, cuts at {cuts} -> "
              + ", ".join(f"{b} {per[b]}" for b in STRATA)
              + f" passing (quota {report['per_stratum']} each)")
    print(f"corpus {'FROZEN' if report['corpus_gate_ok'] and selected else 'NOT FROZEN'}: "
          f"{len(selected)} faults, one per coding task, in {report['elapsed_sec']}s")
    if not report["corpus_gate_ok"]:
        print("the oracle did not clear the mutation gate - do not run experiments on this corpus")
    print(f"wrote {args.data_dir}/oracle_validation.json and {args.data_dir}/tasks.json")


if __name__ == "__main__":
    main()
