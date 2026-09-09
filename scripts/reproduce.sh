#!/usr/bin/env bash
#
# The one command: rebuild the paper from whatever is on disk, running only what
# is missing.
#
#   bash scripts/reproduce.sh                       # everything, both proposers
#   ARTIFACTS=/path/or/url bash scripts/reproduce.sh # install the shipped data first
#   bash scripts/reproduce.sh --dry-run             # the plan: what is hit, what would run
#   bash scripts/reproduce.sh --stage grid          # one stage only
#   bash scripts/reproduce.sh --update-numbers      # accept regenerated paper JSONs as reference
#   bash scripts/reproduce.sh --no-paper            # stop after the numbers check
#
# THE RULE this script implements (RUNBOOK.md): with the artifacts installed and
# no changes to defaults it must rebuild paper/main.pdf with every reported
# number identical to the committed one, starting no model server and running
# no oracle; with no artifacts at all it must run the whole study from scratch,
# given a model endpoint, the benchmark test data and time. The decision between
# the two is made per stage and, for the grid, per CELL: a preset with no cell
# missing from the merged log is a hit and nothing is started for it.
#
# Stages, in order (each per proposer where it says so):
#   0 env        venv + requirements; ConDefects code; Test/ and TeX reported
#   1 artifacts  ARTIFACTS -> download, verify, unpack, install (never overwrites)
#   2 corpus     [local cloud] tasks.json, pool/, candidates.json present, or built
#   3 grid       [local cloud] every preset: expected/complete/missing cells; run the
#                missing ones (eval_shard.sh) and consolidate; hit otherwise
#   4 analyse    [local cloud] the analysis chain; Tier-B artifacts hit if present
#   5 policies   [local] verdict matrix (hit if present) -> simulate -> verify
#   6 numbers    regenerate every paper/common/*.json into a temp dir, compare with
#                the committed ones, fail on a difference unless --update-numbers
#   7 paper      figures, make -C paper/common, paper/main.pdf (+ saner / fse)
#
# Every variable below has a default that reproduces the paper; RUNBOOK.md
# documents each. Every external step is echoed with a leading "+" before it
# runs. A summary table (stage, scope, status, seconds) closes the run, also on
# failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── environment table ───────────────────────────────────────────────────────
ARTIFACTS="${ARTIFACTS:-}"                     # url | path/to/artifacts.zip | path/to/dir
ARTIFACTS_SHA256="${ARTIFACTS_SHA256:-}"       # optional integrity check of the archive
ARTIFACTS_FORCE="${ARTIFACTS_FORCE:-0}"        # 1 = overwrite existing files on install
PROPOSERS="${PROPOSERS:-local cloud}"          # which proposer blocks to run
LOCAL_RUN_DIR="${LOCAL_RUN_DIR:-official-2026-09-01}"
LOCAL_MODEL="${LOCAL_MODEL:-qwen2.5-coder:7b}"
LOCAL_CONTEXT_LENGTH="${LOCAL_CONTEXT_LENGTH:-32768}"
LOCAL_BACKEND="${LOCAL_BACKEND:-ollama}"
PORT="${PORT:-11435}"                          # ollama port (local backend)
CLOUD_RUN_DIR="${CLOUD_RUN_DIR:-gpto4mini-2026-09-06}"
CLOUD_MODEL="${CLOUD_MODEL:-gpt-4o-mini}"
# 200000, not gpt-4o-mini's 128k window: the shipped cloud shards were collected
# with LLM_CONTEXT_TOKENS=200000 (src.llm uses it only to refuse an over-long
# prompt before sending; no prompt in this study comes near either figure) and
# consolidate_evals.py refuses to pool shards whose recorded context_length
# differ, so a top-up shard must carry the same value as the shipped ones.
CLOUD_CONTEXT_LENGTH="${CLOUD_CONTEXT_LENGTH:-200000}"
CLOUD_BACKEND="${CLOUD_BACKEND:-cloud}"
# Cloud only, and only if a cloud cell must actually run. Deliberately NOT
# exported here: scripts/analyze.py prices the ledger from the same variables,
# and the reported (local) run is priced at zero.
CLOUD_PRICE_IN_PER_MTOK="${PRICE_IN_PER_MTOK:-0.15}"
CLOUD_PRICE_OUT_PER_MTOK="${PRICE_OUT_PER_MTOK:-0.60}"
CLOUD_BUDGET_USD_CAP="${BUDGET_USD_CAP:-25}"
LLM_API_KEY="${LLM_API_KEY:-}"
LLM_BASE_URL="${LLM_BASE_URL:-}"
SHARDS="${SHARDS:-1}"                          # split each preset's universe into N ranges, run in sequence
RECOMPUTE_HEAVY="${RECOMPUTE_HEAVY:-0}"        # 1 = recompute Tier-B artifacts even when shipped (needs Test/)
PAPER_TARGETS="${PAPER_TARGETS:-main}"         # main | main saner fse
FILL_DECLARED_GAPS="${FILL_DECLARED_GAPS:-0}"  # 1 = also run cells a run's grid_coverage.json declares missing
# The fault whose candidate patches sit at the sandbox-timeout edge: E1 and the
# guarded arms disagree on success@B for two of its seeds, which the paper
# reports and excludes in a sensitivity analysis (numbers.json
# sensitivity_exclude_timeout_edge). check_consistency.py prints divergences on
# these tasks and does not count them as a guard bug; any other task still fails.
KNOWN_DIVERGENT_TASKS="${KNOWN_DIVERGENT_TASKS:-abc285_e/48880084}"
CONDEFECTS_ROOT="${CONDEFECTS_ROOT:-external/ConDefects}"
TEST_DIR="${CONDEFECTS_TEST_DIR:-$CONDEFECTS_ROOT/Test}"

