"""Where this run's artifacts live - one resolver, imported by everything.

Set `RUN_DIR` and every artifact of the run moves under a folder of that name:

    RUN_DIR=official-2026-09-01    ->  data/official-2026-09-01/...
                                       logs/official-2026-09-01/...

Unset (the default) resolves to plain `data/` and `logs/`, byte-for-byte the
paths this repo used before this module existed. Nothing that already works
changes unless you ask it to.

Why one module rather than 21 copies. Every script under `scripts/` used to
carry its own line

    DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"

while `src/metrics.py` used a CWD-relative `pathlib.Path("data/episodes.jsonl")`.
Those two agree only because every bash entry point does `cd "$ROOT"` first. Two
resolution bases for one concept is how a run ends up writing half its output to
one directory and half to another without erroring, so they are now one.

Deliberately NOT moved:

  cache/   The response cache is a cross-run asset - it is what makes a re-run
           free. Scoping it per run would cold-start every sweep. It has its own
           `CACHE_DIR` override in src.llm for the rare case you want that.

  external/ConDefects   Read-only benchmark input, not output of a run.

Three rules for callers:

  1. Always `mkdir(parents=True, exist_ok=True)` before writing - or call
     `ensure()`. Under a nested RUN_DIR the intermediate directory may not
     exist, and a bare `mkdir(exist_ok=True)` raises FileNotFoundError rather
     than creating it.
  2. Record `str(path)` in provenance blobs, never a hardcoded "data/..." string,
     or two runs produce byte-identical provenance for different data.
  3. Bash gets the same value from `scripts/run_dir_paths.sh`; do not re-derive
     it. That file and this one must agree, and there is a test that says so.
"""
from __future__ import annotations

import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Stripped of surrounding slashes so RUN_DIR=/foo/ and RUN_DIR=foo agree, and so
# an accidental leading slash cannot escape the repo via pathlib's absolute-path
# join semantics (ROOT / "/etc" is "/etc", not ROOT/etc).
RUN_SLUG: str = os.environ.get("RUN_DIR", "").strip().strip("/")

DATA_DIR: pathlib.Path = (ROOT / "data" / RUN_SLUG) if RUN_SLUG else (ROOT / "data")
LOGS_DIR: pathlib.Path = (ROOT / "logs" / RUN_SLUG) if RUN_SLUG else (ROOT / "logs")

# The candidate POOL is a sub-namespace of the run, not a sibling of it: the
# RUN_DIR segment goes BETWEEN data and pool, or a run would write its pool into
# the shared one. data/<run>/pool/tasks.json, never data/pool/<run>/tasks.json.
POOL_DIR: pathlib.Path = DATA_DIR / "pool"

# The merged round log. This is the default of --episodes-path in ten scripts,
# which makes it the single highest-leverage path in the repo.
EPISODES: pathlib.Path = DATA_DIR / "episodes.jsonl"

# Relative forms, for the handful of places that must emit a string a shell or a
# provenance blob will read back (bash entry points all `cd "$ROOT"` first).
DATA_REL: str = f"data/{RUN_SLUG}" if RUN_SLUG else "data"
LOGS_REL: str = f"logs/{RUN_SLUG}" if RUN_SLUG else "logs"


def ensure(*dirs: pathlib.Path) -> None:
    """mkdir -p, for every directory a stage is about to write into."""
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def banner(stage: str) -> str:
    """One line naming the stage and where its output is going.

    Printed at the top of every stage so a run that silently wrote to the wrong
    directory is visible in the first line of its log rather than discovered at
    merge time.
    """
    run = RUN_SLUG or "(default - RUN_DIR unset)"
    return f"[{stage}] run={run}  ->  {DATA_REL}/"


def announce(stage: str) -> None:
    """banner(), to stderr, so it survives `> out.json` redirection.

    Several stages write their payload to stdout. A banner on stdout would end
    up inside the JSON.
    """
    import sys
    print(banner(stage), file=sys.stderr, flush=True)


if __name__ == "__main__":
    print(banner("paths"))
    for name in ("ROOT", "RUN_SLUG", "DATA_DIR", "LOGS_DIR", "POOL_DIR",
                 "EPISODES", "DATA_REL", "LOGS_REL"):
        print(f"  {name:10s} {globals()[name]}")
