"""Fetch the ConDefects benchmark into external/ConDefects and check it over.

Two pieces, and only the first can be automated:

  Code/, date.txt, difficulty.txt   cloned from GitHub by this script
  Test/                             unpacked here from a Test.zip you download
                                    by hand - the archive is too large for the
                                    git repository, so the project hosts it on
                                    OneDrive and Baidu Drive instead

Without Test/ there are no inputs, so there is no oracle: src/adapter.py will
find the faulty programs but every task will report zero test cases. Run this
script with no arguments to clone and report, then again after dropping
Test.zip (or an already-unpacked Test/) into external/ConDefects.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

REPO = "https://github.com/appmlk/ConDefects"
TEST_ZIP_SOURCES = (
    "OneDrive:    https://1drv.ms/u/s!Auo_FVX2RDMxn6g_jFpzEGOjScia7Q?e=lgOeyi",
    "Baidu Drive: https://pan.baidu.com/s/1YsTYWMfV3L16irItGJQlmA?pwd=hdlz",
)


def clone(root: pathlib.Path, *, depth: int) -> None:
    if (root / ".git").is_dir() or (root / "Code").is_dir():
        print(f"{root} already present - skipping clone")
        return
    root.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", str(depth), REPO, str(root)]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def unpack_test_zip(root: pathlib.Path) -> bool:
    """Unpack a Test.zip sitting in the ConDefects root, if there is one."""
    archive = root / "Test.zip"
    if not archive.is_file():
        return False
    if (root / "Test").is_dir():
        print(f"{root / 'Test'} already unpacked - leaving it alone")
        return True
    print(f"unpacking {archive} ...")
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(root)
    # Some mirrors wrap the tree one level deeper (Test/Test/abc221/...).
    nested = root / "Test" / "Test"
    if nested.is_dir() and not any((root / "Test").glob("*/*/in")):
        for child in nested.iterdir():
            shutil.move(str(child), str(root / "Test" / child.name))
        nested.rmdir()
    return (root / "Test").is_dir()


def report(root: pathlib.Path) -> int:
    """Describe what the adapter can actually see. Returns a shell exit code."""
    # discover(), not the module-level TASKS: that snapshot was taken at import
    # time, which is before this script had finished downloading anything.
    from src.adapter import discover, task_dates, test_cases_for

    TASKS = discover()
    if not TASKS:
        print(f"\nno faulty programs found under {root / 'Code'}")
        return 1

    task_ids = {t.task_id for t in TASKS.values()}
    with_tests = {tid for tid in task_ids if test_cases_for(tid)}
    ready = [name for name, t in TASKS.items() if t.task_id in with_tests]
    dates = sorted(d for tid, d in task_dates().items() if tid in task_ids)

    print(f"\nroot:               {root}")
    print(f"faulty programs:    {len(TASKS)} across {len(task_ids)} coding tasks")
    print(f"with test data:     {len(ready)} programs across {len(with_tests)} coding tasks")
    if dates:
        print(f"contest dates:      {dates[0]} .. {dates[-1]}")

    if not with_tests:
        print("\nTest/ is missing or empty. Download Test.zip into")
        print(f"  {root}/Test.zip")
        print("from one of:")
        for source in TEST_ZIP_SOURCES:
            print(f"  {source}")
        print("then re-run this script to unpack it.")
        return 1

    print("\nready. Next: python3 scripts/validate_oracle.py")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=pathlib.Path, default=None,
                        help="default: CONDEFECTS_ROOT from .env, else external/ConDefects")
    parser.add_argument("--depth", type=int, default=1, help="git clone depth (0 = full history)")
    parser.add_argument("--check-only", action="store_true", help="do not clone or unpack, just report")
    args = parser.parse_args()

    from src.adapter import CONDEFECTS_ROOT

    root = args.root or CONDEFECTS_ROOT
    if not args.check_only:
        clone(root, depth=args.depth if args.depth > 0 else 2**31 - 1)
        unpack_test_zip(root)

    raise SystemExit(report(root))


if __name__ == "__main__":
    main()