DRY_RUN=0; ONLY_STAGE=""; UPDATE_NUMBERS=0; NO_PAPER=0
ALL_STAGES=(env artifacts corpus grid analyse policies numbers paper)

usage() { sed -n '2,36p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)        DRY_RUN=1; shift ;;
    --stage)          ONLY_STAGE="$2"; shift 2 ;;
    --update-numbers) UPDATE_NUMBERS=1; shift ;;
    --no-paper)       NO_PAPER=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ -n "$ONLY_STAGE" ]]; then
  ok=0; for s in "${ALL_STAGES[@]}"; do [[ "$s" == "$ONLY_STAGE" ]] && ok=1; done
  (( ok )) || { echo "unknown --stage $ONLY_STAGE (one of: ${ALL_STAGES[*]})" >&2; exit 2; }
fi
[[ "$SHARDS" =~ ^[1-9][0-9]*$ ]] || { echo "SHARDS must be a positive integer" >&2; exit 2; }
for p in $PROPOSERS; do
  [[ "$p" == local || "$p" == cloud ]] || { echo "PROPOSERS holds '$p'; only local and cloud exist" >&2; exit 2; }
done

# ── bookkeeping ─────────────────────────────────────────────────────────────
SUMMARY=()                      # "stage|scope|status|seconds"
CUR_STAGE=""; CUR_SCOPE=""; CUR_T0=0
T_START=$(date +%s)

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
warn() { printf '\n   \033[1;33mWARNING\033[0m %s\n' "$*" >&2; }
die()  { printf '\n   \033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
# Echo, then execute - unless --dry-run, where the echo IS the plan.
run()  { printf '+ %s\n' "$*"; if (( DRY_RUN )); then return 0; fi; "$@"; }
# Same, but a step that only READS state and so runs under --dry-run too.
peek() { printf '+ %s\n' "$*"; "$@"; }

stage_begin() { CUR_STAGE="$1"; CUR_SCOPE="${2:--}"; CUR_T0=$(date +%s); say "$1${2:+ [$2]}"; }
stage_end()   { SUMMARY+=("$CUR_STAGE|$CUR_SCOPE|$1|$(( $(date +%s) - CUR_T0 ))"); CUR_STAGE=""; }
wants()       { [[ -z "$ONLY_STAGE" || "$ONLY_STAGE" == "$1" ]]; }
planned()     { if (( DRY_RUN )); then echo "planned (would run)"; else echo "$1"; fi; }

print_summary() {
  local rc=$?
  trap - EXIT
  rm -f "${GRID_JSON_FILE:-}"
  if [[ -n "$CUR_STAGE" ]]; then
    SUMMARY+=("$CUR_STAGE|$CUR_SCOPE|$([[ $rc -eq 0 ]] && echo ran || echo FAILED)|$(( $(date +%s) - CUR_T0 ))")
  fi
  echo
  printf '%-10s %-7s %-22s %8s\n' stage scope status seconds
  printf '%-10s %-7s %-22s %8s\n' ----- ----- ------ -------
  local row
  for row in "${SUMMARY[@]}"; do
    IFS='|' read -r s sc st sec <<<"$row"
    printf '%-10s %-7s %-22s %8s\n' "$s" "$sc" "$st" "$sec"
  done
  printf '%-10s %-7s %-22s %8s\n' total - "$([[ $rc -eq 0 ]] && echo ok || echo "exit $rc")" "$(( $(date +%s) - T_START ))"
  if (( rc == 0 )) && (( ! DRY_RUN )) && (( ! NO_PAPER )) && [[ -f paper/main.pdf ]] && wants paper; then
    echo; echo "paper: $ROOT/paper/main.pdf"
  fi
  (( DRY_RUN )) && { echo; echo "--dry-run: nothing was started."; }
  exit $rc
}
trap print_summary EXIT

# ── per-proposer variables ──────────────────────────────────────────────────
# P_RUN P_MODEL P_CTX P_BACKEND P_PRESETS for the proposer named.
proposer_vars() {
  case "$1" in
    local) P_RUN="$LOCAL_RUN_DIR"; P_MODEL="$LOCAL_MODEL"; P_CTX="$LOCAL_CONTEXT_LENGTH"; P_BACKEND="$LOCAL_BACKEND" ;;
    cloud) P_RUN="$CLOUD_RUN_DIR"; P_MODEL="$CLOUD_MODEL"; P_CTX="$CLOUD_CONTEXT_LENGTH"; P_BACKEND="$CLOUD_BACKEND" ;;
  esac
  P_PRESETS="$(python3 scripts/presets.py --presets "$1")"
  P_DATA="data/$P_RUN"
}

