"""Validate the oracle against natural mutants and freeze the task list.

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

Stage 2 - does the oracle actually *catch* bugs it has never seen? Up to three
*natural* mutants are taken from the coding task itself: other people's wrong
submissions to the same problem (src.adapter.sibling_faults). The sampling
oracle the repair loop calls is asked to refute each. One it refutes is
`caught`. One it accepts gets a second opinion from the whole shipped pool,
which splits the failure in two:

  missed      the pool refutes the mutant but the sample did not draw the case
              that separates them. This is a real limit of the oracle at this
              `max_examples`, and it is what the stage is here to measure.
  equivalent  the pool does not refute it either. No oracle can catch this, so
              it is excluded from the denominator rather than charged against
              the oracle. Measured at 0 of 192 for natural mutants, against 21
              of 201 when this stage planted its own.

Why natural. An earlier version of this stage wrote its own mutants, one per
fault type of proposal section 3.5, by planting an edit in the reference. That
tests the oracle against bugs we chose, and the bugs one reaches for fail
loudly: a planted mutant was refuted by the very first sampled test 61% of the
time, against 27% for the submission's own real fault. The oracle was sitting
an exam we had written to be easy. A natural mutant is a real developer's real
mistake with a real mistake's detectability - median 3 sampled cases to refute
against 1 for a planted one - and it needs no AST site to occupy, which is why
the old stage 1.5 (excluding branch-free programs) is gone.

A program passes stage 2 when >= 2/3 of its scoreable natural mutants are
caught. The corpus is frozen only if >= 30 of every 40 in the cohort pass. Both
thresholds come from the proposal; neither is inferred from the data.

The cohort and the corpus are deliberately not the same set. The gate is
measured on the first `--corpus-size` candidates so it stays an unbiased
estimate of the pass rate, and the corpus is then topped up past the cohort
until it holds `--corpus-size` *passing* programs. Reporting a pass rate over
a set that was itself filtered on passing would make the gate vacuous.

Selection (--select hard, the default)
--------------------------------------
The corpus is 120 *hard* faults and nothing else. The earlier design balanced
easy/medium/hard quotas, and the pilot (data/pi_pilot.json) is what retired
it: median pi_hat came out 0.300 easy, 0.275 medium, 0.038 hard, so two thirds
of that corpus sat at a pi where the proposer usually succeeds on the first
try and no memory condition can separate from another. The discriminating
cells were all in one band, so the budget now buys 120 of those instead of 40
of them plus 80 that mostly answer themselves.

"Hard" is an absolute AtCoder rating floor (--hard-floor, default 1600), not a
tercile of whatever happens to be on disk. Terciles moved with the test tree -
on the partial tree salvaged from a truncated Test.zip the cut landed at 1577,
on the full tree it lands at 1639 - so the same seed named a different corpus
depending on how much of the download had survived, which is not a property a
frozen corpus may have. 1600 is AtCoder's own blue boundary, fixed outside
this project, and the pilot puts the band it selects at pi_hat ~ 0.04, inside
the paper's Hard range of 0.02-0.08.

The upper tail is deliberately left uncapped but is worth watching: a task at
pi ~ 0 is as uninformative as one at pi ~ 1, because every condition fails it
and nothing is separated. --hard-ceiling exists for when measured pi_hat says
a rating band has gone dead; it is not set from a guess.

Reproducibility. Everything the seed touches is a pure function evaluated
before any program is run:

  1. take the faults in adapter order (discover() sorts them), so the input to
     the shuffle does not depend on filesystem iteration order;
  2. drop those whose coding task ships no test data - a fault without test
     data can never clear stage 1;
  3. drop those whose coding task is rated below --hard-floor (or above
     --hard-ceiling) - a filter on difficulty.txt, no randomness in it;
  4. shuffle with random.Random(--seed), the single source of randomness;
  5. drop those whose coding task supplies no sibling wrong submission, since
     stage 2 would have no natural mutant to judge them with. After the
     shuffle, so that adding the constraint removes faults rather than
     re-indexing the rest - see scripts/select_hard_tasks.py;
  6. drop those whose source is longer than --max-source-chars, which the
     proposer could not rewrite inside its non-streaming output budget;
  7. walk that order, one fault per coding task, until 120 have passed.

Same seed, same floor and same test tree therefore give the same corpus, and
data/tasks.json records all three plus a digest of the candidate order so a
re-run can be checked against the freeze rather than trusted. --jobs only
changes how many candidates are in flight at once, never which ones are
chosen.

Selecting 120 passing faults needs a pool with room for them: the walk claims
a coding task the moment one of its faults enters stage 2, so a task whose fault
fails the mutation gate is spent. main() refuses to start when the hard pool
holds fewer than POOL_HEADROOM x 120 coding tasks rather than discovering it
hours in. The salvaged partial tree holds 123 - just short of the corpus size
itself - which is the concrete reason the full Test.zip has to be unpacked
before this runs.

--select terciles restores the old easy/medium/hard behaviour; --select none
takes one unstratified sample across every rating.

Writes:
    data/oracle_validation.json - full per-fault detail and every mutant, for audit
                                  (each mutant records which submission it was)
    data/tasks.json             - the frozen task list
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import itertools
import json
import math
import os
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import (CONDEFECTS_ROOT, TEST_DIR, SUPPORTED_PROGRAMS, TASKS, load,
                         sibling_faults, task_dates, task_difficulties, test_dir_for)
from src.oracle import differential_test, outputs_equal
from src.sandbox import DEFAULT_TIMEOUT, run_program

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_CORPUS_SIZE = 120
DEFAULT_REFERENCE_CASES = 20   # cases the reference must pass to count as one
# Natural mutants judged per coding task, and the share of the scoreable ones
# the oracle must refute. The proposal's criterion was "2 of 3"; held as the
# fraction so it means the same thing for a task that supplies one sibling as
# for one that supplies three - the corpus guarantees at least one but not
# three (DESIGN.md). At 2/3 a task with a single mutant must have it caught,
# and a task with two must have both.
MUTANTS_PER_TASK = 3
MUTANT_CATCH_FRACTION = 2 / 3
# The proposal's corpus gate is 30 of 40, held as a fraction so that any
# --corpus-size is gated at the same strictness rather than at a threshold it
# could never reach: 90 of 120 at the default, 30 of 40 on the old corpus, and
# a smoke run at 12 still has to clear 9. Written as the literal ratio and not
# as 30/DEFAULT_CORPUS_SIZE, which would have quietly become 25% strict when
# the default corpus grew.
CORPUS_PASS_FRACTION = 30 / 40

# Hard band: AtCoder's blue boundary. Absolute so that the same seed names the
# same corpus whatever fraction of Test.zip is on disk - see the module
# docstring for why terciles could not be.
DEFAULT_HARD_FLOOR = 1600
# Coding tasks per corpus slot the hard pool must hold before the walk starts.
# The pilot spent 102 candidates to pass 64 faults, ~1.6 examined per pass, and
# each examined fault claims its coding task for good.
#
# 1.5, not the 2.0 this started at. Requiring a natural mutant (--min-siblings)
# takes the floor-1600 pool from 336 coding tasks to 188, so 2.0 would refuse
# every run. The pilot's rate is also an overestimate now: 10 of its 102
# candidates were spent on programs excluded as branch-free, which the natural
# mutants no longer exclude, and none of its budget goes on equivalent mutants
# any more. 1.5 x 120 = 180 against a pool of 188 is genuinely tight, so the
# guard warns rather than staying silent whenever headroom is under 2.0.
POOL_HEADROOM = 1.5


def corpus_threshold(corpus_size: int) -> int:
    return math.ceil(CORPUS_PASS_FRACTION * corpus_size)


# Both stages are dominated by `python3` subprocesses that run one candidate on
# one test input, and src.sandbox.run_program keeps every one of them in its own
# temporary directory with its own environment. Nothing is shared, so the work
# parallelises across threads - the GIL is released for the whole subprocess.
DEFAULT_JOBS = max(1, (os.cpu_count() or 4) - 2)


# ── selection: the hard band ─────────────────────────────────────────────
# The paper's Easy/Medium/Hard are bands of pi = P[the proposer gets it right
# in one shot], and scripts/build_strata.py assigns them from *measured*
# pi_hat. That measurement costs a model call per sample, so it cannot drive
# selection: pi_hat is only knowable for faults already in the corpus, and
# paying for it on candidates that go on to fail the mutation gate would burn
# budget on programs nobody keeps.
#
# Selection therefore filters on the free a-priori proxy - the AtCoder rating
# ConDefects ships in difficulty.txt - and the pilot licenses the substitution
# rather than an assumption about it doing so: the band above the pilot's top
# tercile measured pi_hat ~ 0.038, against 0.275 and 0.300 for the two bands
# below it, so a rating floor really does isolate the low-pi regime the
# experiment is about. It remains a proxy; build_strata.py, working from
# measured pi_hat, stays the authority on the strata the paper reports, and
# the agreement between the two is itself worth reporting.
#
# A coding task with no line in difficulty.txt is dropped rather than assumed
# hard. Six of the benchmark's 985 tasks are unrated, and including them would
# put faults in a hard-only corpus on no evidence that they belong there.

def hard_pool(names: list[str], *, floor: int, ceiling: int | None = None) -> list[str]:
    """The faults whose coding task is rated in [floor, ceiling]. Pure."""
    difficulties = task_difficulties()
    kept = []
    for name in names:
        rating = difficulties.get(TASKS[name].task_id)
        if rating is None or rating < floor:
            continue
        if ceiling is not None and rating > ceiling:
            continue
        kept.append(name)
    return kept


def hard_selection_meta(names: list[str], *, floor: int, ceiling: int | None,
                        seed: int) -> dict:
    """What the corpus was drawn from, and the fingerprint of the draw.

    The digest covers the shuffled candidate order, which is the one thing the
    seed produces and the whole corpus follows from. Two freezes that agree on
    it were drawn from the same pool in the same order; two that disagree were
    not, and that is worth knowing before their numbers are compared.
    """
    difficulties = task_difficulties()
    ratings = sorted(difficulties[TASKS[n].task_id] for n in names)
    n = len(ratings)
    return {
        "mode": "hard",
        "proxy": "atcoder_difficulty_rating",
        "note": ("an absolute rating floor, not a tercile of the pool: the same "
                 "seed must name the same corpus whatever fraction of Test.zip is "
                 "unpacked. Licensed by the pilot's pi_hat ~ 0.04 for this band - "
                 "a proxy for the paper's pi bands, not a measurement of pi, see "
                 "scripts/build_strata.py"),
        "hard_floor": floor,
        "hard_ceiling": ceiling,
        "n_candidate_faults": n,
        "n_candidate_coding_tasks": len({TASKS[x].task_id for x in names}),
        "rating_quantiles": ({"min": ratings[0], "q1": ratings[n // 4],
                              "median": ratings[n // 2], "q3": ratings[3 * n // 4],
                              "max": ratings[-1]} if n else {}),
        "seed": seed,
        "candidate_order_sha256": hashlib.sha256("\n".join(names).encode()).hexdigest(),
    }


# ── selection: the retired easy/medium/hard quotas (--select terciles) ────
# Kept because the pilot corpus was frozen this way and re-deriving it has to
# stay possible. Terciles of the candidate pool's own ratings, an equal quota
# each, interleaved round-robin so all three fill at the same rate. The flaw
# the hard band exists to avoid is visible right here: the cuts are a function
# of the pool, so a different test tree silently redefines the bands.

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
    checked = 0
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
        checked += 1
    if checked == 0:
        # Every case was skipped for want of an out/ file, so the loop above
        # proved nothing and returning True would assert criterion 2 without a
        # single piece of evidence. Some Test.zip directories ship in/ without
        # out/ - the oracle can still fall back to executing the reference
        # (src/oracle.py::_expected_outcome), but then the reference is the
        # only authority in the loop and nothing ever cross-checks it against
        # what AtCoder actually accepted. Not a corpus we want.
        return False, "no case in the pool ships an expected output - criterion 2 is unverifiable"
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


# ── stage 2: does the oracle catch natural mutants? ───────────────────────
#
# A natural mutant is another person's wrong submission to the same coding task
# (src.adapter.sibling_faults). It is a known-broken program we did not write,
# which is the whole point: an earlier version of this stage planted its own
# mutants, and planting means choosing how to break the program. The breaks one
# reaches for fail loudly - on the pilot corpus a planted mutant was refuted by
# the very first sampled test 61% of the time, against 27% for the submission's
# own real fault, so the oracle was passing an exam we had written to be easy.
# Natural mutants carry a real mistake's detectability: median 3 sampled cases
# to refute against 1 for a planted one. They are also never wasted - all 192
# available across the corpus are refuted by their task's shipped pool, where 21
# of 201 planted mutants were refuted by no test at all and had to be discarded.

def _judge_mutant(task, program, mutant_source: str, meta: dict, *, max_examples: int,
                  seed: int, timeout: float, full_pool_cap: int) -> tuple[str, dict]:
    """caught | missed | equivalent | inconclusive, plus an audit record."""
    sampled = differential_test(
        task, mutant_source, program.correct_source,
        max_examples=max_examples, seed=seed, timeout=timeout,
    )
    detail = {
        **meta,
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
        task, mutant_source, program.correct_source,
        max_examples=full_pool_cap, seed=seed + 1, timeout=timeout,
    )
    detail["full_pool_examples_tried"] = full.examples_tried
    detail["full_pool_counterexample"] = full.args[0] if full.args else None
    if full.oracle_error is not None:
        return "inconclusive", detail
    return ("missed", detail) if not full.accept else ("equivalent", detail)


def check_mutants(name: str, *, max_examples: int, seed: int, timeout: float,
                  full_pool_cap: int, mutants_per_task: int = MUTANTS_PER_TASK) -> dict:
    """Ask the oracle to refute this coding task's other wrong submissions.

    Capped at `mutants_per_task` so a coding task with eleven submissions does
    not outweigh one with two; the draw guarantees at least one
    (select_hard_tasks.py --min-siblings).

    Only mutants the *full* pool can refute are scored. An `equivalent` verdict
    - no test in the shipped pool separates it from the reference - says nothing
    about the oracle and is excluded from the denominator rather than charged
    against it, exactly as it was for planted mutants. A program whose every
    mutant is unscoreable provides no evidence and does not pass.
    """
    task, program = TASKS[name], load(name)
    siblings = sibling_faults(name)[:mutants_per_task]
    per_mutant: dict[str, dict] = {}

    for index, sibling in enumerate(siblings):
        verdict, detail = _judge_mutant(
            task, program, load(sibling).buggy_source,
            {"source": "natural", "submission": sibling},
            # A distinct seed per mutant, so two siblings of one task are not
            # judged on the identical sample of test cases.
            max_examples=max_examples, seed=seed + 17 * index,
            timeout=timeout, full_pool_cap=full_pool_cap,
        )
        per_mutant[sibling] = {"verdict": verdict, **detail}

    verdicts = [r["verdict"] for r in per_mutant.values()]
    caught = [s for s, r in per_mutant.items() if r["verdict"] == "caught"]
    scored = sum(v in ("caught", "missed") for v in verdicts)
    return {
        "mutants_caught": len(caught),
        "mutants_total": len(siblings),
        "mutants_scored": scored,
        "mutants_natural": len(siblings),
        "caught_submissions": caught,
        "n_missed": verdicts.count("missed"),
        "n_equivalent": verdicts.count("equivalent"),
        "n_inconclusive": verdicts.count("inconclusive"),
        "passes": scored > 0 and len(caught) / scored >= MUTANT_CATCH_FRACTION,
        "mutants": per_mutant,
    }


# ── driver ────────────────────────────────────────────────────────────────

def run(names: list[str], *, corpus_size: int, max_examples: int, mutant_examples: int,
        reference_cases: int, seed: int, timeout: float, full_pool_cap: int,
        top_up: bool, jobs: int, mutants_per_task: int = MUTANTS_PER_TASK,
        band_of: dict[str, str] | None = None, bands: tuple[str, ...] = STRATA,
        per_stratum: int = 0, unstratified_label: str = "all") -> dict:
    """Stage 1 then stage 2, walking `names` until the corpus is full.

    Stage 2 is the expensive half (up to 3 natural mutants x a sampled oracle
    run each), so
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
    # The hard band is the second case: 120 slots, no sub-quotas, so the walk
    # is a plain seeded sample of the pool.
    bands = tuple(bands) if band_of else (unstratified_label,)
    quota = per_stratum if band_of else corpus_size
    cohort_by: dict[str, list[str]] = {b: [] for b in bands}
    passing_by: dict[str, list[str]] = {b: [] for b in bands}
    t0 = time.monotonic()
    batch_size = max(jobs * 3, 1)
    examined = 0
    done = False

    def band(name: str) -> str:
        return band_of.get(name, bands[-1]) if band_of else unstratified_label

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
                print(f"{tag} SKIP  {record['reason']}", flush=True)
                continue
            # At most one fault per coding task: several submissions can fail
            # the same AtCoder problem, and treating them as separate tasks
            # would put near-duplicate programs in the corpus and correlate
            # cells that the analysis assumes are independent.
            if record["task_id"] in seen_task_ids:
                record["reason"] = "another fault from this coding task is already in the corpus"
                print(f"{tag} DUP   {record['task_id']}", flush=True)
                continue
            # There is no stage 1.5 any more. It existed because a planted
            # mutant needs an AST site to occupy, and an accepted AtCoder
            # submission is sometimes branch-free - no `if`, no comparison,
            # just a comprehension and a print - so the taxonomy had nowhere to
            # go and scoring the program "1/3 caught" would have charged the
            # oracle for a property of the program. A natural mutant needs no
            # site: it is another submission, whatever shape this one has. The
            # only precondition left is that the coding task supplies at least
            # one sibling, which the draw guarantees before this script runs.
            siblings = sibling_faults(name)
            record["natural_mutants"] = list(siblings[:MUTANTS_PER_TASK])
            if not siblings:
                record["eligible"] = False
                record["reason"] = (
                    "no other wrong submission to this coding task, so the "
                    "oracle cannot be validated on it - re-run the draw with "
                    "scripts/select_hard_tasks.py --min-siblings 1"
                )
                print(f"{tag} INEL  {record['reason']}", flush=True)
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
                                    mutants_per_task=mutants_per_task),
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
            # flush: this is the only sign of life in a run that takes hours,
            # and stdout block-buffers as soon as it is not a terminal, so a
            # `> log` or `| tee` would otherwise show nothing until it filled.
            print(f"{record.pop('_tag')} {status}  "
                  f"{mutation['mutants_caught']}/{mutation['mutants_scored']} "
                  f"natural mutants caught"
                  f"{label}{extra}", flush=True)

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
        "mutant_source": (
            "natural: other wrong submissions to the same coding task "
            "(src.adapter.sibling_faults), capped at "
            f"{MUTANTS_PER_TASK} per task"
        ),
        "task_pass_threshold": (
            f">= {MUTANT_CATCH_FRACTION:.2f} of the scoreable natural mutants caught"
        ),
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
        "reference_cases": reference_cases,
        "timeout_sec": timeout,
        "seed": seed,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "faults": faults,
    }


