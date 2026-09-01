"""No screen: the corpus is the previous one minus what the gate took.

The requirement this path exists to meet is one sentence - the corpus must be a
SUBSET of the previously evaluated one - and it is the whole reason the run is
affordable: a task that survives keeps its paid episodes, and a task that does
not was never going to be measurable twice anyway.

What is asserted here is that subset property and the three things that make it
mean something:

  * nothing is ever added. Quotas backfill an under-full band from the pool, and
    a backfilled task has no episodes, so the inherit path applies no quota.
  * the frozen screen fields ride along. stratum, screen_pi_hat,
    screen_successes and screen_calls come from the pin, which is what lets the
    screen leave the pipeline: eval_shard.sh still has a pre-treatment stratum
    to interleave shards by.
  * the gate's own facts are refreshed. Mutant scores and reference cost come
    from the CURRENT pool entry, not the pin, or a re-gate would be cosmetic.

No sandbox, no model: the pool, the pin and the gate report are dicts on disk.
"""
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANDS = ("dead", "hard", "medium", "easy", "too_easy")


def fixture(tmp, *, drop=(), n=20):
    """A pin of `n` tasks, and a pool with `drop` removed by the slow filter."""
    tasks = []
    for i in range(n):
        tasks.append({
            "name": f"t{i:03d}/p", "task_id": f"t{i:03d}", "program_id": "p",
            "stratum": BANDS[i % len(BANDS)],
            "screen_pi_hat": 0.01 + 0.07 * (i % len(BANDS)),
            "screen_successes": i, "screen_calls": 40,
            "mutants_caught": 2, "mutants_scored": 3, "difficulty": 1700,
        })
    pin = {"frozen": True, "n_selected": len(tasks), "tasks": tasks}
    (tmp / "tasks.json").write_text(json.dumps(pin))

    kept = [t for t in tasks if t["name"] not in drop]
    # the gate's entries carry FRESH mutant scores, deliberately different
    pool = {"frozen": True, "tasks": [
        {"name": t["name"], "task_id": t["task_id"], "program_id": "p",
         "mutants_caught": 3, "mutants_scored": 3, "difficulty": 1700,
         "reference_sec_max": 0.2}
        for t in kept]}
    (tmp / "pool.json").write_text(json.dumps(pool))

    rep = {"faults": {d: {"slow": True, "usable": False, "reference_timeout": 10.0}
                      for d in drop}}
    (tmp / "oracle_validation.json").write_text(json.dumps(rep))
    return pin, pool


def run(tmp, *extra, expect_ok=True):
    r = subprocess.run(
        [sys.executable, "scripts/select_corpus.py",
         "--pool", str(tmp / "pool.json"), "--pin", str(tmp / "tasks.json"),
         "--out", str(tmp / "out.json"), "--audit-out", str(tmp / "audit.json"),
         "--min-per-band", "0", "--force", *extra],
        cwd=ROOT, capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, r.stdout + r.stderr
        return json.loads((tmp / "out.json").read_text()), r.stdout
    assert r.returncode != 0, "expected a refusal"
    return None, r.stdout + r.stderr


def test_the_corpus_is_a_subset_of_the_pin():
    """THE requirement. k <= n, every member drawn from the pin, nothing new."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        pin, _ = fixture(tmp, drop={"t003/p", "t007/p", "t011/p"})
        out, _ = run(tmp)
        old = {t["name"] for t in pin["tasks"]}
        new = {t["name"] for t in out["tasks"]}
        assert len(new) == 17 <= len(old)
        assert new <= old, f"tasks entered that were never in the pin: {new - old}"
        assert old - new == {"t003/p", "t007/p", "t011/p"}


def test_nothing_is_ever_backfilled():
    """A quota would top an under-full band back up from the pool. That task has
    no paid episodes, which defeats the whole point."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        # the pool holds 30 tasks; the pin only claims 20 of them
        fixture(tmp, n=20)
        pool = json.loads((tmp / "pool.json").read_text())
        pool["tasks"] += [{"name": f"x{i:03d}/p", "task_id": f"x{i:03d}",
                           "program_id": "p", "mutants_caught": 3,
                           "mutants_scored": 3, "difficulty": 1700} for i in range(10)]
        (tmp / "pool.json").write_text(json.dumps(pool))
        out, _ = run(tmp)
        assert len(out["tasks"]) == 20, "the pool's surplus leaked into the corpus"
        assert not any(t["name"].startswith("x") for t in out["tasks"])


def test_the_frozen_screen_fields_are_inherited():
    """This is what lets the screen leave the pipeline: eval_shard.sh still has
    a pre-treatment stratum to interleave by."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        pin, _ = fixture(tmp)
        out, _ = run(tmp)
        by_pin = {t["name"]: t for t in pin["tasks"]}
        for t in out["tasks"]:
            src = by_pin[t["name"]]
            for f in ("stratum", "screen_pi_hat", "screen_successes", "screen_calls"):
                assert t[f] == src[f], f"{t['name']}.{f}: {t[f]} != {src[f]}"


def test_the_gates_own_facts_are_refreshed_not_inherited():
    """A re-gate that carried the old mutant scores forward would be cosmetic."""
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fixture(tmp)
        out, _ = run(tmp)
        assert all(t["mutants_caught"] == 3 for t in out["tasks"]), \
            "the pin's stale mutant score survived the re-gate"


def test_a_slow_drop_is_named_and_does_not_stop_the_run():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fixture(tmp, drop={"t003/p"})
        out, log = run(tmp)
        assert "slow-task filter" in log, log
        lost = out["selection"]["pin"]["lost_settled"]
        assert [x["name"] for x in lost] == ["t003/p"]
        assert out["selection"]["pin"]["lost_recoverable"] == []


def test_it_refuses_to_inherit_with_no_pin():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fixture(tmp)
        _, err = run(tmp, "--no-pin", expect_ok=False)
        assert "inherited from a previous one" in err, err


def test_the_mode_is_on_the_record():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fixture(tmp)
        out, _ = run(tmp)
        assert out["selection"]["mode"] == "inherited"
        assert out["selection"]["screens"] == []


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  ok  {name}"); n += 1
    print(f"{n} passed")
