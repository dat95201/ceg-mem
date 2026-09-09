#!/usr/bin/env python3
"""The generated universe lists: eval_order.txt, sweep_programs.txt, trial_programs.txt.

    python3 scripts/universes.py data/<run>/tasks.json data/<run>

This is the interleave that used to live as a heredoc inside scripts/eval_shard.sh,
moved here so that scripts/grid_status.py can write the same lists the shard
script writes - same header lines, same digest, same refusal when a list on disk
was cut from a different corpus. eval_shard.sh calls this file; nothing else
generates these lists.

Why the order is not data/tasks.json's order
--------------------------------------------
data/tasks.json is grouped by stratum - every `dead` task, then every `hard`,
then `medium`, `easy`, `too_easy`, in the band order select_corpus.py froze.
Cutting index ranges out of that would hand one machine every `dead` task (the
full budget in every cell, by construction - nothing there ever accepts) and
another every `too_easy` one (usually one round), so the shards would differ
~10x in wall clock and, worse, a run that finished three shards of four would
hold a stratum-biased grid rather than a smaller one. So each band is spaced
evenly over the whole universe first, deterministically, and the result written
with the corpus digest that produced it. Every prefix and every suffix of that
order is then proportional.

The `live` universe (E9) is DERIVED from the sweep by scripts/build_live_universe.py;
`demo` and `hardend` are drawn by hand. Neither is written here.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

BANDS = ("dead", "hard", "medium", "easy", "too_easy")
GENERATED = ("eval_order", "sweep_programs", "trial_programs")


def corpus_digest(tasks: list[dict]) -> str:
    """Name AND stratum: the stratum decides where a task lands in the interleave,
    so a re-freeze that kept every name but re-banded one task would leave a
    name-only digest unchanged while every shard index shifted underneath it."""
    return hashlib.sha256(
        "\n".join(f"{t['name']}\t{t['stratum']}" for t in tasks).encode()).hexdigest()


def interleave(groups: dict[str, list[str]]) -> list[str]:
    """Interleave the strata, in the frozen order within each. Deterministic, so
    every machine cuts the same shard from the same index range - and balanced,
    so any contiguous range holds every stratum in roughly its corpus proportion.

    Positional, not a plain round-robin cycle: the quotas are unequal by design
    and a band can under-fill besides, so cycling exhausts the small bands first
    and leaves a tail drawn from whichever band is largest - making the last
    shard the least representative one. Spacing each band evenly over [0,1)
    keeps every prefix AND every suffix proportional, whatever the counts are."""
    placed = [((i + 0.5) / len(names), bi, names[i])
              for bi, (_, names) in enumerate(groups.items()) if names
              for i in range(len(names))]
    return [name for _, _, name in sorted(placed)]


def build(tasks: list[dict]) -> dict[str, list[str]]:
    """The three generated lists, from the frozen corpus."""
    by = {b: [t["name"] for t in tasks if t["stratum"] == b] for b in BANDS}
    order = interleave(by)
    # The E4/E5 subset: six per band, by name, per DESIGN.md - then put through
    # the same interleave so a sweep shard is balanced too. Frozen to a file
    # because scripts/freeze_results.py --sweep-programs-from must receive the
    # identical list days later, and its own default is a different set.
    sweep = interleave({b: sorted(by[b])[:6] for b in BANDS})
    # The trial: one task from each of three bands that behave differently -
    # `dead` never accepts, `easy` usually does on the first or second round. An
    # arm that looks identical on all three is an arm whose flags did not take.
    trial = [by[b][0] for b in ("dead", "medium", "easy") if by[b]]
    return {"eval_order": order, "sweep_programs": sweep, "trial_programs": trial}


def render(name: str, names: list[str], tasks_path: str, digest: str) -> str:
    """The exact file body eval_shard.sh has always written."""
    return (f"# {name}: {len(names)} programs, strata interleaved evenly\n"
            f"# corpus: {tasks_path}\n"
            f"# corpus_sha256: {digest}\n" + "\n".join(names) + "\n")


def read_digest(path: pathlib.Path) -> str | None:
    """The `# corpus_sha256:` header of a list, or None."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.startswith("# corpus_sha256: "):
            return line.split(": ", 1)[1]
    return None


def read_universe(path: pathlib.Path) -> list[str]:
    """The program names in a list file, in file order, comments dropped."""
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def write_universes(tasks_path: str, data_dir: pathlib.Path, *, rewrite: bool = True) -> str:
    """Write (or validate) the three generated lists under data_dir.

    `tasks_path` is recorded in the header AS GIVEN - eval_shard.sh passes the
    repo-relative "data/<run>/tasks.json", so pass the same string to get the
    same bytes. A list already on disk is left alone when identical, rewritten
    when only its header path differs, and REFUSED (SystemExit) when its digest
    says it was cut from a different corpus: every shard index would then mean a
    different task, and episodes already collected were run against the old
    list. Returns the corpus digest.

    rewrite=False (scripts/grid_status.py, a status tool) still validates the
    digest of every list on disk but writes only lists that are ABSENT, so
    asking about a run never touches a file the run already has.
    """
    tasks = json.loads(pathlib.Path(tasks_path).read_text())["tasks"]
    digest = corpus_digest(tasks)
    lists = build(tasks)
    for name in GENERATED:
        names = lists[name]
        path = pathlib.Path(data_dir) / f"{name}.txt"
        body = render(name, names, tasks_path, digest)
        if path.exists():
            prev = [l for l in path.read_text().splitlines()
                    if l.startswith("# corpus_sha256: ")]
            if prev and prev[0].split(": ", 1)[1] != digest:
                sys.exit(
                    f"{path} was cut from a different {tasks_path} "
                    f"({prev[0].split(': ', 1)[1][:12]}... vs {digest[:12]}...).\n"
                    "Every shard index would mean a different task, and episodes\n"
                    "already collected were run against the old list. Move the old\n"
                    f"{data_dir}/*_programs.txt, {data_dir}/eval_order.txt and the episode logs\n"
                    "aside deliberately, or restore the corpus they belong to.")
            if path.read_text() == body or not rewrite:
                continue
        path.write_text(body)
    return digest


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] in ("-h", "--help"):
        print("usage: python3 scripts/universes.py <tasks.json> <data dir>", file=sys.stderr)
        return 2
    # argv[2] is a directory the caller (eval_shard.sh) has already created.
    write_universes(argv[1], pathlib.Path(argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
