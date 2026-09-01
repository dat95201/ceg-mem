"""Manual double-labeling: two humans independently tag failure type on the
same 150-attempt sample, so inter-annotator agreement (and agreement with the
automatic theta()) can be reported alongside the automated coherence proxy in
scripts/measure_coherence.py.

Usage:
    python3 scripts/label_tool.py --annotator alice          # label your share
    python3 scripts/label_tool.py --annotator bob            # second annotator, same sample
    python3 scripts/label_tool.py --compare alice bob         # agreement report

The sample (150 refuted attempts from data/episodes.jsonl, fixed seed) is the
same for every annotator name, so two people running this independently label
the same items. Labels are free-typed but structured as (location, property)
- the same two-part signature theta() uses - so agreement is a plain
normalized string match, not free-text NLP; each row also stores theta()'s
own answer for a three-way (annotator A, annotator B, automatic) comparison.
Resumable: already-labeled ids are skipped, so a session can be split up.
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.adapter import load
from src.metrics import DEFAULT_METRICS_LOG, load_rounds

from src.paths import DATA_DIR, announce  # noqa: E402
SAMPLE_SIZE = 150
SAMPLE_SEED = 20260717


def _attempt_id(row: dict) -> str:
    return f"{row['episode_id']}:{row['round_index']}"


def _sample_rows(episodes_path: pathlib.Path) -> list[dict]:
    refuted = [
        r for r in load_rounds(episodes_path)
        # an inconclusive round (src.oracle) has no counterexample to show and no
        # automatic type to compare a human label against - nothing to label
        if not r["accept"] and not r.get("guarded", False) and not r.get("oracle_error")
    ]
    if not refuted:
        raise SystemExit(f"no refuted rounds in {episodes_path} - run scripts/run_eval.py first")
    refuted.sort(key=_attempt_id)  # deterministic order before sampling
    rng = random.Random(SAMPLE_SEED)
    n = min(SAMPLE_SIZE, len(refuted))
    return rng.sample(refuted, n)


def _labels_path(annotator: str) -> pathlib.Path:
    return DATA_DIR / f"labels_{annotator}.jsonl"


def _load_labels(annotator: str) -> dict[str, dict]:
    path = _labels_path(annotator)
    if not path.exists():
        return {}
    return {row["id"]: row for row in (json.loads(l) for l in path.read_text().splitlines() if l.strip())}


def _auto_type(row: dict, granularity: str) -> tuple[str | None, str | None]:
    key = row.get(f"{granularity}_type")
    if not key:
        return None, None
    parts = key.split("::", 3)  # "fine::task::location::property"
    if len(parts) != 4:
        return None, None
    return parts[2], parts[3]


def _show_attempt(row: dict) -> None:
    buggy = load(row["task"]).buggy_source
    diff = difflib.unified_diff(
        buggy.splitlines(keepends=True), row["patch"].splitlines(keepends=True),
        fromfile="buggy", tofile="patch",
    )
    print("=" * 72)
    print(f"task: {row['task']}   episode: {row['episode_id']}   round: {row['round_index']}")
    print("-" * 72)
    print("".join(diff) or "(no diff - unexpected for a refuted attempt)")
    print("-" * 72)
    print(f"counterexample args: {row['counterexample_args']}")
    print(f"reason: {row['reason']}")
    auto_loc, auto_prop = _auto_type(row, "fine")
    print(f"automatic theta() (fine): location={auto_loc!r} property={auto_prop!r}")
    print("=" * 72)


def label_session(annotator: str, episodes_path: pathlib.Path) -> None:
    sample = _sample_rows(episodes_path)
    done = _load_labels(annotator)
    path = _labels_path(annotator)
    remaining = [r for r in sample if _attempt_id(r) not in done]

    print(f"{len(done)}/{len(sample)} already labeled by {annotator!r}; {len(remaining)} remaining.")
    print("For each attempt: type a location (e.g. 'L12', 'L5-L9', 'wholesale', 'whole_program')")
    print("then a property (e.g. 'IndexError', 'Timeout', 'WrongValue', or free text).")
    print("Type 'quit' at either prompt to stop and save progress.\n")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for row in remaining:
        _show_attempt(row)
        location = input("your location label> ").strip()
        if location.lower() == "quit":
            break
        prop = input("your property label> ").strip()
        if prop.lower() == "quit":
            break
        auto_coarse_loc, auto_coarse_prop = _auto_type(row, "coarse")
        auto_fine_loc, auto_fine_prop = _auto_type(row, "fine")
        record = {
            "id": _attempt_id(row), "annotator": annotator, "task": row["task"],
            "episode_id": row["episode_id"], "round_index": row["round_index"],
            "label_location": location, "label_property": prop,
            "auto_coarse_location": auto_coarse_loc, "auto_coarse_property": auto_coarse_prop,
            "auto_fine_location": auto_fine_loc, "auto_fine_property": auto_fine_prop,
        }
        with path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"saved. ({len(done) + 1}/{len(sample)} total)\n")
        done[record["id"]] = record

    print(f"session done: {len(done)}/{len(sample)} labeled by {annotator!r} -> {path}")


def _normalize(s: str | None) -> str:
    return " ".join((s or "").strip().lower().split())


def _agreement(a: dict[str, dict], b: dict[str, dict], key_loc: str, key_prop: str) -> tuple[float, int]:
    shared = sorted(set(a) & set(b))
    if not shared:
        return float("nan"), 0
    matches = sum(
        _normalize(a[i][key_loc]) == _normalize(b[i][key_loc])
        and _normalize(a[i][key_prop]) == _normalize(b[i][key_prop])
        for i in shared
    )
    return matches / len(shared), len(shared)


def compare(annotator_a: str, annotator_b: str) -> None:
    a, b = _load_labels(annotator_a), _load_labels(annotator_b)
    if not a or not b:
        raise SystemExit(f"need labels from both annotators first (found {len(a)} for {annotator_a}, {len(b)} for {annotator_b})")

    rate_ab, n_ab = _agreement(a, b, "label_location", "label_property")
    print(f"{annotator_a} vs {annotator_b}: agree on {rate_ab:.3f} of {n_ab} shared items")

    for name, labels in ((annotator_a, a), (annotator_b, b)):
        rate_auto, n_auto = _agreement(
            labels, {k: {"label_location": v["auto_fine_location"], "label_property": v["auto_fine_property"]}
                     for k, v in labels.items()},
            "label_location", "label_property",
        )
        print(f"{name} vs automatic theta() (fine): agree on {rate_auto:.3f} of {n_auto} items")

    report = {
        "annotators": [annotator_a, annotator_b],
        "human_agreement_rate": rate_ab, "human_agreement_n": n_ab,
    }
    out_path = DATA_DIR / "label_agreement.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out_path}")


def main() -> None:
    announce('label_tool')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annotator", help="label the shared 150-item sample as this annotator")
    parser.add_argument("--compare", nargs=2, metavar=("ANNOTATOR_A", "ANNOTATOR_B"))
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    args = parser.parse_args()

    if args.compare:
        compare(*args.compare)
    elif args.annotator:
        label_session(args.annotator, args.episodes_path)
    else:
        parser.error("pass --annotator NAME to label, or --compare A B to report agreement")


if __name__ == "__main__":
    main()
