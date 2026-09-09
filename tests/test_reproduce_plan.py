"""scripts/reproduce.sh --dry-run prints a plan and a summary table, and starts nothing.

Run against run directories that do not exist, so the plan is the from-scratch
one and the test does not depend on installed artifacts. Every stage must
appear in the summary, the status column must read "planned"/"hit"/"skipped",
and nothing under data/ may be created.

No model, no benchmark, no network.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAGES = ("env", "artifacts", "corpus", "grid", "analyse", "policies", "numbers", "paper")


def _env(**extra):
    env = dict(os.environ)
    for k in list(env):
        if k in ("ARTIFACTS", "ARTIFACTS_SHA256", "PROPOSERS", "SHARDS", "RUN_DIR"):
            env.pop(k)
    env.update({"LOCAL_RUN_DIR": "_nonexistent_local", "CLOUD_RUN_DIR": "_nonexistent_cloud"})
    env.update(extra)
    return env


def _strip(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_dry_run_prints_every_stage_and_starts_nothing():
    before = set(p.name for p in (ROOT / "data").iterdir())
    r = subprocess.run(["bash", "scripts/reproduce.sh", "--dry-run"], cwd=ROOT, env=_env(),
                       capture_output=True, text=True)
    out = _strip(r.stdout + r.stderr)
    assert r.returncode == 0, out
    assert "DRY RUN" in out and "--dry-run: nothing was started." in out
    table = out[out.index("stage      scope"):]
    for stage in STAGES:
        assert re.search(rf"^{stage}\s", table, re.M), (stage, table)
    for row in table.splitlines()[2:]:
        if not row.strip() or row.startswith("total") or row.startswith("--dry-run"):
            continue
        assert re.search(r"planned|hit|skipped", row), row
    # the from-scratch plan names the corpus stages and every preset
    assert "pipeline.sh candidates" in out and "pipeline.sh corpus" in out
    assert "E9-freeguard" in out and "E3-steer-only" in out
    after = set(p.name for p in (ROOT / "data").iterdir())
    assert after == before


def test_dry_run_with_a_single_stage_and_a_bad_stage():
    r = subprocess.run(["bash", "scripts/reproduce.sh", "--dry-run", "--stage", "numbers"], cwd=ROOT,
                       env=_env(), capture_output=True, text=True)
    out = _strip(r.stdout)
    assert r.returncode == 0, out
    table = out[out.index("stage      scope"):]
    assert re.search(r"^numbers\s", table, re.M) and not re.search(r"^grid\s", table, re.M)
    assert "check_numbers.py" in out
    r = subprocess.run(["bash", "scripts/reproduce.sh", "--stage", "bogus"], cwd=ROOT, env=_env(),
                       capture_output=True, text=True)
    assert r.returncode == 2 and "unknown --stage" in r.stderr


def test_help_and_argument_validation():
    r = subprocess.run(["bash", "scripts/reproduce.sh", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "--dry-run" in r.stdout and "--update-numbers" in r.stdout
    r = subprocess.run(["bash", "scripts/reproduce.sh", "--dry-run"], cwd=ROOT, env=_env(SHARDS="x"),
                       capture_output=True, text=True)
    assert r.returncode == 2 and "SHARDS" in r.stderr
    r = subprocess.run(["bash", "scripts/reproduce.sh", "--dry-run"], cwd=ROOT, env=_env(PROPOSERS="local mars"),
                       capture_output=True, text=True)
    assert r.returncode == 2 and "PROPOSERS" in r.stderr


def main() -> int:
    for fn in (test_dry_run_prints_every_stage_and_starts_nothing, test_dry_run_with_a_single_stage_and_a_bad_stage,
               test_help_and_argument_validation):
        fn()
        print(f"ok   {fn.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
