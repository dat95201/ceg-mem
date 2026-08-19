#!/usr/bin/env bash
#
# Bring up the local proposer for E1-E5, with the context window pinned AND
# verified. Leaves the server running, because the experiment driver is a
# separate long-lived process.
#
#   bash scripts/serve_local.sh          # start (or adopt) and verify
#   bash scripts/serve_local.sh --check  # verify only, start nothing
#   bash scripts/serve_local.sh --unload # unload the weights, leave the server up
#   bash scripts/serve_local.sh --stop   # unload, and stop whatever serves the port
#
# scripts/eval_shard.sh drives all four of these around one shard, so a run
# started by hand and a run started by a shard verify the same way. Use this
# script directly when chaining several things against one warm server.
#
# Why a script and not just .env
# ------------------------------
# .env carries the client half of the protocol (MODEL, TEMPERATURE,
# LLM_CONTEXT_TOKENS, prices). It cannot carry the half that decides whether a
# prompt arrived whole: Ollama picks the context window from available VRAM -
# "4k/32k/256k", per `ollama serve --help` - and TRUNCATES an over-long prompt
# instead of refusing it. That window reaches neither src.llm's cache key nor
# RoundRecord.model, so the same model id on two machines can be two different
# instruments with nothing to say so. A desktop Ollama on this machine serves
# this model at 4096.
#
# The memory arms carry accumulated evidence and so have the longest prompts,
# which means silent truncation lands hardest on exactly the comparison being
# measured. So the window is asserted, not assumed - the model is loaded,
# /api/ps is asked what is actually being served, and anything but
# CONTEXT_LENGTH is a hard stop. Same check scripts/screen_shard.sh runs; the
# screen and the experiment have to be the same instrument.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── PROTOCOL - must match data/tasks.json and SCREENING.md §1 ───────────────
MODEL="${MODEL:-qwen2.5-coder:7b}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-32768}"
PORT="${PORT:-11435}"                        # own port, not the desktop app's

HOST="127.0.0.1:${PORT}"
URL="http://${HOST}"
MODE="start"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)  MODE="check";  shift ;;
    --unload) MODE="unload"; shift ;;
    --stop)   MODE="stop";   shift ;;
    --port)  PORT="$2"; HOST="127.0.0.1:${PORT}"; URL="http://${HOST}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v ollama >/dev/null || { echo "ollama not on PATH" >&2; exit 2; }
command -v curl   >/dev/null || { echo "curl not on PATH" >&2; exit 2; }

if [[ "$MODE" == "unload" || "$MODE" == "stop" ]]; then
  # Unloading the weights and stopping the server are two different things, and
  # on a shared machine the first is the one that matters: the server process is
  # a few MB, the resident model is 6.4 GB. So --unload is the polite default
  # for a run that borrowed someone else's server, and --stop is for one that
  # started its own.
  OLLAMA_HOST="$HOST" ollama stop "$MODEL" >/dev/null 2>&1 || true
  echo "unloaded $MODEL"
  if [[ "$MODE" == "stop" ]]; then
    # -sTCP:LISTEN, not every socket on the port: an unqualified lsof also lists
    # CLIENTS connected to it - a running driver, another shard's python - and
    # SIGTERMing those would kill the very run that asked for the teardown.
    PIDS="$(lsof -t -i ":${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$PIDS" ]]; then
      # shellcheck disable=SC2086
      kill $PIDS 2>/dev/null || true
      echo "stopped the server on ${HOST}"
    fi
  fi
  exit 0
fi

if curl -sf "${URL}/api/tags" >/dev/null 2>&1; then
  echo "server already on ${HOST} - adopting it"
elif [[ "$MODE" == "check" ]]; then
  echo "nothing serving on ${HOST}" >&2; exit 1
else
  echo "starting ollama on ${HOST} with OLLAMA_CONTEXT_LENGTH=${CONTEXT_LENGTH}"
  mkdir -p logs
  # nohup + disown: the driver outlives this script by days, so the server must
  # not be a child that dies with the shell that launched it.
  OLLAMA_HOST="$HOST" \
  OLLAMA_CONTEXT_LENGTH="$CONTEXT_LENGTH" \
  OLLAMA_NUM_PARALLEL=1 \
  OLLAMA_KEEP_ALIVE=60m \
    nohup ollama serve >>logs/ollama.log 2>&1 &
  disown || true
  for _ in $(seq 1 30); do
    curl -sf "${URL}/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "${URL}/api/tags" >/dev/null 2>&1 || {
    echo "server did not come up - see logs/ollama.log" >&2; exit 1; }
fi

if ! curl -sf "${URL}/api/tags" | python3 -c "
import json, sys
want = '$MODEL' if ':' in '$MODEL' else '$MODEL:latest'
sys.exit(0 if any(m['name'] == want for m in json.load(sys.stdin)['models']) else 1)"; then
  [[ "$MODE" == "check" ]] && { echo "$MODEL is not on ${HOST}" >&2; exit 1; }
  echo "$MODEL is not on this server - pulling it"
  OLLAMA_HOST="$HOST" ollama pull "$MODEL"
else
  echo "$MODEL already present - nothing to download"
fi

echo "loading $MODEL and verifying the served window"
curl -sf "${URL}/api/generate" \
  -d "{\"model\":\"${MODEL}\",\"prompt\":\"ok\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null

RUNTIME="$(python3 - "$URL" "$MODEL" "$CONTEXT_LENGTH" <<'PY'
import json, sys, urllib.request
url, model, want = sys.argv[1], sys.argv[2], int(sys.argv[3])
ps = json.load(urllib.request.urlopen(url + "/api/ps"))
m = next((x for x in ps.get("models", []) if x["model"] == model
          or x["model"] == model + ":latest"), None)
if m is None:
    sys.exit(f"{model} is not loaded on {url} - nothing to verify")
got = m.get("context_length")
if got != want:
    sys.exit(
        f"served context is {got}, not {want}. The prompt would be silently\n"
        f"cropped, worst on the arms carrying the most evidence. Restart the\n"
        f"server as:\n"
        f"  OLLAMA_HOST={url.removeprefix('http://')} "
        f"OLLAMA_CONTEXT_LENGTH={want} OLLAMA_NUM_PARALLEL=1 ollama serve\n")
print(json.dumps({"backend": "ollama", "context_length": got,
                  "model_digest": (m.get("digest") or "")[:16],
                  "quantization": (m.get("details") or {}).get("quantization_level")},
                 sort_keys=True))
PY
)"
echo "  ok: $RUNTIME"
echo
echo "ready. .env already points at ${URL} with MODEL=${MODEL}."
echo "next: EXPERIMENT.md - the run order, or bash scripts/eval_shard.sh --exp trial."
