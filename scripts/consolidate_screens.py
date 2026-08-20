"""Merge the per-machine screen shards into one report, and audit the join.

The pi screen is ~26k model calls, so it is cut into index ranges over
data/candidates.json and run on several machines (scripts/screen_shard.sh).
This is the step that puts the pieces back together, and the reason it is a
script rather than a `jq` one-liner is that three of the four ways a sharded
screen goes wrong are invisible in the merged numbers:

  mixed protocol   pi_hat is a property of (model, temperature, oracle
                   strength, sandbox verdict). A shard measured under a
                   different one did not measure the same quantity, and
                   "deepest screen wins" would then prefer whichever machine
                   ran deeper rather than whichever is right. Refused, not
                   averaged.
  silent gaps      a shard killed at task 40 of 110 still writes a report, and
                   scripts/measure_pi.py marks it `complete: true` because that
                   flag only tracks the budget cap. The coverage table below is
                   what catches it: the shard list says 110, the report holds
                   40.
  disagreement     the same task screened on two machines at the same depth
                   should give the identical pi_hat - every draw is cached
                   under a deterministic nonce and the oracle is seeded. A
                   difference means the two machines are not interchangeable
                   (usually a sandbox timeout firing on the slower one), which
                   is a finding about the whole screen, not about that task.
  under-depth      a task screened at K = 12 cannot land in the `hard` band at
                   all: pi_hat lives on a grid of 1/K and [0.02, 0.08) contains
                   no multiple of 1/12. Reported per band edge rather than left
                   for scripts/select_corpus.py --min-calls to silently drop.

Merge rule is scripts/select_corpus.py's, unchanged: per task, the deepest
screen wins. Never an average - a deeper screen replays the shallower one's
draws from cache, so its report is a superset and adding them would count those
draws twice.

Writes data/screen_merged.json in measure_pi.py's own report shape, so it can
be handed to select_corpus.py as a single --screen argument.
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

# Fields every shard must agree on. Each one changes what pi_hat *is*, not just
# how precisely it is measured.
#
# `model`, `seed` and `temperature` also reach src.llm's cache key, so a
# mismatch there additionally means the shards paid for disjoint draws instead
# of sharing them - loud and expensive, but at least self-announcing.
#
# The rest are the dangerous ones, because they are NOT in the key and so leave
# no trace in the cache: `max_examples` re-judges the very same completions
# against a weaker oracle, `sandbox_timeout_sec` turns a slow correct patch into
# a wrong one, and `runtime.context_length` decides whether Ollama served the
# whole prompt or quietly cropped its head. Two shards differing in any of them
# merge without a murmur unless it is checked here.
PROTOCOL_FIELDS = ("model", "seed", "max_examples", "temperature", "sandbox_timeout_sec")
RUNTIME_FIELDS = ("context_length", "model_digest", "quantization", "backend")

# The band edges scripts/select_corpus.py cuts on. A screen of depth K can only
# place a task in a band if some multiple of 1/K falls inside it.
BAND_EDGES = ((0.00, 0.02, "dead"), (0.02, 0.08, "hard"), (0.08, 0.18, "medium"),
              (0.18, 0.3501, "easy"), (0.3501, 1.01, "too_easy"))


def band_of(pi: float) -> str:
    for lo, hi, name in BAND_EDGES:
        if lo <= pi < hi:
            return name
    raise ValueError(pi)


ALL_BANDS = [name for _, _, name in BAND_EDGES]


def grid_points(k: int) -> collections.Counter:
    """How many of the k+1 outcomes a depth-k screen can produce land in each
    band. pi_hat is successes/k, so this is the resolution of the instrument."""
    return collections.Counter(band_of(s / k) for s in range(k + 1))


def reachable_bands(k: int) -> list[str]:
    """Bands a depth-k screen can place a task in at all."""
    hit = grid_points(k)
    return [b for b in ALL_BANDS if hit[b]]


def depth_for(points_in_hard: int, *, cap: int = 400) -> int | None:
    """Smallest k putting `points_in_hard` distinct outcomes inside [0.02, 0.08).

    Reachability alone is a weak bar: k = 13 does put one outcome in `hard`, but
    it is 1/13 = 0.077, hard against the upper edge, so a task at a true pi of
    0.03 lands there only if it draws exactly one success. Asking for three
    interior points is what makes the band a measurement rather than a coin
    flip.
    """
    for k in range(2, cap + 1):
        if grid_points(k)["hard"] >= points_in_hard:
            return k
    return None


def load_shards(paths: list[pathlib.Path]) -> list[dict]:
    shards = []
    for path in paths:
        blob = json.loads(path.read_text())
        if "per_program" not in blob:
            raise SystemExit(f"{path}: not a measure_pi.py report (no per_program)")
        blob["_path"] = str(path)
        shards.append(blob)
    return shards


def _protocol_of(blob: dict) -> dict:
    """The flat protocol view of one shard report, runtime fields folded in."""
    flat = {f: blob.get(f) for f in PROTOCOL_FIELDS}
    runtime = blob.get("runtime") or {}
    for f in RUNTIME_FIELDS:
        flat[f"runtime.{f}"] = runtime.get(f)
    return flat


def check_protocol(shards: list[dict], *, strict: bool) -> dict:
    """Every shard agrees on PROTOCOL_FIELDS, or this stops."""
    fields = tuple(_protocol_of({}))
    seen: dict[str, set] = {f: set() for f in fields}
    missing: dict[str, list[str]] = collections.defaultdict(list)
    for blob in shards:
        flat = _protocol_of(blob)
        for field in fields:
            if flat[field] is not None:
                seen[field].add(flat[field])
            else:
                missing[field].append(blob["_path"])

    problems = []
    for field, values in seen.items():
        if len(values) > 1:
            by_value = collections.defaultdict(list)
            for blob in shards:
                value = _protocol_of(blob)[field]
                if value is not None:
                    by_value[value].append(pathlib.Path(blob["_path"]).name)
            detail = "; ".join(f"{v!r}: {', '.join(f_)}" for v, f_ in sorted(by_value.items(), key=str))
            problems.append(f"  {field} disagrees -> {detail}")
    if problems:
        # Which half it lands in decides what re-running costs, so say so rather
        # than leave it to be discovered.
        in_key = {"model", "temperature", "seed"}
        hit_key = any(f.split(".")[0] in in_key for f in seen if len(seen[f]) > 1)
        cost = ("Those fields are in src.llm's cache key, so the odd shard's draws "
                "sit under keys nothing else will ask for: re-running it buys every "
                "draw again."
                if hit_key else
                "Those fields are NOT in src.llm's cache key, so the draws themselves "
                "are still good - re-running the odd shard replays them from cache and "
                "only re-judges them.")
        raise SystemExit(
            "shards were not measured under the same protocol, so they are not "
            "one sample:\n" + "\n".join(problems) +
            f"\n\n{cost}\nRe-run it under the pinned protocol "
            "(scripts/screen_shard.sh) rather than merging across the difference."
        )

    # A field absent from a report predates the field being recorded at all
    # (measure_pi.py only started writing max_examples/temperature/sandbox
    # timeout once the screen went multi-machine). Unknowable, not equal.
    for field, paths in sorted(missing.items()):
        names = ", ".join(pathlib.Path(p).name for p in paths)
        msg = (f"{field} is not recorded in: {names}\n"
               f"  -> these predate the field. Their protocol cannot be verified, "
               f"only assumed.")
        if strict:
            raise SystemExit(f"--strict: {msg}")
        print(f"WARNING: {msg}")

    agreed = {f: (sorted(v)[0] if v else None) for f, v in seen.items()}
    return (
        {f: agreed[f] for f in PROTOCOL_FIELDS},
        {f: agreed[f"runtime.{f}"] for f in RUNTIME_FIELDS},
    )


def check_coverage(shards: list[dict], pool: pathlib.Path) -> None:
    """Which candidate indices the shards actually cover, and what is missing."""
    if not pool.exists():
        print(f"\n{pool} missing - skipping the coverage audit")
        return
    blob = json.loads(pool.read_text())
    names = [c["name"] for c in blob["candidates"]]
    rank = {n: i + 1 for i, n in enumerate(names)}
    digest = hashlib.sha256("\n".join(names).encode()).hexdigest()

    screened = {n for s in shards for n in s["per_program"]}
    covered = sorted(rank[n] for n in screened if n in rank)
    foreign = sorted(n for n in screened if n not in rank)

    print(f"\ncoverage against {pool} ({len(names)} candidates, "
          f"order sha256 {digest[:12]}…)")
    print(f"  screened {len(covered)}/{len(names)}")

    # Contiguous runs, so 146 indices print as one line rather than 146.
    def runs(xs: list[int]) -> list[tuple[int, int]]:
        out: list[list[int]] = []
        for x in xs:
            if out and x == out[-1][1] + 1:
                out[-1][1] = x
            else:
                out.append([x, x])
        return [(a, b) for a, b in out]

    print("  covered  " + ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in runs(covered)))
    gaps = [i for i in range(1, len(names) + 1) if i not in set(covered)]
    if gaps:
        print("  GAPS     " + ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in runs(gaps)))
    if foreign:
        print(f"  {len(foreign)} screened program(s) are not in this pool at all, "
              f"e.g. {foreign[0]} - a shard was cut from a different "
              f"candidates.json")

    # A shard whose report holds fewer tasks than its own shard list walked was
    # interrupted. `complete: true` does not catch this: it only tracks the
    # budget cap, and a Ctrl-C leaves the last checkpoint claiming completeness.
    for s in shards:
        declared = len(s.get("programs") or [])
        held = len(s["per_program"])
        if declared and held < declared:
            print(f"  PARTIAL  {pathlib.Path(s['_path']).name}: walked {held} of "
                  f"{declared} programs"
                  + (f" ({s['stopped_early']})" if s.get("stopped_early") else
                     " - report still says complete:true, so it was interrupted"))


def merge(shards: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    """task -> deepest per_program record, plus the two kinds of disagreement.

    `conflicts` are the same task screened twice at the same depth with a
    different count. Every draw is cached under a deterministic nonce and the
    oracle is seeded, so identical depth must give identical successes; a
    difference means the machines are not interchangeable.

    `divergent` are the same task whose faulty source or worked examples hash
    differently between two shards. Those are the prompt, hence the cache key -
    so the two shards did not screen the same program and neither shared a
    single cached draw with the other. That is a broken checkout, not a result.
    """
    best: dict[str, dict] = {}
    source: dict[str, str] = {}
    conflicts: list[dict] = []
    divergent: list[dict] = []
    for blob in shards:
        for name, rec in blob["per_program"].items():
            prev = best.get(name)
            if prev is None:
                best[name], source[name] = rec, blob["_path"]
                continue
            for field in ("source_sha256", "spec_sha256"):
                if rec.get(field) and prev.get(field) and rec[field] != prev[field]:
                    divergent.append({
                        "name": name, "field": field,
                        "a": {"source": source[name], "digest": prev[field]},
                        "b": {"source": blob["_path"], "digest": rec[field]},
                    })
            if rec["calls"] == prev["calls"] and rec["successes"] != prev["successes"]:
                conflicts.append({
                    "name": name, "calls": rec["calls"],
                    "a": {"source": source[name], "successes": prev["successes"]},
                    "b": {"source": blob["_path"], "successes": rec["successes"]},
                })
            if rec["calls"] > prev["calls"]:
                best[name], source[name] = rec, blob["_path"]
    return {n: {**r, "_source": source[n]} for n, r in best.items()}, conflicts, divergent


def merge_ledgers(paths: list[pathlib.Path], out: pathlib.Path) -> None:
    """Concatenate the per-shard call ledgers, deduplicated on cache_key.

    A cache hit is never logged - src.llm writes the ledger only on a real call
    - so a duplicate key means two machines genuinely bought the same draw
    because neither had the other's cache. Keeping one row is right: the draw
    exists once, and llm.spent() sums this file.
    """
    seen: set[str] = set()
    rows, dupes = [], 0
    for path in paths:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("cache_key")
            if key and key in seen:
                dupes += 1
                continue
            if key:
                seen.add(key)
            rows.append(line)
    out.write_text("\n".join(rows) + "\n")
    tin = sum(json.loads(r).get("in", 0) for r in rows)
    tout = sum(json.loads(r).get("out", 0) for r in rows)
    usd = sum(json.loads(r).get("usd", 0.0) for r in rows)
    print(f"\nledgers: {len(rows)} calls from {len(paths)} file(s) -> {out}")
    print(f"  {tin:,} in / {tout:,} out tokens · ${usd:.4f}"
          + (f" · {dupes} duplicate call(s) dropped" if dupes else ""))
    if rows:
        print(f"  median profile: {tin // len(rows)} in / {tout // len(rows)} out "
              f"per call - this is what DESIGN.md's rate card must be re-derived from")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--screens", nargs="+", default=None,
                        help="shard reports (default: data/screen*.json, "
                             "excluding this script's own output)")
    parser.add_argument("--pool", type=pathlib.Path, default=DATA_DIR / "candidates.json")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "screen_merged.json")
    parser.add_argument("--ledgers", nargs="*", default=None,
                        help="call ledgers to concatenate (default: "
                             "data/calls_screen_*.jsonl); pass none to skip")
    parser.add_argument("--ledger-out", type=pathlib.Path,
                        default=DATA_DIR / "calls_screen.jsonl")
    parser.add_argument("--strict", action="store_true",
                        help="treat an unrecorded protocol field as an error "
                             "rather than a warning - use this for the freeze "
                             "that goes in the paper")
    args = parser.parse_args()

    # `screen*.json`, not `screen_*.json`: the pre-shard screens on disk are
    # data/screen.json and data/screen_s1.json, and dropping the first would
    # silently discard the only tasks screened deep enough to reach the `hard`
    # band. A merged output of any name is excluded so a re-run cannot fold its
    # own previous result back in and double-count the provenance.
    paths = ([pathlib.Path(p) for p in args.screens] if args.screens else
             sorted(p for p in map(pathlib.Path, glob.glob(str(DATA_DIR / "screen*.json")))
                    if p != args.out and not p.name.startswith("screen_merged")))
    if not paths:
        raise SystemExit(f"no shard reports found under {DATA_DIR}/screen*.json")

    shards = load_shards(paths)
    print(f"{len(shards)} shard report(s):")
    for s in shards:
        print(f"  {pathlib.Path(s['_path']).name:34s} {len(s['per_program']):4d} tasks "
              f"× {s.get('calls_per_program', '?')} draws  "
              f"model={s.get('model')}  seed={s.get('seed')}")

    protocol, runtime = check_protocol(shards, strict=args.strict)
    check_coverage(shards, args.pool)

    best, conflicts, divergent = merge(shards)
    if divergent:
        print(f"\nDIVERGENT CHECKOUT: {len(divergent)} program(s) hash differently "
              f"between shards.")
        print("  The faulty source and the worked examples ARE the prompt, and the "
              "prompt is the\n  cache key. Two shards that disagree here screened "
              "different programs under one\n  name and shared no cached draw. Sync "
              "external/ConDefects and re-run both.")
        for d in divergent[:5]:
            print(f"    {d['name']}  {d['field']}  {d['a']['digest']} vs {d['b']['digest']} "
                  f"({pathlib.Path(d['a']['source']).name} vs "
                  f"{pathlib.Path(d['b']['source']).name})")
    if conflicts:
        print(f"\nDISAGREEMENT: {len(conflicts)} task(s) screened twice at the same "
              f"depth with different results.")
        print("  Every draw is cached under a deterministic nonce and the oracle is "
              "seeded, so\n  identical depth must give identical successes. A "
              "difference means the machines are\n  not interchangeable - usually "
              "SANDBOX_TIMEOUT_SEC firing on the slower one. Fix the\n  protocol and "
              "re-run both; do not pick a winner.")
        for c in conflicts[:5]:
            print(f"    {c['name']}  {c['a']['successes']} vs {c['b']['successes']} "
                  f"of {c['calls']}  ({pathlib.Path(c['a']['source']).name} vs "
                  f"{pathlib.Path(c['b']['source']).name})")

    # Depth audit: which bands each task's screen could even have placed it in.
    by_depth = collections.Counter(r["calls"] for r in best.values())
    print("\ndepth (pi_hat lives on a grid of 1/K, so K decides which bands are reachable)")
    for k in sorted(by_depth):
        reach = reachable_bands(k) if k else []
        blind = [b for _, _, b in BAND_EDGES if b not in reach]
        note = f"  BLIND TO: {', '.join(blind)}" if blind else ""
        print(f"  K={k:3d}  {by_depth[k]:4d} tasks{note}")

    bands = collections.Counter(band_of(r["pi_hat"]) for r in best.values())
    print("\nwhere the screened tasks sit (indicative: corpus selection is a later "
          "step and re-derives this)")
    for lo, hi, name in BAND_EDGES:
        print(f"  {name:9s} [{lo:.2f},{hi:.2f})  {bands.get(name, 0):4d}")

    total_calls = sum(r["calls"] for r in best.values())
    total_succ = sum(r["successes"] for r in best.values())
    total_unparsed = sum(r.get("unparsed", 0) for r in best.values())
    merged = {
        "merged_from": [str(p) for p in paths],
        "note": "one report per task, deepest screen wins; never averaged - a "
                "deeper screen replays the shallower one's draws from cache, so "
                "its report is a superset",
        "corpus_source": f"merged from {len(paths)} shard(s) of {args.pool}",
        "calls_per_program": max(by_depth) if by_depth else 0,
        "min_calls_per_program": min(by_depth) if by_depth else 0,
        **protocol,
        "runtime": runtime,
        "pi_hat_pooled": total_succ / total_calls if total_calls else 0.0,
        "total_successes": total_succ,
        "total_calls": total_calls,
        "total_unparsed": total_unparsed,
        "unparsed_rate": total_unparsed / total_calls if total_calls else 0.0,
        "n_conflicts": len(conflicts),
        "conflicts": conflicts,
        "n_divergent": len(divergent),
        "divergent": divergent,
        "complete": not conflicts and not divergent,
        "programs": sorted(best),
        "per_program": {n: {k: v for k, v in r.items() if k != "_source"}
                        for n, r in sorted(best.items())},
        "source_by_program": {n: r["_source"] for n, r in sorted(best.items())},
    }
    args.out.write_text(json.dumps(merged, indent=2) + "\n")
    print(f"\nmerged {len(best)} tasks · {total_calls:,} draws · "
          f"pooled pi_hat = {merged['pi_hat_pooled']:.3f}")
    print(f"wrote {args.out}")

    # The previous merged ledger is folded back in rather than overwritten: it
    # holds the calls of every screen run before sharding, whose per-shard files
    # no longer exist. Dedup on cache_key makes that idempotent, so re-running
    # this script never grows or loses the ledger.
    ledger_paths = ([pathlib.Path(p) for p in args.ledgers] if args.ledgers is not None else
                    sorted(map(pathlib.Path, glob.glob(str(DATA_DIR / "calls_screen*.jsonl")))))
    if ledger_paths:
        merge_ledgers(ledger_paths, args.ledger_out)

    # Deepening, not corpus selection: pi_hat lives on a grid of 1/K, so the
    # question this report answers is whether K is fine enough yet. Re-running a
    # shard at a larger --calls replays every draw already bought and pays only
    # the difference, so the answer is cheap to act on.
    shallow = min(by_depth) if by_depth else 0
    if shallow:
        reach, resolve = depth_for(1), depth_for(3)
        print(f"\ndeepening (a re-run at a larger --calls replays what is already "
              f"bought and pays\nonly the difference, so this is cheap to act on)")
        for k, why in ((reach, "puts any outcome in `hard` at all"),
                       (resolve, "puts 3 outcomes inside `hard` - a measurement, "
                                 "not a coin flip")):
            mark = "have" if shallow >= k else f"+{k - shallow}/task"
            print(f"  K={k:3d}  {why:58s} [{mark}]")
        if shallow < resolve:
            print(f"  -> re-run the same shards with --calls {resolve}")


if __name__ == "__main__":
    main()
