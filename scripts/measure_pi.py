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
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import SUPPORTED_PROGRAMS, TASKS, load
from src.llm import MODEL as LLM_MODEL
from src.oracle import differential_test
from src.proposer import propose

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_CALLS_PER_PROGRAM = 40


# Two freezes can name a corpus, and they are not interchangeable:
#
#   tasks.json    the gated corpus - every fault in it has cleared stage 1
#                 (usable) and stage 2 (mutation gate). Authoritative when it
#                 exists.
#   hard_120.json the pre-gate draw (scripts/select_hard_tasks.py): the right
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
            return tuple(t["name"] for t in entries), f"data/{filename} ({kind})"
    return (), ""


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
            "no frozen corpus: neither data/tasks.json nor data/hard_120.json "
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
) -> dict:
    per_program = {}
    t0 = time.monotonic()
    n_tasks = len(programs)
    task_w = len(str(n_tasks))

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
            patch = propose(
                name, program.buggy_source, task.name,
                mode="no_memory", history=[], model=model,
                nonce=f"pi-pilot|{name}|seed{seed}|call{i}",
                spec_note=task.spec_note,
            )
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
        per_program[name] = {
            "successes": successes,
            "calls": calls_per_program,
            "pi_hat": successes / calls_per_program,
            "detail": calls,
        }

    total_successes = sum(p["successes"] for p in per_program.values())
    total_calls = sum(p["calls"] for p in per_program.values())

    return {
        "programs": list(programs),
        "corpus_source": corpus_source,
        "calls_per_program": calls_per_program,
        "model": model,
        "seed": seed,
        "pi_hat_pooled": total_successes / total_calls,
        "total_successes": total_successes,
        "total_calls": total_calls,
        "elapsed_sec": round(time.monotonic() - t0, 1),
        "per_program": per_program,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--programs", nargs="+", default=None,
                        help="default: the gated corpus in data/tasks.json, "
                             "falling back to the draw in data/hard_120.json")
    parser.add_argument("--calls-per-program", type=int, default=DEFAULT_CALLS_PER_PROGRAM)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-examples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="default: data/pi_pilot.json. Point the stages of a "
                             "screen at separate files so one does not clobber "
                             "the next; scripts/select_corpus.py merges them.")
    args = parser.parse_args()

    programs, corpus_source = _resolve_programs(args.programs)
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

    report = measure(
        programs, args.calls_per_program,
        model=model, max_examples=args.max_examples, seed=args.seed,
        corpus_source=corpus_source,
    )

    out_path = args.out or (DATA_DIR / "pi_pilot.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    print()
    print(f"pooled pi_hat = {report['pi_hat_pooled']:.3f} over {report['total_calls']} calls")
    for name, p in report["per_program"].items():
        print(f"  {name:30s} pi_hat={p['pi_hat']:.3f} ({p['successes']}/{p['calls']})")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
