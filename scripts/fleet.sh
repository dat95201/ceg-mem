#!/usr/bin/env bash
#
# A fleet: N shards of one stage, launched into the background on one machine,
# and one place to ask where they are.
#
#   bash scripts/fleet.sh screen --shards 5 --from 401 --to 450 --calls 40
#   bash scripts/fleet.sh eval   --exp E1 --shards 6
#   bash scripts/fleet.sh eval   --exp E2 --shards 4 -- --check-regression
#   bash scripts/fleet.sh status
#   bash scripts/fleet.sh tail 3          # or `tail` for every shard's last lines
#   bash scripts/fleet.sh wait            # blocks; the only command that does
#   bash scripts/fleet.sh stop
#
# Launching returns immediately - that is the whole point on a Colab runtime,
# where a blocking cell is a cell you cannot use. Nothing here talks to a model,
# cuts a corpus or writes an episode: every shard is the same screen_shard.sh or
# eval_shard.sh invocation you would have typed by hand, with the same protocol
# pins, the same cache and the same artifacts. What this script owns is the four
# things that go wrong when you type six of those into a notebook cell.
#
#   ranges     Contiguous, non-overlapping, and covering [FROM, TO] exactly.
#              Hand-typed ranges are off by one eventually, and neither mistake
#              announces itself: a skipped index turns up in the merge audit
#              days later, and an overlapped one is silently re-run work whose
#              only symptom is time. The universe size is never written down
#              here either - see `universe_size_*` below.
#
#   the model  Every shard gets --keep-serving AND --no-stop-model, and the
#              weights are unloaded once, by `wait`, after the last shard exits.
#              This is not tidiness, it is the bug this script exists for. Both
#              shard scripts unload the model on exit BY DEFAULT and on purpose
#              - "a shard that walks away leaving 6.4 GB pinned on a machine it
#              did not start is a bad guest" - and that default is applied even
#              when the server was already there and the shard is only
#              borrowing it. So the FIRST of six shards to finish runs
#              `ollama stop` on the model the other five are still calling, and
#              each of them then pays a cold reload. --keep-serving alone does
#              NOT prevent this: it keeps the server process, not the resident
#              weights. The two flags are different tear-downs.
#
#   the cap    src.llm.spent() reads only its own process's ledger, and every
#              shard is given its own ledger. Six shards each honouring
#              BUDGET_USD_CAP=25 is a real ceiling of $150. On --backend cloud
#              the cap is divided by the shard count and the arithmetic is
#              printed. On the local backend calls are priced at zero and the
#              cap is a tripwire, so this does nothing there.
#
#   the books  One log per shard under logs/fleet/<run>/, plus a manifest saying
#              which pid is walking which range under which command. `output2.log`
#              beside `output11.log` stops being a record of anything the moment
#              the session that named them is gone - and on Colab that is hours.
#
# Sizing a fleet. Ollama is started with OLLAMA_NUM_PARALLEL=1, so shards do not
# get parallel *generation* - their model calls queue at the server. What does
# overlap is everything else: the oracle runs each candidate patch in a sandbox
# subprocess, and that is CPU, off the GPU's critical path. So the useful shard
# count is bounded by cores, not by VRAM, and the gain flattens quickly. Four to
# six is the honest range on a Colab T4; twelve mostly buys context switches.
#
# One fleet at a time. A second launch refuses while shards are still alive
# rather than interleaving two manifests - `stop` first, or `wait`.
#
# Not a substitute for the merge audit. A fleet that reports every shard `done`
# says every process exited 0, nothing more. Coverage is still
# `consolidate_screens.py` / `consolidate_evals.py --dry-run`, as RUNBOOK says.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. scripts/run_dir_paths.sh

FLEET_ROOT="$RUN_LOGS/fleet"
LATEST_PTR="$FLEET_ROOT/LATEST"      # a file, not a symlink: logs/ is often a
                                     # Drive mount, and Drive and symlinks are
                                     # a bad pair.
