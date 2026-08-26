#!/usr/bin/env bash
#
# One entry point for the whole study: where you are, and what runs next.
#
#   bash scripts/pipeline.sh                      # status
#   bash scripts/pipeline.sh candidates           # run one stage
#   bash scripts/pipeline.sh screen --from 1 --to 132
#   bash scripts/pipeline.sh --dry-run corpus     # print the command, run nothing
#
# The stages are DESIGN.md's, in order. This script does not reimplement any of
# them - each is its own program with its own flags, and passing extra arguments
# through is the normal way to use them. What it owns is the three things that
# are easy to get wrong between stages and silent when they are:
#
#   order        a stage refuses to start when the artifact it reads is absent,
#                naming the stage that produces it. Running the corpus freeze
#                against a half-merged screen is not an error anything else
#                catches - it just freezes a smaller corpus.
#   wiring       --min-calls for select_corpus.py is READ OFF the merged screen
#                report rather than retyped. It must equal the depth the screen
#                actually reached: pi_hat lives on a grid of 1/K, so a wrong K
#                silently empties whichever band has no reachable grid point.
#   protocol     the model-facing stages (screen, eval) are delegated to their
#                own shard scripts, which pin the model, verify the served
#                context window, and tear the server down. Nothing here talks
#                to a model.
#
# Sharded stages take an index range and are run once per shard, possibly on
# several machines; see RUNBOOK.md and RUNBOOK.md. Everything else is
# single-shot and CPU-only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DRY=0
while [[ ${1:-} == --* ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '3,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
STAGE="${1:-status}"; shift || true

# ── the stage table ─────────────────────────────────────────────────────────
# key | artifact it produces | the stage that must have run first | what it is
STAGES=(
  "benchmark|external/ConDefects/Test||the ConDefects checkout and its test data"
  "candidates|data/candidates.json|benchmark|Stage 0: the screening candidate pool"
  "gate|data/pool/tasks.json|candidates|E0: the oracle gate, earns Assumption 1"
  "screen|data/screen_merged.json|gate|E0b: measure pi_hat  (sharded, local model)"
  "corpus|data/tasks.json|screen|freeze the corpus on the pi bands"
  "pool-strength|data/pool_strength.json|corpus|the oracle's blind spot"
  "eval|data/episodes.jsonl|corpus|E1-E5: the grid  (sharded, local model)"
  "analyse|data/analysis.json|eval|freeze, analyse, fit, figures, consistency"
)

artifact_of() { for row in "${STAGES[@]}"; do IFS='|' read -r k a _ _ <<<"$row"; [[ $k == "$1" ]] && { echo "$a"; return; }; done; }
needs_of()    { for row in "${STAGES[@]}"; do IFS='|' read -r k _ n _ <<<"$row"; [[ $k == "$1" ]] && { echo "$n"; return; }; done; }
label_of()    { for row in "${STAGES[@]}"; do IFS='|' read -r k _ _ l <<<"$row"; [[ $k == "$1" ]] && { echo "$l"; return; }; done; }

have() { [[ -e "$1" ]]; }

require() {
  local dep; dep="$(needs_of "$1")"
  [[ -z "$dep" ]] && return 0
  local art; art="$(artifact_of "$dep")"
  have "$art" && return 0
  echo "stage '$1' reads $art, which does not exist yet." >&2
  echo "Run:  bash scripts/pipeline.sh $dep" >&2
  exit 1
}

run() {
  if (( DRY )); then echo "  $*"; return 0; fi
  echo "+ $*"
  "$@"
}

# ── status ──────────────────────────────────────────────────────────────────
if [[ "$STAGE" == "status" ]]; then
  echo
  printf '%-14s %-9s %-34s %s\n' STAGE STATE ARTIFACT WHAT
  printf '%-14s %-9s %-34s %s\n' "-----" "-----" "--------" "----"
  next=""
  for row in "${STAGES[@]}"; do
    IFS='|' read -r key art _ label <<<"$row"
    if have "$art"; then state="done"
    else state="--"; [[ -z "$next" ]] && next="$key"; fi
    printf '%-14s %-9s %-34s %s\n' "$key" "$state" "$art" "$label"
  done
  echo
  if [[ -n "$next" ]]; then
    echo "next: bash scripts/pipeline.sh $next"
  else
    echo "every stage has produced its artifact."
  fi
  # A sharded stage is 'done' the moment its merged artifact exists, which says
  # nothing about coverage - that is the merge audit's job, not a file test.
  if have data/screen_merged.json || have data/episodes.jsonl; then
    echo "coverage of the sharded stages is audited, not inferred:"
    have data/screen_merged.json && echo "  python3 scripts/consolidate_screens.py"
    have data/episodes.jsonl     && echo "  python3 scripts/consolidate_evals.py --dry-run"
  fi
  echo
  exit 0
fi

# ── the stages ──────────────────────────────────────────────────────────────
case "$STAGE" in

  benchmark)
    run python3 scripts/fetch_condefects.py "$@"
    ;;

  candidates)
    require candidates
    # Stage 0 decides which faults the whole study is ever allowed to see, and
    # its output order is the seeded stratified traversal every later index
    # range is cut from - so it is run once and then treated as frozen.
    run python3 scripts/select_candidates.py "$@"
    ;;

  gate)
    require gate
    run bash scripts/oracle_gate.sh "$@"
    ;;

  screen)
    require screen
    if [[ "${1:-}" == "--merge" ]]; then
      shift
      run python3 scripts/consolidate_screens.py "$@"
    elif [[ $# -eq 0 ]]; then
      echo "the screen is sharded: give it an index range over data/candidates.json." >&2
      echo "  bash scripts/pipeline.sh screen --from 1 --to 132" >&2
      echo "  bash scripts/pipeline.sh screen --merge        # once every shard is in" >&2
      echo "RUNBOOK.md is the runbook." >&2
      exit 2
    else
      run bash scripts/screen_shard.sh "$@"
    fi
    ;;

  corpus)
    require corpus
    # --min-calls is read off the merged report, never retyped. pi_hat lives on
    # a grid of 1/K: ask for a depth the screen never reached and select_corpus
    # drops every task below it; ask for less and a band whose edges enclose no
    # multiple of 1/K comes out empty with nothing to say so.
    K="$(python3 -c "import json;print(json.load(open('data/screen_merged.json'))['min_calls_per_program'])")"
    echo "screen depth reached: K = $K  (data/screen_merged.json)"
    run python3 scripts/select_corpus.py \
        --pool data/pool/tasks.json \
        --screen data/screen_merged.json \
        --min-calls "$K" "$@"
    run python3 scripts/build_strata.py
    ;;

  pool-strength)
    require pool-strength
    run python3 scripts/measure_pool_strength.py "$@"
    ;;

  eval)
    require eval
    if [[ "${1:-}" == "--merge" ]]; then
      shift
      run python3 scripts/consolidate_evals.py "$@"
    elif [[ $# -eq 0 ]]; then
      echo "the grid is sharded by experiment and index range." >&2
      echo "  bash scripts/pipeline.sh eval --exp trial" >&2
      echo "  bash scripts/pipeline.sh eval --exp E1 --from 1 --to 30" >&2
      echo "  bash scripts/pipeline.sh eval --merge          # once every shard is in" >&2
      echo "RUNBOOK.md is the runbook; --exp -h lists the presets." >&2
      exit 2
    else
      run bash scripts/eval_shard.sh "$@"
    fi
    ;;

  analyse)
    require analyse
    # Order matters: the freeze needs the strata, fit_theory needs the frozen
    # results, and build_strata's drift audit needs fit_theory.
    run python3 scripts/freeze_results.py --experiment main
    run python3 scripts/analyze.py
    run python3 scripts/fit_theory.py
    run python3 scripts/build_strata.py --force
    run python3 scripts/measure_coherence.py
    run python3 scripts/measure_anchoring.py
    # Post-hoc and free: every one of these reads the round log and the sources
    # already on disk. No model calls.
    run python3 scripts/measure_redundancy.py
    run python3 scripts/measure_patch_quality.py
    run python3 figures/make_figures.py
    run python3 scripts/check_consistency.py
    # NOT run here: scripts/measure_typing_coherence.py. It re-executes every
    # logged patch against the full test pool, which is hours of sandbox time
    # rather than seconds, and it takes caps that have to be chosen rather than
    # defaulted. Run it deliberately - RUNBOOK.md SS8.
    echo
    echo "next, deliberately (not part of this stage - hours of sandbox time):"
    echo "  python3 scripts/measure_typing_coherence.py --limit-tasks 20   # pilot first"
    ;;

  *)
    echo "unknown stage: $STAGE" >&2
    echo "stages: $(for row in "${STAGES[@]}"; do IFS='|' read -r k _ _ _ <<<"$row"; printf '%s ' "$k"; done)" >&2
    echo "  bash scripts/pipeline.sh status" >&2
    exit 2
    ;;
esac
