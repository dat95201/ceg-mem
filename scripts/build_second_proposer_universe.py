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

No task from `easy` or `too_easy` survived - 0 of 11 - and that is the cut this
list makes. `hard` also scored 0, but on 5 tasks: 0/5 against medium's 1/5 is a
difference of ONE task, and the bands are ordered dead < hard < medium < easy by
construction, so a rule that drops `hard` while keeping `medium` contradicts the
band definitions to chase a single observation. It stays in.

THE RULE, STATED BEFORE THE RUN.  Select every corpus program whose frozen
stratum is `dead`, `hard` or `medium` - equivalently, drop `easy` and
`too_easy`. Stated that way the rule needs no pilot at all: a task the 7B
proposer already solves easily cannot be one a stronger proposer finds
non-trivial, so those two bands have no room for the mechanism by construction.
The pilot only confirmed it. That matters for how this is reported: the cut is
mechanical, not fitted, and it is a property of data/tasks.json, fixed on
2026-07-17 and digest-checked here - not a property of any o4-mini measurement,
so the selection cannot be tuned by what the second proposer does.

PILOT AND CONFIRMATORY ARE NOT THE SAME SET.  The rule above was chosen AFTER
looking at the 27-task pilot, so the pilot tasks inside the selection are not
independent evidence for it. They are marked in the sidecar and must be reported
as the pilot; the task-level test belongs to the confirmatory tasks, which this
script counts separately. Merging the two and reporting one n is the error this
file exists to prevent.

    python3 scripts/build_second_proposer_universe.py --dry-run
    python3 scripts/build_second_proposer_universe.py --piloted-from '~/o4/episodes_eval_E1_o4-mini_*.jsonl'
    bash scripts/fleet.sh eval --exp E1 --shards 4 -- --universe hardend ...
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
SELECT = ("dead", "hard", "medium")


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
                    help="default: <data dir>/hardend_programs.txt, read by --universe hardend")
    ap.add_argument("--frozen", type=pathlib.Path, default=None,
                    help="the first proposer's frozen task list, for the matched set "
                         "(default: runs/2026-09-01/tasks.json if present)")
    ap.add_argument("--piloted-from", nargs="*", default=[],
                    help="episode-log globs for the second proposer, to label the pilot")
    ap.add_argument("--dry-run", action="store_true", help="print the draw, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing demo list drawn for something else")
    args = ap.parse_args()

    data = paths.DATA_DIR
    corpus_path = args.corpus or (data / "tasks.json")
    order_path = args.order or (data / "eval_order.txt")
    out_path = args.out or (data / "hardend_programs.txt")
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

    # The matched set. A cross-proposer table may only be built on tasks BOTH
    # proposers ran, and the frozen run is 99 of the 106 corpus programs, so the
    # draw and the frozen run are not the same list. Computed here rather than
    # left to whoever writes the table, because "99 against 70" is the mistake
    # this whole universe exists to avoid.
    frozen_path = args.frozen or pathlib.Path("runs/2026-09-01/tasks.json")
    matched: list[str] = []
    n_first = 0
    if frozen_path.is_file():
        first = {t["name"] for t in json.loads(frozen_path.read_text())["tasks"]}
        n_first = len(first)
        matched = [t for t in names if t in first]

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
          f"easy/too_easy: 0 of 11 pilot tasks kept any room for the mechanism")
    if matched:
        gap = sorted(set(names) - set(matched))
        print(f"matched            {len(matched)}   also in {frozen_path} "
              f"({len(matched)}/{n_first} of the first proposer's run) - the ONLY "
              f"set a cross-proposer table may use")
        print(f"  no first-proposer result: {len(gap)}  {gap[:4]}")

    body = (f"# hardend_programs: {len(names)} programs, strata interleaved evenly\n"
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
    # Named after the list, not after `demo`: two drawn universes must not share
    # a sidecar, or the second draw silently relabels the first one's pilot set.
    meta_path = out_path.with_name(
        out_path.stem.replace("_programs", "") + "_universe_meta.json")
    meta = {
        "rule": f"stratum in {list(SELECT)}",
        "rule_source": "data/tasks.json, frozen 2026-07-17",
        "rule_chosen_after_pilot": True,
        "rule_note": ("stated as 'drop easy and too_easy', the cut follows from the "
                      "band definitions alone and needs no pilot; it was nonetheless "
                      "arrived at while looking at the 27-task pilot, so the pilot "
                      "tasks below are reported as pilot, not as confirmation"),
        "corpus_sha256": digest,
        "n_drawn": len(names),
        "pilot": sorted(pilot),
        "confirmatory": confirm,
        "matched_with_first_proposer": matched,
        "matched_note": ("the first proposer's frozen run covers 99 of the 106 corpus "
                         "programs, so a cross-proposer table is built on this "
                         "intersection and on nothing wider"),
    }
    meta_path.write_text(json.dumps(meta, indent=1))
    print(f"\nwrote {out_path}  and  {meta_path}")
    print("next: bash scripts/fleet.sh eval --exp E1 --shards 4 -- --universe hardend ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