PORT="${PORT:-11435}"
STAGGER="${STAGGER:-5}"

die() { echo "fleet: $*" >&2; exit 2; }

usage() {
  sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'USAGE'

  screen  --shards N [--from A --to B] [--calls K] [--port P] [--stagger S] [-- ...]
  eval    --exp NAME --shards N [--from A --to B] [--port P] [--stagger S] [-- ...]

      --from/--to   1-based inclusive, over that stage's own universe. Omit both
                    to take the whole of it; the size is asked of the stage
                    rather than written down here.
      --shards N    how many background shards to cut the range into.
      --stagger S   seconds between launches (default 5). Not cosmetic: N shards
                    starting in the same second all probe the server at once.
      --            everything after it is forwarded verbatim to the shard
                    script - --check-regression, --budget, --seeds, --backend
                    cloud, --model, whatever. This script deliberately does not
                    know that list.

  status            one line per shard: alive, progress, elapsed.
  tail [N]          follow shard N (Ctrl-C to stop), or the last lines of all.
  wait [--no-unload]   block until the fleet drains, then unload the weights.
  stop              SIGTERM the fleet. Shards resume on an identical re-run.

Read RUNBOOK.md for what the stages mean. This only runs them side by side.
USAGE
}

# ── where a universe size comes from ────────────────────────────────────────
# Not from here. For the screen it is the length of the frozen candidate list;
# for the grid it is whatever eval_shard.sh says its own universe is, asked in
# --dry-run and read off the line it prints. Two scripts holding two opinions
# about how big a universe is would cut ranges past the end of it, and the one
# that owns the answer is the one that walks it.
universe_size_screen() {
  [[ -f "$RUN_DATA/candidates.json" ]] || die "$RUN_DATA/candidates.json missing - run: bash scripts/pipeline.sh candidates"
  python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['candidates']))" "$RUN_DATA/candidates.json"
}

# A dry-run proves the ARGUMENTS are good. It exits before eval_shard.sh ever
# touches the model server, so it cannot prove the run will start - which is how
# six shards came to fork and all exit 2 within a second of each other, with
# nothing on the status board but "exit 2" six times. Check the thing the shards
# will need, once, here, where one error message is cheaper than six log files.
# $1 = port, $2 = backend. Both passed explicitly, and that is the whole point:
# the first version of this parsed --port out of the FORWARDED args, but fleet
# consumes --port itself into $PORT and only forwards what comes after a bare
# `--`. So it always probed the 11434 default and reported "nothing is serving
# on 127.0.0.1:11434" at a user whose server was up on 11435.
preflight_backend() {
  local port="$1" backend="$2"
  [[ "$backend" == "ollama" ]] || return 0     # cloud: src.llm validates its own key

  # Reachable is the only thing that matters. A shard talks HTTP to this port;
  # whether the `ollama` binary is on PATH is irrelevant once something is
  # answering, and checking PATH first turned a healthy server into a refusal.
  if curl -sf "http://127.0.0.1:${port}/api/tags" >/dev/null 2>&1; then
    return 0
  fi

  # Not reachable. A shard would try to start its own server - six of them
  # racing to do that is not a plan, and if the start fails they all exit at
  # once, which is a status board of bare exit codes and no reason.
  command -v ollama >/dev/null || die "nothing is serving on 127.0.0.1:${port}, and ollama is not on PATH.
On Colab the runtime was probably recycled - re-run the ollama install and serve
cells (section 9) before fanning out."
  die "ollama is on PATH but nothing is answering on 127.0.0.1:${port}.
Re-run the serve cell, or point --port at the one that is up. To check:
  curl -s http://127.0.0.1:${port}/api/tags | head -c 200"
}

