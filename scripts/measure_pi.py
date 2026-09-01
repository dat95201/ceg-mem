"""Pilot: measure pi_hat = P[the LLM proposes a correct patch in one shot].

N independent calls per fault. Every call uses mode="no_memory" with an empty
history - no error feedback, no conversation, no evidence or exclusion in the
prompt - so each call is i.i.d, matching pi = P[G proposes a correct patch]
from the proposal's notation (Table 1). This is a baseline measurement, not an
episode: each call is scored independently by the oracle and discarded, not
fed forward.

Writes data/pi_pilot.json: per-fault success count/rate, the pooled estimate
across all calls, and `corpus_source` - which freeze the corpus came from, so a
pi_hat measured before the mutation gate is never mistaken for one measured
after it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import SUPPORTED_PROGRAMS, TASKS, load
from src.llm import MODEL as LLM_MODEL, TEMPERATURE as LLM_TEMPERATURE, BudgetExceeded
from src.oracle import differential_test
from src.proposer import TruncatedResponse, propose
from src.sandbox import DEFAULT_TIMEOUT as SANDBOX_TIMEOUT

from src.paths import DATA_DIR, announce  # noqa: E402

# One number for the whole screen, and scripts/screen_shard.sh passes the same
# one explicitly. 10 is a first pass: it places a task roughly, and deepening is
# cheap because a re-run at a larger K replays draws 0..K-1 from cache and buys
# only the difference. It is deliberately not enough to resolve every band -
# pi_hat lives on a grid of 1/K and [0.02, 0.08) contains no multiple of 1/10 -
# and scripts/consolidate_screens.py says so, with the K that would.
DEFAULT_CALLS_PER_PROGRAM = 10

# Facts about the backend that the launcher observed and this process cannot:
# the served context window, the model blob digest behind the tag, the
# quantisation, the server version. Written straight into the report so a shard
# run on another machine can be checked against this one rather than trusted.
#
# The window is the one that corrupts silently. Ollama picks its default from
# available VRAM ("4k/32k/256k", per `ollama serve --help`) and TRUNCATES an
# over-long prompt instead of refusing it, so the same model id on two machines
# can be two different instruments. scripts/screen_shard.sh reads it back from
# /api/ps and refuses to start if it is not the pinned value; this records what
# it saw.
RUNTIME = json.loads(os.environ.get("SCREEN_RUNTIME", "{}"))


# Two freezes can name a corpus, and they are not interchangeable:
#
#   tasks.json    the gated corpus - every fault in it has cleared stage 1
#                 (usable) and stage 2 (mutation gate). Authoritative when it
#                 exists.
#   hard_120.json the pre-gate draw (was scripts/select_hard_tasks.py, deleted
#                 in the results reset 748cabc and not replaced): the right
#                 120 faults, but none of them checked yet. Measuring pi on it
#                 is legitimate - a pilot is allowed to run ahead of the gates
#                 - as long as the report says so, because some of these faults
#                 will turn out to be unusable and their pi_hat means nothing.
#
# The source is recorded in the report so a pi_hat measured before the gates
# can never be mistaken for one measured after them.
_CORPUS_SOURCES = (("tasks.json", "gated"), ("hard_120.json", "pre-gate draw"))


def _frozen_programs() -> tuple[tuple[str, ...], str]:
    for filename, kind in _CORPUS_SOURCES:
        path = DATA_DIR / filename
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        if not data.get("frozen"):
            continue
        entries = data.get("tasks") or data.get("selected") or []
        if entries:
            return tuple(t["name"] for t in entries), f"{DATA_DIR / filename} ({kind})"
    return (), ""


def _programs_from_file(path: pathlib.Path, *, block: int | None) -> tuple[tuple[str, ...], str]:
    """Read the program list from a file, preserving its order.

    Preferred over `--programs` for anything larger than a handful, for two
    reasons that are not convenience. The order in data/candidates.json is the
    seeded traversal order (scripts/select_candidates.py), so every prefix is
    balanced on K_proxy - passing names on the command line and truncating by
    hand loses that. And the report records the *path*, so a screen can be
    traced back to the pool it came from; `--programs` can only record the
    string "--programs", which is the provenance gap the paper complains about.

    Accepts data/candidates.json, data/tasks.json, or a plain text file with
    one "<task>/<program>" per line (# comments allowed).
    """
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        entries = data.get("candidates") or data.get("tasks") or data.get("selected") or []
        if not entries:
            raise SystemExit(f"{path} carries no candidates/tasks/selected list")
        if block is not None:
            entries = [e for e in entries if e.get("block") == block]
            if not entries:
                raise SystemExit(f"{path} has no entries with block == {block}")
        names = tuple(e["name"] for e in entries)
        qualifier = f", block {block}" if block is not None else ""
        return names, f"{path} ({len(names)} entries{qualifier})"
    if block is not None:
        raise SystemExit("--block applies only to a .json pool, not a name list")
    names = tuple(
        line.strip() for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if not names:
        raise SystemExit(f"{path} is empty")
    return names, f"{path} ({len(names)} names)"


def _resolve_programs(names: list[str] | None) -> tuple[tuple[str, ...], str]:
    """No --programs, or "--programs all" -> the frozen corpus. pi_hat has to
    cover every frozen task, not a hand-picked subset, because
    scripts/build_strata.py stratifies on the whole distribution it
    produces."""
    if names and names != ["all"]:
        return tuple(names), "--programs"
    frozen, source = _frozen_programs()
    if not frozen:
        raise SystemExit(
            f"no frozen corpus: neither {DATA_DIR / 'tasks.json'} nor {DATA_DIR / 'hard_120.json'} "
            "exists.\nRun `python3 scripts/select_hard_tasks.py` for the draw, "
            "or scripts/validate_oracle.py for the gated corpus, or pass "
            "--programs explicitly"
        )
    return frozen, source


def measure(
    programs: tuple[str, ...],
    calls_per_program: int,
    *,
    model: str | None,
    max_examples: int,
    seed: int,
    corpus_source: str = "",
    checkpoint=None,
) -> dict:
    per_program = {}
    t0 = time.monotonic()
    n_tasks = len(programs)
    task_w = len(str(n_tasks))
    stopped_early = ""

    def report() -> dict:
        total_successes = sum(p["successes"] for p in per_program.values())
        total_calls = sum(p["calls"] for p in per_program.values())
        total_unparsed = sum(p["unparsed"] for p in per_program.values())
        return {
            "programs": list(programs),
            "corpus_source": corpus_source,
            "calls_per_program": calls_per_program,
            "model": model,
            "seed": seed,
            # The rest of the protocol, recorded because a screen is now run in
            # shards across several machines and merged
            # (scripts/consolidate_screens.py). Two shards that disagree on any
            # of these did not measure the same quantity: `max_examples` is how
            # strong the oracle judging each draw was, `temperature` is the
            # distribution the draw came from and part of src.llm's cache key,
            # and `sandbox_timeout_sec` decides whether a slow candidate counts
            # as wrong or as never judged. None of them was written down before,
            # so a mixed merge could not even be detected after the fact.
            "max_examples": max_examples,
            "temperature": LLM_TEMPERATURE,
            "sandbox_timeout_sec": SANDBOX_TIMEOUT,
            "runtime": RUNTIME,
            "pi_hat_pooled": total_successes / total_calls if total_calls else 0.0,
            "total_successes": total_successes,
            "total_calls": total_calls,
            # Draws that never produced a candidate. Counted as failures in
            # pi_hat (see the handler below), and reported separately because a
            # high rate means pi_hat is measuring format compliance rather than
            # repair ability, and the band it lands the task in is meaningless.
            "total_unparsed": total_unparsed,
            "unparsed_rate": total_unparsed / total_calls if total_calls else 0.0,
            # A screen cut short by the budget cap has fewer calls per program
            # than asked for. Recorded so a partial screen can never be read as
            # a complete one - scripts/select_corpus.py --min-calls filters on
            # the per-program count, which is now the actual one.
            "complete": not stopped_early,
            "stopped_early": stopped_early,
            "elapsed_sec": round(time.monotonic() - t0, 1),
            "per_program": per_program,
        }

    for t_idx, name in enumerate(programs, start=1):
        prefix = f"[task {t_idx:{task_w}d}/{n_tasks}]"
        task = TASKS[name]
        program = load(name)
        calls = []
        for i in range(calls_per_program):
            # Distinct nonce per call. The no-memory prompt is byte-identical
            # across all `calls_per_program` calls (empty history, no feedback -
            # that is the point of the measurement), so without it src.llm's
            # cache would answer calls 2..N with call 1's completion and pi_hat
            # could only ever come out 0.0 or 1.0.
            try:
                patch = propose(
                    name, program.buggy_source, task.name,
                    mode="no_memory", history=[], model=model,
                    nonce=f"pi-pilot|{name}|seed{seed}|call{i}",
                    spec_note=task.spec_note,
                )
            except TruncatedResponse as exc:
                # A reply carrying no usable code block. Counted as a failed
                # draw rather than skipped, because src.loop.run_episode spends
                # a round of the budget B on exactly this outcome and moves on -
                # so pi_hat has to be measured over the same denominator the
                # loop will realise, or (1-(1-pi)^B) stops predicting success@B.
                #
                # Not fatal, unlike the oracle error below: the oracle failing
                # means nothing can be judged, whereas this is a draw with a
                # verdict. src.loop already treats it this way; this script used
                # to let it abort the whole pilot.
                calls.append({"call": i + 1, "accept": False,
                              "reason": f"proposal unusable: {exc}", "unparsed": True})
                print(f"{prefix} {name} [{i + 1:2d}/{calls_per_program}] "
                      f"unparsed", flush=True)
                continue
            except BudgetExceeded as exc:
                # Stop cleanly and keep what was measured. Re-running resumes
                # for free: every call is cached under a deterministic nonce.
                stopped_early = str(exc)
                print(f"\nBUDGET CAP REACHED ({exc}) - stopping the screen cleanly.",
                      flush=True)
                break
            result = differential_test(
                task, patch, program.correct_source,
                max_examples=max_examples, seed=seed + i,
            )
            if result.oracle_error is not None:
                # accept=False here means "the oracle could not run", not "the
                # patch is wrong" - counting it as a failed draw would report
                # pi_hat=0.0 for a misconfiguration and keep spending to do it.
                raise SystemExit(
                    f"\n{name}: the oracle could not judge this patch - {result.reason}\n"
                    f"({result.oracle_error}). pi_hat would be measured against nothing, "
                    f"so this run stops here rather than billing for zeros.\n"
                    f"Check CONDEFECTS_TEST_DIR / that Test.zip is unpacked: "
                    f"`python3 scripts/fetch_condefects.py --check-only`."
                )
            calls.append({"call": i + 1, "accept": result.accept, "reason": result.reason})
            # flush: this loop is the only sign of life in a run that takes
            # hours, and stdout block-buffers as soon as it is not a terminal -
            # piping to `tee` for a log would otherwise hold the progress trace
            # in an 8 KB buffer and show nothing until it filled.
            print(f"{prefix} {name} [{i + 1:2d}/{calls_per_program}] "
                  f"{'accept' if result.accept else 'reject'}", flush=True)

        successes = sum(c["accept"] for c in calls)
        # len(calls), not calls_per_program: the loop can end early, and a
        # hardcoded denominator would have understated pi_hat for a program the
        # budget cap cut short.
        n = len(calls)
        per_program[name] = {
            "successes": successes,
            "calls": n,
            "pi_hat": successes / n if n else 0.0,
            "unparsed": sum(1 for c in calls if c.get("unparsed")),
            # The two inputs that decide the prompt, and therefore the cache
            # key: src.llm keys on the prompt text, and the prompt is the faulty
            # source plus the worked examples Task.spec_note picks. Both are
            # deterministic given the checkout, so a shard whose digests differ
            # from another's for the same program was run against a different
            # external/ConDefects - which means its draws were bought under
            # different keys and its pi_hat measured a different program.
            # Cheap to record here, undetectable afterwards if not.
            "source_sha256": hashlib.sha256(program.buggy_source.encode()).hexdigest()[:16],
            "spec_sha256": hashlib.sha256(task.spec_note.encode()).hexdigest()[:16],
            "detail": calls,
        }
        # Written after every task, not once at the end: a screen is hours long
        # and anything that stops it used to discard every task already paid for.
        if checkpoint is not None:
            checkpoint(report())
        if stopped_early:
            break

    return report()


def main() -> None:
    announce('measure_pi')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--programs", nargs="+", default=None,
                        help="default: the gated corpus in data/tasks.json, "
                             "falling back to the draw in data/hard_120.json")
    parser.add_argument("--programs-from", type=pathlib.Path, default=None,
                        help="read the list from a file instead, preserving its "
                             "order: data/candidates.json, data/tasks.json, or "
                             "one '<task>/<program>' per line. Preferred for "
                             "large screens - it records the path as the corpus "
                             "source, and it avoids the shell (zsh does not "
                             "word-split $VAR, so a 527-name variable arrives as "
                             "a single argument).")
    parser.add_argument("--block", type=int, default=None, choices=(1, 2),
                        help="with --programs-from on a pool that has blocks: "
                             "1 = primary, 2 = pre-declared reserve")
    parser.add_argument("--limit", type=int, default=None,
                        help="screen only the first N in file order. The order "
                             "in data/candidates.json is stratified, so a prefix "
                             "is a balanced pilot rather than an arbitrary slice.")
    parser.add_argument("--calls-per-program", type=int, default=DEFAULT_CALLS_PER_PROGRAM)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-examples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="default: data/pi_pilot.json. Point the stages of a "
                             "screen at separate files so one does not clobber "
                             "the next; scripts/select_corpus.py merges them.")
    args = parser.parse_args()

    if args.programs_from and args.programs:
        raise SystemExit("pass --programs or --programs-from, not both")
    if args.programs_from:
        programs, corpus_source = _programs_from_file(args.programs_from, block=args.block)
    else:
        if args.block is not None:
            raise SystemExit("--block requires --programs-from")
        programs, corpus_source = _resolve_programs(args.programs)
    if args.limit is not None:
        programs = programs[:args.limit]
        corpus_source += f" [first {len(programs)} in file order]"
    print(f"corpus: {len(programs)} faults from {corpus_source}", flush=True)
    unknown = [p for p in programs if p not in TASKS]
    if unknown:
        raise SystemExit(f"unknown program(s): {unknown}")

    # Cheapest possible check, run before the first billable call. Without test
    # data every draw is scored by an oracle with nothing to test against, and
    # the whole pilot reports pi_hat=0.0 - a number indistinguishable from a
    # model that simply never succeeds.
    empty = [p for p in programs if not TASKS[p].test_cases]
    if empty:
        from src.adapter import TEST_DIR
        raise SystemExit(
            f"{len(empty)}/{len(programs)} programs have no test cases under {TEST_DIR} "
            f"(e.g. {empty[0]}).\nSet CONDEFECTS_TEST_DIR or unpack Test.zip - "
            f"`python3 scripts/fetch_condefects.py --check-only` reports what is visible.\n"
            f"Refusing to spend the budget measuring pi against an empty test pool."
        )

    # Resolve before the first call so the artifact states which model produced
    # it. Recording args.model wrote "model": null whenever the id came from
    # .env, which is the normal case (paper SS VI-D-b).
    model = args.model or LLM_MODEL
    if not model:
        raise SystemExit("no model configured - set MODEL in .env or pass --model")

    out_path = args.out or (DATA_DIR / "pi_pilot.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def write(blob: dict) -> None:
        out_path.write_text(json.dumps(blob, indent=2) + "\n")

    report = measure(
        programs, args.calls_per_program,
        model=model, max_examples=args.max_examples, seed=args.seed,
        corpus_source=corpus_source, checkpoint=write,
    )
    write(report)

    print()
    print(f"pooled pi_hat = {report['pi_hat_pooled']:.3f} over {report['total_calls']} calls")
    for name, p in report["per_program"].items():
        flag = f"  [{p['unparsed']} unparsed]" if p["unparsed"] else ""
        print(f"  {name:30s} pi_hat={p['pi_hat']:.3f} ({p['successes']}/{p['calls']}){flag}")

    # Loud, because it is the difference between "this model cannot fix these
    # faults" and "this model cannot follow the output format". Both give a low
    # pi_hat; only the first is a property of the corpus, and only the first
    # makes the band assignment in scripts/select_corpus.py mean anything.
    if report["unparsed_rate"] > 0.05:
        print(f"\nWARNING: {report['total_unparsed']}/{report['total_calls']} draws "
              f"({report['unparsed_rate']:.0%}) carried no usable code block and were "
              f"counted as failures. Above a few percent, pi_hat is measuring format "
              f"compliance as much as repair ability - check data/calls*.jsonl: "
              f"finish_reason='length' means the output budget was too small, "
              f"'stop' means the model simply did not emit a fenced block.")
    if not report["complete"]:
        print(f"\nINCOMPLETE screen ({report['stopped_early']}). Partial results are in "
              f"{out_path}; re-run the same command to resume - cached calls replay free.")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