have_test_data() { [[ -d "$TEST_DIR" ]] && [[ -n "$(ls -A "$TEST_DIR" 2>/dev/null)" ]]; }
have_tex()       { command -v latexmk >/dev/null && command -v pdflatex >/dev/null; }

# ── stage 0: env ────────────────────────────────────────────────────────────
stage_env() {
  stage_begin env
  local status=hit
  if [[ ! -d .venv ]]; then
    run python3 -m venv .venv; status=ran
  fi
  # shellcheck disable=SC1091
  [[ -f .venv/bin/activate ]] && source .venv/bin/activate
  if ! python3 -c "import numpy, scipy, matplotlib, openai, dotenv" 2>/dev/null; then
    run python3 -m pip install -q -r requirements.txt; status=ran
  fi
  note "python: $(python3 --version 2>&1) at $(command -v python3)"
  if [[ -d "$CONDEFECTS_ROOT/Code" ]]; then
    note "ConDefects code: $CONDEFECTS_ROOT/Code present"
  else
    note "ConDefects code: absent - cloning (125 MB)"
    # fetch_condefects.py exits 1 when Test/ is still missing after the clone;
    # that is the report we want, not a failure, so only the clone is asserted.
    run python3 scripts/fetch_condefects.py || true
    (( DRY_RUN )) || [[ -d "$CONDEFECTS_ROOT/Code" ]] || die "$CONDEFECTS_ROOT/Code still absent after the clone"
    status=ran
  fi
  if have_test_data; then
    note "ConDefects test data: $TEST_DIR present (oracle available)"
  else
    note "ConDefects test data: $TEST_DIR ABSENT - no oracle. Fine for a run from the shipped"
    note "  artifacts; required to run any missing cell or to recompute a Tier-B artifact."
  fi
  if have_tex; then
    note "TeX: $(command -v latexmk), $(command -v pdflatex)"
  else
    warn "TeX (latexmk + pdflatex) not found: the paper stage will be SKIPPED."
  fi
  if [[ " $PROPOSERS " == *" local "* && "$LOCAL_BACKEND" == ollama ]]; then
    if command -v ollama >/dev/null; then note "ollama: $(command -v ollama) (started only if a local cell must run)"
    else note "ollama: not on PATH (needed only if a local cell must run)"; fi
  fi
  stage_end "$status"
}

