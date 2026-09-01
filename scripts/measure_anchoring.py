"""Anchoring rate: how often typed steering rules out the class that held the fix.

The proposal's SS5 metric list ends with "and, for typed memory, the anchoring
rate (episodes that fail because mis-typing wrongly eliminated the correct
class)", and RQ2 asks it as a question: "what new failure mode (anchoring) does
typed steering introduce?" E5 is the experiment that answers RQ2, so without
this number E5 answers half of it.

**Steering, not guarding.** In this implementation anchoring cannot arise at the
guard. `TypedMemory.guard` looks up the type-matched bucket and then re-runs its
stored counterexample through `memory._still_refutes`, blocking only when that
counterexample *still* refutes the new candidate - so a blocked candidate
provably fails, at any value of c. Mistyping changes which bucket is consulted,
and therefore what the guard costs (Proposition 4.5), but it cannot make the
guard reject a correct patch.

What mistyping can do is put the wrong entry in `proposer._exclusion_block`,
which tells the proposer in plain words not to produce a patch at some edit
location. If that location is where the fix has to go, the proposer is being
steered away from the answer. That is the failure mode, and it is a property of
generation, not of verification.

**No model calls.** Everything is read from data/episodes.jsonl plus the
reference source already on disk.

Definitions, per typed episode with steering on:

  target        edit_location(buggy, reference) - the location the author's own
                accepted patch occupies, in the same key space theta() uses.
                Validated against ConDefects' own annotated faultLocation.txt
                on the 120-fault corpus: the target span contains an annotated
                fault line on 112 of 118 comparable tasks (94.9%), and 117 of
                118 (99.2%) within one line - the six misses are the diff
                naming the line above the edit, not a different location. Two
                tasks are excluded because their reference is a wholesale
                rewrite, which theta() degrades to a tag rather than a span.
  excluded      target entered the eliminated set at some round, i.e. it was
                printed into the exclusion block from that round onward.
  anchored      excluded AND the episode never accepted. This is the proposal's
                metric: episodes that *fail because* the correct class was
                ruled out. `excluded` without failure is reported alongside,
                because a proposer that ignores the exclusion and repairs
                anyway is evidence about the steering channel's strength.

and the round that first excluded the target is attributed:

  by_noise      the stored location differs from the true one - Def. 3.1's
                mistyping, the thing c parameterises. Should vanish at c = 1.
  by_conflation the stored location *is* the true one: a genuinely refuted
                patch really does edit where the fix goes, and theta cannot
                separate them because its location granularity is coarser than
                the distinction. This survives at c = 1 and is a property of
                the real type function, not of the noise model - which is why
                it is worth reporting separately rather than folded in.

Writes data/anchoring.json.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import TASKS, load
from src.metrics import DEFAULT_METRICS_LOG, group_by_episode, load_rounds
from src.typer import WHOLE_PROGRAM, edit_location

from src.paths import DATA_DIR, announce  # noqa: E402


def _location_of(type_key: str | None) -> str | None:
    """FailureType.key is "{granularity}::{task}::{location}::{property}"."""
    if not type_key:
        return None
    parts = type_key.split("::")
    return parts[2] if len(parts) >= 4 else None


def _strata() -> dict[str, str]:
    path = DATA_DIR / "strata.json"
    if not path.exists():
        return {}
    return {t["name"]: t["stratum"] for t in json.loads(path.read_text()).get("tasks", [])}


def _target_locations(tasks: set[str], granularity: str) -> dict[str, str]:
    """task -> the location the reference patch occupies.

    Computed once per task, not per episode: it depends only on the fault, and
    the diff is the expensive part.
    """
    out = {}
    for name in sorted(tasks):
        if granularity == "coarse":
            out[name] = WHOLE_PROGRAM
            continue
        program = load(name)
        out[name] = edit_location(program.buggy_source, program.correct_source)
    return out


def audit_episode(rows: list[dict], target: str) -> dict:
    """Walk one episode's rounds and decide whether the target was excluded."""
    rows = sorted(rows, key=lambda r: r["round_index"])
    accepted = any(r["accept"] for r in rows)

    eliminated: set[str] = set()
    first_excluded_round = None
    attribution = None
    for r in rows:
        if r["accept"] or r.get("guarded") or r.get("oracle_error") or r.get("proposal_error"):
            continue
        # Fall back to the true type for episodes logged before stored_type
        # existed; at c = 1.0 the two are identical, so those rows are still
        # readable, and the by_noise/by_conflation split is simply unavailable.
        granularity = r.get("granularity", "fine")
        true_key = r.get("coarse_type") if granularity == "coarse" else r.get("fine_type")
        stored_key = r.get("stored_type") or true_key
        stored_loc = _location_of(stored_key)
        if stored_loc is None:
            continue
        if stored_loc not in eliminated:
            eliminated.add(stored_loc)
            if stored_loc == target and first_excluded_round is None:
                first_excluded_round = r["round_index"]
                if r.get("stored_type") is None:
                    attribution = "unknown"      # pre-stored_type log
                elif _location_of(true_key) != stored_loc:
                    attribution = "by_noise"
                else:
                    attribution = "by_conflation"

    first = rows[0]
    return {
        "episode_id": first["episode_id"], "task": first["task"], "seed": first.get("seed", 0),
        "typing_noise_c": first.get("typing_noise_c", 1.0),
        "granularity": first.get("granularity", "fine"),
        "max_examples": first.get("max_examples", 100),
        "target_location": target,
        "accepted": accepted,
        "excluded": first_excluded_round is not None,
        "first_excluded_round": first_excluded_round,
        "attribution": attribution,
        "anchored": first_excluded_round is not None and not accepted,
    }


