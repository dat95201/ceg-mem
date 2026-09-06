#!/usr/bin/env python3
"""Build notebook v5 from v4: the P1-1 baseline-comparison stage, and the branch.

v5 = v4 plus one new section and two edits. Nothing in v4 is reordered or
rewritten, so a reader who knows v4 can diff the two in a minute:

  * the config cell points BRANCH at feat/p1-1-policy-comparison
  * the title cell says what v5 adds
  * a new section 13, "P1-1 - the baseline comparison", inserted before the
    optional second-proposer section, which becomes 14
"""
import json
import pathlib
import sys

SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "notebooks/CEGMem_Colab_v4.ipynb")
DST = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "notebooks/CEGMem_Colab_v5.ipynb")
BRANCH = "feat/p1-1-policy-comparison"

HDR = "# " + "─" * 76 + "\n"


def md(text):
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "execution_count": None, "metadata": {},
            "outputs": [], "source": text.splitlines(keepends=True)}


NEW = [
md("""---

## 13. P1-1 — the baseline comparison

Reviews 1 and 3 both ask for the same thing and it is the one item that decides
the novelty question: measure the guard against the classical mechanisms it
resembles — fault-recorded test prioritization (Qi et al., ICSM'13) and
modification-point aware prioritization (Venugopal et al., 2020). Until this
runs, Table I is argued rather than measured, and §IX says so.

**No model calls.** The candidates are already in the frozen log: in the
`no_memory` arm the proposer is shown nothing the validator found, so the
candidate stream is a function of (task, seed, round) alone and does not depend
on which cases were run or in what order. Five policies replay over
byte-identical candidates. The only new cost is sandbox time.

Two stages:

| | | cost |
|---|---|---|
| `build_verdict_matrix.py` | run every logged candidate against its task's whole pool | ~50 CPU-h, 0 model calls |
| `simulate_policies.py` | replay five ordering policies over that matrix | seconds |

The matrix is what the log cannot give: it records where each candidate failed
**first**, and Qi'13 needs to know how many candidates each case has killed,
which is a whole column of the kill matrix.

**Where to run stage 1.** It is resumable — completed `(task, patch)` pairs are
skipped — but 50 CPU-h on a 2-vCPU Colab is about a day of wall clock, longer
than a session lasts. Either run it here in the background across several
sessions with `RUN_DIR` on Drive, or run it on a workstation overnight and copy
`verdicts.jsonl` + `verdicts_cases.json` into `data/<RUN_DIR>/`. Nothing about
the result depends on which machine produced it: no model is called and the
sandbox verdict is a pure function of (program, input)."""),

code(HDR + """# STAGE   P1-1 stage 1: cost of the verdict matrix - nothing runs
# READS   data/<RUN_DIR>/episodes.jsonl (or --episodes)
# WRITES  nothing
# TIME    ~10 s
# SKIP?   NO. Read the estimate before spending a day of CPU
""" + HDR + """import os; os.chdir(WORKDIR)
EPISODES = "runs/2026-09-01/episodes.jsonl"   # the frozen log; or leave to RUN_DIR's own
!python3 scripts/build_verdict_matrix.py --episodes {EPISODES} --plan-only"""),

code(HDR + """# STAGE   P1-1 stage 1: build the matrix
# READS   episodes.jsonl, external/ConDefects/Test
# WRITES  data/<RUN_DIR>/verdicts.jsonl, verdicts_cases.json
# TIME    ~50 CPU-hours. Resumable: re-run this cell after a disconnect
# SKIP?   NO for P1-1
""" + HDR + """import os; os.chdir(WORKDIR)
RUN_LOGS = f"logs/{RUN_DIR}" if RUN_DIR else "logs"
os.makedirs(RUN_LOGS, exist_ok=True)
# --jobs: sandbox runs are subprocesses, so this is bounded by cores, not VRAM.
# The adapter's case cache is dropped between tasks (one AtCoder task's test data
# reaches 84 MB), so memory stays flat however long this runs.
!nohup python3 scripts/build_verdict_matrix.py --episodes {EPISODES} \\
    --jobs 2 --progress-every 120 > {RUN_LOGS}/verdicts.log 2>&1 &
print("launched; watch it with the next cell. Safe to re-run after a disconnect.")"""),

code(HDR + """# STAGE   watch the matrix build
# READS   logs/<RUN_DIR>/verdicts.log
# WRITES  nothing
# TIME    instant
# SKIP?   re-run as often as you like
""" + HDR + """import os; os.chdir(WORKDIR)
RUN_DATA = f"data/{RUN_DIR}" if RUN_DIR else "data"
RUN_LOGS = f"logs/{RUN_DIR}" if RUN_DIR else "logs"
!pgrep -fa build_verdict_matrix.py | head -1 || echo "not running"
!tail -4 {RUN_LOGS}/verdicts.log
!wc -l {RUN_DATA}/verdicts.jsonl 2>/dev/null || echo "no rows yet\""""),

code(HDR + """# STAGE   P1-1 stage 1: the acceptance gate
# READS   episodes.jsonl, data/<RUN_DIR>/verdicts.jsonl
# WRITES  nothing
# TIME    ~1 min
# SKIP?   NO. Declared before the matrix was built
""" + HDR + """import os; os.chdir(WORKDIR)
# Replays every logged oracle round against the matrix and reproduces the draw
# exactly (src.oracle._sample is random.Random(seed + round_index).sample).
# The gate is 95%: below that the sandbox is not deterministic beyond timeout
# jitter, and every policy simulated on top would be measuring the jitter.
!python3 scripts/build_verdict_matrix.py --verify --episodes {EPISODES}"""),

code(HDR + """# STAGE   P1-1 stage 2: five policies over one candidate stream
# READS   data/<RUN_DIR>/verdicts.jsonl, verdicts_cases.json, episodes.jsonl
# WRITES  data/<RUN_DIR>/policies.json, policies_cells.json
# TIME    seconds
# SKIP?   NO for P1-1
""" + HDR + """import os; os.chdir(WORKDIR)
!python3 scripts/simulate_policies.py --episodes {EPISODES}"""),

md("""### Reading the result

`policies.json` carries a `criterion` block with the pre-declared test:
cegmem-guard reaches its first refutation in no more case executions than the
better of qi13 / venugopal20 on at least 60 of the 99 tasks, one-sided
task-level Wilcoxon p < 0.05.

**Both outcomes ship.** If it is met, Table I stops being argued and starts
being measured. If it is not, the finding is that a classical prioritizer
matches the guard on this corpus, and the narrowing paragraph written in advance
is the one that goes into §II — the claim becomes the key and the evidence
rather than the saving, and Table I moves CEGMem into the same cell as Qi'13.
Neither outcome is a reason to leave the comparison out of the paper.

One number to read next to the criterion, because the two can disagree and the
disagreement is the point: **oracle rounds per episode**. A prioritizer reorders
the cases inside one validation, so it can cut executions without cutting
validations — oracle rounds stay at one per candidate by construction. The guard
blocks the validation outright. If the criterion fails while oracle rounds fall
by a large factor, the honest reading is that the two mechanisms are not
substitutes, and the paper should say which resource each one buys."""),
]


