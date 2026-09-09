#!/usr/bin/env python3
"""Pack the shipped artifacts (data/, logs/, optionally cache/) from raw run directories.

    python3 scripts/build_artifacts.py \\
        --local-run  /path/to/data/official-2026-09-01  --logs-local /path/to/logs/official-2026-09-01 \\
        --cloud-run  /path/to/data/gpto4mini-2026-09-06 --logs-cloud /path/to/logs/gpto4mini-2026-09-06 \\
        [--cache /path/to/cache] --out artifacts/ [--zip]

What goes in, per run directory (RUNBOOK.md "Artifacts"):

  corpus inputs   tasks.json, pool/tasks.json, pool/oracle_validation.json,
                  candidates.json, screening.json
  universe lists  eval_order.txt, sweep_programs.txt, trial_programs.txt,
                  live_programs.txt (+ demo/hardend lists if the run drew them)
  Tier A          episodes.jsonl, overfit_checks.jsonl, calls.jsonl - the MERGED
                  model-call outputs - and eval_shards/*.meta.json, the protocol
                  record of every shard that produced them
  Tier B          the oracle-heavy, model-free artifacts: pool_strength.json,
                  coherence_report.json, verdicts.jsonl, verdicts_cases.json
  logs            logs/<run>/*.log, the shard traces, when --logs-* is given

What stays out: the per-shard episode/call/overfit logs (their content is in the
merged files; consolidate_evals.py is how they got there), the shard program
lists (regenerated), and every Tier C artifact - results_real, theory_fit,
strata, analysis, anchoring, redundancy, patch_quality, failure_taxonomy,
policies - which scripts/reproduce.sh recomputes in seconds and check_consistency
verifies. cache/ (the model-response cache) is copied only with --cache.

Coverage is validated first with scripts/grid_status.py's logic: every preset
in scripts/presets.py's LOCAL_PRESETS must be complete on the local run and
every CLOUD_PRESETS one on the cloud run, cell by cell. A preset that is not
complete stops the build unless it is named with --allow-partial; then its
missing cells are DECLARED in data/<run>/grid_coverage.json, which is what lets
a reproduction from these artifacts rebuild the paper at the coverage the
paper reports instead of trying to extend the grid (see grid_status.py).

Writes artifacts/MANIFEST.json (sha256, bytes, tier and producer stage per
file; the git commit and date) and artifacts/README.md. --zip also writes
<out>.zip beside the directory. scripts/fetch_artifacts.py installs the result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import grid_status as GS   # noqa: E402
import presets as P        # noqa: E402

# (relative path or glob, tier, producer stage, required?)
CORPUS_FILES = [
    ("tasks.json", "corpus", "corpus", True),
    ("pool/tasks.json", "corpus", "gate", True),
    ("pool/oracle_validation.json", "corpus", "gate", True),
    ("candidates.json", "corpus", "candidates", True),
    ("screening.json", "corpus", "corpus", False),
]
UNIVERSE_FILES = [
    ("eval_order.txt", "universe", "grid", True),
    ("sweep_programs.txt", "universe", "grid", True),
    ("trial_programs.txt", "universe", "grid", False),
    ("live_programs.txt", "universe", "grid", False),
    ("demo_programs.txt", "universe", "grid", False),
    ("hardend_programs.txt", "universe", "grid", False),
    ("hardend_universe_meta.json", "universe", "grid", False),
]
TIER_A_FILES = [
    ("episodes.jsonl", "A", "grid", True),
    ("overfit_checks.jsonl", "A", "grid", True),
    ("calls.jsonl", "A", "grid", False),
    ("eval_shards/*.meta.json", "A", "grid", True),
]
TIER_B_LOCAL = [
    ("pool_strength.json", "B", "analyse", True),
    ("coherence_report.json", "B", "analyse", True),
    ("verdicts.jsonl", "B", "policies", True),
    ("verdicts_cases.json", "B", "policies", True),
]
TIER_B_CLOUD = [
    ("coherence_report.json", "B", "analyse", False),
]
# Named so the README can say what was deliberately left out.
TIER_C = ("results_real.json", "theory_fit.json", "strata.json", "analysis.json",
          "anchoring.json", "redundancy.json", "patch_quality.json",
          "failure_taxonomy.json", "policies.json", "policies_cells.json")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_info() -> dict:
    out = {}
    for key, cmd in (("commit", ["git", "rev-parse", "HEAD"]),
                     ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"])):
        try:
            out[key] = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                      check=True).stdout.strip()
        except Exception:      # not a checkout, or no git
            out[key] = None
    try:
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout
        out["dirty"] = bool(dirty.strip())
    except Exception:
        out["dirty"] = None
    return out


def collect(src: pathlib.Path, table: list[tuple[str, str, str, bool]],
            label: str) -> list[tuple[pathlib.Path, str, str, str]]:
    """-> [(source file, relative path, tier, producer)], failing on a missing required file."""
    out, missing = [], []
    for pattern, tier, producer, required in table:
        matches = sorted(src.glob(pattern)) if any(c in pattern for c in "*?[") \
            else ([src / pattern] if (src / pattern).is_file() else [])
        matches = [m for m in matches if m.is_file()]
        if not matches:
            if required:
                missing.append(pattern)
            continue
        for m in matches:
            out.append((m, str(m.relative_to(src)), tier, producer))
    if missing:
        raise SystemExit(f"{label}: required file(s) missing under {src}: {', '.join(missing)}")
    return out


def copy_run(src: pathlib.Path, dest: pathlib.Path, base: pathlib.Path, tables, label: str,
             force: bool) -> list[dict]:
    """Copy one run's shipped files into dest; -> manifest entries (paths relative to base)."""
    entries = []
    for table in tables:
        for path, rel, tier, producer in collect(src, table, label):
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and not force:
                if target.stat().st_size != path.stat().st_size or sha256(target) != sha256(path):
                    raise SystemExit(f"{target} exists and differs from {path}; pass --force "
                                     f"to overwrite or use a fresh --out")
            else:
                shutil.copy2(path, target)
            entries.append({"path": str(target.relative_to(base)), "bytes": target.stat().st_size,
                            "sha256": sha256(target), "tier": tier, "producer": producer})
    return entries


