"""Compare freshly regenerated provenance JSONs with the committed ones.

The paper's numbers all live in eight JSON files under paper/common/
(numbers, figdata, addenda, corpus, related, review3, secondproposer - and the
figures are drawn from the first two). scripts/reproduce.sh regenerates them
from data/<RUN_DIR>/ into a scratch directory and calls this to decide whether
the reproduction holds: every leaf must agree, except

  * provenance blocks (`provenance`, `_provenance`, `results_path`, ... - the
    machine-specific paths a tool records about where it read from), which are
    ignored wherever they sit in the tree;
  * NaN, which equals NaN here (a fit with no residual writes NaN on both sides);
  * floats, compared with rel_tol 1e-6 / abs_tol 1e-9, so a different libm's
    last bit is not a failed reproduction. Integers and strings must be equal.

Exit 0 when every file agrees, 1 when any differs (the differing leaves are
printed, capped per file), 2 on usage errors. `--update` copies the regenerated
files over the committed ones AFTER printing the comparison - the only sanctioned
way to move the reference, and scripts/reproduce.sh passes it only under
--update-numbers.

Usage: python3 check_numbers.py --regenerated DIR --committed DIR [--update] [--files a b ...]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import shutil
import sys

FILES = ("numbers", "figdata", "addenda", "corpus", "related", "review3", "secondproposer")
IGNORED_KEYS = {"provenance", "_provenance", "results_path", "episodes_path", "run",
                "pi_source", "strata_source", "selection_source", "reported_source",
                "band_source", "local_run", "cloud_run"}
REL_TOL = 1e-6
ABS_TOL = 1e-9


def walk(new, old, path, diffs):
    if isinstance(new, dict) and isinstance(old, dict):
        for k in sorted(set(new) | set(old), key=str):
            if k in IGNORED_KEYS:
                continue
            if k not in new:
                diffs.append((f"{path}/{k}", "missing in regenerated"))
            elif k not in old:
                diffs.append((f"{path}/{k}", "missing in committed"))
            else:
                walk(new[k], old[k], f"{path}/{k}", diffs)
        return
    if isinstance(new, list) and isinstance(old, list):
        if len(new) != len(old):
            diffs.append((path, f"list length {len(new)} vs {len(old)}"))
            return
        for i, (a, b) in enumerate(zip(new, old)):
            walk(a, b, f"{path}[{i}]", diffs)
        return
    num = (int, float)
    if isinstance(new, num) and isinstance(old, num) and not isinstance(new, bool) and not isinstance(old, bool):
        if isinstance(new, float) and isinstance(old, float) and math.isnan(new) and math.isnan(old):
            return
        if new == old or math.isclose(new, old, rel_tol=REL_TOL, abs_tol=ABS_TOL):
            return
        diffs.append((path, f"{new!r} (regenerated) vs {old!r} (committed)"))
        return
    if new != old:
        diffs.append((path, f"{str(new)[:60]!r} vs {str(old)[:60]!r}"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regenerated", type=pathlib.Path, required=True)
    ap.add_argument("--committed", type=pathlib.Path, required=True)
    ap.add_argument("--files", nargs="*", default=list(FILES),
                    help="basenames without .json (default: all eight)")
    ap.add_argument("--update", action="store_true",
                    help="after comparing, copy the regenerated files over the committed ones")
    ap.add_argument("--max-print", type=int, default=15)
    args = ap.parse_args()

    failed = []
    for name in args.files:
        new_p = args.regenerated / f"{name}.json"
        old_p = args.committed / f"{name}.json"
        if not new_p.exists():
            print(f"MISSING  {name}.json was not regenerated ({new_p})")
            failed.append(name)
            continue
        if not old_p.exists():
            print(f"NEW      {name}.json has no committed copy yet ({old_p})"
                  + (" - will be added" if args.update else " - run with --update to add it"))
            failed.append(name)
            continue
        new = json.loads(new_p.read_text())
        old = json.loads(old_p.read_text())
        diffs: list[tuple[str, str]] = []
        walk(new, old, "", diffs)
        if diffs:
            failed.append(name)
            print(f"DIFF     {name}.json: {len(diffs)} differing leaf/leaves")
            for p, why in diffs[: args.max_print]:
                print(f"           {p}: {why}")
            if len(diffs) > args.max_print:
                print(f"           ... and {len(diffs) - args.max_print} more")
        else:
            print(f"OK       {name}.json identical (provenance ignored)")

    if args.update:
        args.committed.mkdir(parents=True, exist_ok=True)
        for name in args.files:
            src = args.regenerated / f"{name}.json"
            if src.exists():
                shutil.copyfile(src, args.committed / f"{name}.json")
        print(f"UPDATED  {len([n for n in args.files if (args.regenerated / f'{n}.json').exists()])} "
              f"file(s) copied into {args.committed}")
        return 0
    if failed:
        print(f"\n{len(failed)} of {len(args.files)} file(s) differ: {', '.join(failed)}")
        return 1
    print(f"\nall {len(args.files)} files agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
