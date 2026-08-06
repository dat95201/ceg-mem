"""Draw the hard corpus: 120 ConDefects faults, seeded and reproducible.

This is *selection only* — the pure prefix of the pipeline, with no program
executed and no model called. It answers "which 120 faults" and nothing else.
The usability and mutation gates live in scripts/validate_oracle.py and still
have to run; π̂ is measured afterwards from the no-memory arm. A fault that
later fails a gate is replaced from `reserve`, which this script also writes,
in order, so the replacement is as determined as the original draw.

The rule, in order. Every step is a pure function of (benchmark, floor, seed):

  1. take the faults in adapter order — discover() sorts by name, so the input
     to the shuffle never depends on filesystem iteration order;
  2. drop those whose coding task ships no test data;
  3. drop those whose coding task is rated outside [floor, ceiling] in
     difficulty.txt — a task with no rating is dropped, not assumed hard;
  4. shuffle with random.Random(seed) — the single source of randomness;
  5. walk that order and keep the first fault of each coding task, because
     several submissions fail the same task and a corpus holding two of them
     would not have independent cells;
  6. the first `corpus_size` of that walk are the corpus; the rest is reserve.

Where "ships test data" is read from
--------------------------------------
Not from whatever happens to be unpacked. An earlier freeze filtered against a
partial tree salvaged from a truncated download, and the same seed then named a
different corpus depending on how much of the download had survived — which is
not a property a frozen corpus may have. So availability is resolved from the
*archive*: Test.zip's central directory lists every case in the complete
benchmark whether or not it has been unpacked yet, costs no disk, and is
identical before and after `fetch_condefects.py` runs. An unpacked Test/ is
used only when the archive is gone, and Test_partial/ is refused outright
unless --allow-partial says otherwise.

Writes data/hard_120.json: the draw, its provenance, and a SHA-256 of the
shuffled candidate order so a re-run can be *checked* against the freeze rather
than trusted (--check does exactly that and exits non-zero on drift).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import (CONDEFECTS_ROOT, TEST_DIR, TASKS, task_dates,
                         task_difficulties)

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

DEFAULT_CORPUS_SIZE = 120
DEFAULT_SEED = 20260717
# AtCoder's own blue boundary, fixed outside this project. Absolute rather than
# a tercile of the pool for the reason in the module docstring; the pilot puts
# the band above it at median π̂ = 0.038, inside the paper's Hard range of
# 0.02–0.08. See SELECTION.md §2 for why it is not higher.
DEFAULT_HARD_FLOOR = 1600

_ZIP_ENTRY = re.compile(r"^Test/([^/]+)/([^/]+)/in/")


# ── where test data is known to exist ────────────────────────────────────

def _from_archive(archive: pathlib.Path) -> set[tuple[str, str]]:
    """(contest, LETTER) pairs with an in/ tree, read from the central
    directory. Never decompresses: the directory is an index at the end of the
    file, so this is O(entries) and touches none of the 67 GB of payload."""
    with zipfile.ZipFile(archive) as zf:          # raises BadZipFile if truncated
        names = zf.namelist()
    found = set()
    for entry in names:
        match = _ZIP_ENTRY.match(entry)
        if match:
            found.add((match.group(1), match.group(2)))
    return found


def _from_tree(root: pathlib.Path) -> set[tuple[str, str]]:
    return {(d.parent.parent.name, d.parent.name) for d in root.glob("*/*/in")}


def test_data_manifest(*, allow_partial: bool = False) -> tuple[set[tuple[str, str]], str]:
    """The set of contest/problem directories that ship test data, and a label
    saying where that set came from. The label is written into the freeze: a
    corpus drawn against a partial tree and one drawn against the archive are
    different corpora and must not be confusable."""
    archive = CONDEFECTS_ROOT / "Test.zip"
    if archive.is_file():
        return _from_archive(archive), f"{archive} (central directory)"
    if TEST_DIR.is_dir():
        if TEST_DIR.name.endswith("_partial") and not allow_partial:
            raise SystemExit(
                f"{TEST_DIR} is a salvaged partial tree and {archive} is gone.\n"
                "A corpus drawn against it is not the corpus the full benchmark "
                "gives. Restore Test.zip, or pass --allow-partial and accept "
                "that the freeze is provisional."
            )
        return _from_tree(TEST_DIR), str(TEST_DIR)
    raise SystemExit(f"no test data: neither {archive} nor {TEST_DIR} exists")


def has_test_data(task_id: str, available: set[tuple[str, str]]) -> bool:
    """Mirrors adapter.test_dir_for: the letter after the underscore, uppercased,
    with "Ex" as the fallback — AtCoder names a Beginner Contest's eighth
    problem "Ex" rather than "H"."""
    contest, _, suffix = task_id.partition("_")
    return any((contest, letter) in available for letter in (suffix.upper(), "Ex"))


# ── the draw ─────────────────────────────────────────────────────────────

def hard_pool(available: set[tuple[str, str]], *, floor: int,
              ceiling: int | None) -> list[str]:
    """Faults that ship test data and whose coding task is rated in
    [floor, ceiling]. Pure, and in adapter order."""
    difficulties = task_difficulties()
    kept = []
    for name, task in TASKS.items():
        rating = difficulties.get(task.task_id)
        if rating is None or rating < floor:
            continue
        if ceiling is not None and rating > ceiling:
            continue
        if not has_test_data(task.task_id, available):
            continue
        kept.append(name)
    return kept


def draw(pool: list[str], *, seed: int, corpus_size: int) -> tuple[list[str], list[str], str]:
    """(selected, reserve, digest). The digest covers the shuffled order, which
    is the one thing that determines both lists; comparing it is a stronger
    check than comparing the 120 names, because it also catches a pool that
    changed outside the selected prefix."""
    order = list(pool)
    random.Random(seed).shuffle(order)
    digest = hashlib.sha256("\n".join(order).encode()).hexdigest()

    walk, claimed = [], set()
    for name in order:
        task_id = TASKS[name].task_id
        if task_id in claimed:
            continue
        claimed.add(task_id)
        walk.append(name)
    return walk[:corpus_size], walk[corpus_size:], digest


def describe(name: str, difficulties: dict[str, int], dates: dict[str, str]) -> dict:
    task = TASKS[name]
    return {
        "name": name,
        "task_id": task.task_id,
        "program_id": task.program_id,
        "difficulty": difficulties.get(task.task_id),
        "date": dates.get(task.task_id),
    }


def build(*, floor: int, ceiling: int | None, seed: int, corpus_size: int,
          allow_partial: bool) -> dict:
    available, source = test_data_manifest(allow_partial=allow_partial)
    pool = hard_pool(available, floor=floor, ceiling=ceiling)
    if len(pool) < corpus_size:
        raise SystemExit(
            f"hard pool holds {len(pool)} faults, fewer than the {corpus_size} "
            f"asked for (floor {floor}). Lower --hard-floor or the corpus size."
        )
    selected, reserve, digest = draw(pool, seed=seed, corpus_size=corpus_size)
    if len(selected) < corpus_size:
        raise SystemExit(
            f"pool holds {len(pool)} faults but only {len(selected)} distinct "
            f"coding tasks, fewer than {corpus_size}."
        )

    difficulties, dates = task_difficulties(), task_dates()
    ratings = sorted(difficulties[TASKS[n].task_id] for n in selected)
    return {
        "frozen": True,
        "kind": "pre-gate hard draw (selection only: no program run, no model called)",
        "benchmark": "ConDefects (Python)",
        "test_data_source": source,
        "seed": seed,
        "hard_floor": floor,
        "hard_ceiling": ceiling,
        "corpus_size": corpus_size,
        "rule": [
            "faults in adapter order",
            "drop coding tasks with no test data",
            f"drop coding tasks rated outside [{floor}, {ceiling if ceiling is not None else 'inf'}]",
            f"shuffle with random.Random({seed})",
            "keep the first fault of each coding task",
            f"first {corpus_size} of that walk = corpus; the rest = reserve",
        ],
        "pool": {
            "faults_in_benchmark": len(TASKS),
            "faults_in_hard_pool": len(pool),
            "coding_tasks_in_hard_pool": len(selected) + len(reserve),
            "headroom_vs_corpus": round((len(selected) + len(reserve)) / corpus_size, 2),
        },
        "rating": {
            "min": ratings[0],
            "median": ratings[len(ratings) // 2],
            "max": ratings[-1],
        },
        "candidate_order_sha256": digest,
        "gates_not_yet_applied": [
            "stage 1 usability (reference passes; fault exposed; no timeout)",
            "stage 1.5 fault-taxonomy capacity",
            "stage 2 mutation gate (>= 2/3 mutants caught)",
        ],
        "selected": [describe(n, difficulties, dates) for n in selected],
        "reserve": [describe(n, difficulties, dates) for n in reserve],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-size", type=int, default=DEFAULT_CORPUS_SIZE)
    parser.add_argument("--hard-floor", type=int, default=DEFAULT_HARD_FLOOR,
                        help="AtCoder rating floor (default: %(default)s)")
    parser.add_argument("--hard-ceiling", type=int, default=None,
                        help="only if measured pi says a rating band has gone dead")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--allow-partial", action="store_true",
                        help="draw against a salvaged partial tree; the freeze is provisional")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "hard_120.json")
    parser.add_argument("--check", action="store_true",
                        help="re-derive and compare against --out; exit 1 on drift")
    parser.add_argument("--print", dest="print_names", action="store_true",
                        help="print the selected fault names, one per line")
    args = parser.parse_args()

    freeze = build(floor=args.hard_floor, ceiling=args.hard_ceiling, seed=args.seed,
                   corpus_size=args.corpus_size, allow_partial=args.allow_partial)
    names = [t["name"] for t in freeze["selected"]]

    if args.check:
        if not args.out.is_file():
            raise SystemExit(f"{args.out} does not exist — nothing to check against")
        old = json.loads(args.out.read_text())
        drift = [k for k in ("candidate_order_sha256", "seed", "hard_floor",
                             "hard_ceiling", "corpus_size", "test_data_source")
                 if old.get(k) != freeze[k]]
        if [t["name"] for t in old["selected"]] != names:
            drift.append("selected")
        if drift:
            print(f"DRIFT in {', '.join(drift)}", file=sys.stderr)
            for key in drift:
                if key != "selected":
                    print(f"  {key}: frozen={old.get(key)!r} now={freeze[key]!r}", file=sys.stderr)
            raise SystemExit(1)
        print(f"{args.out} matches: {len(names)} faults, digest "
              f"{freeze['candidate_order_sha256'][:16]}…")
        return

    if args.print_names:
        print("\n".join(names))
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(freeze, indent=2) + "\n")
    pool, rating = freeze["pool"], freeze["rating"]
    print(f"test data   {freeze['test_data_source']}")
    print(f"hard pool   {pool['faults_in_hard_pool']} faults over "
          f"{pool['coding_tasks_in_hard_pool']} coding tasks "
          f"({pool['headroom_vs_corpus']}x the corpus)")
    print(f"selected    {len(names)} faults, one per coding task")
    print(f"rating      min {rating['min']} · median {rating['median']} · max {rating['max']}")
    print(f"digest      {freeze['candidate_order_sha256']}")
    print(f"wrote       {args.out}  (+{len(freeze['reserve'])} in reserve)")


if __name__ == "__main__":
    main()
