#!/usr/bin/env bash
#
# Install Ollama on a Colab (or any Debian/Ubuntu) runtime and pull the local
# proposer's weights, so that scripts/reproduce.sh can start its own server when
# a local cell has to run. Nothing here is needed when every local cell is a hit.
#
#   bash scripts/install_ollama_colab.sh                 # qwen2.5-coder:7b
#   MODEL=qwen2.5-coder:7b bash scripts/install_ollama_colab.sh
#
# The server itself is NOT left running: scripts/eval_shard.sh starts one on
# $PORT with the context window pinned (OLLAMA_CONTEXT_LENGTH) and verified
# (scripts/serve_local.sh), and tears it down afterwards. Pulling the weights
# needs a server, so one is started here on the same port for the pull only and
# stopped again. Re-running is cheap: the installer is skipped when `ollama` is
# already on PATH and the pull is a no-op when the weights are present.
set -euo pipefail

MODEL="${MODEL:-qwen2.5-coder:7b}"
PORT="${PORT:-11435}"
HOST="127.0.0.1:${PORT}"

if ! command -v ollama >/dev/null; then
  # The installer unpacks a zstd-compressed bundle; Colab images do not ship zstd.
  if command -v apt-get >/dev/null; then
    apt-get -qq install -y zstd curl lsof >/dev/null || sudo apt-get -qq install -y zstd curl lsof >/dev/null
  fi
  echo "+ curl -fsSL https://ollama.com/install.sh | sh"
  curl -fsSL https://ollama.com/install.sh | sh
fi
echo "ollama: $(command -v ollama) ($(ollama --version 2>/dev/null | head -1))"

started=0
if ! curl -sf "http://${HOST}/api/tags" >/dev/null 2>&1; then
  echo "+ OLLAMA_HOST=${HOST} ollama serve   (for the pull only)"
  OLLAMA_HOST="$HOST" nohup ollama serve >/tmp/ollama-install.log 2>&1 &
  started=1
  for _ in $(seq 1 40); do
    curl -sf "http://${HOST}/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "http://${HOST}/api/tags" >/dev/null 2>&1 || { echo "ollama did not come up - see /tmp/ollama-install.log" >&2; exit 1; }
fi

echo "+ OLLAMA_HOST=${HOST} ollama pull ${MODEL}"
OLLAMA_HOST="$HOST" ollama pull "$MODEL"
OLLAMA_HOST="$HOST" ollama list | grep -F "${MODEL%%:*}" || true

if (( started )); then
  PIDS="$(lsof -t -i ":${PORT}" -sTCP:LISTEN 2>/dev/null || pgrep -f "ollama serve" || true)"
  [[ -n "$PIDS" ]] && kill $PIDS 2>/dev/null || true
  echo "stopped the pull-only server; scripts/eval_shard.sh starts its own with the window pinned"
fi
echo "ready: ${MODEL} is pulled. Nothing is served; nothing needs to be."