# ── stage 1: artifacts ──────────────────────────────────────────────────────
stage_artifacts() {
  stage_begin artifacts
  if [[ -z "$ARTIFACTS" ]]; then
    note "ARTIFACTS not set - nothing to install (the study runs from what is in data/ and cache/)"
    stage_end skipped; return
  fi
  local args=(--source "$ARTIFACTS")
  [[ -n "$ARTIFACTS_SHA256" ]] && args+=(--sha256 "$ARTIFACTS_SHA256")
  [[ "$ARTIFACTS_FORCE" == 1 ]] && args+=(--force)
  (( DRY_RUN )) && args+=(--dry-run)
  peek python3 scripts/fetch_artifacts.py "${args[@]}"
  if (( DRY_RUN )); then
    note "NOTE the plan below is computed on what is installed NOW, not on what the artifacts would add."
    note "     For the plan after the install: bash scripts/reproduce.sh --stage artifacts && bash scripts/reproduce.sh --dry-run"
  fi
  stage_end "$(planned ran)"
}

# ── stage 2: corpus ─────────────────────────────────────────────────────────
corpus_files() { echo "$1/tasks.json" "$1/pool/tasks.json" "$1/pool/oracle_validation.json" "$1/candidates.json"; }
corpus_present() { local f; for f in $(corpus_files "$1"); do [[ -f "$f" ]] || return 1; done; }

stage_corpus() {
  local who="$1"; proposer_vars "$who"
  stage_begin corpus "$who"
  if corpus_present "$P_DATA"; then
    note "$P_DATA: tasks.json, pool/tasks.json, pool/oracle_validation.json, candidates.json present"
    stage_end hit; return
  fi
  if [[ "$who" == cloud ]]; then
    # The second proposer inherits the local run's frozen corpus and banding:
    # same 99 tasks, same pool, same candidate list (RUNBOOK.md).
    local src="data/$LOCAL_RUN_DIR"
    if ! corpus_present "$src"; then
      (( DRY_RUN )) || die "$P_DATA has no corpus and neither has $src to inherit it from - run the local corpus stage first"
      note "$P_DATA: corpus absent - would inherit the local run's frozen corpus from $src once it exists"
      stage_end "planned (would run)"; return
    fi
    note "$P_DATA: corpus absent - inheriting the local run's frozen corpus from $src"
    run mkdir -p "$P_DATA/pool"
    run cp -n "$src/tasks.json" "$P_DATA/tasks.json"
    run cp -n "$src/candidates.json" "$P_DATA/candidates.json"
    run cp -n "$src/pool/tasks.json" "$P_DATA/pool/tasks.json"
    run cp -n "$src/pool/oracle_validation.json" "$P_DATA/pool/oracle_validation.json"
    [[ -f "$src/screening.json" ]] && run cp -n "$src/screening.json" "$P_DATA/screening.json"
    stage_end "$(planned ran)"; return
  fi
  note "$P_DATA: corpus absent - building it from the benchmark (hours of oracle time)"
  (( DRY_RUN )) || have_test_data || die "building a corpus needs $TEST_DIR (ConDefects Test.zip) - see README.md"
  local st art
  for st in candidates gate corpus; do
    case "$st" in
      candidates) art="$P_DATA/candidates.json" ;;
      gate)       art="$P_DATA/pool/tasks.json" ;;
      corpus)     art="$P_DATA/tasks.json" ;;
    esac
    if [[ -f "$art" ]]; then note "pipeline.sh $st: $art present, skipping"; continue; fi
    run env RUN_DIR="$P_RUN" bash scripts/pipeline.sh "$st"
  done
  stage_end "$(planned ran)"
}

# ── stage 3: grid ───────────────────────────────────────────────────────────
# One grid_status call per proposer; its JSON is kept in a temp file (a
# from-scratch run lists thousands of missing cells - too much for an argv) and
# read back for the table and for the ranges each preset still has to run.
GRID_JSON_FILE="$(mktemp "${TMPDIR:-/tmp}/ceg-mem-grid.XXXXXX")"

