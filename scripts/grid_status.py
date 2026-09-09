#!/usr/bin/env python3
"""Which cells of an experiment preset are still missing from a run's merged log.

    python3 scripts/grid_status.py --run-dir official-2026-09-01 --preset E1
    python3 scripts/grid_status.py --run-dir gpto4mini-2026-09-06 --all-presets cloud --model gpt-4o-mini
    python3 scripts/grid_status.py --run-dir official-2026-09-01 --all-presets local --json

Exit status: 0 when nothing is missing, 3 when cells are missing, 2 on error.
This is the decision scripts/reproduce.sh makes before it starts anything: a
preset with no missing cell is a hit - no server is started, no oracle runs.

What "a cell" and "complete" mean is NOT defined here. The cell identity is
scripts/run_eval.py::cell_key (task, mode, seed, and every flag that changes
what the cell is - model included), and "complete" is run_eval's own resume
rule: the episode ran to the budget, or it accepted and the arm is allowed to
stop early. Both are imported from the driver, so this script can never call a
cell missing that the driver would skip, or the reverse. The preset table is
scripts/presets.py, the same one scripts/eval_shard.sh reads.

The decision is made on data/<run>/episodes.jsonl - the MERGED log - and so does
not depend on how the grid was sharded when it was collected: consolidate the
shard logs first (scripts/consolidate_evals.py) or the cells they hold are
invisible here. Universe lists (eval_order.txt, sweep_programs.txt,
trial_programs.txt) are generated exactly as eval_shard.sh generates them when
absent; live_programs.txt through scripts/build_live_universe.py.

Declared gaps
-------------
A run can ship a `grid_coverage.json` beside its log (written by
scripts/build_artifacts.py --allow-partial): cells listed there as missing were
missing when the paper's numbers were extracted, and the paper reports the
grid at that coverage. They are shown as "declared" and do not count toward
exit status 3 unless --strict is given - so a reproduction from the shipped
artifacts rebuilds the paper as published rather than extending the grid under
it. --strict (scripts/reproduce.sh: FILL_DECLARED_GAPS=1) counts them.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import presets as P            # noqa: E402
import universes as U          # noqa: E402

COVERAGE_FILE = "grid_coverage.json"
EXIT_OK, EXIT_ERROR, EXIT_MISSING = 0, 2, 3


def _run_eval():
    """scripts/run_eval.py, imported lazily.

    It pulls in src.adapter and src.llm; both tolerate a checkout without
    external/ConDefects and without an API key, and neither talks to anything
    at import. Lazy so `--help` costs nothing.
    """
    import run_eval  # noqa: E402  (scripts/ is on sys.path)
    return run_eval


# ── run directory ────────────────────────────────────────────────────────────
def resolve_run_dir(arg: str | None) -> tuple[pathlib.Path, str, str | None]:
    """-> (data dir, the tasks.json path string to record in list headers, slug).

    A slug names data/<slug>/ under the repo, exactly as RUN_DIR does, and the
    header path is the repo-relative "data/<slug>/tasks.json" eval_shard.sh
    writes (both scripts run with the repo root as cwd). An existing directory
    anywhere else is taken as is - the case scripts/build_artifacts.py has when
    it validates a raw run directory before packing it - and has no slug.
    """
    arg = (arg if arg is not None else os.environ.get("RUN_DIR", "")).strip()
    if not arg:
        return ROOT / "data", "data/tasks.json", ""
    candidate = pathlib.Path(arg)
    slug = arg.strip("/")
    if (ROOT / "data" / slug).is_dir() or not candidate.is_dir():
        return ROOT / "data" / slug, f"data/{slug}/tasks.json", slug
    candidate = candidate.resolve()
    return candidate, str(candidate / "tasks.json"), None


# ── universes ────────────────────────────────────────────────────────────────
def ensure_universe(data_dir: pathlib.Path, universe: str, tasks_header_path: str,
                    slug: str | None, *, write: bool = True) -> tuple[pathlib.Path, list[str]]:
    """The list file a universe stands for, generated if absent (as eval_shard.sh does)."""
    fname = P.universe_file(universe)
    if not fname:
        raise SystemExit(f"unknown universe '{universe}'")
    path = data_dir / fname
    tasks_json = data_dir / "tasks.json"
    if universe in P.GENERATED_UNIVERSES:
        if not tasks_json.exists():
            raise SystemExit(f"{tasks_json} missing - freeze the corpus first (RUNBOOK.md)")
        if write:
            # The interleave eval_shard.sh runs on every start, with one
            # difference: a list already on disk is validated (digest) but never
            # rewritten - this is a status tool, and a run's own files stay its
            # own. Only an ABSENT list is written.
            U.write_universes(tasks_header_path, data_dir, rewrite=False)
        elif not path.exists():
            raise SystemExit(f"{path} is absent and --no-write was given")
    elif universe in P.DERIVED_UNIVERSES:
        need = not path.exists()
        order = data_dir / "eval_order.txt"
        if not need and order.exists():
            want, have = U.read_digest(order), U.read_digest(path)
            need = bool(want) and have != want
        if need:
            if not write:
                raise SystemExit(f"{path} is absent or stale and --no-write was given")
            if slug is None:
                raise SystemExit(
                    f"{path} is absent or stale. It is derived by scripts/build_live_universe.py, "
                    f"which addresses runs by RUN_DIR under data/ - run it with the run "
                    f"installed there, or copy the list beside the log.")
            # Generated lists first: the live list is cut from the sweep.
            ensure_universe(data_dir, "sweep", tasks_header_path, slug, write=True)
            env = dict(os.environ, RUN_DIR=slug)
            print(f"building {path} (derived: the sweep minus the dead band)", file=sys.stderr)
            # Its progress lines go to stderr, as eval_shard.sh sends them: this
            # tool's stdout may be --json.
            r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_live_universe.py")],
                               cwd=ROOT, env=env, stdout=sys.stderr)
            if r.returncode != 0 or not path.exists():
                raise SystemExit(f"could not build {path}")
    elif not path.exists():
        raise SystemExit(
            f"{path} does not exist. The '{universe}' universe is a drawn list, not a "
            f"generated one - see eval_shard.sh --help.")
    names = U.read_universe(path)
    if not names:
        raise SystemExit(f"{path} holds no programs")
    return path, names


# ── cells ────────────────────────────────────────────────────────────────────
def completed_cells(rows: list[dict], budget: int, force_full_budget: bool) -> set[tuple]:
    """run_eval._completed_cells, over rows already loaded.

    The driver re-reads the log per call; a status over fifteen presets would
    read a 77 MB file fifteen times, so the rows are loaded once and the rule is
    restated here verbatim. tests/test_grid_status.py asserts the two agree on
    every (budget, force_full_budget) combination the presets use.
    """
    re_ = _run_eval()
    by_cell: dict[tuple, list[dict]] = {}
    for row in rows:
        by_cell.setdefault(re_._cell_key(row), []).append(row)
    done = set()
    for cell, cell_rows in by_cell.items():
        rounds_seen = max(r["round_index"] for r in cell_rows)
        already_accepted = any(r["accept"] for r in cell_rows)
        if rounds_seen >= budget or (already_accepted and not force_full_budget):
            done.add(cell)
    return done


def expected_cells(preset: dict, universe: list[str], model: str, granularity: str,
                   reasoning_effort: str | None, seeds: list[int] | None = None) -> dict[tuple, tuple]:
    """cell -> (task, mode, seed) for every cell the preset would walk."""
    re_ = _run_eval()
    params = P.cell_params(preset["extra"])
    if reasoning_effort:
        params["reasoning_effort"] = reasoning_effort
    out = {}
    for task in universe:
        for mode in preset["modes"]:
            for seed in (seeds or preset["seeds"]):
                key = re_.cell_key(task, mode, seed, params["guard_on"], params["steer_on"],
                                   params["max_examples"], params["typing_noise_c"],
                                   params["force_full_budget"], model, granularity,
                                   params["audit_guarded"], params["reasoning_effort"],
                                   params["typing_random"], params["free_guarded_rounds"],
                                   params["history"], params["selftest"], params["oracle_skip_p"])
                out[key] = (task, mode, seed)
    return out


def load_coverage(data_dir: pathlib.Path) -> dict:
    path = data_dir / COVERAGE_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def shard_ranges(n: int, shards: int) -> list[tuple[int, int]]:
    """SHARDS contiguous 1-based inclusive ranges over 1..n (the last may be shorter)."""
    shards = max(1, min(shards, n))
    size = -(-n // shards)
    return [(lo, min(lo + size - 1, n)) for lo in range(1, n + 1, size)]


def preset_status(name: str, rows: list[dict], data_dir: pathlib.Path, tasks_header_path: str,
                  slug: str | None, model: str, granularity: str, reasoning_effort: str | None,
                  coverage: dict, *, seeds=None, budget=None, shards: int = 1,
                  write: bool = True) -> dict:
    preset = P.get(name)
    budget = budget or preset["budget"]
    params = P.cell_params(preset["extra"])
    path, universe = ensure_universe(data_dir, preset["universe"], tasks_header_path, slug,
                                     write=write)
    expected = expected_cells(preset, universe, model, granularity, reasoning_effort, seeds)
    done = completed_cells(rows, budget, params["force_full_budget"])
    missing = {k: v for k, v in expected.items() if k not in done}
    declared_list = coverage.get("presets", {}).get(name, {}).get("missing", [])
    declared = {tuple(c) for c in declared_list}
    undeclared = sorted(v for v in missing.values() if v not in declared)
    rank = {t: i + 1 for i, t in enumerate(universe)}
    missing_pos = sorted({rank[t] for t, _, _ in missing.values()})
    ranges = shard_ranges(len(universe), shards)
    ranges_todo = [r for r in ranges if any(r[0] <= p <= r[1] for p in missing_pos)]
    return {
        "preset": name, "universe": preset["universe"], "universe_file": str(path),
        "n_universe": len(universe), "modes": preset["modes"],
        "seeds": list(seeds or preset["seeds"]), "budget": budget,
        "extra": preset["extra"], "model": model, "granularity": granularity,
        "expected": len(expected), "complete": len(expected) - len(missing),
        "missing": len(missing), "missing_declared": len(missing) - len(undeclared),
        "missing_undeclared": len(undeclared),
        "missing_cells": sorted(missing.values()),
        "missing_undeclared_cells": undeclared,
        "missing_positions": missing_pos,
        "shards": len(ranges), "ranges": ranges, "ranges_todo": ranges_todo,
    }


def status_word(s: dict, strict: bool) -> str:
    if s["missing"] == 0:
        return "hit"
    if not strict and s["missing_undeclared"] == 0:
        return "hit (declared)"
    return "RUN"


def print_table(run_label: str, model: str, n_rows: int, statuses: list[dict], strict: bool,
                verbose: bool) -> None:
    print(f"run {run_label}   model {model}   merged log: {n_rows} rounds")
    hdr = f"{'preset':20s} {'universe':8s} {'size':>4s} {'expected':>8s} {'complete':>8s} " \
          f"{'missing':>7s} {'declared':>8s}  status"
    print(hdr)
    print("-" * len(hdr))
    for s in statuses:
        print(f"{s['preset']:20s} {s['universe']:8s} {s['n_universe']:4d} {s['expected']:8d} "
              f"{s['complete']:8d} {s['missing']:7d} {s['missing_declared']:8d}  "
              f"{status_word(s, strict)}")
        if verbose and s["missing"]:
            cells = s["missing_cells"] if strict else s["missing_undeclared_cells"]
            for task, mode, seed in cells[:40]:
                print(f"      missing  {task:28s} {mode:10s} seed={seed}")
            if len(cells) > 40:
                print(f"      ... and {len(cells) - 40} more")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=None,
                    help="run slug under data/ (default: $RUN_DIR), or a directory path")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--preset", action="append", help="preset name (repeatable)")
    g.add_argument("--all-presets", choices=sorted(P.PROPOSER_PRESETS),
                   help="every preset scripts/reproduce.sh runs for this proposer")
    ap.add_argument("--model", default=P.LOCAL_MODEL,
                    help=f"model id in the cell key (default {P.LOCAL_MODEL})")
    ap.add_argument("--granularity", default="fine", choices=["coarse", "fine"])
    ap.add_argument("--reasoning-effort", default=None)
    ap.add_argument("--seeds", nargs="+", type=int, default=None, help="override the preset's seeds")
    ap.add_argument("--budget", type=int, default=None, help="override the preset's budget")
    ap.add_argument("--shards", type=int, default=1,
                    help="report which of SHARDS contiguous ranges hold missing cells")
    ap.add_argument("--episodes", type=pathlib.Path, default=None,
                    help="merged log (default: <run>/episodes.jsonl)")
    ap.add_argument("--strict", action="store_true",
                    help="count cells declared missing in grid_coverage.json as missing")
    ap.add_argument("--no-write", action="store_true",
                    help="never generate an absent universe list; fail instead")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="list the missing cells")
    args = ap.parse_args(argv)

    os.chdir(ROOT)      # relative header paths and RUN_DIR semantics, as in eval_shard.sh
    data_dir, tasks_header, slug = resolve_run_dir(args.run_dir)
    if not (data_dir / "tasks.json").exists():
        print(f"{data_dir / 'tasks.json'} missing - freeze the corpus first (RUNBOOK.md)",
              file=sys.stderr)
        return EXIT_ERROR
    episodes = args.episodes or (data_dir / "episodes.jsonl")
    rows = _run_eval().load_rounds(episodes) if episodes.exists() else []
    coverage = load_coverage(data_dir)
    names = list(P.PROPOSER_PRESETS[args.all_presets]) if args.all_presets else args.preset
    statuses = []
    for name in names:
        if name not in P.PRESETS:
            print(f"unknown preset: {name}", file=sys.stderr)
            return EXIT_ERROR
        statuses.append(preset_status(
            name, rows, data_dir, tasks_header, slug, args.model, args.granularity,
            args.reasoning_effort, coverage, seeds=args.seeds, budget=args.budget,
            shards=args.shards, write=not args.no_write))

    key = "missing" if args.strict else "missing_undeclared"
    n_missing = sum(s[key] for s in statuses)
    rc = EXIT_MISSING if n_missing else EXIT_OK
    run_label = str(data_dir.relative_to(ROOT)) if data_dir.is_relative_to(ROOT) else str(data_dir)
    if args.json:
        print(json.dumps({
            "run_dir": run_label, "slug": slug, "model": args.model,
            "episodes": str(episodes), "rounds": len(rows), "strict": args.strict,
            "coverage_file": str(data_dir / COVERAGE_FILE) if coverage else None,
            "presets": [dict(s, status=status_word(s, args.strict)) for s in statuses],
            "missing_total": n_missing, "exit_code": rc,
        }, indent=2))
    else:
        print_table(run_label, args.model, len(rows), statuses, args.strict, args.verbose)
        if coverage and not args.strict and any(s["missing_declared"] for s in statuses):
            print(f"\n{sum(s['missing_declared'] for s in statuses)} cell(s) are declared "
                  f"missing in {data_dir / COVERAGE_FILE} and are not counted; --strict counts them.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
