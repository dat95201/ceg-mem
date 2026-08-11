"""Recover what is readable from a truncated ConDefects Test.zip.

Test.zip is ~6.4 GB, hosted on OneDrive and Baidu Drive, and downloaded by
hand (scripts/fetch_condefects.py explains why it cannot be automated). At
that size a transfer that stops early is a routine outcome, and it fails in a
way that hides itself: the first local file header is intact, so `file(1)`
still calls the result a zip archive, and only the end-of-central-directory
record at the very end is missing. `zipfile` then refuses the whole thing with
BadZipFile even though almost all of the entries are sitting there intact.

A zip is a sequence of self-describing entries followed by a directory that
merely indexes them. Every entry carries its own name, compression method and
sizes in its local header, so the archive can be walked forward from byte zero
without the directory ever being consulted - up to the first entry whose data
runs past end-of-file, which is where the download stopped.

Because entries are written in the order the archive was built (alphabetical,
here), what a truncated Test.zip loses is not a random sample: it is a
*suffix*. That matters more than the byte count suggests. ConDefects covers
AtCoder contests from October 2021 to June 2024, and losing the tail means
losing the recent contests - exactly the slice the contamination argument in
the README rests on. Check the reported date range before trusting a corpus
frozen on salvaged data, and prefer re-downloading.

The recovered tree therefore lands in Test_partial/, not Test/. Two reasons:
fetch_condefects.py skips unpacking when Test/ exists, so a salvage parked
there would silently survive the real download; and src/adapter.py reads
$CONDEFECTS_TEST_DIR, so pointing at the partial tree stays an explicit,
visible act.

    python3 scripts/salvage_test_zip.py
    CONDEFECTS_TEST_DIR=external/ConDefects/Test_partial python3 scripts/validate_oracle.py ...
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import sys
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

LOCAL_HEADER = b"PK\x03\x04"
LOCAL_HEADER_LEN = 30
STORED, DEFLATED = 0, 8
CHUNK = 4 * 1024 * 1024
STREAMED = 0x08   # general-purpose bit 3: sizes live in a trailing descriptor


def _entries(f, size: int):
    """Yield (name, method, data_start, csize) until the archive stops making sense."""
    off = 0
    while True:
        f.seek(off)
        head = f.read(LOCAL_HEADER_LEN)
        if len(head) < LOCAL_HEADER_LEN or head[:4] != LOCAL_HEADER:
            return
        (_sig, _ver, flags, method, _time, _date, _crc,
         csize, _usize, fnlen, exlen) = struct.unpack("<IHHHHHIIIHH", head)
        raw_name = f.read(fnlen)
        if len(raw_name) < fnlen:
            return
        if flags & STREAMED or method not in (STORED, DEFLATED):
            # Neither appears in this archive; bail rather than guess at sizes.
            print(f"unsupported entry at byte {off:,} (flags={flags:#x} method={method})")
            return
        data_start = off + LOCAL_HEADER_LEN + fnlen + exlen
        if data_start + csize > size:
            return   # this is the entry the download was in the middle of
        yield raw_name.decode("utf-8", "replace"), method, data_start, csize
        off = data_start + csize


def _extract_one(f, dest: pathlib.Path, method: int, data_start: int, csize: int) -> None:
    f.seek(data_start)
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS) if method == DEFLATED else None
    remaining = csize
    with open(dest, "wb") as out:
        while remaining:
            buf = f.read(min(CHUNK, remaining))
            if not buf:
                break
            out.write(decompressor.decompress(buf) if decompressor else buf)
            remaining -= len(buf)
        if decompressor:
            out.write(decompressor.flush())


def salvage(archive: pathlib.Path, dest_root: pathlib.Path, *, strip: str = "Test") -> dict:
    size = archive.stat().st_size
    dest_root.mkdir(parents=True, exist_ok=True)
    files = dirs = 0
    consumed = 0
    task_dirs: set[str] = set()

    with open(archive, "rb", buffering=CHUNK) as f:
        for name, method, data_start, csize in _entries(f, size):
            consumed = data_start + csize
            parts = [p for p in name.split("/") if p not in ("", ".", "..")]
            if parts and parts[0] == strip:
                parts = parts[1:]     # so dest_root *is* the test tree
            if not parts:
                continue
            target = dest_root.joinpath(*parts)
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                dirs += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _extract_one(f, target, method, data_start, csize)
            files += 1
            # Test/<contest>/<LETTER>/in|out/<case> - the unit adapter.py looks up.
            if len(parts) >= 3:
                task_dirs.add(f"{parts[0]}/{parts[1]}")
            if files % 5000 == 0:
                print(f"  {files:,} files ...", flush=True)

    return {"files": files, "dirs": dirs, "task_dirs": len(task_dirs),
            "consumed": consumed, "size": size}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=pathlib.Path, default=None,
                        help="default: $CONDEFECTS_ROOT/Test.zip")
    parser.add_argument("--dest", type=pathlib.Path, default=None,
                        help="default: $CONDEFECTS_ROOT/Test_partial")
    args = parser.parse_args()

    from src.adapter import CONDEFECTS_ROOT

    archive = args.archive or CONDEFECTS_ROOT / "Test.zip"
    dest = args.dest or CONDEFECTS_ROOT / "Test_partial"
    if not archive.is_file():
        raise SystemExit(f"{archive} not found - see scripts/fetch_condefects.py")

    stats = salvage(archive, dest)
    lost = stats["size"] - stats["consumed"]
    print(f"\nrecovered {stats['files']:,} files and {stats['dirs']:,} directories into {dest}")
    print(f"covering {stats['task_dirs']:,} <contest>/<letter> test directories")
    print(f"read {stats['consumed']:,} of {stats['size']:,} bytes "
          f"({stats['consumed'] / stats['size']:.4%}); {lost:,} bytes unreadable")
    if lost:
        print("\nThis archive is incomplete. Entries are stored alphabetically, so what is")
        print("missing is the tail of the contest range, not a random sample - re-download")
        print("Test.zip before freezing the corpus the paper reports.")
    print(f"\nTo use it: CONDEFECTS_TEST_DIR={dest} python3 scripts/validate_oracle.py")


if __name__ == "__main__":
    main()