grid_table() {   # $1 proposer; fills GRID_JSON_FILE, prints the table, returns 0/3
  local strict=(); (( FILL_DECLARED_GAPS )) && strict=(--strict)
  local rc=0
  set +e
  python3 scripts/grid_status.py --run-dir "$P_RUN" --all-presets "$1" --model "$P_MODEL" \
          --shards "$SHARDS" ${strict[@]+"${strict[@]}"} --json >"$GRID_JSON_FILE"
  rc=$?
  set -e
  (( rc == 0 || rc == 3 )) || die "grid_status.py failed (exit $rc) for $P_DATA"
  python3 - "$GRID_JSON_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"   merged log {d['episodes']}: {d['rounds']} rounds"
      + (f"   (declared gaps: {d['coverage_file']})" if d.get('coverage_file') else ""))
print(f"   {'preset':20s} {'universe':8s} {'expected':>8s} {'complete':>8s} {'missing':>7s} {'declared':>8s}  decision")
for p in d["presets"]:
    todo = p["missing"] if d["strict"] else p["missing_undeclared"]
    if todo:
        ranges = ", ".join(f"{a}-{b}" for a, b in p["ranges_todo"])
        word = f"RUN {todo} cell(s) in range(s) {ranges}"
    elif p["missing"]:
        word = "hit (missing cells are declared in grid_coverage.json - not run)"
    else:
        word = "hit"
    print(f"   {p['preset']:20s} {p['universe']:8s} {p['expected']:8d} {p['complete']:8d} "
          f"{p['missing']:7d} {p['missing_declared']:8d}  {word}")
PY
  return $rc
}

grid_ranges() {   # $1 preset -> "lo-hi" per line, the ranges still holding missing cells
  python3 - "$GRID_JSON_FILE" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for p in d["presets"]:
    if p["preset"] == sys.argv[2]:
        todo = p["missing"] if d["strict"] else p["missing_undeclared"]
        if todo:
            for a, b in p["ranges_todo"]:
                print(f"{a}-{b}")
PY
}

