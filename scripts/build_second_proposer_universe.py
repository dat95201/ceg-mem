#!/usr/bin/env python3
"""Draw the universe for the second-proposer run: the bands that survive it.

WHY A DRAWN UNIVERSE AND NOT THE WHOLE CORPUS.  A stronger proposer does not
make every task harder or easier by the same amount - it drains the bottom of
the difficulty range. Measured on the 27-task o4-mini pilot, band under the 7B
proposer against band under o4-mini:

    qwen band   n   still in hard/medium/easy under o4-mini
    dead        6   2
    hard        5   0
    medium      5   1
    easy        6   0
    too_easy    5   0

Every task that still exercises the mechanism came from `dead` or `medium`.
`hard`, `easy` and `too_easy` contributed 0 of 16. Running the other 66 corpus
programs therefore buys ~0 additional usable tasks at ~3x the bill, which is why
this list is drawn rather than the corpus walked.

THE RULE, STATED BEFORE THE RUN.  Select every corpus program whose frozen
stratum is `dead` or `medium`. That is a property of data/tasks.json, fixed on
2026-07-17 and digest-checked here - it is not a property of any o4-mini
measurement, so the selection cannot be tuned by what the second proposer does.

PILOT AND CONFIRMATORY ARE NOT THE SAME SET.  The rule above was chosen AFTER
looking at the 27-task pilot, so the pilot tasks inside the selection are not
independent evidence for it. They are marked in the sidecar and must be reported
as the pilot; the task-level test belongs to the confirmatory tasks, which this
script counts separately. Merging the two and reporting one n is the error this
file exists to prevent.

    python3 scripts/build_second_proposer_universe.py --dry-run
    python3 scripts/build_second_proposer_universe.py --piloted-from '~/o4/episodes_eval_E1_o4-mini_*.jsonl'
    bash scripts/fleet.sh eval --exp E1 --shards 4 -- --universe demo ...
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import paths

# The rule. Two names, written down before the run and not derived from any
# o4-mini number at run time.
SELECT = ("dead", "medium")


def read_list(path: pathlib.Path) -> list[str]:
    return [l.strip() for l in path.read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def header_digest(path: pathlib.Path) -> str:
    for l in path.read_text().splitlines():
        if l.startswith("# corpus_sha256: "):
            return l.split(": ", 1)[1].strip()
    return ""


def piloted(patterns: list[str]) -> set[str]:
    """Tasks the second proposer has already been run on, from its episode logs.

    Used only to LABEL the draw, never to filter it: a task is in the universe
    because of its frozen stratum, and knowing it was piloted must not change
    whether it is run.
    """
    out: set[str] = set()
    for pat in patterns:
        for f in glob.glob(os.path.expanduser(pat)):
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("task"):
                        out.add(row["task"])
    return out


def interleave(by_band: dict[str, list[str]]) -> list[str]:
    """Round-robin the bands, as the generated lists do.

    A shard is a contiguous range of this list, so a list grouped by band would
    hand one shard every dead task and another every medium one. Their costs
    differ by more than an order of magnitude and the fleet would finish wildly
    out of step.
    """
    order = [b for b in SELECT if by_band.get(b)]
    out: list[str] = []
    i = 0
    while any(len(by_band[b]) > i for b in order):
        for b in order:
            if len(by_band[b]) > i:
                out.append(by_band[b][i])
        i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=pathlib.Path, default=None,
                    help="default: <data dir>/tasks.json")
    ap.add_argument("--order", type=pathlib.Path, default=None,
                    help="default: <data dir>/eval_order.txt - membership and the digest")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="default: <data dir>/demo_programs.txt, the hand-drawn universe")
    ap.add_argument("--piloted-from", nargs="*", default=[],
                    help="episode-log globs for the second proposer, to label the pilot")
    ap.add_argument("--dry-run", action="store_true", help="print the draw, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing demo list drawn for something else")
    args = ap.parse_args()

    data = paths.DATA_DIR
    corpus_path = args.corpus or (data / "tasks.json")
    order_path = args.order or (data / "eval_order.txt")
    out_path = args.out or (data / "demo_programs.txt")
    for p in (corpus_path, order_path):
        if not p.is_file():
            print(f"missing {p} - freeze the corpus first", file=sys.stderr)
            return 2

    stratum = {t["name"]: t.get("stratum")
               for t in json.loads(corpus_path.read_text())["tasks"]}
    corpus = read_list(order_path)
    digest = header_digest(order_path)
    if not digest:
        print(f"{order_path} has no '# corpus_sha256:' header - eval_shard.sh checks "
              f"the drawn list against it, so the draw would be unusable", file=sys.stderr)
        return 2

    missing = [t for t in corpus if t not in stratum]
    if missing:
        print(f"{len(missing)} corpus program(s) carry no stratum in {corpus_path}, "
              f"e.g. {missing[:3]} - the draw would silently drop them", file=sys.stderr)
        return 2

    by_band: dict[str, list[str]] = {b: [] for b in SELECT}
    for t in corpus:
        if stratum[t] in by_band:
            by_band[stratum[t]].append(t)
    names = interleave(by_band)

    pilot = piloted(args.piloted_from) & set(names) if args.piloted_from else set()
    confirm = [t for t in names if t not in pilot]

    full = collections.Counter(stratum[t] for t in corpus)
    print(f"corpus            {len(corpus)}   " +
          "  ".join(f"{b}={full[b]}" for b in
                    ("dead", "hard", "medium", "easy", "too_easy")))
    print(f"rule              stratum in {list(SELECT)}  (frozen 2026-07-17, not measured here)")
    print(f"drawn             {len(names)}   " +
          "  ".join(f"{b}={len(by_band[b])}" for b in SELECT))
    if args.piloted_from:
        print(f"  pilot           {len(pilot)}   already run by the second proposer; "
              f"the selection rule was chosen after seeing these")
        print(f"  confirmatory    {len(confirm)}   independent of the rule - "
              f"the task-level test belongs to this set")
    print(f"not drawn         {len(corpus) - len(names)}   "
          f"contributed 0 of 16 usable tasks in the pilot")

    body = (f"# demo_programs: {len(names)} programs, strata interleaved evenly\n"
            f"# drawn by scripts/build_second_proposer_universe.py, "
            f"rule: stratum in {list(SELECT)}\n"
            f"# corpus: {corpus_path.name and 'data/tasks.json'}\n"
            f"# corpus_sha256: {digest}\n" + "\n".join(names) + "\n")

    if args.dry_run:
        print(f"\n--dry-run: would write {len(names)} programs to {out_path}")
        return 0

    if out_path.exists() and not args.force:
        prev = header_digest(out_path)
        if prev and prev != digest:
            print(f"{out_path} was drawn from a different corpus "
                  f"({prev[:12]}... vs {digest[:12]}...). Move it aside deliberately.",
                  file=sys.stderr)
            return 2
        if read_list(out_path) != names:
            print(f"{out_path} already holds a different draw "
                  f"({len(read_list(out_path))} programs). Pass --force to replace it, "
                  f"but every episode already collected under `--universe demo` was run "
                  f"against the old one.", file=sys.stderr)
            return 2

    out_path.write_text(body)
    meta = {
        "rule": f"stratum in {list(SELECT)}",
        "rule_source": "data/tasks.json, frozen 2026-07-17",
        "rule_chosen_after_pilot": True,
        "corpus_sha256": digest,
        "n_drawn": len(names),
        "pilot": sorted(pilot),
        "confirmatory": confirm,
    }
    (out_path.parent / "demo_universe_meta.json").write_text(json.dumps(meta, indent=1))
    print(f"\nwrote {out_path}  and  {out_path.parent / 'demo_universe_meta.json'}")
    print("next: bash scripts/fleet.sh eval --exp E1 --shards 4 -- --universe demo ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