def main():
    nb = json.loads(SRC.read_text())
    cells = nb["cells"]

    # 1. the branch
    hits = 0
    for c in cells:
        s = "".join(c["source"])
        if c["cell_type"] == "code" and "BRANCH   =" in s:
            c["source"] = s.replace('BRANCH   = "official/2026-09-01"',
                                    f'BRANCH   = "{BRANCH}"').splitlines(keepends=True)
            hits += 1
    if hits != 1:
        print(f"warning: patched BRANCH in {hits} cells, expected 1", file=sys.stderr)

    # 2. the title
    first = "".join(cells[0]["source"])
    cells[0]["source"] = first.replace(
        "# CEGMem on Colab — v4",
        "# CEGMem on Colab — v5\n\n"
        "**v5 = v4 + section 13, the P1-1 baseline comparison** (Qi'13 and\n"
        "Venugopal'20 against the guard, on byte-identical candidates, zero model\n"
        "calls). `BRANCH` points at `" + BRANCH + "`.",
    ).splitlines(keepends=True)

    # 3. renumber the optional second-proposer section, then insert before it
    at = None
    for i, c in enumerate(cells):
        s = "".join(c["source"])
        if c["cell_type"] == "markdown" and "## 13. Optional: a second proposer" in s:
            c["source"] = s.replace("## 13. Optional: a second proposer",
                                    "## 14. Optional: a second proposer").splitlines(keepends=True)
            at = i
            break
    if at is None:
        at = len(cells)
        print("warning: section 13 not found, appending at the end", file=sys.stderr)
    nb["cells"] = cells[:at] + NEW + cells[at:]

    DST.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    print(f"{DST}: {len(nb['cells'])} cells ({len(cells)} + {len(NEW)}), BRANCH={BRANCH}")


if __name__ == "__main__":
    main()
