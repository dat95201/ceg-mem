#!/usr/bin/env bash
#
# E0 - the oracle gate. CPU only, no model calls, independent of the screen.
#
#   bash scripts/oracle_gate.sh                 # --jobs 6, ~2 h
#   bash scripts/oracle_gate.sh --jobs 1        # publication freeze, ~6-12 h
#   bash scripts/oracle_gate.sh --dry-run       # print the plan, spend nothing
#
# Writes:
#
#   data/pool/tasks.json              the frozen CANDIDATE POOL (not the corpus)
#   data/pool/oracle_validation.json  the gate report
#   logs/oracle_gate.log              the trace
#
# Earns Assumption 1. The paper takes oracle soundness as given; here it is
# demonstrated - up to three natural mutants per fault, judged by the same
# sampling oracle the repair loop calls, and a fault passes at >= 2/3 caught.
# RUNBOOK.md is the runbook; this file is that runbook made executable.
#
# There is no resume
# ------------------
# Unlike screen_shard.sh, nothing here is cached and nothing is written until
# the walk finishes: validate_oracle.py writes tasks.json and
# oracle_validation.json once, at the end. A Ctrl-C at hour five costs five
# hours. Run it under nohup or tmux and leave it alone.
#
# Why --programs is not optional
# ------------------------------
# It hands the script the Stage-0 candidate list in candidates.json's own seeded
# K_proxy-stratified order - the order the screen was sharded on and the order
# select_corpus.py walks when filling bands. Left to itself validate_oracle.py
# builds its own shuffle over every supported fault, which would validate faults
# the screen never measured and order the survivors differently.
#
# The cost of passing it: --programs also switches off both of the script's own
# headroom guards (the refusal at 1.5x and the warning at 2.0x are inside the
# `args.programs is None` branch). So this wrapper computes and prints that
# ratio itself, before the hours are spent rather than after.
#
# The pinned values are the instrument
# ------------------------------------
# --timeout above all. A timeout is a VERDICT here, not a retry: a fault that
# times out is unusable and a mutant that times out is caught. src.sandbox reads
# SANDBOX_TIMEOUT_SEC from .env, and .env.example ships 10.0 while this machine's
# .env carries 30.0 - so two machines running the identical command would score
# different pools and neither would say so. It is passed explicitly here.
#
# Same reasoning for --max-examples, --mutants-per-task and --reference-cases:
# each one changes what "the oracle caught it" means, so each is stated rather
# than defaulted. --corpus-size is both the cohort size the pass rate is measured
# on and the number of passing faults to freeze - see RUNBOOK.md section 2.
#
# Do not run this next to a screen shard
# --------------------------------------
# Both hammer the sandbox, and here a wall-clock timeout is a verdict. A fault
# near the limit fails under parallel load when it would have passed serially,
# which silently shrinks the pool. This script refuses to start while
# measure_pi.py is running; --allow-busy overrides, and --jobs 1 is the honest
# way to share a machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/run_dir_paths.sh

# -- PROTOCOL - what the gate measures. Identical on every machine, or the pools
#    are not the same artifact.
CORPUS_SIZE="${CORPUS_SIZE:-360}"
MIN_SIBLINGS="${MIN_SIBLINGS:-1}"
MAX_EXAMPLES="${MAX_EXAMPLES:-80}"
MUTANTS_PER_TASK="${MUTANTS_PER_TASK:-3}"
REFERENCE_CASES="${REFERENCE_CASES:-20}"
# The slow-task filter. Empty means "whatever validate_oracle.py declares"
# (DEFAULT_REFERENCE_TIMEOUT, currently 10s) - the threshold lives in one
# place, and this script does not get to disagree with it silently.
REFERENCE_TIMEOUT="${REFERENCE_TIMEOUT:-}"
SEED="${SEED:-20260717}"
TIMEOUT="${TIMEOUT:-30.0}"
RECURSION_LIMIT="${SANDBOX_RECURSION_LIMIT:-10000}"
# -- knobs that are free to differ per machine ------------------------------
JOBS="${JOBS:-6}"
POOL="$RUN_DATA/candidates.json"
DATA_DIR="$RUN_DATA/pool"
TEST_DIR="external/ConDefects/Test"

DRY_RUN=0; FORCE=0; ALLOW_BUSY=0