def copy_tree(src: pathlib.Path, dest: pathlib.Path, base: pathlib.Path, tier: str, producer: str,
              force: bool, pattern: str = "**/*") -> list[dict]:
    """Copy every file matching pattern under src into dest; -> manifest entries."""
    entries = []
    for path in sorted(src.glob(pattern)):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or force:
            shutil.copy2(path, target)
        entries.append({"path": str(target.relative_to(base)), "bytes": target.stat().st_size,
                        "sha256": sha256(target), "tier": tier, "producer": producer})
    return entries


def validate_coverage(run_dir: pathlib.Path, presets: tuple[str, ...], model: str,
                      allow_partial: set[str], label: str, *, write_lists: bool) -> dict:
    """grid_status over the run; -> {preset: status dict}. Stops on an undeclared gap."""
    data_dir, tasks_header, slug = GS.resolve_run_dir(str(run_dir))
    episodes = data_dir / "episodes.jsonl"
    rows = GS._run_eval().load_rounds(episodes) if episodes.exists() else []
    statuses = {}
    bad = []
    print(f"\ncoverage: {label} ({data_dir}), model {model}, {len(rows)} merged rounds")
    for name in presets:
        s = GS.preset_status(name, rows, data_dir, tasks_header, slug, model, "fine", None, {},
                             write=write_lists)
        statuses[name] = s
        word = "complete" if not s["missing"] else (
            f"{s['missing']} missing - ALLOWED (declared partial)" if name in allow_partial
            else f"{s['missing']} missing")
        print(f"  {name:20s} {s['universe']:7s} {s['complete']:4d}/{s['expected']:<4d} {word}")
        if s["missing"] and name not in allow_partial:
            bad.append(name)
    if bad:
        raise SystemExit(
            f"\n{label}: preset(s) {', '.join(bad)} are not complete on {data_dir}. Either finish "
            f"them (scripts/eval_shard.sh + consolidate_evals.py) or, if the paper reports the grid "
            f"at this coverage, name them with --allow-partial so the gap is declared in "
            f"grid_coverage.json rather than shipped silently.")
    return statuses


def coverage_declaration(statuses: dict, run_name: str, model: str, allow_partial: set[str]) -> dict | None:
    partial = {n: s for n, s in statuses.items() if s["missing"] and n in allow_partial}
    if not partial:
        return None
    return {
        "schema": 1,
        "run_dir": run_name, "model": model,
        "declared_by": "scripts/build_artifacts.py",
        "declared_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": ("Cells listed here were missing from the merged log when the paper's numbers "
                 "were extracted; the paper reports these presets at this coverage. "
                 "scripts/grid_status.py treats them as declared (not counted) unless --strict."),
        "presets": {
            n: {"expected": s["expected"], "complete": s["complete"], "missing_count": s["missing"],
                "universe": s["universe"], "modes": s["modes"], "seeds": s["seeds"],
                "missing": [list(c) for c in s["missing_cells"]]}
            for n, s in partial.items()
        },
    }


