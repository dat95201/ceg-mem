#!/usr/bin/env python3
"""P1-1, stage 1 - the (candidate x case) verdict matrix.

The frozen log records where each candidate failed FIRST, not where it fails.
`src.oracle.differential_test` stops at the first case that separates the
candidate from the reference, so a round leaves one case name behind and says
nothing about the other 38 in that task's pool. Every ordering policy we need to
compare against needs the rest: Qi et al.'s fault-recorded prioritization orders
cases by *how many earlier candidates each one killed*, which is a column of the
kill matrix, and the log only ever holds its first non-zero entry per row.

So this stage fills the matrix in. For every distinct (task, patch) the log
holds, run the patch against every usable case in the task's shipped pool and
record the verdict. No model is called: the patches are already in the log
(`patch`, the full source), and the reference answers come from the shipped
`out/` files. The only cost is sandbox time.

    python3 scripts/build_verdict_matrix.py --plan-only         # cost first
    python3 scripts/build_verdict_matrix.py --jobs 4            # build it
    python3 scripts/build_verdict_matrix.py --verify            # then check it

WHY `no_memory` IS THE DEFAULT UNIVERSE.  A policy comparison is only clean if
every policy sees the same candidates. In the no-memory arm the proposer is
shown nothing the validator found, so the candidate stream is a function of
(task, seed, round) alone and is therefore *independent of the validation
policy*: five policies can be replayed over byte-identical candidates. The typed
and untyped arms steer the prompt, so their streams are policy-dependent and
cannot serve as a common denominator. `--modes` widens the universe if you want
the matrix for something else; it does not make those arms comparable.

WHAT "USABLE" MEANS.  Mirrors `src.oracle`: a case whose reference answer cannot
be established is skipped rather than counted against the candidate. For
ConDefects that is rare - almost every case ships an `out/` file - but the rule
has to be the same one the oracle used or the replay is measuring a different
instrument.

RESUMABLE.  Verdicts append to `verdicts.jsonl` and completed (task, patch)
pairs are skipped on a re-run, so a killed job costs one candidate, not the
whole matrix. The file is the artifact; nothing is held in memory across runs.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import paths
from src.adapter import TASKS, test_cases_for, test_dir_for
from src.oracle import outputs_equal
from src.sandbox import DEFAULT_TIMEOUT, run_program

DEFAULT_MODES = ("no_memory",)


def patch_sha(source: str) -> str:
    """Identity of a candidate. The same 12 hex digits `simulate_policies.py`
    joins on, and short enough that the matrix stays readable by eye."""
    return hashlib.sha256(source.encode()).hexdigest()[:12]


def reference_values(task_name: str, timeout: float) -> dict[str, str]:
    """case name -> the reference answer, for every case that has one.

    Computed once per task rather than once per candidate: with 8,570 candidates
    over 99 tasks, running the reference per candidate would multiply the bill by
    the pool size for information that does not vary. Cases absent from the
    result are the "unusable" ones `src.oracle` skips.
    """
    task = TASKS[task_name]
    out: dict[str, str] = {}
    for case in task.test_cases:
        if case.expected_output is not None:
            out[case.name] = case.expected_output
            continue
        ref = run_program(task_correct_source(task_name), case.input_text, timeout=timeout)
        if ref.ok:
            out[case.name] = ref.value
    return out


def case_names(task_id: str) -> list[str]:
    """Every shipped case name for a coding task, without reading a byte of it.

    `src.adapter.test_cases_for` is `lru_cache(maxsize=None)` and reads the input
    AND expected-output text of every case. One AtCoder task's test data reaches
    84 MB, so materialising 99 of them to answer "how many cases are there" costs
    gigabytes and gets the process killed. The names are all the plan needs, and
    all `simulate_policies.py` ever needs.
    """
    root = test_dir_for(task_id)
    if root is None:
        return []
    return sorted(p.name for p in (root / "in").iterdir()
                  if p.is_file() and not p.name.startswith("."))


_CORRECT: dict[str, str] = {}


def task_correct_source(task_name: str) -> str:
    if task_name not in _CORRECT:
        from src.adapter import load
        _CORRECT[task_name] = load(task_name).correct_source
    return _CORRECT[task_name]


# The fields anything downstream of this module reads off a round. Everything
# else - `patch` above all - is dropped as the line is parsed.
KEEP = ("episode_id", "task", "mode", "seed", "round_index", "max_examples",
        "guarded", "counterexample_args", "typing_random", "audit_guarded",
        "free_guarded_rounds")


def stream_rounds(episodes: pathlib.Path, source_modes: tuple[str, ...] = ()):
    """Last-write-wins per (episode_id, round_index), one line at a time.

    `src.metrics.load_rounds` performs the same collapse, but it materialises
    every row - and the frozen log is 77 MB of rows each carrying a full patch
    source, which is roughly 2 GB once parsed. A Colab VM is killed by that, and
    nothing here needs a whole row: the patch is reduced to its sha as the line
    is read, and the source text is kept only for the arms that will actually be
    executed.

    Returns (rows, sources): rows carry KEEP plus `patch_sha`, sources maps a sha
    to its text for the requested arms only.
    """
    latest: dict[tuple, dict] = {}
    sources: dict[str, str] = {}
    with episodes.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = raw.get("patch")
            sha = patch_sha(src) if src else None
            if sha and (not source_modes or raw.get("mode") in source_modes):
                sources.setdefault(sha, src)
            row = {k: raw.get(k) for k in KEEP}
            row["patch_sha"] = sha
            latest[(row["episode_id"], row["round_index"])] = row
    return list(latest.values()), sources


def candidates_from_log(episodes: pathlib.Path, modes: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """task -> {patch_sha: patch source}, over the requested arms."""
    rows, sources = stream_rounds(episodes, source_modes=modes)
    out: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for row in rows:
        if modes and row["mode"] not in modes:
            continue
        if not row["patch_sha"]:
            continue
        out[row["task"]][row["patch_sha"]] = sources[row["patch_sha"]]
    return dict(out)


def already_done(verdicts: pathlib.Path) -> set[tuple[str, str]]:
    """(task, patch_sha) pairs the file already holds a complete row for."""
    if not verdicts.is_file():
        return set()
    seen: set[tuple[str, str]] = set()
    with verdicts.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line from a kill; the pair re-runs
            if row.get("complete"):
                seen.add((row["task"], row["patch_sha"]))
    return seen


def run_candidate(task_name: str, sha: str, source: str,
                  refs: dict[str, str], timeout: float) -> dict:
    """One row of the matrix: this candidate against every usable case."""
    task = TASKS[task_name]
    fails: list[str] = []
    cases: dict[str, bool] = {}
    t0 = time.perf_counter()
    for case in task.test_cases:
        if case.name not in refs:
            continue
        cand = run_program(source, case.input_text, timeout=timeout)
        # The verdict rule is src.oracle.differential_test's, verbatim: a timeout
        # or a crash is a failure, otherwise compare judge-normalised stdout.
        ok = not (cand.timed_out or not cand.ok) and outputs_equal(cand.value, refs[case.name])
        cases[case.name] = ok
        if not ok:
            fails.append(case.name)
    return {
        "task": task_name,
        "patch_sha": sha,
        "n_cases": len(cases),
        "n_fail": len(fails),
        "fails": fails,          # the row's information content; passes are the complement
        "sec": round(time.perf_counter() - t0, 3),
        "complete": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--episodes", type=pathlib.Path, default=paths.EPISODES)
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="default: <data dir>/verdicts.jsonl")
    ap.add_argument("--modes", nargs="*", default=list(DEFAULT_MODES),
                    help="arms whose candidates enter the matrix (default: no_memory)")
    ap.add_argument("--tasks", nargs="*", default=None, help="restrict to these tasks")
    ap.add_argument("--limit", type=int, default=0, help="stop after N candidates (smoke test)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--progress-every", type=float, default=60.0)
    ap.add_argument("--plan-only", action="store_true",
                    help="print the cost estimate and execute nothing")
    ap.add_argument("--verify", action="store_true",
                    help="do not build; replay the frozen log against an existing matrix")
    args = ap.parse_args()

    paths.ensure(paths.DATA_DIR)
    out_path = args.out or (paths.DATA_DIR / "verdicts.jsonl")

    if args.verify:
        return verify(args.episodes, out_path)

    paths.announce("verdict-matrix")
    if not TASKS:
        print("no ConDefects tasks loaded - is external/ConDefects/Test unpacked?",
              file=sys.stderr)
        return 2

    by_task = candidates_from_log(args.episodes, tuple(args.modes))
    if args.tasks:
        by_task = {t: v for t, v in by_task.items() if t in set(args.tasks)}
    missing = [t for t in by_task if t not in TASKS]
    if missing:
        print(f"{len(missing)} task(s) in the log are not in the benchmark, skipping: "
              f"{missing[:3]}", file=sys.stderr)
        by_task = {t: v for t, v in by_task.items() if t in TASKS}

    pool = {t: len(case_names(TASKS[t].task_id)) for t in by_task}
    n_cand = sum(len(v) for v in by_task.values())
    n_exec = sum(len(v) * pool[t] for t, v in by_task.items())
    # 0.808 s/case is this study's own measured rate: 249,448 case executions in
    # 201,491 oracle-seconds on the frozen log. Not a guess, and not the 0.15 s
    # PLAN-experiments.md assumed.
    est_h = n_exec * 0.808 / 3600
    print(f"tasks       {len(by_task)}")
    print(f"candidates  {n_cand}")
    print(f"executions  {n_exec:,}")
    print(f"estimate    {est_h:.1f} CPU-hours  ({est_h / max(1, args.jobs):.1f} h wall at "
          f"--jobs {args.jobs})")
    if args.plan_only:
        return 0

    done = already_done(out_path)
    todo = [(t, sha, src) for t, v in sorted(by_task.items())
            for sha, src in sorted(v.items()) if (t, sha) not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"already done {len(done)}, to run {len(todo)}", flush=True)
    if not todo:
        return 0

    # Grouped by task, and the adapter's unbounded case cache is dropped between
    # tasks. Without that the process holds every task's test data at once - 84 MB
    # for one AtCoder problem, gigabytes across the corpus - and is killed. The
    # grouping is also why the reference answers are computed once per task.
    by_task_todo: dict[str, list[tuple[str, str, str]]] = collections.defaultdict(list)
    for item in todo:
        by_task_todo[item[0]].append(item)

    refs_cache: dict[str, dict[str, str]] = {}
    refs_lock = threading.Lock()
    # The usable case list per task, in pool order. simulate_policies.py needs it
    # to order a suite, and it cannot re-derive it: "usable" depends on whether a
    # reference answer could be established, which is exactly what this stage
    # spent the time finding out. Rewritten whole on each new task - 99 short
    # lists, so the cost is nothing and a killed job still leaves it valid.
    cases_path = out_path.parent / "verdicts_cases.json"
    usable_by_task: dict[str, list[str]] = {}
    if cases_path.is_file():
        try:
            usable_by_task = json.loads(cases_path.read_text())
        except json.JSONDecodeError:
            usable_by_task = {}

    def refs_for(task_name: str) -> dict[str, str]:
        with refs_lock:
            if task_name in refs_cache:
                return refs_cache[task_name]
        r = reference_values(task_name, args.timeout)
        names = case_names(TASKS[task_name].task_id)
        with refs_lock:
            refs_cache.clear()                 # one task's references at a time
            refs_cache[task_name] = r
            # `pool` is every case in pool order, which is the universe the
            # oracle's seeded draw samples from; `usable` is the subset with an
            # establishable reference answer, which is what a policy may charge
            # for. simulate_policies.py needs both and can then run without the
            # benchmark checkout at all.
            usable_by_task[task_name] = {"pool": names,
                                         "usable": [n for n in names if n in r]}
            cases_path.write_text(json.dumps(usable_by_task, indent=1))
        return r

    write_lock = threading.Lock()
    state = {"n": 0, "exec": 0, "t0": time.time(), "last": 0.0}

    def work(item):
        task_name, sha, src = item
        row = run_candidate(task_name, sha, src, refs_for(task_name), args.timeout)
        with write_lock:
            with out_path.open("a") as f:
                f.write(json.dumps(row) + "\n")
            state["n"] += 1
            state["exec"] += row["n_cases"]
            now = time.time()
            if now - state["last"] >= args.progress_every:
                state["last"] = now
                el = now - state["t0"]
                rate = state["n"] / el if el else 0
                left = (len(todo) - state["n"]) / rate if rate else float("inf")
                print(f"  {state['n']}/{len(todo)} candidates  "
                      f"{state['exec']:,} executions  "
                      f"{el/60:.1f} min elapsed  ~{left/60:.1f} min left", flush=True)

    for task_name in sorted(by_task_todo):
        with ThreadPoolExecutor(max_workers=args.jobs) as pool_exec:
            list(pool_exec.map(work, by_task_todo[task_name]))
        test_cases_for.cache_clear()           # release this task's test data

    print(f"done: {state['n']} candidates, {state['exec']:,} executions, "
          f"{(time.time() - state['t0'])/60:.1f} min -> {out_path}")
    print(f"usable-case lists for {len(usable_by_task)} tasks -> {cases_path}")
    print("next: python3 scripts/build_verdict_matrix.py --verify")
    return 0


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def load_matrix(path: pathlib.Path) -> dict[tuple[str, str], set[str]]:
    """(task, patch_sha) -> the set of cases the candidate FAILS."""
    m: dict[tuple[str, str], set[str]] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("complete"):
                m[(row["task"], row["patch_sha"])] = set(row["fails"])
    return m


def verify(episodes: pathlib.Path, matrix_path: pathlib.Path) -> int:
    """Replay every logged oracle round against the matrix.

    THE ACCEPTANCE GATE for this stage, declared before it was built: the matrix
    must reproduce the counterexample the harness actually found in at least 95%
    of the log's oracle rounds. Anything lower means the sandbox is not
    deterministic beyond timeout jitter, and every policy simulated on top of the
    matrix would be measuring that jitter instead of the policy.

    The draw is reproducible: `src.oracle._sample` is
    `random.Random(seed).sample(...)` and `src.loop` calls it with
    `seed = seed + round_index`, both recorded per round.
    """
    import random

    if not matrix_path.is_file():
        print(f"no matrix at {matrix_path} - run without --verify first", file=sys.stderr)
        return 2
    matrix = load_matrix(matrix_path)
    print(f"matrix rows {len(matrix)}")

    agree = disagree = missing = skipped = 0
    examples: list[str] = []
    rows, _ = stream_rounds(episodes)          # no sources needed: the sha is enough
    for row in rows:
        if row.get("guarded") or not row.get("patch_sha"):
            continue
        if not row.get("counterexample_args"):
            continue  # accepted, or an oracle error: no first-failing case to check
        key = (row["task"], row["patch_sha"])
        if key not in matrix:
            missing += 1
            continue
        task = TASKS.get(row["task"])
        if task is None:
            skipped += 1
            continue
        k = row.get("max_examples") or 100
        seed = (row.get("seed") or 0) + (row.get("round_index") or 0)
        cases = case_names(task.task_id)       # names only: see case_names()
        draw = cases if k >= len(cases) else random.Random(seed).sample(list(cases), k)
        fails = matrix[key]
        first = next((c for c in draw if c in fails), None)
        if first == row["counterexample_args"][0]:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 5:
                examples.append(f"{row['task']} seed={row['seed']} r={row['round_index']}: "
                                f"log={row['counterexample_args'][0]} matrix={first}")

    total = agree + disagree
    rate = agree / total if total else 0.0
    print(f"oracle rounds checked {total}   agree {agree}   disagree {disagree}")
    print(f"rows not in the matrix {missing}   tasks not loaded {skipped}")
    print(f"reproduction rate {rate:.4f}   gate 0.95   "
          f"{'PASS' if rate >= 0.95 else 'FAIL'}")
    for e in examples:
        print("  mismatch:", e)
    return 0 if rate >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