stage_grid() {
  local who="$1"; proposer_vars "$who"
  stage_begin grid "$who"
  if [[ ! -f "$P_DATA/tasks.json" ]]; then
    (( DRY_RUN )) || die "$P_DATA/tasks.json missing - the corpus stage has not run"
    note "$P_DATA/tasks.json not present yet: after the corpus stage, EVERY cell of every preset"
    note "would run ($P_PRESETS)"
    stage_end "planned (would run)"; return
  fi
  local rc=0
  grid_table "$who" || rc=$?
  if (( rc == 0 )); then
    note "every preset complete - nothing to run, no server started"
    stage_end hit; return
  fi
  note "some presets have missing cells; only their missing cells will be run (run_eval resumes)"
  if (( ! DRY_RUN )); then
    have_test_data || die "cells are missing for $P_RUN and $TEST_DIR is absent: the oracle cannot run. Install the shipped artifacts (ARTIFACTS=...) or the ConDefects test data."
    if [[ "$P_BACKEND" == ollama ]]; then
      command -v ollama >/dev/null || die "cells are missing for $P_RUN and ollama is not on PATH (LOCAL_BACKEND=ollama)"
    else
      [[ -n "$LLM_API_KEY" ]] || die "cells are missing for $P_RUN and LLM_API_KEY is empty (backend $P_BACKEND)"
    fi
  fi
  local preset ranges r lo hi
  for preset in $P_PRESETS; do
    ranges="$(grid_ranges "$preset")"
    [[ -n "$ranges" ]] || continue
    local ran_this=0
    while read -r r; do
      [[ -n "$r" ]] || continue
      lo="${r%-*}"; hi="${r#*-}"
      local envs=(RUN_DIR="$P_RUN" CONTEXT_LENGTH="$P_CTX")
      if [[ "$P_BACKEND" == ollama ]]; then
        envs+=(PORT="$PORT")
      else
        envs+=(LLM_API_KEY="$LLM_API_KEY" LLM_BASE_URL="$LLM_BASE_URL"
               PRICE_IN_PER_MTOK="$CLOUD_PRICE_IN_PER_MTOK" PRICE_OUT_PER_MTOK="$CLOUD_PRICE_OUT_PER_MTOK"
               BUDGET_USD_CAP="$CLOUD_BUDGET_USD_CAP")
      fi
      if (( DRY_RUN )); then
        # The key is not something to print, even redacted into a plan.
        printf '+ env RUN_DIR=%s CONTEXT_LENGTH=%s%s bash scripts/eval_shard.sh --exp %s --backend %s --model %s --from %s --to %s\n' \
          "$P_RUN" "$P_CTX" "$([[ "$P_BACKEND" == ollama ]] && echo " PORT=$PORT" || echo " LLM_API_KEY=... PRICE_IN_PER_MTOK=$CLOUD_PRICE_IN_PER_MTOK PRICE_OUT_PER_MTOK=$CLOUD_PRICE_OUT_PER_MTOK BUDGET_USD_CAP=$CLOUD_BUDGET_USD_CAP")" \
          "$preset" "$P_BACKEND" "$P_MODEL" "$lo" "$hi"
      else
        printf '+ env RUN_DIR=%s CONTEXT_LENGTH=%s bash scripts/eval_shard.sh --exp %s --backend %s --model %s --from %s --to %s\n' \
          "$P_RUN" "$P_CTX" "$preset" "$P_BACKEND" "$P_MODEL" "$lo" "$hi"
        env "${envs[@]}" bash scripts/eval_shard.sh --exp "$preset" --backend "$P_BACKEND" --model "$P_MODEL" \
            --from "$lo" --to "$hi"
      fi
      ran_this=1
    done <<<"$ranges"
    (( ran_this )) || continue
    # Merge what the shards wrote into the merged log - the existing merged file
    # FIRST so nothing already there is lost (consolidate_evals.py orders it so).
    local shard_logs=("$P_DATA"/episodes_eval_*.jsonl)
    if (( DRY_RUN )); then
      echo "+ env RUN_DIR=$P_RUN python3 scripts/consolidate_evals.py --episodes $P_DATA/episodes.jsonl $P_DATA/episodes_eval_*.jsonl"
    elif [[ -e "${shard_logs[0]}" ]]; then
      local inputs=()
      [[ -f "$P_DATA/episodes.jsonl" ]] && inputs+=("$P_DATA/episodes.jsonl")
      inputs+=("${shard_logs[@]}")
      run env RUN_DIR="$P_RUN" python3 scripts/consolidate_evals.py --episodes "${inputs[@]}"
    else
      warn "no shard log was produced for $preset - consolidation skipped"
    fi
  done
  if (( DRY_RUN )); then stage_end "planned (would run)"; return; fi
  # The re-check is the contract: after the run, nothing may still be missing.
  rc=0; grid_table "$who" || rc=$?
  (( rc == 0 )) || die "cells are still missing for $P_RUN after the run - see logs/$P_RUN/eval_*.log"
  stage_end ran
}

# ── stage 4: analyse ────────────────────────────────────────────────────────
stage_analyse() {
  local who="$1"; proposer_vars "$who"
  stage_begin analyse "$who"
  if [[ ! -f "$P_DATA/episodes.jsonl" ]]; then
    (( DRY_RUN )) || die "$P_DATA/episodes.jsonl missing - the grid stage has not run"
    note "$P_DATA/episodes.jsonl not present yet: the whole chain would run after the grid"
    stage_end "planned (would run)"; return
  fi
  local E=(env RUN_DIR="$P_RUN")
  note "Tier C - recomputed every time (seconds): results_real, theory_fit, strata, analysis,"
  note "anchoring, redundancy, patch_quality, failure_taxonomy"
  run "${E[@]}" python3 scripts/freeze_results.py --experiment main --force   # first pass, no strata yet
  run "${E[@]}" python3 scripts/fit_theory.py                                  # theory_fit.json: E1's pi_hat
  run "${E[@]}" python3 scripts/build_strata.py --force                        # reported banding from E1
  run "${E[@]}" python3 scripts/freeze_results.py --experiment main --force   # stamps the stratum
  run "${E[@]}" python3 scripts/analyze.py
  run "${E[@]}" python3 scripts/fit_theory.py
  run "${E[@]}" python3 scripts/measure_anchoring.py
  run "${E[@]}" python3 scripts/measure_redundancy.py
  run "${E[@]}" python3 scripts/measure_patch_quality.py
  local heavy_ran=0
  # Tier B: oracle-heavy, no model calls. Hit when shipped.
  if [[ -f "$P_DATA/coherence_report.json" && "$RECOMPUTE_HEAVY" != 1 ]]; then
    note "Tier B  coherence_report.json present - hit (RECOMPUTE_HEAVY=1 recomputes; hours of sandbox time)"
  else
    note "Tier B  coherence_report.json: recomputing (needs $TEST_DIR; hours)"
    (( DRY_RUN )) || have_test_data || die "measure_coherence.py needs $TEST_DIR"
    run "${E[@]}" python3 scripts/measure_coherence.py; heavy_ran=1
  fi
  if [[ "$who" == local ]]; then
    if [[ -f "$P_DATA/pool_strength.json" && "$RECOMPUTE_HEAVY" != 1 ]]; then
      note "Tier B  pool_strength.json present - hit"
    else
      note "Tier B  pool_strength.json: recomputing (needs $TEST_DIR; hours)"
      (( DRY_RUN )) || have_test_data || die "measure_pool_strength.py needs $TEST_DIR"
      run "${E[@]}" python3 scripts/measure_pool_strength.py; heavy_ran=1
    fi
  fi
  # shellcheck disable=SC2086
  run "${E[@]}" python3 scripts/check_consistency.py --allow-divergent $KNOWN_DIVERGENT_TASKS
  if (( DRY_RUN )); then stage_end "planned ($([[ $heavy_ran == 1 ]] && echo 'would run tier B' || echo 'tier C only'))"; return; fi
  stage_end "$([[ $heavy_ran == 1 ]] && echo ran || echo "ran (tier B hit)")"
}

