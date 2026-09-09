#!/usr/bin/env python3
"""The experiment preset table - the ONE place `--exp NAME` is defined.

scripts/eval_shard.sh used to carry the table as a bash `case`; the pipeline
now has a second reader (scripts/grid_status.py decides, cell by cell, whether a
preset still has work to do), and two copies of a flag table is how a shard
lands in a cell key nothing else recognises. So the table lives here, in plain
data, and both readers query it:

    python3 scripts/presets.py --list
    python3 scripts/presets.py --exp E1 --field modes      # -> no_memory
    python3 scripts/presets.py --exp E1 --field extra      # -> --force-full-budget
    python3 scripts/presets.py --exp E4-k8 --field universe  # -> sweep
    python3 scripts/presets.py --exp E1 --field seeds      # -> 1 2 3 4 5
    python3 scripts/presets.py --exp E1 --field budget     # -> 20
    python3 scripts/presets.py --exp E1 --shell            # bash assignments, one call
    python3 scripts/presets.py --exp E1 --json

Standard library only: eval_shard.sh queries it before the venv is activated.

What a preset carries
---------------------
  modes      the arms run_eval.py walks (--modes)
  extra      the flags that make the arm what it is (--force-full-budget,
             --max-examples K, ...). Flags in the CELL KEY live here; audits that
             are not (--check-regression) are appended by eval_shard.sh instead.
  universe   which frozen list the shard indices range over: corpus, sweep,
             live, trial (generated from tasks.json by scripts/universes.py and
             scripts/build_live_universe.py) or demo/hardend (drawn by hand)
  seeds      the default seeds (--seeds overrides)
  budget     the default attempt budget (--budget overrides)
  mergeable  whether consolidate_evals.py may merge the shard (the trial is a
             rehearsal and writes *_trial.jsonl, which the merge never globs)
  resume_merged  whether the shard consults the merged history for finished
             cells. Only the trial says no: a rehearsal that skipped cells
             another machine ran would rehearse nothing.

The PROPOSER lists at the bottom are what scripts/reproduce.sh walks: the local
proposer (qwen2.5-coder:7b) ran the whole grid; the second proposer
(gpt-4o-mini) ran the three arms the paper's RQ6 compares. E2 on the cloud run
was collected WITHOUT --check-regression - do not add it to the cloud preset.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys

DEFAULT_SEEDS = (1, 2, 3)
DEFAULT_BUDGET = 20

# name -> which list under data/<run>/ it is. `universe_list()` in eval_shard.sh
# is the bash mirror of this dict; tests/test_presets.py asserts they agree.
UNIVERSE_FILES = {
    "corpus": "eval_order.txt",
    "sweep": "sweep_programs.txt",
    "trial": "trial_programs.txt",
    "live": "live_programs.txt",
    "demo": "demo_programs.txt",
    "hardend": "hardend_programs.txt",
}
# Written from tasks.json by scripts/universes.py (the interleave) ...
GENERATED_UNIVERSES = ("corpus", "sweep", "trial")
# ... or derived from those by scripts/build_live_universe.py. demo and hardend
# are DRAWN lists: nothing regenerates them.
DERIVED_UNIVERSES = ("live",)


def _p(modes, extra=(), universe="corpus", seeds=DEFAULT_SEEDS, budget=DEFAULT_BUDGET,
       mergeable=True, resume_merged=True, note=""):
    return {
        "modes": list(modes), "extra": list(extra), "universe": universe,
        "seeds": list(seeds), "budget": budget, "mergeable": mergeable,
        "resume_merged": resume_merged, "note": note,
    }


# Order is the order --list prints and the order the usage text documents.
PRESETS: dict[str, dict] = {
    "trial": _p(["no_memory", "untyped", "typed"], ["--check-overfit"],
                universe="trial", seeds=[1], budget=5, mergeable=False, resume_merged=False,
                note="all three arms, 3 bands x 1 task, 1 seed, B=5. A rehearsal of the flags, never data"),
    "E1": _p(["no_memory"], ["--force-full-budget"], seeds=[1, 2, 3, 4, 5],
             note="no-memory arm; every round an independent draw of pi"),
    "E2": _p(["untyped", "typed"], ["--check-overfit"], seeds=[1, 2, 3, 4, 5],
             note="the two memory arms of the main grid"),
    "E3-guard-only": _p(["typed"], ["--steer", "off"],
                        note="typed with the prompt-side steering off"),
    "E3-steer-only": _p(["typed"], ["--guard", "off"],
                        note="typed with the guard off"),
    "E4-k20": _p(["typed"], ["--max-examples", "20", "--check-overfit"], universe="sweep",
                 note="oracle informativeness sweep"),
    "E4-k8": _p(["typed"], ["--max-examples", "8", "--check-overfit"], universe="sweep"),
    "E4-k3": _p(["typed"], ["--max-examples", "3", "--check-overfit"], universe="sweep"),
    "E5-c90": _p(["typed"], ["--typing-noise-c", "0.9"], universe="sweep",
                 note="typing coherence sweep"),
    "E5-c75": _p(["typed"], ["--typing-noise-c", "0.75"], universe="sweep"),
    "E5-c50": _p(["typed"], ["--typing-noise-c", "0.5"], universe="sweep"),
    # Two more c levels: four points (1.0 from E2, then .9/.75/.5) give a slope
    # but not a crossover; these reach far enough down that they should.
    "E5-c25": _p(["typed"], ["--typing-noise-c", "0.25"], universe="sweep"),
    "E5-c00": _p(["typed"], ["--typing-noise-c", "0.0"], universe="sweep"),
    # The c axis's NULL, which c=0.00 is not. TypedMemory.store noises only the
    # location half of a type and the first store of an episode has nowhere else
    # to file itself, so the bottom of the sweep is a lower bound on the damage
    # rather than random assignment. This arm files every refutation under a
    # location drawn uniformly from those seen so far, its own included: memory
    # still partitions the evidence and the guard is still O(1), but the partition
    # carries no information about failure type. Without it, "typing helps because
    # the classes are right" is not separable from "any partition helps" - which
    # is the first question a reviewer asks about a typed index.
    "E5-random": _p(["typed"], ["--typing-random"], universe="sweep",
                    note="the c axis's null: classes assigned at random"),
    # ── baseline-comparison arms ──────────────────────────────────────────
    # E10: the ChatRepair-family baseline (ISSTA'24), as a mechanism port: the
    # prompt carries the whole conversation so far - every failed patch and the
    # counterexample that killed it - and the conversation RESTARTS (transcript
    # cleared) after 5 straight fails or ~6k transcript tokens. No guard: mode is
    # no_memory, so round 1 is byte-identical to E1's draw (CRN) and every
    # divergence after it is the transcript's own doing. Corpus universe at 3
    # seeds - it pairs with the E3 ablation arms, not the 5-seed main grid.
    "E10-chat": _p(["no_memory"], ["--history", "chat"], seeds=[1, 2, 3],
                   note="ChatRepair-family baseline: the whole conversation in the prompt"),
    # E11: the CodeT-family baseline (ICLR'23): one cached model call generates
    # test cases per task, and every proposal must pass them BEFORE the oracle is
    # paid - the one baseline that attacks oracle cost the way the guard does, by
    # blocking calls, but with a-priori model knowledge instead of accumulated
    # refutations. E11b composes it WITH the typed guard (steer off).
    "E11-selftest": _p(["no_memory"], ["--selftest", "on", "--check-overfit"], universe="sweep",
                       note="CodeT-family baseline: model-generated tests before the oracle"),
    "E11b-selftest-guard": _p(["typed"], ["--selftest", "on", "--steer", "off", "--check-overfit"],
                              universe="sweep", note="typed guard + selftest"),
    # E12: the control for "To Run or Not to Run" (ISSTA'26): skip oracle calls
    # AT RANDOM with p=0.37, the typed guard's measured block share of proposals
    # on the main grid. The guard skips calls it can prove would fail; this skips
    # blindly at the same rate. Success is predicted to DROP.
    "E12-randskip": _p(["no_memory"], ["--oracle-skip-p", "0.37"], universe="sweep",
                       note="control: skip the oracle at random at the guard's block share"),
    # Redundancy audit: pay the oracle on guarded rounds too, so a guarded round
    # carries the failure type it would have had. Without it every type-based
    # redundancy count is censored in exactly the arms that guard. Sweep subset
    # only - it spends the oracle time E2 exists to show can be saved.
    "E8-audit": _p(["untyped", "typed"], ["--audit-guarded"], universe="sweep",
                   note="untyped+typed with the oracle paid on guarded rounds; sweep subset"),
    # E8 over the whole corpus, not the 30-task sweep: the CRN join recovers a
    # guarded round's failure type for free in every arm whose prompt is
    # unconditioned, but the STEERED typed arm diverges from the no-memory draw
    # sequence, so its type-keyed metrics (FSRR, type entropy, the revisit
    # curve) need the audit over all tasks. Replays E2's cached draws: no model
    # calls.
    "E8-corpus": _p(["untyped", "typed"], ["--audit-guarded"], universe="corpus", seeds=[1, 2, 3],
                    note="E8-audit over the whole corpus; no model calls"),
    # E9: the same three arms as E2 under the OTHER budget accounting, where a
    # guarded round is free. E2 measures what memory saves per attempt; E9 what
    # it buys when the attempts it saves are handed back to the search. Separate
    # cells by construction (--free-guarded-rounds is in the cell key). Cap 3,
    # not the default 10: on this universe the untyped guard blocks ~54% of
    # candidates, so 3x20 = 60 draws already reaches a 20-attempt budget, and
    # 10x would spend 46 GPU-hours proving that dead tasks stay dead.
    "E9-freeguard": _p(["no_memory", "untyped", "typed"],
                       ["--free-guarded-rounds", "--free-guard-draw-cap", "3", "--check-overfit"],
                       universe="live", seeds=[1, 2, 3],
                       note="the three arms with guarded rounds free of budget; live subset"),
}

# What scripts/reproduce.sh runs per proposer, in this order.
LOCAL_PRESETS = ("E1", "E2", "E3-guard-only", "E3-steer-only",
                 "E4-k20", "E4-k8", "E4-k3",
                 "E5-c90", "E5-c75", "E5-c50", "E5-c25", "E5-c00", "E5-random",
                 "E8-corpus", "E9-freeguard")
CLOUD_PRESETS = ("E1", "E2", "E3-steer-only")
PROPOSER_PRESETS = {"local": LOCAL_PRESETS, "cloud": CLOUD_PRESETS}

# The proposers as scripts/reproduce.sh knows them: the shard tag folds the
# model id in whenever it is not the pinned local one (eval_shard.sh), so the
# local id is a constant both sides have to agree on.
LOCAL_MODEL = "qwen2.5-coder:7b"


class UnknownPreset(KeyError):
    pass


def get(name: str) -> dict:
    try:
        return PRESETS[name]
    except KeyError:
        raise UnknownPreset(name) from None


def universe_file(universe: str) -> str:
    """Basename of the list a universe name stands for ('' if unknown)."""
    return UNIVERSE_FILES.get(universe, "")


# ── the cell-key half of a preset ────────────────────────────────────────────
# scripts/run_eval.py::cell_key takes the knobs as explicit arguments. These are
# the argparse defaults of that driver, restated, and cell_params() below maps a
# preset's EXTRA onto them - so grid_status.py can build the cell a preset would
# produce without importing the driver's parser (which lives inside main()).
# tests/test_grid_status.py checks each mapping against the driver's own parser.
CELL_DEFAULTS = {
    "guard_on": True, "steer_on": True, "max_examples": 100, "typing_noise_c": 1.0,
    "force_full_budget": False, "audit_guarded": False, "reasoning_effort": None,
    "typing_random": False, "free_guarded_rounds": False, "history": "off",
    "selftest": False, "oracle_skip_p": 0.0,
}

# flag -> (cell field, how to read its value). None = a bare switch.
_FLAG_TO_FIELD = {
    "--guard": ("guard_on", lambda v: v == "on"),
    "--steer": ("steer_on", lambda v: v == "on"),
    "--max-examples": ("max_examples", int),
    "--typing-noise-c": ("typing_noise_c", float),
    "--force-full-budget": ("force_full_budget", None),
    "--audit-guarded": ("audit_guarded", None),
    "--reasoning-effort": ("reasoning_effort", str),
    "--typing-random": ("typing_random", None),
    "--free-guarded-rounds": ("free_guarded_rounds", None),
    "--history": ("history", str),
    "--selftest": ("selftest", lambda v: v == "on"),
    "--oracle-skip-p": ("oracle_skip_p", float),
}
# Flags a preset may carry that are NOT in the cell key (audits, rendering
# policy). Listed so an unknown flag is an error rather than a silent no-op.
_NOT_IN_KEY = {
    "--check-overfit": 0, "--check-regression": 0, "--regression-cap": 1,
    "--free-guard-draw-cap": 1, "--history-cap-tokens": 1,
    "--history-restart-after": 1, "--selftest-cases": 1,
}


def cell_params(extra: list[str]) -> dict:
    """The cell-key fields a run_eval.py invocation with these EXTRA flags produces.

    Returns a dict with exactly the keys of CELL_DEFAULTS; pass it as **kwargs
    to run_eval.cell_key together with task, mode, seed, model, granularity.
    """
    out = dict(CELL_DEFAULTS)
    i = 0
    while i < len(extra):
        flag = extra[i]
        if flag in _FLAG_TO_FIELD:
            field, conv = _FLAG_TO_FIELD[flag]
            if conv is None:
                out[field] = True
                i += 1
            else:
                if i + 1 >= len(extra):
                    raise ValueError(f"{flag} needs a value")
                out[field] = conv(extra[i + 1])
                i += 2
        elif flag in _NOT_IN_KEY:
            i += 1 + _NOT_IN_KEY[flag]
        else:
            raise ValueError(f"unknown flag in preset extra: {flag}")
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
FIELDS = ("modes", "extra", "universe", "seeds", "budget", "mergeable", "resume_merged", "note")


def _field_text(preset: dict, field: str) -> str:
    v = preset[field]
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def shell_assignments(preset: dict) -> str:
    """The bash variables eval_shard.sh's `case` block used to set."""
    lines = [
        f"MODES={shlex.quote(' '.join(preset['modes']))}",
        f"EXTRA={shlex.quote(' '.join(preset['extra']))}",
        f"UNIVERSE={shlex.quote(preset['universe'])}",
        f"DEF_SEEDS={shlex.quote(' '.join(str(s) for s in preset['seeds']))}",
        f"DEF_BUDGET={int(preset['budget'])}",
        f"MERGEABLE={1 if preset['mergeable'] else 0}",
    ]
    # Only the trial forces this off; every other preset leaves the caller's
    # --no-resume-merged decision alone, exactly as the case block did.
    if not preset["resume_merged"]:
        lines.append("RESUME_MERGED=0")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", help="preset name")
    ap.add_argument("--field", choices=FIELDS, help="print one field of --exp, space-separated")
    ap.add_argument("--shell", action="store_true",
                    help="print bash assignments for --exp (MODES EXTRA UNIVERSE DEF_SEEDS "
                         "DEF_BUDGET MERGEABLE [RESUME_MERGED])")
    ap.add_argument("--json", action="store_true", help="print --exp (or the whole table) as JSON")
    ap.add_argument("--list", action="store_true", help="one line per preset")
    ap.add_argument("--presets", choices=sorted(PROPOSER_PRESETS),
                    help="print the preset list scripts/reproduce.sh runs for this proposer")
    args = ap.parse_args(argv)

    if args.presets:
        print(" ".join(PROPOSER_PRESETS[args.presets]))
        return 0
    if args.list:
        for name, p in PRESETS.items():
            print(f"{name:20s} universe={p['universe']:7s} modes={','.join(p['modes']):26s} "
                  f"seeds={','.join(map(str, p['seeds'])):10s} budget={p['budget']:<3d} "
                  f"extra={' '.join(p['extra'])}")
        return 0
    if args.json and not args.exp:
        print(json.dumps({"presets": PRESETS, "local": LOCAL_PRESETS, "cloud": CLOUD_PRESETS,
                          "universe_files": UNIVERSE_FILES, "local_model": LOCAL_MODEL}, indent=2))
        return 0
    if not args.exp:
        ap.print_usage(sys.stderr)
        return 2
    try:
        preset = get(args.exp)
    except UnknownPreset:
        print(f"unknown --exp: {args.exp}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"name": args.exp, **preset, "cell_params": cell_params(preset["extra"])},
                         indent=2))
    elif args.shell:
        print(shell_assignments(preset))
    elif args.field:
        print(_field_text(preset, args.field))
    else:
        for f in FIELDS:
            print(f"{f:14s} {_field_text(preset, f)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
