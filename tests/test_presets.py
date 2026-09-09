"""scripts/presets.py is the preset table; scripts/eval_shard.sh must agree with it.

eval_shard.sh reads MODES/EXTRA/UNIVERSE/DEF_SEEDS/DEF_BUDGET from presets.py,
so the two cannot drift by construction - but the WIRING can: a renamed
variable, a quoting slip in --shell, a universe name the bash universe_list()
does not know. So for every preset this runs the real `eval_shard.sh --dry-run`
against a synthetic frozen corpus and checks the plan it prints - modes, seeds,
budget, extra flags, universe file, cell count, episodes path, resume policy -
against the table.

No model, no benchmark: --dry-run starts nothing, and the corpus is ten made-up
tasks (two per band), which is all the interleave needs.

Run: pytest tests/test_presets.py   -   or   python3 tests/test_presets.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import presets as P  # noqa: E402

BANDS = ("dead", "hard", "medium", "easy", "too_easy")


def synthetic_corpus() -> dict:
    tasks = [{"name": f"abc{100 + 10 * bi + i}_a/{1000 + 10 * bi + i}", "stratum": b}
             for bi, b in enumerate(BANDS) for i in range(2)]
    return {"frozen": True, "n_selected": len(tasks), "tasks": tasks}


class RunDir:
    """A throwaway RUN_DIR under data/, removed on exit."""

    def __init__(self, tag: str):
        self.slug = f"_test_{tag}_{os.getpid()}"
        self.path = ROOT / "data" / self.slug

    def __enter__(self):
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "tasks.json").write_text(json.dumps(synthetic_corpus()))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        shutil.rmtree(ROOT / "logs" / self.slug, ignore_errors=True)


def dry_run(slug: str, exp: str) -> str:
    env = dict(os.environ, RUN_DIR=slug)
    r = subprocess.run(["bash", "scripts/eval_shard.sh", "--exp", exp, "--dry-run"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"--exp {exp} --dry-run failed:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def plan_of(out: str) -> dict:
    """The fields eval_shard.sh prints in its plan block."""
    g = re.search(r'grid\s+modes="([^"]*)"\s+seeds="([^"]*)"\s+budget=(\d+)', out)
    assert g, out
    cells = re.search(r"^\s+(\d+) cells(?:\s+·\s+(.*))?$", out, re.M)
    assert cells, out
    shard = re.search(r"shard\s+(\d+)-(\d+) of (\d+) \((\d+) tasks, universe (\S+)\)", out)
    assert shard, out
    episodes = re.search(r"^episodes\s+(\S+)", out, re.M)
    resume = re.search(r"^resume\s+(.*)$", out, re.M)
    return {
        "modes": g.group(1).split(), "seeds": [int(s) for s in g.group(2).split()],
        "budget": int(g.group(3)), "cells": int(cells.group(1)),
        "extra": (cells.group(2) or "").split(),
        "n_universe": int(shard.group(3)), "universe_file": pathlib.Path(shard.group(5)).name,
        "episodes": episodes.group(1), "resume": resume.group(1).strip(),
    }


def test_every_preset_matches_the_dry_run_plan():
    with RunDir("presets") as rd:
        (rd.path / "episodes.jsonl").write_text("")     # a merged history, for the resume line
        for name, preset in P.PRESETS.items():
            plan = plan_of(dry_run(rd.slug, name))
            assert plan["modes"] == preset["modes"], name
            assert plan["seeds"] == preset["seeds"], name
            assert plan["budget"] == preset["budget"], name
            assert plan["extra"] == preset["extra"], name
            assert plan["universe_file"] == P.universe_file(preset["universe"]), name
            assert plan["cells"] == plan["n_universe"] * len(preset["modes"]) * len(preset["seeds"]), name
            if preset["mergeable"]:
                assert plan["episodes"].endswith(f"episodes_eval_{name}_001_{plan['n_universe']:03d}.jsonl"), name
            else:
                assert plan["episodes"].endswith("episodes_trial.jsonl"), name
            if preset["resume_merged"]:
                assert plan["resume"] == f"--resume-from data/{rd.slug}/episodes.jsonl", name
            else:
                assert plan["resume"].startswith("none"), name


def test_unknown_preset_is_refused_with_usage():
    with RunDir("unknown") as rd:
        env = dict(os.environ, RUN_DIR=rd.slug)
        r = subprocess.run(["bash", "scripts/eval_shard.sh", "--exp", "E99-nope", "--dry-run"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        assert r.returncode == 2
        assert "unknown --exp: E99-nope" in r.stderr
        assert "usage:" in r.stderr


def test_bash_universe_list_matches_the_table():
    sh = (ROOT / "scripts" / "eval_shard.sh").read_text()
    body = sh[sh.index("universe_list() {"):sh.index("universe_size() {")]
    pairs = dict(re.findall(r'^\s+(\w+)\)\s+echo "\$RUN_DATA/([\w.]+)"', body, re.M))
    assert pairs == P.UNIVERSE_FILES


def test_field_cli_and_shell_output():
    for field, want in (("modes", "no_memory"), ("extra", "--force-full-budget"),
                        ("universe", "corpus"), ("seeds", "1 2 3 4 5"), ("budget", "20")):
        r = subprocess.run([sys.executable, "scripts/presets.py", "--exp", "E1", "--field", field],
                           cwd=ROOT, capture_output=True, text=True, check=True)
        assert r.stdout.strip() == want, (field, r.stdout)
    r = subprocess.run([sys.executable, "scripts/presets.py", "--exp", "trial", "--shell"],
                       cwd=ROOT, capture_output=True, text=True, check=True)
    assert "RESUME_MERGED=0" in r.stdout and "MERGEABLE=0" in r.stdout and "DEF_BUDGET=5" in r.stdout
    r = subprocess.run([sys.executable, "scripts/presets.py", "--exp", "E2", "--shell"],
                       cwd=ROOT, capture_output=True, text=True, check=True)
    assert "RESUME_MERGED" not in r.stdout and "MERGEABLE=1" in r.stdout
    r = subprocess.run([sys.executable, "scripts/presets.py", "--exp", "nope"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and "unknown --exp" in r.stderr
    for who, want in (("local", P.LOCAL_PRESETS), ("cloud", P.CLOUD_PRESETS)):
        r = subprocess.run([sys.executable, "scripts/presets.py", "--presets", who],
                           cwd=ROOT, capture_output=True, text=True, check=True)
        assert r.stdout.split() == list(want)


def test_proposer_presets_exist_and_are_mergeable():
    for name in (*P.LOCAL_PRESETS, *P.CLOUD_PRESETS):
        assert name in P.PRESETS
        assert P.PRESETS[name]["mergeable"], name
    assert "E2" in P.CLOUD_PRESETS and "--check-regression" not in P.PRESETS["E2"]["extra"]


def _run_eval_defaults() -> dict[str, str]:
    """flag -> default literal, read off run_eval.py's parser source."""
    src = (ROOT / "scripts" / "run_eval.py").read_text()
    out = {}
    for m in re.finditer(r'add_argument\(\s*"(--[a-z0-9-]+)"(.*?)\)\s*\n\s*parser\.', src, re.S):
        flag, body = m.group(1), m.group(2)
        d = re.search(r'default=([^,\)]+)', body)
        out[flag] = d.group(1).strip() if d else ("store_true" if "store_true" in body else "?")
    return out


