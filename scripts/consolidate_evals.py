"""Merge the per-shard experiment logs into data/episodes.jsonl, and audit the join.

The grid is ~24k model calls and ~150 hours, so it is cut into index ranges over
the corpus and run in shards, across sessions and machines
(scripts/eval_shard.sh). This is the step that puts the pieces back together,
and the reason it is a script rather than a `cat` is that the ways a sharded
grid goes wrong are invisible in the merged numbers:

  mixed protocol   every metric here is a property of (model, granularity, and
                   the oracle/sandbox settings the arm was run under). A shard
                   run under a different model did not measure the same
                   quantity - and because `model` is part of the cell key, its
                   rows will never merge with the others' either: they sit in
                   the file as a parallel grid that analysis reports as
                   "missing cells" days later. Refused here instead.
  silent gaps      a shard killed at task 40 of 85 leaves a valid log. Nothing
                   in it says how far it was supposed to get, so the coverage
                   table below compares what is present against the universe
                   the shard was cut from.
  truncated cells  an episode that neither accepted nor reached the budget is a
                   cell that stopped mid-flight. Its rows are real, and every
                   round-averaged estimator will happily average them - as a
                   task that took fewer rounds than it did.
  disagreement     the same (episode, round) collected twice must be identical:
                   the draw is cached under a deterministic nonce, the loop is
                   seeded, and the oracle is deterministic given the pool. A
                   difference means the two machines are not interchangeable -
                   usually a sandbox timeout firing on the slower one - which is
                   a finding about the whole grid, not about that round.

Merge rule is src.metrics.load_rounds's, unchanged: last write wins per
(episode_id, round_index). A cell that died halfway and was re-run appends a
second copy of its early rounds under the same deterministic episode id, and
collapsing on that key is what makes the rewrite mean what it looks like.

Writes data/episodes.jsonl - the path every downstream script reads by default -
plus the concatenated call ledger and overfit log.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.metrics import load_rounds  # noqa: E402

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# Fields every shard must agree on. Unlike the sweep knobs (max_examples,
# typing_noise_c, guard_on, steer_on) which are *supposed* to differ between
# arms, these two are properties of the instrument: two values of either mean
# two grids, not one.
PROTOCOL_FIELDS = ("model", "granularity")

# Which universe each arm was cut from, and how many seeds it should carry.
# scripts/eval_shard.sh's presets, restated - so a shard run with the preset's
# defaults is audited against them, and one run with --seeds is not.
PRESET_SEEDS = {"E1": 5, "E2": 5}
DEFAULT_PRESET_SEEDS = 3


def arm_key(row: dict) -> tuple:
    """Identity of an experiment arm - a cell key with the task and seed dropped."""
    return (
        row["mode"], row.get("guard_on", True), row.get("steer_on", True),
        row.get("max_examples", 100), row.get("typing_noise_c", 1.0),
        row.get("force_full_budget", False),
    )


def arm_name(arm: tuple) -> str:
    """The --exp name that would have produced this arm, where there is one."""
    mode, guard, steer, max_ex, c, ffb = arm
    if mode == "no_memory" and ffb:
        return "E1"
    if mode in ("untyped", "typed") and guard and steer and max_ex == 100 and c == 1.0:
        return "E2"
    if mode == "typed" and guard and not steer:
        return "E3-guard-only"
    if mode == "typed" and not guard and steer:
        return "E3-steer-only"
    if mode == "typed" and max_ex != 100 and c == 1.0:
        return f"E4-k{max_ex}"
    if mode == "typed" and c != 1.0 and max_ex == 100:
        return f"E5-c{int(round(c * 100)):02d}"
    return "?"


def arm_universe(arm: tuple) -> str:
    """E4/E5 ran over the sweep subset; everything else over the whole corpus."""
    _, _, _, max_ex, c, _ = arm
    return "sweep" if (max_ex != 100 or c != 1.0) else "corpus"


def read_universe(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def runs(xs: list[int]) -> list[tuple[int, int]]:
    """Contiguous runs, so 60 indices print as one line rather than 60."""
    out: list[list[int]] = []
    for x in sorted(xs):
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [(a, b) for a, b in out]


def fmt_runs(xs: list[int]) -> str:
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in runs(xs))


def load_shards(paths: list[pathlib.Path]) -> list[tuple[pathlib.Path, list[dict]]]:
    out = []
    for path in paths:
        # dedupe=False: the whole point here is to see every row as written, so
        # that two shards writing the same round can be compared rather than one
        # silently winning.
        rows = load_rounds(path, dedupe=False)
        if not rows:
            print(f"  {path.name}: empty - skipped")
            continue
        out.append((path, rows))
    return out


def check_protocol(shards: list[tuple[pathlib.Path, list[dict]]]) -> bool:
    """Every shard measured the same instrument. Hard stop if not."""
    print("\nprotocol")
    ok = True
    for field in PROTOCOL_FIELDS:
        seen: dict[object, list[str]] = {}
        for path, rows in shards:
            for value in {r.get(field) for r in rows}:
                seen.setdefault(value, []).append(path.name)
        if len(seen) == 1:
            value = next(iter(seen))
            print(f"  {field:12s} {value}")
            continue
        ok = False
        print(f"  {field:12s} DISAGREEMENT - {len(seen)} values:")
        for value, files in sorted(seen.items(), key=lambda kv: str(kv[0])):
            shown = sorted(set(files))
            print(f"    {value!r:32s} {len(shown)} shard(s): {', '.join(shown[:4])}"
                  + (" ..." if len(shown) > 4 else ""))
    if not ok:
        print("\n  `model` and `granularity` are both in the experiment cell key "
              "(scripts/run_eval.py::cell_key),\n  so these rows will never pool: "
              "they are a second grid sharing one file. Move the odd\n  shard's log "
              "aside, or re-run it under the protocol in EXPERIMENT.md §1.")
    return ok


def check_conflicts(shards: list[tuple[pathlib.Path, list[dict]]]) -> list[dict]:
    """The same (episode, round) collected twice, with different content.

    Compared on what the loop actually decided, not on the whole row: `patch`
    comes from a cached draw and `accept`/`reason` from the oracle, and those are
    the three that must be reproducible across machines.
    """
    FIELDS = ("patch", "accept", "reason", "guarded", "examples_tried")
    first: dict[tuple[str, int], tuple[str, dict]] = {}
    conflicts = []
    for path, rows in shards:
        for row in rows:
            key = (row["episode_id"], row["round_index"])
            prev = first.get(key)
            if prev is None:
                first[key] = (path.name, row)
                continue
            prev_name, prev_row = prev
            if prev_name == path.name:
                continue          # a re-run inside one shard: last write wins, by design
            differing = [f for f in FIELDS if prev_row.get(f) != row.get(f)]
            if differing:
                conflicts.append({
                    "episode_id": key[0], "round": key[1], "task": row["task"],
                    "mode": row["mode"], "seed": row.get("seed"),
                    "fields": differing, "a": prev_name, "b": path.name,
                })
    return conflicts


def check_coverage(rows: list[dict], budget: int) -> tuple[int, int]:
    """Per arm: which (task, seed) cells are present, and which episodes stopped
    mid-flight. Returns (n_missing, n_truncated)."""
    corpus = read_universe(DATA_DIR / "eval_order.txt")
    sweep = read_universe(DATA_DIR / "sweep_programs.txt")
    universes = {"corpus": corpus, "sweep": sweep}

    by_arm: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_arm[arm_key(row)].append(row)

    total_missing = total_truncated = 0
    print(f"\ncoverage (budget {budget}; corpus {len(corpus)} tasks, "
          f"sweep subset {len(sweep)})")
    for arm in sorted(by_arm, key=lambda a: (arm_name(a), a)):
        arm_rows = by_arm[arm]
        uni_name = arm_universe(arm)
        universe = universes[uni_name]
        rank = {n: i + 1 for i, n in enumerate(universe)}
        seeds = sorted({r.get("seed", 0) for r in arm_rows})
        expect_seeds = PRESET_SEEDS.get(arm_name(arm), DEFAULT_PRESET_SEEDS)

        # An episode is complete if it accepted, or ran the full budget. Same
        # rule scripts/run_eval.py resumes on - stated once, here, so the audit
        # cannot disagree with the driver about what "done" means.
        by_ep: dict[str, list[dict]] = collections.defaultdict(list)
        for r in arm_rows:
            by_ep[r["episode_id"]].append(r)
        done, truncated = set(), []
        for ep_rows in by_ep.values():
            r0 = ep_rows[0]
            accepted = any(r["accept"] for r in ep_rows)
            n_rounds = max(r["round_index"] for r in ep_rows)
            cell = (r0["task"], r0.get("seed", 0))
            if n_rounds >= budget or (accepted and not r0.get("force_full_budget", False)):
                done.add(cell)
            else:
                truncated.append((r0["task"], r0.get("seed", 0), n_rounds))

        if not universe:
            print(f"\n  {arm_name(arm):14s} {arm[0]:10s} - "
                  f"data/{'eval_order' if uni_name == 'corpus' else 'sweep_programs'}.txt "
                  f"missing, so\n    there is nothing to check coverage against. "
                  f"Run any shard (even --dry-run) to write it.")
            continue

        label = f"{arm_name(arm):14s} {arm[0]:10s}"
        detail = (f"guard={'on' if arm[1] else 'off'} steer={'on' if arm[2] else 'off'} "
                  f"k={arm[3]} c={arm[4]} ffb={int(arm[5])}")
        n_expected = len(universe) * len(seeds)
        print(f"\n  {label} {detail}")
        print(f"    seeds {seeds}" + (f"   <- preset runs {expect_seeds}"
                                      if len(seeds) != expect_seeds else ""))
        print(f"    complete {len(done)}/{n_expected} cells over the {uni_name} universe")

        foreign = sorted({t for t, _ in done} - set(universe))
        if foreign:
            print(f"    FOREIGN  {len(foreign)} task(s) not in the {uni_name} universe, "
                  f"e.g. {foreign[0]} - cut from a different corpus")
        for seed in seeds:
            missing = [rank[n] for n in universe if (n, seed) not in done]
            if missing:
                total_missing += len(missing)
                print(f"    GAPS     seed {seed}: {len(missing)} task(s) at "
                      f"{uni_name} index {fmt_runs(missing)}")
        if truncated:
            total_truncated += len(truncated)
            head = ", ".join(f"{t}#{s}@{n}r" for t, s, n in sorted(truncated)[:3])
            print(f"    TRUNCATED {len(truncated)} episode(s) neither accepted nor "
                  f"reached budget: {head}" + (" ..." if len(truncated) > 3 else ""))
    return total_missing, total_truncated


def merge_jsonl(paths: list[pathlib.Path], out: pathlib.Path, key, *, label: str) -> None:
    """Concatenate JSONL shards, last write wins per `key`."""
    if not paths:
        print(f"\n{label}: no shard files found - skipped")
        return
    kept: dict = {}
    n_in = 0
    for path in paths:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            n_in += 1
            kept[key(row)] = line
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(kept.values()) + "\n")
    dropped = n_in - len(kept)
    print(f"\n{label}: {len(kept)} rows from {len(paths)} file(s) -> {out}"
          + (f" ({dropped} duplicate row(s) collapsed)" if dropped else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes", nargs="+", default=None,
                        help="shard episode logs (default: data/episodes_eval_*.jsonl). "
                             "data/episodes_trial.jsonl is deliberately not matched: a "
                             "B=5 rehearsal is not data")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "episodes.jsonl")
    parser.add_argument("--budget", type=int, default=20,
                        help="the budget the shards ran at, for the truncation audit")
    parser.add_argument("--ledgers", nargs="*", default=None,
                        help="call ledgers to concatenate (default: "
                             "data/calls_eval_*.jsonl); pass none to skip")
    parser.add_argument("--ledger-out", type=pathlib.Path, default=DATA_DIR / "calls.jsonl")
    parser.add_argument("--overfit-out", type=pathlib.Path,
                        default=DATA_DIR / "overfit_checks.jsonl")
    parser.add_argument("--dry-run", action="store_true",
                        help="audit only, write nothing")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an output log that holds rounds no shard "
                             "accounts for")
    args = parser.parse_args()

    paths = [pathlib.Path(p) for p in (args.episodes or
             sorted(glob.glob(str(DATA_DIR / "episodes_eval_*.jsonl"))))]
    paths = [p for p in paths if p.resolve() != args.out.resolve()]
    if not paths:
        raise SystemExit(
            f"no shard logs found (looked for {DATA_DIR}/episodes_eval_*.jsonl).\n"
            "scripts/eval_shard.sh writes one per shard; a hand-run "
            "scripts/run_eval.py writes straight to data/episodes.jsonl and needs "
            "no merge.")

    print(f"{len(paths)} shard log(s):")
    for p in paths:
        print(f"  {p.name}")
    shards = load_shards(paths)
    if not shards:
        raise SystemExit("every shard log is empty")

    protocol_ok = check_protocol(shards)

    conflicts = check_conflicts(shards)
    if conflicts:
        print(f"\nDISAGREEMENT: {len(conflicts)} round(s) collected twice with "
              f"different results")
        for c in conflicts[:10]:
            print(f"  {c['task']} {c['mode']} seed={c['seed']} round {c['round']}: "
                  f"{', '.join(c['fields'])} differ between {c['a']} and {c['b']}")
        if len(conflicts) > 10:
            print(f"  ... and {len(conflicts) - 10} more")
        print("  Draws are cached under deterministic nonces and the loop is seeded, "
              "so this cannot\n  happen unless the machines are not interchangeable - "
              "usually SANDBOX_TIMEOUT_SEC\n  firing on the slower one. Fix and re-run "
              "both shards; do not pick a winner.")

    all_rows = [row for _, rows in shards for row in rows]
    merged: dict[tuple[str, int], str] = {}
    for _, rows in shards:
        for row in rows:
            merged[(row["episode_id"], row["round_index"])] = json.dumps(row)
    n_missing, n_truncated = check_coverage(
        [json.loads(v) for v in merged.values()], args.budget)

    print(f"\nmerge: {len(all_rows)} rows in, {len(merged)} after collapsing on "
          f"(episode_id, round_index)")

    if not protocol_ok:
        raise SystemExit("\nrefusing to merge shards that did not measure the same "
                         "instrument.")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return

    # An output log holding rounds no shard accounts for is either a hand-run
    # grid or a leftover from an earlier corpus. Either way, overwriting it
    # silently destroys the only copy.
    if args.out.exists():
        orphans = {(r["episode_id"], r["round_index"]) for r in load_rounds(args.out)} - set(merged)
        if orphans and not args.force:
            raise SystemExit(
                f"\n{args.out} holds {len(orphans)} round(s) that no shard log "
                f"accounts for - e.g. episode {sorted(orphans)[0][0]}.\n"
                "They are either a hand-run scripts/run_eval.py, rows from a "
                "superseded corpus, or a\nshard log that has since been deleted. "
                "Three ways out, and they are not equivalent:\n"
                f"  keep them   re-run with the file listed as an input:\n"
                f"              --episodes data/episodes_eval_*.jsonl {args.out}\n"
                "  set aside   mv it somewhere, then merge into a clean path\n"
                "  --force     DISCARDS those rounds - only if you know they are stale")

    merge_jsonl(paths, args.out, lambda r: (r["episode_id"], r["round_index"]),
                label="episodes")

    ledgers = [pathlib.Path(p) for p in (args.ledgers if args.ledgers is not None else
               sorted(glob.glob(str(DATA_DIR / "calls_eval_*.jsonl"))))]
    ledgers = [p for p in ledgers if p.resolve() != args.ledger_out.resolve()]
    if ledgers:
        # Deduplicated on cache_key: a cache hit is never logged, so a duplicate
        # key means two machines genuinely bought the same draw because neither
        # had the other's cache. Keeping one row is right - the draw exists once,
        # and llm.spent() sums this file.
        merge_jsonl(ledgers, args.ledger_out, lambda r: r.get("cache_key") or json.dumps(r),
                    label="ledgers")
        rows = [json.loads(l) for l in args.ledger_out.read_text().splitlines() if l.strip()]
        sec = sum(r.get("sec", 0.0) for r in rows)
        tin = sum(r.get("in", 0) for r in rows)
        tout = sum(r.get("out", 0) for r in rows)
        print(f"  {tin:,} in / {tout:,} out tokens · {sec / 3600:.1f} h of model time"
              + (f" · median profile {tin // len(rows)} in / {tout // len(rows)} out"
                 if rows else ""))

    overfits = sorted(glob.glob(str(DATA_DIR / "overfit_eval_*.jsonl")))
    if overfits:
        merge_jsonl([pathlib.Path(p) for p in overfits], args.overfit_out,
                    lambda r: (r["episode_id"],), label="overfit checks")

    print("\nnext: scripts/freeze_results.py --experiment main"
          + ("\n  (fill the gaps above first, or freeze_results will name them "
             "cell by cell)" if n_missing or n_truncated else ""))


if __name__ == "__main__":
    main()
