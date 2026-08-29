"""Write data/live_programs.txt: the sweep universe minus the dead band.

E9's universe. The question E9 asks is whether the attempts a guard saves convert
into repairs when they are handed back to the search - and on a `dead` task
(pi_hat ~ 0) they provably cannot, because no number of attempts finds a patch
the proposer never produces. Including those tasks would spend most of the arm's
GPU hours re-establishing that dead tasks stay dead: measured against the frozen
logs, keeping them takes E9 from ~13 GPU-h to ~46.

That is a defensible exclusion and an indefensible silence, so it is a named
universe with its own file rather than a filter buried in an analysis script, and
the file records how many tasks each band contributed.

Derived, never drawn: re-running this after a re-freeze gives the list the new
corpus implies. The `# corpus_sha256:` header is what scripts/eval_shard.sh
checks on read - a list cut from a different data/tasks.json names different
tasks at the same shard indices.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
EXCLUDE = ("dead",)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=pathlib.Path, default=DATA / "sweep_programs.txt")
    ap.add_argument("--out", type=pathlib.Path, default=DATA / "live_programs.txt")
    ap.add_argument("--exclude", nargs="+", default=list(EXCLUDE),
                    help=f"bands to drop (default: {' '.join(EXCLUDE)})")
    args = ap.parse_args()

    tasks_path = DATA / "tasks.json"
    if not tasks_path.exists():
        raise SystemExit(f"{tasks_path} is missing - freeze the corpus first")
    if not args.source.exists():
        raise SystemExit(f"{args.source} is missing - run the corpus stage first")

    # The SAME digest scripts/eval_shard.sh computes over the frozen corpus, and
    # deliberately not sha256(tasks.json): name AND stratum, in frozen order, so a
    # re-freeze that kept every name but re-banded one task changes it. A digest
    # over the raw bytes would also change on any cosmetic edit to the file, and
    # would then never match the header eval_shard.sh writes.
    tasks = json.loads(tasks_path.read_text())["tasks"]
    digest = hashlib.sha256(
        "\n".join(f"{t['name']}\t{t['stratum']}" for t in tasks).encode()).hexdigest()
    stratum = {t["name"]: t["stratum"] for t in tasks}

    source = [ln.strip() for ln in args.source.read_text().splitlines()
              if ln.strip() and not ln.startswith("#")]
    unknown = [t for t in source if t not in stratum]
    if unknown:
        raise SystemExit(
            f"{len(unknown)} task(s) in {args.source} are not in {tasks_path} "
            f"(first: {unknown[0]}). The two were cut from different corpora.")

    kept = [t for t in source if stratum[t] not in args.exclude]
    if not kept:
        raise SystemExit(f"excluding {args.exclude} left nothing")

    counts = collections.Counter(stratum[t] for t in source)
    header = [
        "# live universe: the sweep minus " + ", ".join(args.exclude),
        "# The tasks where pi_hat > 0, i.e. where extra attempts can convert into",
        "# a repair. Written by scripts/build_live_universe.py - do not hand-edit.",
        f"# source: {args.source.relative_to(ROOT)} ({len(source)} tasks)",
        "# bands:  " + "  ".join(
            f"{b}={counts.get(b, 0)}{' [dropped]' if b in args.exclude else ''}"
            for b in ("dead", "hard", "medium", "easy", "too_easy") if counts.get(b)),
        f"# corpus_sha256: {digest}",
    ]
    args.out.write_text("\n".join(header + kept) + "\n")
    print(f"wrote {args.out} - {len(kept)} of {len(source)} tasks "
          f"(dropped {len(source) - len(kept)}: {', '.join(args.exclude)})")
    for b in ("hard", "medium", "easy", "too_easy"):
        n = sum(1 for t in kept if stratum[t] == b)
        if n:
            print(f"  {b:9s} {n}")


if __name__ == "__main__":
    sys.exit(main())