# ── stage 5: policies (local only) ──────────────────────────────────────────
stage_policies() {
  proposer_vars local
  stage_begin policies local
  [[ " $PROPOSERS " == *" local "* ]] || { note "PROPOSERS excludes local - skipped"; stage_end skipped; return; }
  if [[ ! -f "$P_DATA/episodes.jsonl" ]]; then
    (( DRY_RUN )) || die "$P_DATA/episodes.jsonl missing - the grid stage has not run"
    note "$P_DATA/episodes.jsonl not present yet: matrix, simulation and verification would run after the grid"
    stage_end "planned (would run)"; return
  fi
  local E=(env RUN_DIR="$P_RUN") status="ran (tier B hit)"
  if [[ -f "$P_DATA/verdicts.jsonl" && -f "$P_DATA/verdicts_cases.json" && "$RECOMPUTE_HEAVY" != 1 ]]; then
    note "Tier B  verdicts.jsonl + verdicts_cases.json present - hit (build_verdict_matrix.py not run)"
  else
    note "Tier B  verdict matrix: building (needs $TEST_DIR; many CPU-hours)"
    (( DRY_RUN )) || have_test_data || die "build_verdict_matrix.py needs $TEST_DIR"
    run "${E[@]}" python3 scripts/build_verdict_matrix.py
    status=ran
  fi
  run "${E[@]}" python3 scripts/simulate_policies.py
  run python3 scripts/verify_policies.py "$P_DATA"
  stage_end "$(planned "$status")"
}

# ── stage 6: numbers ────────────────────────────────────────────────────────
PAPER_JSONS=(numbers figdata addenda corpus related review3 secondproposer)