def write_tasks_json(report: dict, selected: list[str], *, corpus_size: int, seed: int,
                     data_dir: pathlib.Path = DATA_DIR) -> None:
    frozen = report["corpus_gate_ok"] and bool(selected)
    meta = report.get("strata_selection") or {}
    # The one sentence a reader of the frozen file needs about *which* faults
    # these are, ahead of the mechanics of how many survived what.
    if meta.get("mode") == "hard":
        band = (f"every task is drawn from the hard band alone - AtCoder rating "
                f">= {meta['hard_floor']}"
                + (f" and <= {meta['hard_ceiling']}" if meta.get("hard_ceiling") else "")
                + f", {meta['n_candidate_coding_tasks']} coding tasks in the pool, "
                  f"shuffled with seed {seed}; ")
    else:
        band = ""
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
            f"{band}at most one fault per coding task; the gate is measured on the "
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
                "mutants_scored": report["faults"][name]["mutants_scored"],
                "natural_mutants": report["faults"][name]["natural_mutants"],
                "caught_submissions": report["faults"][name]["caught_submissions"],
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
    # Say so here rather than letting a later filter report an empty pool and
    # blame its own criterion for what is really a missing download.
    if not names:
        raise SystemExit(
            f"no fault has test data: nothing under {TEST_DIR}\n"
            f"Unpack Test.zip with `python3 scripts/fetch_condefects.py`, or point "
            f"CONDEFECTS_TEST_DIR at a tree that exists "
            f"(e.g. {CONDEFECTS_ROOT / 'Test_partial'})."
        )
    with_test_data = len(names)

    if args.select == "hard":
        # The whole strategy, in one line: keep the faults whose coding task
        # is rated hard, then draw from that pool alone. Applied before the
        # shuffle so the seed indexes the hard pool and not the benchmark -
        # raising --hard-floor changes which faults exist, not just where they
        # sit in a shared order.
        names = hard_pool(names, floor=args.hard_floor, ceiling=args.hard_ceiling)
        if not names:
            raise SystemExit(
                f"none of the {with_test_data} faults with test data under {TEST_DIR} "
                f"has a coding task rated >= {args.hard_floor}"
                + (f" and <= {args.hard_ceiling}" if args.hard_ceiling else "")
                + " - lower --hard-floor, or check that difficulty.txt covers this tree"
            )

    # Validating all ~2900 faults costs real wall time and the corpus needs a
    # bounded number of them, so shuffle first and stop once enough have
    # passed. The shuffle is the only randomness in selection: same seed, same
    # pool, same corpus.
    random.Random(args.seed).shuffle(names)

    # After the shuffle, exactly as scripts/select_hard_tasks.draw() does it, so
    # the two build the same order and this script walks the frozen draw rather
    # than re-deriving a different one. Filtering here rather than before the
    # shuffle is what makes the constraint additive: it removes the faults that
    # cannot be validated and leaves every other fault where it was.
    if args.min_siblings:
        names = [n for n in names if len(sibling_faults(n)) >= args.min_siblings]
    if args.max_source_chars:
        # Same ceiling, same place in the order, as scripts/select_hard_tasks.py:
        # a program longer than this cannot be rewritten inside the proposer's
        # non-streaming output budget, so an episode on it would record a
        # mechanical failure rather than a hard one.
        names = [n for n in names if len(load(n).buggy_source) <= args.max_source_chars]
        if not names:
            raise SystemExit(
                f"no fault in the pool has {args.min_siblings} sibling wrong "
                f"submission(s) to validate the oracle against - lower "
                f"--min-siblings, or check that Code/ is the full checkout"
            )

    if args.select == "hard":
        return names, {}, hard_selection_meta(
            names, floor=args.hard_floor, ceiling=args.hard_ceiling, seed=args.seed)
    if args.select == "none":
        return names, {}, {
            "mode": "none",
            "note": "one unstratified sample across every rating",
            "n_candidate_faults": len(names),
            "seed": args.seed,
            "candidate_order_sha256": hashlib.sha256("\n".join(names).encode()).hexdigest(),
        }

    band_of, meta = stratify_by_difficulty(names)
    if not band_of:
        raise SystemExit("no difficulty ratings available - cannot stratify; use --select none")
    # Interleave the strata round-robin so all three fill at the same rate.
    # Walking them one after another would spend the whole candidate budget on
    # "easy" before ever testing whether "hard" can be filled at all.
    per_band = [[n for n in names if band_of.get(n) == b] for b in STRATA]
    interleaved = [n for row in itertools.zip_longest(*per_band) for n in row if n is not None]
    # The digest covers the interleaved order, which is the order actually
    # walked - the shuffled order is only an input to it.
    meta = {"mode": "terciles", **meta, "seed": args.seed,
            "candidate_order_sha256": hashlib.sha256("\n".join(interleaved).encode()).hexdigest()}
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
    parser.add_argument("--mutants-per-task", type=int, default=MUTANTS_PER_TASK,
                        help="natural mutants judged per coding task, so a task with "
                             "eleven wrong submissions does not outweigh one with two")
    parser.add_argument("--reference-cases", type=int, default=DEFAULT_REFERENCE_CASES,
                        help="cases the reference must answer correctly")
    parser.add_argument("--no-top-up", action="store_true",
                        help="freeze only the passing members of the cohort, do not look further")
    parser.add_argument("--select", choices=("hard", "terciles", "none"), default="hard",
                        help="hard (default): draw the whole corpus from faults rated "
                             "at or above --hard-floor, the low-pi band the experiment "
                             "is about; terciles: the retired easy/medium/hard equal "
                             "quotas; none: one unstratified sample")
    parser.add_argument("--hard-floor", type=int, default=DEFAULT_HARD_FLOOR,
                        help=f"--select hard keeps coding tasks rated >= this "
                             f"(default: {DEFAULT_HARD_FLOOR}, AtCoder's blue boundary)")
    parser.add_argument("--hard-ceiling", type=int, default=None,
                        help="drop coding tasks rated above this; for excluding a tail "
                             "that measured pi_hat shows is dead (no default - do not "
                             "set it from a guess)")
    parser.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                        help=f"candidates to evaluate concurrently (default: {DEFAULT_JOBS}); "
                             "1 is the serial path, immune to timeout jitter under load")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-source-chars", type=int, default=48_000,
                        help="longest program to select; must match "
                             "scripts/select_hard_tasks.py --max-source-chars")
    parser.add_argument("--min-siblings", type=int, default=1,
                        help="natural mutants a fault must bring; must match "
                             "scripts/select_hard_tasks.py --min-siblings")
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

    stratified = args.select == "terciles" and args.programs is None
    if stratified and args.corpus_size % len(STRATA):
        raise SystemExit(
            f"--corpus-size {args.corpus_size} is not divisible by {len(STRATA)}; a "
            f"stratified corpus holds an equal quota per level (try "
            f"{args.corpus_size - args.corpus_size % len(STRATA)}) or pass --select none"
        )

    names, band_of, strata_meta = _candidates(args)
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        raise SystemExit(f"unknown fault(s): {unknown}")
    if args.programs is None:
        names = names[:args.max_candidates or (max(args.corpus_size, 1) * 12)]
        band_of = {n: b for n, b in band_of.items() if n in set(names)}

    # Refuse a walk that cannot finish, before it spends hours proving it.
    # Only distinct coding tasks count: the corpus holds one fault each, and a
    # task is spent as soon as one of its faults reaches stage 2, pass or fail.
    if args.select == "hard" and args.programs is None:
        n_tasks = len({TASKS[n].task_id for n in names})
        needed = math.ceil(POOL_HEADROOM * args.corpus_size)
        if n_tasks < needed:
            raise SystemExit(
                f"the eligible pool holds {n_tasks} coding tasks (floor {args.hard_floor}, "
                f">= {args.min_siblings} sibling, test tree {TEST_DIR}); a "
                f"{args.corpus_size}-task corpus needs about {needed} to absorb the "
                f"faults that fail the gate.\n"
                f"Unpack the full Test.zip - a partial tree is a prefix of the contest "
                f"range, not a sample of it - or lower --hard-floor / --min-siblings / "
                f"--corpus-size, knowing that lowering the floor moves the corpus out "
                f"of the low-pi band and lowering --min-siblings leaves faults with no "
                f"natural mutant to validate the oracle against."
            )
        if n_tasks < 2.0 * args.corpus_size:
            # Above the refusal line but below the margin this had before the
            # sibling requirement halved the pool. Say so now: the failure mode
            # is a walk that runs for hours and then cannot fill the corpus.
            print(f"warning: {n_tasks} eligible coding tasks for a {args.corpus_size}-task "
                  f"corpus ({n_tasks / args.corpus_size:.2f}x). The pilot spent ~1.6 "
                  f"candidates per passing fault; if this run's rate is worse the pool "
                  f"will run out before the corpus fills.", file=sys.stderr)

    report = run(
        names,
        corpus_size=args.corpus_size,
        max_examples=args.max_examples,
        mutant_examples=args.mutant_examples or args.max_examples,
        mutants_per_task=args.mutants_per_task,
        reference_cases=args.reference_cases,
        seed=args.seed,
        timeout=args.timeout,
        full_pool_cap=args.full_pool_cap,
        top_up=not args.no_top_up,
        jobs=max(1, args.jobs),
        band_of=band_of or None,
        bands=STRATA,
        per_stratum=args.corpus_size // len(STRATA) if stratified else 0,
        # Every task in a hard-only corpus is labelled "hard", not "all": the
        # label travels into data/tasks.json and has to say what the task is.
        unstratified_label="hard" if args.select == "hard" else "all",
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
          f"{report['n_ineligible']} excluded for having no sibling submission")
    print(f"stage 2: {report['n_cohort_passing']}/{report['n_cohort']} of the cohort caught "
          f">= {MUTANT_CATCH_FRACTION:.0%} of their natural mutants "
          f"(threshold {corpus_threshold(args.corpus_size)}/{args.corpus_size}) "
          f"-- {'MET' if report['corpus_gate_ok'] else 'NOT MET'}")
    n_caught = sum(r.get("mutants_caught", 0) for r in report["faults"].values())
    n_scored = sum(r.get("mutants_scored", 0) for r in report["faults"].values())
    print(f"         {n_caught}/{n_scored} natural mutants caught overall; "
          f"{n_missed} missed by the sample, {n_equiv} conceded equivalent")
    meta = report.get("strata_selection") or {}
    if meta.get("mode") == "hard":
        ratings = sorted(report["faults"][n]["difficulty"] for n in selected)
        span = (f"{ratings[0]}..{ratings[-1]}, median {ratings[len(ratings) // 2]}"
                if ratings else "-")
        print(f"select:  hard only, AtCoder rating >= {meta['hard_floor']}"
              + (f" and <= {meta['hard_ceiling']}" if meta.get("hard_ceiling") else "")
              + f" -- pool {meta['n_candidate_faults']} faults over "
                f"{meta['n_candidate_coding_tasks']} coding tasks")
        print(f"         selected ratings {span}; "
              f"order {meta['candidate_order_sha256'][:12]} (seed {args.seed})")
    elif meta.get("cuts"):
        per = {b: report["strata"][b]["n_passing"] for b in STRATA}
        print(f"strata:  AtCoder difficulty terciles, cuts at {meta['cuts']} -> "
              + ", ".join(f"{b} {per[b]}" for b in STRATA)
              + f" passing (quota {report['per_stratum']} each)")
    print(f"corpus {'FROZEN' if report['corpus_gate_ok'] and selected else 'NOT FROZEN'}: "
          f"{len(selected)} faults, one per coding task, in {report['elapsed_sec']}s")
    if not report["corpus_gate_ok"]:
        print("the oracle did not clear the mutation gate - do not run experiments on this corpus")
    print(f"wrote {args.data_dir}/oracle_validation.json and {args.data_dir}/tasks.json")


if __name__ == "__main__":
    main()
