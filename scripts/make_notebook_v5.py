#!/usr/bin/env python3
"""Build notebook v5 from v4: one job, the P1-1 baseline comparison.

v5 is v4 with section 13 replaced. A reader who knows v4 can diff the two in a
minute:

  * the config cell points BRANCH at feat/p1-1-policy-comparison
  * section 13 is now "P1-1 - the baseline comparison": Qi'13 and Venugopal'20
    against the guard, over byte-identical candidates, zero model calls
  * v4's section 13, the optional cloud proposer, is REMOVED along with its
    CLOUD_* settings

Removing the cloud section is scope, not a judgement on it: a second proposer is
still the answer to the review's external-validity point (P1-10), and it is
still needed. It is out of v5 because v5 exists to run one thing, and a notebook
that offers a paid API call next to a free one invites the wrong cell to be run
first. v4 keeps it intact, and re-adding it here is a copy of two cells.
"""
import json
import pathlib
import sys

SRC = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "notebooks/CEGMem_Colab_v4.ipynb")
DST = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "notebooks/CEGMem_Colab_v5.ipynb")
BRANCH = "feat/p1-1-policy-comparison"

HDR = "# " + "─" * 76 + "\n"


CONFIG = """

# ── P1-1, the baseline comparison (section 13) ──────────────────────────────
# Which log the verdict matrix and the policy simulation read. The frozen run is
# the one the paper reports; point this at RUN_DIR's own episodes.jsonl only if
# you are rebuilding the matrix for a different run.
EPISODES = "runs/2026-09-01/episodes.jsonl"

# Sandbox workers for the matrix build. Every candidate-case pair is a
# subprocess, so this is bounded by CORES, not VRAM. Set it to `nproc` and no
# higher: these are CPU-bound children, so over-subscribing only adds context
# switches. It changes nothing measured; only how long it takes. The build cell
# prints a warning if this disagrees with the machine.
MATRIX_JOBS = 2

# How many tasks share one worker pool. The adapter's case cache must be dropped
# periodically (one AtCoder task's test data reaches 84 MB), but draining ONE
# task before starting the next makes every task's tail serial - measured on this
# corpus, a candidate costs p50 15.9 s and max 2,767.7 s, so one task's last
# candidate can hold a single core for 46 minutes while the rest idle. 4 gives
# the pool ~220 candidates to interleave. Raise it if you have RAM to spare.
MATRIX_TASK_GROUP = 4
"""


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

code(HDR + """# STAGE   P1-1: the policy tests
# READS   tests/test_policies.py
# WRITES  nothing
# TIME    ~5 s
# SKIP?   NO. Eight seconds here beats a day of CPU on a broken ordering
""" + HDR + """import os; os.chdir(WORKDIR)
# No LLM and no sandbox: a hand-built verdict matrix, five policies, eight
# claims. The one that matters pins the 2026-08-31 soundness correction - the
# indexed guard must block exactly the rounds the flat store blocks, because the
# bucket says where to look FIRST and never where to STOP.
!python3 tests/test_policies.py"""),

code(HDR + """# STAGE   P1-1 stage 1: cost of the verdict matrix - nothing runs
# READS   data/<RUN_DIR>/episodes.jsonl (or --episodes)
# WRITES  nothing
# TIME    ~10 s
# SKIP?   NO. Read the estimate before spending a day of CPU
""" + HDR + """import os; os.chdir(WORKDIR)
# EPISODES and MATRIX_JOBS are set in section 1, with everything else. --jobs is
# passed so the "h wall" line is the estimate for the machine you are on.
# NO MODEL IS CALLED, here or in the build below: the candidates are already in
# the frozen log. --episodes chooses WHICH RUN's candidates, not which model to
# call - point it at the run the paper reports, not at whatever is running now.
!python3 scripts/build_verdict_matrix.py --episodes {EPISODES} \\
    --jobs {MATRIX_JOBS} --plan-only"""),

code(HDR + """# STAGE   P1-1 stage 1: build the matrix
# READS   episodes.jsonl, external/ConDefects/Test
# WRITES  data/<RUN_DIR>/verdicts.jsonl, verdicts_cases.json
# TIME    ~50 CPU-hours. Resumable: re-run this cell after a disconnect
# SKIP?   NO for P1-1
""" + HDR + """import os; os.chdir(WORKDIR)
RUN_LOGS = f"logs/{RUN_DIR}" if RUN_DIR else "logs"
os.makedirs(RUN_LOGS, exist_ok=True)
if MATRIX_JOBS != os.cpu_count():
    print(f"note: MATRIX_JOBS={MATRIX_JOBS} but this machine has {os.cpu_count()} "
          f"cores. Set it to the core count in section 1 and RE-RUN THAT CELL - "
          f"editing it is not enough, the kernel keeps the old value.")

# --timeout 30 is HARDCODED, not a section-1 knob, and must stay 30. It is the
# value the frozen run used (runs/2026-09-01/eval_shards/*.meta.json,
# protocol.sandbox_timeout_sec). A matrix holding some rows judged at 10 s and
# some at 30 s is two instruments in one table, and every policy simulated on
# top would be measuring the mixture.
#
# Resumable per (task, patch): a disconnect costs the candidates in flight, not
# the matrix. Re-run this cell and it prints "already done N".
!nohup python3 scripts/build_verdict_matrix.py --episodes {EPISODES} \\
    --timeout 30 --jobs {MATRIX_JOBS} --task-group {MATRIX_TASK_GROUP} \\
    --progress-every 120 --slow-note 300 > {RUN_LOGS}/verdicts.log 2>&1 &
print("launched; watch it with the next cell. Safe to re-run after a disconnect.")"""),