def test_cell_params_defaults_match_run_eval_parser():
    defaults = _run_eval_defaults()
    want = {
        "--guard": ('"on"', True), "--steer": ('"on"', True), "--max-examples": ("100", 100),
        "--typing-noise-c": ("1.0", 1.0), "--history": ('"off"', "off"),
        "--selftest": ('"off"', False), "--oracle-skip-p": ("0.0", 0.0),
        "--reasoning-effort": ("None", None),
    }
    params = P.cell_params([])
    for flag, (literal, value) in want.items():
        assert defaults.get(flag) == literal, (flag, defaults.get(flag))
        field = P._FLAG_TO_FIELD[flag][0]
        assert params[field] == value, field
    for flag in ("--force-full-budget", "--audit-guarded", "--typing-random", "--free-guarded-rounds"):
        assert defaults.get(flag) == "store_true", flag
        assert P.cell_params([])[P._FLAG_TO_FIELD[flag][0]] is False
    # and every preset's extra parses, with every flag known to the driver
    for name, preset in P.PRESETS.items():
        P.cell_params(preset["extra"])
        for flag in (f for f in preset["extra"] if f.startswith("--")):
            assert flag in defaults, (name, flag)


def main() -> int:
    for fn in (test_every_preset_matches_the_dry_run_plan, test_unknown_preset_is_refused_with_usage,
               test_bash_universe_list_matches_the_table, test_field_cli_and_shell_output,
               test_proposer_presets_exist_and_are_mergeable, test_cell_params_defaults_match_run_eval_parser):
        fn()
        print(f"ok   {fn.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
