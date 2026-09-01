"""One env var, two readers, and they must not disagree.

RUN_DIR is resolved twice: by src/paths.py for every python stage, and by
scripts/run_dir_paths.sh for every bash entry point. A repo that resolves the
same run to two directories writes half its output to each and never errors,
so the agreement is asserted here rather than assumed.

Also pins the two properties the default depends on:

  * RUN_DIR unset resolves to plain `data/` and `logs/` - byte-for-byte the
    paths this repo used before src/paths.py existed. That is what "nothing
    that already works breaks" means, and it is one assertion, not a hope.
  * A leading slash cannot escape the repo. `ROOT / "/etc"` is `/etc` under
    pathlib's join semantics, so RUN_DIR=/etc without the strip would point a
    run's artifacts at the filesystem root.

No LLM, no sandbox, no network: subprocesses only, one per case.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# input -> (expected data dir, expected logs dir), relative to ROOT
CASES = {
    None:                    ("data", "logs"),
    "":                      ("data", "logs"),
    "   ":                   ("data", "logs"),
    "official-2026-09-01":   ("data/official-2026-09-01", "logs/official-2026-09-01"),
    "/official-2026-09-01/": ("data/official-2026-09-01", "logs/official-2026-09-01"),
    "  spaced  ":            ("data/spaced", "logs/spaced"),
    "a/b":                   ("data/a/b", "logs/a/b"),
    "/etc":                  ("data/etc", "logs/etc"),
}


def _env(run_dir):
    env = dict(os.environ)
    env.pop("RUN_DIR", None)
    if run_dir is not None:
        env["RUN_DIR"] = run_dir
    return env


def _python(run_dir):
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from src.paths import DATA_DIR, LOGS_DIR;"
         " print(DATA_DIR); print(LOGS_DIR)"],
        cwd=ROOT, env=_env(run_dir), capture_output=True, text=True, check=True)
    return tuple(out.stdout.split())


def _bash(run_dir):
    out = subprocess.run(
        ["bash", "-c",
         '. scripts/run_dir_paths.sh; printf "%s\\n%s\\n" "$RUN_DATA" "$RUN_LOGS"'],
        cwd=ROOT, env=_env(run_dir), capture_output=True, text=True, check=True)
    return tuple(str(ROOT / p) for p in out.stdout.split())


def test_python_resolves_every_case():
    for run_dir, (data, logs) in CASES.items():
        got = _python(run_dir)
        want = (str(ROOT / data), str(ROOT / logs))
        assert got == want, f"RUN_DIR={run_dir!r}: python gave {got}, expected {want}"


def test_bash_agrees_with_python():
    for run_dir in CASES:
        py, sh = _python(run_dir), _bash(run_dir)
        assert py == sh, f"RUN_DIR={run_dir!r}: python {py} vs bash {sh}"


def test_unset_is_exactly_the_old_layout():
    """The whole no-migration promise, in two strings."""
    data, logs = _python(None)
    assert data == str(ROOT / "data") and logs == str(ROOT / "logs")


def test_a_leading_slash_cannot_escape_the_repo():
    for probe in ("/etc", "//tmp", "/etc/"):
        data, logs = _python(probe)
        assert data.startswith(str(ROOT / "data")), f"RUN_DIR={probe!r} escaped to {data}"
        assert logs.startswith(str(ROOT / "logs")), f"RUN_DIR={probe!r} escaped to {logs}"


def test_pool_sits_under_the_run_not_beside_it():
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from src.paths import POOL_DIR; print(POOL_DIR)"],
        cwd=ROOT, env=_env("r1"), capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(ROOT / "data" / "r1" / "pool"), \
        f"got {out.stdout.strip()}; data/pool/<run> would share one pool across runs"


def test_metrics_default_follows_run_dir():
    """DEFAULT_METRICS_LOG is the default of --episodes-path in ten scripts. It
    used to be CWD-relative, which is how a run splits itself across two
    directories without erroring."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from src.metrics import DEFAULT_METRICS_LOG;"
         " print(DEFAULT_METRICS_LOG)"],
        cwd=ROOT, env=_env("r1"), capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(ROOT / "data" / "r1" / "episodes.jsonl")


def test_the_cache_does_not_move():
    """Scoping the response cache per run would cold-start every sweep - the
    cache is the reason a re-run is free."""
    env = _env("r1")
    env["CALLS_LOG"] = ""          # what .env.example now ships: follow RUN_DIR
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from src.llm import CACHE, LOG;"
         " print(CACHE); print(LOG)"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    cache, log = out.stdout.split()
    assert cache == "cache", f"the cache moved to {cache}"
    assert log == str(ROOT / "data" / "r1" / "calls.jsonl"), \
        f"the call ledger IS run output and must follow RUN_DIR; got {log}"


def test_an_explicit_calls_log_still_wins_but_says_so():
    """Every shard sets CALLS_LOG - that is how six of them append to six
    ledgers instead of racing on one - so an explicit value must be honoured.
    A STALE one (a .env still pinning data/calls.jsonl while RUN_DIR is set) is
    the one remaining way a run splits across two directories, so it warns."""
    env = _env("r1"); env["CALLS_LOG"] = "data/r1/calls_eval_x.jsonl"
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from src.llm import LOG; print(LOG)"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "data/r1/calls_eval_x.jsonl", "a shard's ledger was overridden"
    assert "WARNING" not in out.stderr, "a ledger inside the run dir must not warn"

    env["CALLS_LOG"] = "data/calls.jsonl"      # the stale .env
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); from src.llm import LOG; print(LOG)"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "data/calls.jsonl", "an explicit override must be honoured"
    assert "WARNING" in out.stderr and "CALLS_LOG" in out.stderr, \
        f"a ledger outside the run dir must be flagged; stderr was {out.stderr!r}"


def test_every_stage_announces_where_it_writes():
    """Spec A: print the real path at the head of each stage. On stderr, so it
    survives `> out.json`."""
    # Four stages spanning the pipeline, all stdlib-only so this runs on a box
    # without scipy/numpy/matplotlib (the device VM is one).
    for stem in ("select_corpus", "build_strata", "summarize", "consolidate_evals"):
        r = subprocess.run([sys.executable, f"scripts/{stem}.py", "--help"],
                           cwd=ROOT, env=_env("r1"), capture_output=True, text=True)
        assert f"[{stem}] run=r1  ->  data/r1/" in r.stderr, f"{stem}: {r.stderr[:300]}"


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  ok  {name}"); n += 1
    print(f"{n} passed")