code(HDR + """# STAGE   watch the matrix build
# READS   logs/<RUN_DIR>/verdicts.log
# WRITES  nothing
# TIME    instant
# SKIP?   re-run as often as you like
""" + HDR + """import os; os.chdir(WORKDIR)
RUN_DATA = f"data/{RUN_DIR}" if RUN_DIR else "data"
RUN_LOGS = f"logs/{RUN_DIR}" if RUN_DIR else "logs"
# [b] so pkill/pgrep patterns never match this shell's own command line.
!pgrep -fa "[b]uild_verdict_matrix" | head -1 || echo "not running"
!tail -4 {RUN_LOGS}/verdicts.log
!wc -l {RUN_DATA}/verdicts.jsonl 2>/dev/null || echo "no rows yet"
# load average should sit near the core count. Near 1.0 with --jobs 8 means the
# pool has been starved by one task's tail - the thing --task-group fixes.
!nproc; uptime"""),

code(HDR + """# STAGE   stop the matrix build
# READS   nothing
# WRITES  nothing
# TIME    ~5 s
# SKIP?   only needed to restart the build cleanly
""" + HDR + """import os, time; os.chdir(WORKDIR)
# The build APPENDS to verdicts.jsonl, so it must be gone before the cell above
# is re-run - two writers can tear a line. A torn line is survivable (the pair
# just re-runs) but there is no reason to make one.
#
# The [b] is not decoration: `pkill -f build_verdict_matrix` matches the shell
# running the pkill, so it can kill itself before delivering the signal.
!pkill -f "[b]uild_verdict_matrix"
time.sleep(5)
!pgrep -fa "[b]uild_verdict_matrix" || echo 'stopped - safe to re-run the build cell'"""),

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
#
# Two lines to read, in order. COVERAGE first - the rate is computed only over
# rounds the matrix can answer, so a matrix 1% built and perfect on that 1%
# would print 1.0000. Below 0.99 coverage the cell prints INCOMPLETE and exits
# 3, which decides nothing: finish the build and come back. Only then does
# PASS/FAIL on the 0.95 rate mean anything.
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

    # 1b. section 13's two settings replace v4's CLOUD_* block in the config cell
    for c in cells:
        s = "".join(c["source"])
        if c["cell_type"] == "code" and "STAGE   configuration" in s:
            lines = s.splitlines(keepends=True)
            start = next((i for i, l in enumerate(lines)
                          if l.startswith("# ── the second proposer")), None)
            if start is not None:
                end = next((i for i in range(start, len(lines))
                            if lines[i].startswith("CLOUD_BUDGET")), start)
                del lines[start:end + 1]
                print(f"  dropped {end - start + 1} CLOUD_* lines from the config cell")
            s = "".join(lines).rstrip("\n")
            if "EPISODES" not in s:
                s += "\n" + CONFIG
            c["source"] = s.splitlines(keepends=True)
            break
    else:
        print("warning: configuration cell not found", file=sys.stderr)

    # 2. the title
    first = "".join(cells[0]["source"])
    cells[0]["source"] = first.replace(
        "# CEGMem on Colab — v4",
        "# CEGMem on Colab — v5\n\n"
        "**v5 = v4 + section 13, the P1-1 baseline comparison** (Qi'13 and\n"
        "Venugopal'20 against the guard, on byte-identical candidates, zero model\n"
        "calls). `BRANCH` points at `" + BRANCH + "`.",
    ).splitlines(keepends=True)

    # 3. v4's section 13 (the cloud proposer) comes out; P1-1 takes its place.
    #    Matched on content rather than position: the markdown heading, and the
    #    one code cell that reads CLOUD_API_KEY. Everything after it - the fleet
    #    status cell, "After a disconnect" - is generic and stays.
    at = None
    for i, c in enumerate(cells):
        if c["cell_type"] == "markdown" and \
                "## 13. Optional: a second proposer" in "".join(c["source"]):
            at = i
            break
    if at is None:
        at = len(cells)
        print("warning: v4 section 13 not found; appending P1-1 at the end",
              file=sys.stderr)
        drop = 0
    else:
        drop = 1
        if at + 1 < len(cells) and "CLOUD_API_KEY" in "".join(cells[at + 1]["source"]):
            drop = 2
        print(f"  dropped {drop} cell(s) of v4's optional cloud proposer")
    nb["cells"] = cells[:at] + NEW + cells[at + drop:]

    DST.write_text(json.dumps(nb, indent=1, ensure_ascii=False))
    print(f"{DST}: {len(nb['cells'])} cells ({len(cells)} + {len(NEW)}), BRANCH={BRANCH}")


if __name__ == "__main__":
    main()