def _rate(rows: list[dict], key: str) -> float | None:
    return (sum(1 for r in rows if r[key]) / len(rows)) if rows else None


def main() -> None:
    announce('measure_anchoring')
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "anchoring.json")
    args = parser.parse_args()

    if not args.episodes_path.exists():
        raise SystemExit(f"{args.episodes_path} missing - run E2/E5 first")

    episodes = group_by_episode(load_rounds(args.episodes_path))

    # Typed arm with steering on. guard=on/off does not matter: the exclusion
    # block is steering's channel and it is present whenever steer_on is True,
    # so E3's guard-only ablation (steer off) is correctly excluded here and its
    # steering-only ablation (guard off) is correctly included.
    selected = {
        eid: rows for eid, rows in episodes.items()
        if rows[0]["mode"] == "typed" and rows[0].get("steer_on", True)
    }
    if not selected:
        raise SystemExit("no typed episodes with steering on in "
                         f"{args.episodes_path} - nothing to audit")

    unknown = {rows[0]["task"] for rows in selected.values()} - set(TASKS)
    if unknown:
        raise SystemExit(f"episodes reference {len(unknown)} unknown tasks, e.g. {sorted(unknown)[:3]}")

    granularities = {rows[0].get("granularity", "fine") for rows in selected.values()}
    targets: dict[tuple[str, str], str] = {}
    for gran in sorted(granularities):
        tasks = {rows[0]["task"] for rows in selected.values()
                 if rows[0].get("granularity", "fine") == gran}
        for task, loc in _target_locations(tasks, gran).items():
            targets[(gran, task)] = loc

    audited = [
        audit_episode(rows, targets[(rows[0].get("granularity", "fine"), rows[0]["task"])])
        for rows in selected.values()
    ]

    strata = _strata()
    for a in audited:
        a["stratum"] = strata.get(a["task"])

    by_c: dict[float, list[dict]] = collections.defaultdict(list)
    for a in audited:
        by_c[a["typing_noise_c"]].append(a)

    levels = {}
    for c in sorted(by_c, reverse=True):
        rows = by_c[c]
        per_stratum = {}
        for stratum in sorted({r["stratum"] for r in rows if r["stratum"]}):
            sub = [r for r in rows if r["stratum"] == stratum]
            per_stratum[stratum] = {
                "n_episodes": len(sub),
                "anchoring_rate": _rate(sub, "anchored"),
                "exclusion_rate": _rate(sub, "excluded"),
            }
        attribution = collections.Counter(
            r["attribution"] for r in rows if r["excluded"] and r["attribution"])
        levels[f"{c}"] = {
            "n_episodes": len(rows),
            "anchoring_rate": _rate(rows, "anchored"),
            "exclusion_rate": _rate(rows, "excluded"),
            "attribution": dict(attribution),
            "per_stratum": per_stratum,
        }

    coarse_note = None
    if "coarse" in granularities:
        coarse_note = ("coarse granularity puts every failure at whole_program, so the "
                       "target is excluded by construction after the first refutation - "
                       "the coarse anchoring rate is a property of the granularity, not "
                       "a measurement, and should not be reported as one")

    report = {
        "episodes_path": str(args.episodes_path),
        "n_episodes": len(audited),
        "definition": "anchored = the reference patch's edit location entered the "
                      "exclusion block AND the episode never accepted",
        "guard_note": "the typed guard re-runs its bucket's stored counterexample and "
                      "blocks only when it still refutes, so a blocked candidate provably "
                      "fails; anchoring here is a steering effect, not a guard effect",
        "coarse_note": coarse_note,
        "by_typing_coherence": levels,
        "episodes": sorted(audited, key=lambda a: (a["task"], a["typing_noise_c"], a["seed"])),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"audited {len(audited)} typed episodes with steering on\n")
    print(f"{'c':>6} {'n':>6} {'anchored':>10} {'excluded':>10}   attribution")
    for c, data in levels.items():
        attr = ", ".join(f"{k}={v}" for k, v in sorted(data["attribution"].items())) or "-"
        print(f"{c:>6} {data['n_episodes']:>6} {data['anchoring_rate']:>9.1%} "
              f"{data['exclusion_rate']:>10.1%}   {attr}")
    if coarse_note:
        print(f"\nNOTE: {coarse_note}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