usage() {
  # @DATA@ rather than $RUN_DATA: the heredoc is quoted, so that the usage
  # text can contain $ and backticks safely. Same trick as eval_shard.sh.
  sed -e "s|@DATA@|$RUN_DATA|g" <<'USAGE'
usage: bash scripts/oracle_gate.sh [options]

  --jobs N          parallel workers (default 6, ~2 h). Use 1 for a publication
                    freeze (~6-12 h): a timeout is a verdict, not a retry, so a
                    fault near the wall-clock limit can fail under parallel load
                    when it would have passed serially.
  --corpus-size N|auto
                    cohort size for the gate AND passing faults to freeze
                    (default 360). See RUNBOOK.md section 2 for why not 115.
                    `auto` takes the cohort to be every eligible fault, which is
                    what you want whenever --reference-timeout has moved: the
                    surviving count cannot be known before the walk, and a
                    declared size the walk cannot reach refuses to freeze.
  --data-dir PATH   where tasks.json is written (default @DATA@/pool). Point a
                    rehearsal somewhere else so it cannot overwrite a freeze.
  --pool PATH       candidate list (default @DATA@/candidates.json)
  --timeout SEC     sandbox wall clock per run (default 30.0, matching the screen)
  --reference-timeout SEC
                    the SLOW-TASK FILTER: seconds the reference gets per case
                    (default 10). A coding task whose correct solution cannot
                    answer one of its own inputs in this long is dropped before
                    stage 2. Measured on all 526 candidates it costs 3 of the
                    106-task corpus and removes 34 candidates
  --jobs/--timeout aside, every value below is in what the gate MEANS:
                    --max-examples, --mutants-per-task, --reference-cases,
                    --reference-timeout, --seed
  --allow-busy      start even though a screen shard is running (do not)
  --force           overwrite a pool that is already frozen
  --dry-run         print the plan and the headroom check, start nothing
  -h, --help        this

Then:
  python3 scripts/select_corpus.py --pool @DATA@/pool/tasks.json \
          --screen @DATA@/screen_merged.json --min-calls 38
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs)             JOBS="$2"; shift 2 ;;
    --corpus-size)      CORPUS_SIZE="$2"; shift 2 ;;
    --data-dir)         DATA_DIR="$2"; shift 2 ;;
    --pool)             POOL="$2"; shift 2 ;;
    --timeout)          TIMEOUT="$2"; shift 2 ;;
    --max-examples)     MAX_EXAMPLES="$2"; shift 2 ;;
    --mutants-per-task) MUTANTS_PER_TASK="$2"; shift 2 ;;
    --reference-cases)  REFERENCE_CASES="$2"; shift 2 ;;
    --reference-timeout) REFERENCE_TIMEOUT="$2"; shift 2 ;;
    --min-siblings)     MIN_SIBLINGS="$2"; shift 2 ;;
    --seed)             SEED="$2"; shift 2 ;;
    --allow-busy)       ALLOW_BUSY=1; shift ;;
    --force)            FORCE=1; shift ;;
    --dry-run)          DRY_RUN=1; shift ;;
    -h|--help)          usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

LOG="$RUN_LOGS/oracle_gate.log"
OUT="$DATA_DIR/tasks.json"
mkdir -p "$DATA_DIR" "$RUN_LOGS"

# -- refusals, all of them cheap and all of them before the hours ------------
[[ -f "$POOL" ]] || { echo "$POOL missing - run scripts/select_candidates.py first" >&2; exit 2; }
[[ -d "$TEST_DIR" ]] || {
  echo "$TEST_DIR missing - unpack Test.zip (scripts/fetch_condefects.py prints the links)." >&2
  echo "A partial tree is a prefix of the contest range, not a sample of it." >&2; exit 2; }

if [[ -f "$OUT" ]] && (( ! FORCE )); then
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('$OUT')).get('frozen') else 1)"; then
    echo "$OUT is already frozen - pass --force to overwrite it" >&2
    echo "(this run takes hours and writes nothing until the end; do not lose a good freeze)" >&2
    exit 2
  fi
fi

if (( ! ALLOW_BUSY )) && pgrep -f "measure_pi.py" >/dev/null 2>&1; then
  cat >&2 <<'BUSY'
a screen shard (measure_pi.py) is running on this machine - refusing to start.

Both walks hammer the sandbox and here a timeout is a verdict, not a retry: a
fault near the wall-clock limit fails under parallel load when it would have
passed serially, and the pool silently comes out smaller. Wait for the shard,
run this on another machine, or --allow-busy --jobs 1 if you have to share.
BUSY
  exit 2
fi