stage_numbers() {
  stage_begin numbers
  command -v make >/dev/null || die "make is required for the numbers stage (paper/common/Makefile regen)"
  local local_data="data/$LOCAL_RUN_DIR" cloud_data="data/$CLOUD_RUN_DIR"
  [[ -f "$local_data/results_real.json" ]] || (( DRY_RUN )) || die "$local_data/results_real.json missing - the analyse stage has not run"
  [[ -d "$cloud_data" ]] || (( DRY_RUN )) || die "$cloud_data missing - secondproposer.json needs both runs (PROPOSERS=\"local cloud\")"
  local regen; regen="$(mktemp -d "${TMPDIR:-/tmp}/ceg-mem-numbers.XXXXXX")"
  note "regenerating ${PAPER_JSONS[*]} into $regen"
  # Run paths RELATIVE to paper/common (as the Makefile's defaults are): they end
  # up in the JSONs' provenance blocks, and a machine-specific absolute path
  # there would make every reproduction a git diff.
  run make -C paper/common regen "NUMBERS_OUT=$regen" "RUN=../../$local_data" "CLOUD_RUN=../../$cloud_data"
  local args=(--regenerated "$regen" --committed paper/common)
  local rc=0
  if (( DRY_RUN )); then
    echo "+ python3 paper/common/tools/check_numbers.py ${args[*]}"
    note "would fail on any difference (provenance paths ignored, NaN==NaN, rel_tol 1e-6) unless --update-numbers"
    rmdir "$regen" 2>/dev/null || true
    stage_end "planned (would run)"; return
  fi
  # Compare first, always without --update, so the outcome is known before
  # anything under paper/common is touched.
  set +e; run python3 paper/common/tools/check_numbers.py "${args[@]}"; rc=$?; set -e
  local status
  if (( rc == 0 )); then
    # Identical: the committed files stay the reference, byte for byte. Nothing
    # under paper/common is written, so a reproduction leaves git clean even
    # where another platform's libm or Python's sum() differ in the last bit
    # (inside the tolerance, and a committed float never moves for that).
    status="ran (identical)"
  elif (( UPDATE_NUMBERS )); then
    warn "regenerated numbers differ from the committed ones; --update-numbers accepts them as the new reference"
    run python3 paper/common/tools/check_numbers.py "${args[@]}" --update || true
    status="ran (UPDATED reference)"
  else
    die "regenerated paper numbers differ from paper/common/*.json (kept in $regen for inspection). Re-run with --update-numbers only if the change is intended."
  fi
  rm -rf "$regen"
  stage_end "$status"
}

# ── stage 7: paper ──────────────────────────────────────────────────────────
stage_paper() {
  stage_begin paper
  if (( NO_PAPER )); then note "--no-paper"; stage_end skipped; return; fi
  if ! have_tex; then
    warn "TeX not found (latexmk + pdflatex): paper NOT built. Install TeX Live with IEEEtran and acmart, then: bash scripts/reproduce.sh --stage paper"
    stage_end "skipped (no TeX)"; return
  fi
  run python3 paper/common/tools/make_figures.py paper/common
  run make -C paper/common
  run cp paper/common/main.pdf paper/main.pdf
  local t
  for t in $PAPER_TARGETS; do
    case "$t" in
      main)  ;;
      saner) run make -C paper/saner2027 ;;
      fse)   run make -C paper/fse2027 ;;
      *) die "PAPER_TARGETS holds '$t'; known: main saner fse" ;;
    esac
  done
  stage_end "$(planned ran)"
}

# ── main ────────────────────────────────────────────────────────────────────
say "CEGMem reproduce$( (( DRY_RUN )) && echo ' - DRY RUN')"
note "repo         $ROOT ($(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo 'no git'))"
note "proposers    $PROPOSERS"
note "local        RUN_DIR=$LOCAL_RUN_DIR model=$LOCAL_MODEL context=$LOCAL_CONTEXT_LENGTH backend=$LOCAL_BACKEND port=$PORT"
note "cloud        RUN_DIR=$CLOUD_RUN_DIR model=$CLOUD_MODEL context=$CLOUD_CONTEXT_LENGTH backend=$CLOUD_BACKEND"
note "artifacts    ${ARTIFACTS:-(none)}${ARTIFACTS_SHA256:+  sha256=$ARTIFACTS_SHA256}"
note "shards=$SHARDS  recompute_heavy=$RECOMPUTE_HEAVY  fill_declared_gaps=$FILL_DECLARED_GAPS  paper_targets=$PAPER_TARGETS"
note "known divergent task(s) for check_consistency: $KNOWN_DIVERGENT_TASKS"
[[ -n "$ONLY_STAGE" ]] && note "stage        $ONLY_STAGE only"

# The venv, if it already exists, before anything imports numpy.
# shellcheck disable=SC1091
[[ -f .venv/bin/activate ]] && source .venv/bin/activate

wants env       && stage_env
wants artifacts && stage_artifacts
for who in $PROPOSERS; do wants corpus  && stage_corpus  "$who"; done
for who in $PROPOSERS; do wants grid    && stage_grid    "$who"; done
for who in $PROPOSERS; do wants analyse && stage_analyse "$who"; done
wants policies  && stage_policies
wants numbers   && stage_numbers
wants paper     && stage_paper
exit 0