universe_size_eval() {          # $1 = exp; rest forwarded so --backend etc. apply
  local exp="$1"; shift
  # $PORT is fleet's own global (set from --port); $backend is the caller's
  # local, computed from the forwarded args, visible here by dynamic scope.
  preflight_backend "${PORT:-11435}" "${backend:-ollama}"
  local out n
  out="$(bash scripts/eval_shard.sh --exp "$exp" --dry-run "$@" 2>&1)" || {
    echo "$out" >&2; die "eval_shard.sh --dry-run refused --exp $exp; fix that before fanning it out"; }
  n="$(sed -n 's/^shard  *[0-9]*-[0-9]* of \([0-9]*\) .*/\1/p' <<<"$out" | head -1)"
  [[ "$n" =~ ^[0-9]+$ ]] || { echo "$out" >&2; die "could not read the universe size out of --dry-run"; }
  echo "$n"
}

# ── the split ───────────────────────────────────────────────────────────────
# The remainder goes to the FIRST shards, one index each, never all onto the
# last: a fat tail is the shard still going when the other five have finished,
# which is the one thing a fleet is supposed to avoid.
plan_ranges() {                 # FROM TO SHARDS -> "from to" per line
  local from=$1 to=$2 n=$3
  local span=$(( to - from + 1 ))
  (( span >= 1 ))  || die "empty range: --from $from --to $to"
  (( n >= 1 ))     || die "--shards must be at least 1"
  (( n <= span ))  || die "--shards $n over a range of $span index(es) - at most one each"
  local base=$(( span / n )) rem=$(( span % n )) cur=$from i size
  for (( i = 0; i < n; i++ )); do
    size=$(( base + (i < rem ? 1 : 0) ))
    echo "$cur $(( cur + size - 1 ))"
    cur=$(( cur + size ))
  done
}

run_dir() {
  [[ -f "$LATEST_PTR" ]] || die "no fleet has been launched from this checkout yet"
  local d; d="$(cat "$LATEST_PTR")"
  [[ -d "$d" ]] || die "$LATEST_PTR points at $d, which is gone"
  echo "$d"
}

alive() { kill -0 "$1" 2>/dev/null; }

fleet_is_running() {
  [[ -f "$LATEST_PTR" ]] || return 1
  local d; d="$(cat "$LATEST_PTR")"
  [[ -f "$d/manifest.tsv" ]] || return 1
  local pid
  while IFS=$'\t' read -r _ _ _ pid _; do alive "$pid" && return 0; done < "$d/manifest.tsv"
  return 1
}

