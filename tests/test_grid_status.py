"""scripts/grid_status.py: which cells of a preset are missing from a merged log.

Built on a synthetic run under data/_test_grid_<pid>/: a ten-task frozen corpus
(two per band) and a hand-written episodes.jsonl for the E4-k3 preset (typed,
sweep universe, seeds 1-3, budget 20) in which every cell is complete except
one that stopped after five rounds without accepting and one that never ran.

  * the two are reported missing, nothing else is; exit status 3
  * the rows-based `completed_cells` agrees with run_eval._completed_cells on
    every (budget, force_full_budget) combination the presets use
  * a grid_coverage.json declaring the two turns them into "declared": exit 0
    by default, exit 3 under --strict
  * the universe lists it generates are byte-identical to eval_shard.sh's
  * the preset table it reads is eval_shard.sh's (the dry-run plan agrees)

No model, no benchmark, no oracle.

Run: pytest tests/test_grid_status.py   -   or   python3 tests/test_grid_status.py
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import grid_status as GS   # noqa: E402
import presets as P        # noqa: E402
import run_eval            # noqa: E402
import universes as U      # noqa: E402

BANDS = ("dead", "hard", "medium", "easy", "too_easy")
MODEL = P.LOCAL_MODEL
PRESET = "E4-k3"      # typed, --max-examples 3 --check-overfit, sweep, seeds 1 2 3, budget 20


def synthetic_corpus() -> dict:
    tasks = [{"name": f"abc{100 + 10 * bi + i}_a/{1000 + 10 * bi + i}", "stratum": b}
             for bi, b in enumerate(BANDS) for i in range(2)]
    return {"frozen": True, "n_selected": len(tasks), "tasks": tasks}


def row(task: str, mode: str, seed: int, round_index: int, accept: bool, **kw) -> dict:
    """A RoundRecord with every field _cell_key reads, and the ones _completed_cells reads."""
    params = dict(P.CELL_DEFAULTS)
    params.update(kw)
    episode_id = hashlib.sha1(f"{task}|{mode}|{seed}|{sorted(params.items())}".encode()).hexdigest()[:16]
    return {
        "episode_id": episode_id, "task": task, "mode": mode, "seed": seed,
        "round_index": round_index, "accept": accept,
        "guard_on": params["guard_on"], "steer_on": params["steer_on"],
        "max_examples": params["max_examples"], "typing_noise_c": params["typing_noise_c"],
        "force_full_budget": params["force_full_budget"], "model": MODEL, "granularity": "fine",
        "audit_guarded": params["audit_guarded"], "reasoning_effort": params["reasoning_effort"],
        "typing_random": params["typing_random"], "free_guarded_rounds": params["free_guarded_rounds"],
        "history": params["history"], "selftest": params["selftest"],
        "oracle_skip_p": params["oracle_skip_p"],
    }


class SyntheticRun:
    def __init__(self):
        self.slug = f"_test_grid_{os.getpid()}"
        self.path = ROOT / "data" / self.slug
        self.corpus = synthetic_corpus()
        self.universe = U.build(self.corpus["tasks"])["sweep_programs"]     # all ten: <= 6 per band

    def __enter__(self):
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "tasks.json").write_text(json.dumps(self.corpus))
        params = P.cell_params(P.PRESETS[PRESET]["extra"])
        rows = []
        self.truncated = (self.universe[3], "typed", 2)        # 5 rounds, no accept
        self.absent = (self.universe[7], "typed", 1)           # never ran
        for task in self.universe:
            for seed in (1, 2, 3):
                cell = (task, "typed", seed)
                if cell == self.absent:
                    continue
                if cell == self.truncated:
                    rows += [row(task, "typed", seed, r, False, **params) for r in range(1, 6)]
                elif seed == 1:      # accepted at round 3 - complete because the arm may stop early
                    rows += [row(task, "typed", seed, r, r == 3, **params) for r in range(1, 4)]
                else:                # ran to the budget
                    rows += [row(task, "typed", seed, r, False, **params) for r in range(1, 21)]
        # noise from another preset (E1: no_memory, force_full_budget), full budget
        e1 = P.cell_params(P.PRESETS["E1"]["extra"])
        rows += [row(self.universe[0], "no_memory", 1, r, r == 2, **e1) for r in range(1, 21)]
        (self.path / "episodes.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)
        shutil.rmtree(ROOT / "logs" / self.slug, ignore_errors=True)


def run_status(slug: str, *args: str) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, "scripts/grid_status.py", "--run-dir", slug, "--json", *args],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode in (0, 3), r.stderr
    return r.returncode, json.loads(r.stdout)


def test_missing_cells_are_reported_and_nothing_else():
    with SyntheticRun() as run:
        rc, d = run_status(run.slug, "--preset", PRESET, "--model", MODEL)
        assert rc == 3
        p = d["presets"][0]
        assert p["universe"] == "sweep" and p["n_universe"] == 10
        assert p["expected"] == 30 and p["complete"] == 28 and p["missing"] == 2
        assert sorted(tuple(c) for c in p["missing_cells"]) == sorted([run.truncated, run.absent])
        assert p["missing_declared"] == 0 and p["missing_undeclared"] == 2
        # positions are 1-based indices into the universe, for the shard ranges
        want = sorted({run.universe.index(run.truncated[0]) + 1, run.universe.index(run.absent[0]) + 1})
        assert p["missing_positions"] == want
        assert p["ranges_todo"] == [[1, 10]]
        # E1 on the same log: one cell of 50 present (the noise), 49 missing
        rc, d = run_status(run.slug, "--preset", "E1", "--model", MODEL)
        assert rc == 3 and d["presets"][0]["complete"] == 1 and d["presets"][0]["expected"] == 50
        # a different model sees none of it
        rc, d = run_status(run.slug, "--preset", PRESET, "--model", "gpt-4o-mini")
        assert d["presets"][0]["complete"] == 0


def test_completed_cells_agrees_with_run_eval():
    with SyntheticRun() as run:
        path = run.path / "episodes.jsonl"
        rows = run_eval.load_rounds(path)
        for budget in (5, 20, 3):
            for ffb in (False, True):
                assert GS.completed_cells(rows, budget, ffb) == run_eval._completed_cells(path, budget, ffb), (budget, ffb)


def test_declared_gaps_are_hits_unless_strict():
    with SyntheticRun() as run:
        (run.path / GS.COVERAGE_FILE).write_text(json.dumps({
            "schema": 1, "presets": {PRESET: {"missing": [list(run.truncated), list(run.absent)]}}}))
        rc, d = run_status(run.slug, "--preset", PRESET, "--model", MODEL)
        assert rc == 0
        p = d["presets"][0]
        assert p["missing"] == 2 and p["missing_declared"] == 2 and p["missing_undeclared"] == 0
        assert p["status"] == "hit (declared)" and p["ranges_todo"] == [[1, 10]]
        rc, d = run_status(run.slug, "--preset", PRESET, "--model", MODEL, "--strict")
        assert rc == 3 and d["presets"][0]["status"] == "RUN"
        # a declaration that names only one of the two leaves the other undeclared
        (run.path / GS.COVERAGE_FILE).write_text(json.dumps({
            "schema": 1, "presets": {PRESET: {"missing": [list(run.absent)]}}}))
        rc, d = run_status(run.slug, "--preset", PRESET, "--model", MODEL)
        assert rc == 3 and d["presets"][0]["missing_undeclared"] == 1
        assert [tuple(c) for c in d["presets"][0]["missing_undeclared_cells"]] == [run.truncated]


def test_generated_universes_are_byte_identical_to_eval_shard():
    with SyntheticRun() as run:
        names = ("eval_order.txt", "sweep_programs.txt", "trial_programs.txt")
        assert not any((run.path / n).exists() for n in names)
        run_status(run.slug, "--preset", PRESET, "--model", MODEL)
        mine = {n: (run.path / n).read_bytes() for n in names}
        for n in names:
            (run.path / n).unlink()
        env = dict(os.environ, RUN_DIR=run.slug)
        r = subprocess.run(["bash", "scripts/eval_shard.sh", "--exp", PRESET, "--dry-run"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        theirs = {n: (run.path / n).read_bytes() for n in names}
        assert mine == theirs
        # and the status tool never rewrites a list that is already there
        before = (run.path / "eval_order.txt").stat().st_mtime_ns
        run_status(run.slug, "--preset", PRESET, "--model", MODEL)
        assert (run.path / "eval_order.txt").stat().st_mtime_ns == before
        # a list cut from another corpus is refused, exactly as eval_shard.sh refuses it
        text = (run.path / "eval_order.txt").read_text().replace("# corpus_sha256: ", "# corpus_sha256: 0000")
        (run.path / "eval_order.txt").write_text(text)
        r = subprocess.run([sys.executable, "scripts/grid_status.py", "--run-dir", run.slug,
                            "--preset", PRESET, "--json"], cwd=ROOT, capture_output=True, text=True)
        assert r.returncode == 1 and "was cut from a different" in r.stderr


def test_preset_table_matches_eval_shard_plan():
    """The cells this tool expects are the cells the shard script would walk."""
    with SyntheticRun() as run:
        rc, d = run_status(run.slug, "--all-presets", "local", "--model", MODEL)
        env = dict(os.environ, RUN_DIR=run.slug)
        for p in d["presets"]:
            r = subprocess.run(["bash", "scripts/eval_shard.sh", "--exp", p["preset"], "--dry-run"],
                               cwd=ROOT, env=env, capture_output=True, text=True)
            assert r.returncode == 0, (p["preset"], r.stderr)
            import re
            cells = int(re.search(r"^\s+(\d+) cells", r.stdout, re.M).group(1))
            assert cells == p["expected"], p["preset"]


def test_live_universe_is_derived_when_absent():
    with SyntheticRun() as run:
        rc, d = run_status(run.slug, "--preset", "E9-freeguard", "--model", MODEL)
        p = d["presets"][0]
        assert (run.path / "live_programs.txt").exists()
        assert p["n_universe"] == 8            # ten minus the two dead tasks
        assert p["expected"] == 8 * 3 * 3 and p["complete"] == 0


def main() -> int:
    for fn in (test_missing_cells_are_reported_and_nothing_else, test_completed_cells_agrees_with_run_eval,
               test_declared_gaps_are_hits_unless_strict, test_generated_universes_are_byte_identical_to_eval_shard,
               test_preset_table_matches_eval_shard_plan, test_live_universe_is_derived_when_absent):
        fn()
        print(f"ok   {fn.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