def write_readme(out: pathlib.Path, manifest: dict) -> None:
    runs = manifest["runs"]
    lines = [
        "# CEGMem artifacts",
        "",
        f"Built {manifest['created']} from commit `{manifest['git']['commit']}` "
        f"(branch `{manifest['git']['branch']}`) by `scripts/build_artifacts.py`.",
        "",
        "Install into a checkout of the repository with",
        "",
        "    python3 scripts/fetch_artifacts.py --source <this directory or the .zip>",
        "",
        "or let `scripts/reproduce.sh` do it: `ARTIFACTS=<path or URL> bash scripts/reproduce.sh`.",
        "Existing files are never overwritten (ARTIFACTS_FORCE=1 / --force overrides).",
        "",
        "## Layout",
        "",
        "```",
        f"{'MANIFEST.json':32s} sha256, bytes, tier and producer stage of every file",
    ]
    for key in ("local", "cloud"):
        if key in runs:
            r = runs[key]
            lines.append(f"{'data/' + r['run_dir'] + '/':32s} {key} proposer run ({r['model']}): "
                         f"corpus inputs, universe lists, Tier A, Tier B")
            if r.get("logs"):
                lines.append(f"{'logs/' + r['run_dir'] + '/':32s} its shard traces")
    if manifest.get("cache_files"):
        lines.append(f"{'cache/':32s} model-response cache (src.llm memoisation)")
    lines += [
        "```",
        "",
        "## Tiers",
        "",
        "* **corpus** - `tasks.json` (the frozen corpus), `pool/` (the oracle-gated candidate pool),",
        "  `candidates.json`, `screening.json`. The second proposer inherits the local run's corpus.",
        "* **universe** - the frozen program lists the shard indices range over.",
        "* **A** - the merged model-call outputs: `episodes.jsonl`, `overfit_checks.jsonl`,",
        "  `calls.jsonl`, plus `eval_shards/*.meta.json` (the protocol record of every shard).",
        "  Per-shard logs are not shipped; their content is in the merged files.",
        "* **B** - oracle-heavy, model-free: `pool_strength.json`, `coherence_report.json`,",
        "  `verdicts.jsonl`, `verdicts_cases.json`. Hours of sandbox time; `RECOMPUTE_HEAVY=1` redoes them.",
        "* **C** (not shipped) - " + ", ".join(TIER_C) + ": recomputed in seconds by",
        "  `scripts/reproduce.sh` stage `analyse`/`policies` and verified by `check_consistency.py`.",
        "",
    ]
    if manifest.get("coverage"):
        lines += ["## Declared gaps", ""]
        for key, cov in manifest["coverage"].items():
            for name, s in cov["presets"].items():
                lines.append(f"* `{runs[key]['run_dir']}` / `{name}`: {s['missing_count']} of "
                             f"{s['expected']} cells missing from the merged log (declared in "
                             f"`data/{runs[key]['run_dir']}/grid_coverage.json`; the paper reports "
                             f"the preset at this coverage).")
        lines.append("")
    lines += [
        "## Coverage at build time",
        "",
        "| run | preset | universe | complete / expected |",
        "|---|---|---|---|",
    ]
    for key, r in runs.items():
        for name, s in r["presets"].items():
            lines.append(f"| {r['run_dir']} | {name} | {s['universe']} | {s['complete']} / {s['expected']} |")
    lines.append("")
    (out / "README.md").write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local-run", type=pathlib.Path, required=True, help="raw local-proposer run directory")
    ap.add_argument("--cloud-run", type=pathlib.Path, default=None, help="raw cloud-proposer run directory")
    ap.add_argument("--logs-local", type=pathlib.Path, default=None)
    ap.add_argument("--logs-cloud", type=pathlib.Path, default=None)
    ap.add_argument("--cache", type=pathlib.Path, default=None, help="model-response cache to include")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "artifacts")
    ap.add_argument("--local-run-name", default=None, help="data/<name>/ in the artifacts (default: basename)")
    ap.add_argument("--cloud-run-name", default=None)
    ap.add_argument("--local-model", default=P.LOCAL_MODEL)
    ap.add_argument("--cloud-model", default="gpt-4o-mini")
    ap.add_argument("--allow-partial", nargs="*", default=[], metavar="PRESET",
                    help="presets allowed to be incomplete; their gaps are declared in grid_coverage.json")
    ap.add_argument("--zip", action="store_true", help="also write <out>.zip")
    ap.add_argument("--force", action="store_true", help="overwrite files already in --out")
    ap.add_argument("--write-lists", action="store_true",
                    help="allow generating an absent universe list INSIDE the raw run directory "
                         "(default: refuse; run eval_shard.sh --dry-run there instead)")
    args = ap.parse_args(argv)

    allow = set(args.allow_partial)
    unknown = allow - set(P.PRESETS)
    if unknown:
        raise SystemExit(f"--allow-partial: unknown preset(s) {sorted(unknown)}")
    out = args.out.resolve()
    runs = [("local", args.local_run.resolve(), args.logs_local, args.local_model,
             args.local_run_name or args.local_run.resolve().name, P.LOCAL_PRESETS,
             [CORPUS_FILES, UNIVERSE_FILES, TIER_A_FILES, TIER_B_LOCAL])]
    if args.cloud_run:
        runs.append(("cloud", args.cloud_run.resolve(), args.logs_cloud, args.cloud_model,
                     args.cloud_run_name or args.cloud_run.resolve().name, P.CLOUD_PRESETS,
                     [CORPUS_FILES, UNIVERSE_FILES, TIER_A_FILES, TIER_B_CLOUD]))

    # 1. coverage, before a byte is copied
    statuses = {}
    for key, run_dir, _, model, _, presets, _ in runs:
        if not run_dir.is_dir():
            raise SystemExit(f"{run_dir} is not a directory")
        statuses[key] = validate_coverage(run_dir, presets, model, allow, key, write_lists=args.write_lists)

    # 2. copy
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict] = []
    manifest_runs: dict = {}
    coverage_out: dict = {}
    for key, run_dir, logs_dir, model, name, presets, tables in runs:
        dest = out / "data" / name
        print(f"\ncopying {key} run {run_dir} -> {dest}")
        entries = copy_run(run_dir, dest, out, tables, key, args.force)
        decl = coverage_declaration(statuses[key], name, model, allow)
        if decl:
            cov_path = dest / GS.COVERAGE_FILE
            cov_path.write_text(json.dumps(decl, indent=2) + "\n")
            entries.append({"path": str(cov_path.relative_to(out)), "bytes": cov_path.stat().st_size,
                            "sha256": sha256(cov_path), "tier": "coverage", "producer": "build_artifacts"})
            coverage_out[key] = decl
        n_logs = 0
        if logs_dir:
            logs_dir = logs_dir.resolve()
            if not logs_dir.is_dir():
                raise SystemExit(f"{logs_dir} is not a directory")
            log_entries = copy_tree(logs_dir, out / "logs" / name, out, "logs", "grid", args.force, "*.log")
            entries += log_entries
            n_logs = len(log_entries)
        files += entries
        manifest_runs[key] = {
            "run_dir": name, "model": model, "source": str(run_dir),
            "presets": {n: {"universe": s["universe"], "expected": s["expected"],
                            "complete": s["complete"], "missing": s["missing"]}
                        for n, s in statuses[key].items()},
            "files": len(entries), "logs": n_logs,
        }
        print(f"  {len(entries)} file(s), {sum(e['bytes'] for e in entries) / 1e6:.1f} MB")

    cache_files = 0
    if args.cache:
        cache_dir = args.cache.resolve()
        if not cache_dir.is_dir():
            raise SystemExit(f"{cache_dir} is not a directory")
        ce = copy_tree(cache_dir, out / "cache", out, "cache", "grid", args.force)
        files += ce
        cache_files = len(ce)
        print(f"\ncache: {cache_files} file(s), {sum(e['bytes'] for e in ce) / 1e6:.1f} MB")

    # 3. manifest + readme
    manifest = {
        "schema": 1,
        "created": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": git_info(),
        "builder": "scripts/build_artifacts.py",
        "runs": manifest_runs,
        "coverage": coverage_out,
        "cache_files": cache_files,
        "files": sorted(files, key=lambda e: e["path"]),
        "total_bytes": sum(e["bytes"] for e in files),
    }
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_readme(out, manifest)
    print(f"\n{len(files)} files, {manifest['total_bytes'] / 1e6:.1f} MB -> {out}")
    print(f"MANIFEST.json + README.md written")

    if args.zip:
        zpath = out.parent / f"{out.name}.zip"
        # One top-level directory, always called artifacts/ whatever --out was
        # named: `unzip artifacts.zip` then yields artifacts/{MANIFEST.json,data,logs},
        # the layout fetch_artifacts.py and the RUNBOOK describe.
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    zf.write(p, str(pathlib.PurePosixPath("artifacts") / p.relative_to(out).as_posix()))
        print(f"{zpath}: {zpath.stat().st_size / 1e6:.1f} MB")
        print(f"sha256 {sha256(zpath)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