# ── launch ──────────────────────────────────────────────────────────────────
cmd_launch() {
  local stage="$1"; shift
  local exp="" from="" to="" shards="" calls="" passthru=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --exp)     exp="$2"; shift 2 ;;
      --from)    from="$2"; shift 2 ;;
      --to)      to="$2"; shift 2 ;;
      --shards)  shards="$2"; shift 2 ;;
      --calls)   calls="$2"; shift 2 ;;
      --port)    PORT="$2"; shift 2 ;;
      --stagger) STAGGER="$2"; shift 2 ;;
      --)        shift; passthru=("$@"); break ;;
      *) die "unknown option '$1' for '$stage' - forward shard flags after a bare --" ;;
    esac
  done
  [[ -n "$shards" ]] || die "--shards is required"
  [[ "$stage" != eval || -n "$exp" ]] || die "eval needs --exp (see: bash scripts/eval_shard.sh -h)"
  fleet_is_running && die "a fleet is still running - 'fleet.sh status', then 'stop' or 'wait'"

  local fwd=(); [[ ${#passthru[@]} -gt 0 ]] && fwd=("${passthru[@]}")

  # The cap first, then the universe. Deliberately this order: probing the
  # universe on --backend cloud makes a credentialed call, so a missing
  # BUDGET_USD_CAP would otherwise surface as eval_shard.sh's key error and send
  # you looking for the wrong thing.
  local backend=ollama i
  for (( i = 0; i < ${#fwd[@]}; i++ )); do
    [[ "${fwd[i]}" == "--backend" && "${fwd[i+1]:-}" == "cloud" ]] && backend=cloud
  done
  local cap_note="local backend: calls priced at 0, so the cap is a tripwire and division is moot"
  local per_shard_cap=""
  if [[ "$backend" == cloud ]]; then
    [[ -n "${BUDGET_USD_CAP:-}" ]] || die "--backend cloud needs BUDGET_USD_CAP set to the TOTAL for the whole fleet - it is divided by --shards here, because src.llm.spent() only ever sees one shard's ledger"
    per_shard_cap="$(python3 -c "print(f'{float(\"$BUDGET_USD_CAP\")/$shards:.6f}')")" \
      || die "BUDGET_USD_CAP=$BUDGET_USD_CAP is not a number"
    cap_note="cloud: total \$$BUDGET_USD_CAP / $shards shards = \$$per_shard_cap per shard"
  fi

  # Universe, then range.
  local total
  if [[ "$stage" == screen ]]; then total="$(universe_size_screen)"
  else                              total="$(universe_size_eval "$exp" ${fwd[@]+"${fwd[@]}"})"; fi
  from="${from:-1}"; to="${to:-$total}"
  (( to <= total )) || die "--to $to is past the end of this universe ($total)"

  # The screen's depth is a protocol pin, and omitting it is silent: pi_hat lives
  # on a grid of 1/K, so a fleet run at screen_shard.sh's default K=10 cannot put
  # anything in `hard` = [0.02, 0.08) at all - the band the paper predicts its
  # largest effect in. Warned, not refused: a shallow pilot is a legitimate thing
  # to want, just never by accident.
  if [[ "$stage" == screen && -z "$calls" ]]; then
    local reached=""
    [[ -f "$RUN_DATA/screen_merged.json" ]] && reached="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1])).get('min_calls_per_program',''))" "$RUN_DATA/screen_merged.json" 2>/dev/null || true)"
    echo "  WARNING   no --calls given, so every shard takes screen_shard.sh's default depth."
    [[ -n "$reached" ]] && echo "            $RUN_DATA/screen_merged.json was measured at K=$reached - pass --calls $reached to match it."
    echo
  fi

  local run="$FLEET_ROOT/${stage}${exp:+-$exp}-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$run"
  : > "$run/manifest.tsv"
  { echo "stage=$stage"; echo "exp=$exp"; echo "from=$from"; echo "to=$to"
    echo "shards=$shards"; echo "port=$PORT"; echo "backend=$backend"
    echo "forwarded=${fwd[*]-}"; } > "$run/meta"

  # An array, not ${var:+word}: `0` is a NON-EMPTY string, so ${use_setsid:+setsid}
  # expands to setsid even when setsid is missing - which is a command-not-found
  # for every shard, at launch, with the reason buried in a per-shard log.
  local use_setsid=0; local -a SETSID=()
  if command -v setsid >/dev/null; then use_setsid=1; SETSID=(setsid); fi

  printf '\n%s fleet: %s%s  range %s-%s of %s  into %s shard(s)\n' \
         "$(date -u +%H:%M:%SZ)" "$stage" "${exp:+ $exp}" "$from" "$to" "$total" "$shards"
  echo "  budget    $cap_note"
  echo "  server    --keep-serving --no-stop-model on every shard; 'fleet.sh wait' unloads once at the end"
  echo "  logs      $run/"
  (( use_setsid )) || echo "  NOTE      setsid not found: 'stop' can only signal the shard, not its children"
  echo

  local idx=0 f t cmd
  while read -r f t; do
    idx=$(( idx + 1 ))
    local log="$run/$(printf '%02d' "$idx").log"
    if [[ "$stage" == screen ]]; then
      cmd=(bash scripts/screen_shard.sh --from "$f" --to "$t" --port "$PORT")
      [[ -n "$calls" ]] && cmd+=(--calls "$calls")
    else
      cmd=(bash scripts/eval_shard.sh --exp "$exp" --from "$f" --to "$t" --port "$PORT")
    fi
    # Forced, not defaulted: see "the model" in the header. A caller who passes
    # them again does no harm - both scripts take the last occurrence.
    cmd+=(--keep-serving --no-stop-model)
    cmd+=(${fwd[@]+"${fwd[@]}"})

    # Wrapped so the shard's real exit status lands in a file. Reading it out of
    # the log instead does not work and quietly mis-reports: eval_shard.sh prints
    # "shard exited with status N" from its trap, screen_shard.sh prints nothing
    # of the kind, so a screen shard that died on "ollama not on PATH" reads as a
    # clean `done`. A wrong green is worse than no colour at all.
    local rc="$run/$(printf '%02d' "$idx").rc"
    local runner='rc_file="$1"; shift; "$@"; echo "$?" > "$rc_file"'
    if [[ -n "$per_shard_cap" ]]; then
      BUDGET_USD_CAP="$per_shard_cap" nohup ${SETSID[@]+"${SETSID[@]}"} \
        bash -c "$runner" _ "$rc" "${cmd[@]}" > "$log" 2>&1 < /dev/null &
    else
      nohup ${SETSID[@]+"${SETSID[@]}"} \
        bash -c "$runner" _ "$rc" "${cmd[@]}" > "$log" 2>&1 < /dev/null &
    fi
    local pid=$!
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$idx" "$f" "$t" "$pid" "$use_setsid" "${cmd[*]}" \
      >> "$run/manifest.tsv"
    printf '  shard %02d  %4s-%-4s  pid %-7s  %s\n' "$idx" "$f" "$t" "$pid" "$(basename "$log")"
    (( idx < shards )) && sleep "$STAGGER"
  done < <(plan_ranges "$from" "$to" "$shards")

  echo "$run" > "$LATEST_PTR"
  echo
  echo "launched; this command is done. Next:"
  echo "  bash scripts/fleet.sh status"
  echo "  bash scripts/fleet.sh tail 1"
}

# ── status ──────────────────────────────────────────────────────────────────
cmd_status() {
  local run; run="$(run_dir)"
  echo; sed 's/^/  /' "$run/meta"; echo
  printf '  %-3s %-11s %-8s %-8s %-9s %s\n' '#' 'RANGE' 'PID' 'STATE' 'ELAPSED' 'PROGRESS'
  printf '  %-3s %-11s %-8s %-8s %-9s %s\n' '-' '-----' '---' '-----' '-------' '--------'
  local idx f t pid _s cmd n_alive=0 n_done=0 n_fail=0
  while IFS=$'\t' read -r idx f t pid _s cmd; do
    local log="$run/$(printf '%02d' "$idx").log" state elapsed prog
    if alive "$pid"; then
      state=running; n_alive=$(( n_alive + 1 ))
      elapsed="$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ' || echo '?')"
    else
      elapsed='-'
      local rc="$run/$(printf '%02d' "$idx").rc"
      if [[ -f "$rc" ]] && [[ "$(cat "$rc")" == 0 ]]; then
        state=done; n_done=$(( n_done + 1 ))
      elif [[ -f "$rc" ]]; then
        state="exit $(cat "$rc")"; n_fail=$(( n_fail + 1 ))
      else
        # No pid and no recorded status: killed, or the runtime went away under
        # it. Distinct from a non-zero exit, and worth distinguishing - the first
        # is something you did, the second is something the shard found.
        state=KILLED; n_fail=$(( n_fail + 1 ))
      fi
    fi
    # `|| true` on both, and it is load-bearing: this script runs under
    # `set -o pipefail`, so a grep that simply finds nothing yet - which is every
    # shard for its first minute - fails the whole pipeline, fails the
    # assignment, and `set -e` then kills the status report before it prints a
    # single row. A missing match is the normal case here, not an error.
    prog="$(grep -oE '^\[ *[0-9]+/[0-9]+\]' "$log" 2>/dev/null | tail -1 | tr -d '[] ' || true)"
    [[ -n "$prog" ]] || prog="$(grep -oE '\[task *[0-9]+/[0-9]+\]' "$log" 2>/dev/null | tail -1 | tr -d '[]' | sed 's/task *//' || true)"
    printf '  %-3s %-11s %-8s %-8s %-9s %s\n' "$idx" "$f-$t" "$pid" "$state" "$elapsed" "${prog:-—}"
  done < "$run/manifest.tsv"
  echo
  echo "  $n_alive running · $n_done done · $n_fail failed"
  if (( n_alive == 0 )); then
    echo
    echo "  the fleet has drained. Coverage is NOT proven by that - audit it:"
    [[ "$(sed -n 's/^stage=//p' "$run/meta")" == screen ]] \
      && echo "    python3 scripts/consolidate_screens.py" \
      || echo "    python3 scripts/consolidate_evals.py --dry-run"
  fi
  if (( n_fail > 0 )); then
    echo "  a failed shard resumes on an identical re-run - read its log first: bash scripts/fleet.sh tail <n>"
    # Every shard failing is one fault, not N. Print it here rather than making
    # the reader open six identical logs to find one line.
    if (( n_alive == 0 && n_done == 0 )); then
      local first
      first="$(ls "$run"/*.log 2>/dev/null | head -1)"
      if [[ -n "$first" ]]; then
        echo
        echo "  EVERY shard failed, so this is one fault. Tail of $(basename "$first"):"
        sed -e 's/^/    | /' <(tail -n 15 "$first")
      fi
    fi
  fi
}

cmd_tail() {
  local run; run="$(run_dir)"
  if [[ $# -ge 1 ]]; then
    local log="$run/$(printf '%02d' "$1").log"
    [[ -f "$log" ]] || die "no such shard: $1"
    exec tail -f "$log"
  fi
  local l
  for l in "$run"/*.log; do
    echo "───── $(basename "$l") ─────"; tail -4 "$l"; echo
  done
  echo "follow one with: bash scripts/fleet.sh tail <n>"
}

cmd_wait() {
  local unload=1
  [[ "${1:-}" == "--no-unload" ]] && unload=0
  local run; run="$(run_dir)"
  local last=-1
  while :; do
    local n=0 pid
    while IFS=$'\t' read -r _ _ _ pid _; do alive "$pid" && n=$(( n + 1 )); done < "$run/manifest.tsv"
    (( n == 0 )) && break
    (( n == last )) || printf '%s  %d shard(s) still running\n' "$(date -u +%H:%M:%SZ)" "$n"
    last=$n
    sleep 20
  done
  echo "$(date -u +%H:%M:%SZ)  fleet drained"
  if (( unload )) && [[ "$(sed -n 's/^backend=//p' "$run/meta")" != cloud ]]; then
    # The one unload for the whole fleet - the reason every shard was told not
    # to do it itself.
    bash scripts/serve_local.sh --unload --port "$PORT" || true
  fi
  cmd_status
}

cmd_stop() {
  local run; run="$(run_dir)"
  local idx pid s cmd n=0
  while IFS=$'\t' read -r idx _ _ pid s cmd; do
    alive "$pid" || continue
    if [[ "$s" == 1 ]]; then kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    else                     kill -TERM "$pid" 2>/dev/null || true; fi
    echo "  sent TERM to shard $idx (pid $pid)"; n=$(( n + 1 ))
  done < "$run/manifest.tsv"
  (( n )) || echo "  nothing was running"
  echo "  shards resume on an identical re-run: every model call already bought replays from cache."
}

# ── dispatch ────────────────────────────────────────────────────────────────
case "${1:-}" in
  screen|eval) stage="$1"; shift; mkdir -p "$FLEET_ROOT"; cmd_launch "$stage" "$@" ;;
  status)      shift; cmd_status ;;
  tail)        shift; cmd_tail "$@" ;;
  wait)        shift; cmd_wait "$@" ;;
  stop)        shift; cmd_stop ;;
  -h|--help|"") usage ;;
  *) echo "unknown command: $1" >&2; usage >&2; exit 2 ;;
esac