# -- the candidate list, one name per line so no shell splits it ------------
# bash 3.2 (what macOS ships) has no mapfile, hence the read loop.
NAMES=()
while IFS= read -r name; do NAMES+=("$name"); done < <(
  python3 - "$POOL" <<'PY'
import json, pathlib, sys
for c in json.loads(pathlib.Path(sys.argv[1]).read_text())["candidates"]:
    print(c["name"])
PY
)
(( ${#NAMES[@]} > 0 )) || { echo "$POOL holds no candidates" >&2; exit 2; }

# -- the headroom that --programs switches off ------------------------------
# validate_oracle.py refuses below 1.5x and warns below 2.0x, but only on the
# path where it picks the candidates itself. We pass --programs, so it will not
# look. Print it here: the failure mode is a walk that runs for hours and then
# cannot fill the cohort, which comes out as "frozen": false.
python3 - "${#NAMES[@]}" "$CORPUS_SIZE" <<'PY'
import sys
n, raw = int(sys.argv[1]), sys.argv[2]
if raw.strip().lower() == "auto":
    # Nothing to be short of: the cohort will be whatever survives.
    print(f"headroom     {n} candidates, cohort = every eligible fault (--corpus-size auto)")
    raise SystemExit
size = int(raw)
ratio = n / size if size else 0
print(f"headroom     {n} candidates for a {size}-fault pool ({ratio:.2f}x)")
if ratio < 1.5:
    print(f"             below validate_oracle.py's own 1.5x refusal line, which "
          f"--programs\n             bypasses. The pilot spent ~1.6 candidates per "
          f"passing fault, so\n             {size} may need ~{round(size * 1.6)}. If the walk "
          f"cannot seat {size} faults in\n             the cohort the pool comes out "
          f'"frozen": false and select_corpus.py\n             refuses it - lower '
          f"--corpus-size rather than re-running blind.")
PY

cat <<EOP

gate         >= 2/3 of a fault's scoreable natural mutants caught
             pool freezes at >= $(python3 -c "
import math, sys
s = sys.argv[1]
print('75%' if s.strip().lower() == 'auto'
      else f'{math.ceil(30/40*int(s))}/{s}')" "$CORPUS_SIZE") of the cohort passing
protocol     corpus_size=$CORPUS_SIZE  min_siblings=$MIN_SIBLINGS  max_examples=$MAX_EXAMPLES
             mutants_per_task=$MUTANTS_PER_TASK  reference_cases=$REFERENCE_CASES  seed=$SEED
             reference_timeout=${REFERENCE_TIMEOUT:-<validate_oracle.py default>}  (the slow-task filter)
             timeout=${TIMEOUT}s  recursion_limit=$RECURSION_LIMIT
candidates   ${#NAMES[@]} from $POOL, in its own seeded order
jobs         $JOBS $( (( JOBS == 1 )) && echo "(serial - the publication setting)" || echo "(~2 h; use --jobs 1 to freeze for publication)" )
pool         $OUT
report       $DATA_DIR/oracle_validation.json
log          $LOG
EOP
echo

if (( DRY_RUN )); then
  echo "--dry-run: nothing started."
  exit 0
fi

[[ -d .venv ]] && source .venv/bin/activate

# python-dotenv does not override an existing environment variable, so this wins
# over .env without touching it - and --timeout is passed explicitly anyway.
export SANDBOX_TIMEOUT_SEC="$TIMEOUT"
export SANDBOX_RECURSION_LIMIT="$RECURSION_LIMIT"

echo "started $(date '+%Y-%m-%d %H:%M:%S') - no output is written until this finishes"
python3 scripts/validate_oracle.py \
    --programs "${NAMES[@]}" \
    --corpus-size "$CORPUS_SIZE" \
    --min-siblings "$MIN_SIBLINGS" \
    --max-examples "$MAX_EXAMPLES" \
    --mutants-per-task "$MUTANTS_PER_TASK" \
    --reference-cases "$REFERENCE_CASES" \
    --seed "$SEED" \
    --timeout "$TIMEOUT" \
    ${REFERENCE_TIMEOUT:+--reference-timeout "$REFERENCE_TIMEOUT"} \
    --jobs "$JOBS" \
    --data-dir "$DATA_DIR" 2>&1 | tee -a "$LOG"

# -- what the next step will check ------------------------------------------
python3 - "$OUT" "$DATA_DIR/oracle_validation.json" "$RUN_DATA" <<'PY'
import json, pathlib, sys
pool = json.loads(pathlib.Path(sys.argv[1]).read_text())
rep = json.loads(pathlib.Path(sys.argv[2]).read_text())
DATA = sys.argv[3]
frozen = pool.get("frozen")
print()
print(f"frozen       {frozen}   tasks held: {len(pool.get('tasks', []))}")
print(f"cohort       {rep.get('n_cohort_passing')}/{rep.get('n_cohort')} passing "
      f"(needs {rep.get('corpus_pass_threshold')})")
if frozen:
    print(f"\nnext:\n  python3 scripts/select_corpus.py --pool {DATA}/pool/tasks.json \\\n"
          f"          --screen {DATA}/screen_merged.json --min-calls 38")
else:
    print("\nselect_corpus.py will refuse this pool. Read corpus_gate_ok, n_cohort and\n"
          "n_cohort_passing above: a short cohort means the candidate list ran out\n"
          "(lower --corpus-size), a low pass rate means the oracle itself did not clear\n"
          "the gate (do not run experiments on it).")
PY
