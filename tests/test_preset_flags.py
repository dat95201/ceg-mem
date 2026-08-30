"""Every flag any --exp preset passes must exist in run_eval.py's parser.

This test exists because that invariant broke silently and cost a fleet launch.
Removing the E6 arm deleted a block of scripts/run_eval.py by slicing between two
string offsets rather than by matching exact text, and the slice ran from
`--transcript-window` up to `--free-guard-draw-cap` - swallowing the
`--free-guarded-rounds` definition that happened to sit between them. The file
still compiled, `src/loop.py` still had the parameter, and every call site still
referenced `args.free_guarded_rounds`. Nothing caught it until six shards forked
and all six died on

    run_eval.py: error: unrecognized arguments: --free-guarded-rounds

`--help` does NOT catch this: argparse handles -h as soon as it sees it and exits
0 without validating the rest of the command line, so a smoke test that appends
--help passes on a command that would fail for real. That is exactly the check I
ran, and exactly why it passed.

No model server and no ollama needed - this is pure argument-surface checking.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def defined_flags() -> set[str]:
    """Long options run_eval.py's parser actually defines."""
    src = (ROOT / "scripts" / "run_eval.py").read_text()
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src))


def preset_flags() -> dict[str, set[str]]:
    """--exp NAME -> the long options its EXTRA passes through."""
    sh = (ROOT / "scripts" / "eval_shard.sh").read_text()
    out: dict[str, set[str]] = {}
    # A preset is `  NAME)` ... up to the next `;;`, and its flags are whatever
    # appears in an EXTRA= assignment inside it.
    for m in re.finditer(r'^  ([A-Za-z0-9][A-Za-z0-9-]*)\)\s*\n(.*?);;', sh, re.S | re.M):
        name, body = m.group(1), m.group(2)
        flags: set[str] = set()
        for e in re.finditer(r'EXTRA="([^"]*)"', body):
            flags |= set(re.findall(r'(--[a-z0-9-]+)', e.group(1)))
        out[name] = flags
    return out


def main() -> int:
    defined = defined_flags()
    presets = preset_flags()
    if not presets:
        print("FAIL: parsed no presets out of eval_shard.sh - the regex is stale")
        return 1
    bad = {n: sorted(f - defined) for n, f in presets.items() if f - defined}
    for name in sorted(presets):
        flags = sorted(presets[name])
        mark = "FAIL" if name in bad else "ok  "
        print(f"  {mark} {name:16s} {' '.join(flags) if flags else '(no extra flags)'}")
    print()
    if bad:
        for name, missing in sorted(bad.items()):
            print(f"FAIL: --exp {name} passes {' '.join(missing)}, "
                  f"which run_eval.py does not define")
        return 1

    # Belt and braces: actually invoke the parser with one preset's flags and a
    # sentinel, and confirm the ONLY thing it rejects is the sentinel. This is
    # what proves the flags parse rather than merely being present in the source.
    probe = ["--free-guarded-rounds", "--free-guard-draw-cap", "3", "--check-overfit"]
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_eval.py"),
         "--programs", "x/1", "--modes", "typed", "--seeds", "1", "--budget", "1",
         *probe, "--zzz-sentinel"],
        capture_output=True, text=True, cwd=ROOT)
    unrecognised = re.search(r"unrecognized arguments: (.*)", r.stderr or "")
    rejected = unrecognised.group(1).split() if unrecognised else []
    if rejected != ["--zzz-sentinel"]:
        print(f"FAIL: parser rejected {rejected or 'nothing'}; expected only the sentinel")
        return 1
    print(f"{len(presets)} presets, every flag defined, and the parser rejects "
          f"only the sentinel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
