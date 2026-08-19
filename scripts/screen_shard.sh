#!/usr/bin/env bash
#
# One shard of the pi screen. Reported data, not a smoke test.
#
#   bash scripts/screen_shard.sh --from 1 --to 130
#   bash scripts/screen_shard.sh --from 1 --to 130 --calls 50   # later, deeper
#
# Writes, for a shard covering candidates FROM..TO of data/candidates.json:
#
#   data/screen_<from>_<to>.json        the pi_hat report
#   data/shards/shard_<from>_<to>.txt   the exact program list, with the pool digest
#   data/calls_screen_<from>_<to>.jsonl that shard's call ledger
#   logs/screen_<from>_<to>.log         the trace
#
# Indices are 1-based and inclusive, over the order in data/candidates.json -
# the seeded K_proxy-stratified round-robin from scripts/select_candidates.py.
# Any contiguous range is already balanced on type-space capacity, so a shard
# is a smaller screen and not a skewed one. Shards must not overlap.
#
# Incremental by construction
# ---------------------------
# src.llm keys a call on (model, temperature, max_tokens, nonce, prompt), and
# scripts/measure_pi.py numbers its draws `pi-pilot|<task>|seed<S>|call<i>` for
# i in 0..K-1. So re-running the same shard at a larger --calls replays every
# earlier draw from cache and buys only the difference: 10 -> 50 costs 40 new
# calls per task, not 50. Nothing else in the key moves - max_tokens is
# budget_for_source(faulty source), the prompt is that source plus the two
# worked examples Task.spec_note picks, both fixed by the checkout.
#
# Which means the four values under PROTOCOL are what you are committing to for
# the life of the screen. Change any of them later and every draw is re-bought
# at full price - they are the cache key. --max-examples is the exception in the
# other direction: it is NOT in the key, so changing it silently re-judges the
# same completions against a weaker oracle and yields a different pi_hat for
# free. That is why it is pinned here and recorded in every report.
#
# The model id is the upstream tag. Ollama then picks the context window from
# available VRAM - `ollama serve --help` says "4k/32k/256k based on VRAM" - and
# TRUNCATES an over-long prompt rather than refusing it, so the same tag on two
# machines can be two different instruments, and the difference reaches neither
# the cache key nor the report. A desktop Ollama on this machine serves this
# model at 4096.
#
# So the window is verified rather than assumed: the model is loaded, /api/ps is
# asked what is actually being served, and the run refuses to start on anything
# but CONTEXT_LENGTH. That check is what makes a shard trustworthy - not who
# started the server. A server already listening on --port is therefore reused
# (and left running); otherwise one is started here with the window pinned, and
# torn down at the end. Both paths go through the same check.
#
# Long job: run under tmux, or `nohup bash scripts/screen_shard.sh ... &`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── PROTOCOL - in src.llm's cache key, or in what pi_hat means. Identical on
#    every machine and for the life of the screen, or the shards do not merge.
MODEL="${MODEL:-qwen2.5-coder:7b}"
TEMPERATURE="${TEMPERATURE:-1.0}"
SEED="${SEED:-20260717}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-32768}"
MAX_EXAMPLES="${MAX_EXAMPLES:-100}"          # oracle strength; not in the key
SANDBOX_TIMEOUT_SEC="${SANDBOX_TIMEOUT_SEC:-30.0}"
# ── knobs that are free to differ per machine ───────────────────────────────
CALLS="${CALLS:-10}"                         # draws per task; deepen later
PORT="${PORT:-11435}"                        # own port, not the desktop app's
POOL="data/candidates.json"

FROM=""; TO=""; OUT=""; DRY_RUN=0; KEEP_SERVING=0; STOP_MODEL=1

usage() {
  cat <<'USAGE'
usage: bash scripts/screen_shard.sh --from N --to M [options]

  --from N          first candidate index, 1-based inclusive
  --to M            last candidate index, 1-based inclusive
  --calls K         draws per task (default 10). Raising it later re-uses every
                    draw already bought - see "Incremental" in this file.
  --out PATH        override the report path
  --port P          ollama port (default 11435). A server already listening
                    there is reused and left running; otherwise one is started
                    and torn down at the end. Either way the served context
                    window is verified before anything is spent.
  --stop-model      unload the model from memory on exit (default)
  --no-stop-model   leave it loaded, to warm-start the next shard
  --keep-serving    leave our own server up on exit (the process, not the weights;
                    pair it with --no-stop-model to chain shards without a reload)
  --dry-run         print the plan and the shard list, start nothing
  -h, --help        this

Pinned, and not overridable per run without also changing every other machine:
  MODEL=qwen2.5-coder:7b  TEMPERATURE=1.0  SEED=20260717
  CONTEXT_LENGTH=32768    MAX_EXAMPLES=100 SANDBOX_TIMEOUT_SEC=30.0

Merge the shards with:
  python3 scripts/consolidate_screens.py
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from)    FROM="$2"; shift 2 ;;
    --to)      TO="$2"; shift 2 ;;
    --calls)   CALLS="$2"; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --port)    PORT="$2"; shift 2 ;;
    --stop-model)    STOP_MODEL=1; shift ;;
    --no-stop-model) STOP_MODEL=0; shift ;;
    --keep-serving)  KEEP_SERVING=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$FROM" && -n "$TO" ]] || { echo "--from and --to are required" >&2; usage >&2; exit 2; }
[[ "$FROM" =~ ^[0-9]+$ && "$TO" =~ ^[0-9]+$ ]] || { echo "--from/--to must be integers" >&2; exit 2; }
# 10#: this script's own outputs are zero-padded (shard_001_132.txt), and bash
# reads a leading-zero literal as octal in (( )) and printf while the heredoc
# below reads it as decimal - so `--from 031` would screen tasks 31.. under a
# report named 025.., and `--from 085` would die blaming the range.
FROM=$((10#$FROM)); TO=$((10#$TO))
[[ -f "$POOL" ]] || { echo "$POOL missing - run scripts/select_candidates.py first" >&2; exit 2; }

N_POOL="$(python3 -c "import json;print(len(json.load(open('$POOL'))['candidates']))")"
(( FROM >= 1 && TO >= FROM && TO <= N_POOL )) || {
  echo "range $FROM-$TO is outside 1-$N_POOL" >&2; exit 2; }

TAG="$(printf '%03d_%03d' "$FROM" "$TO")"
SHARD="data/shards/shard_${TAG}.txt"
OUT="${OUT:-data/screen_${TAG}.json}"
LEDGER="data/calls_screen_${TAG}.jsonl"
LOG="logs/screen_${TAG}.log"
mkdir -p data/shards logs

# ── the shard list ──────────────────────────────────────────────────────────
# A file, not a command line. measure_pi.py records the *path* as the corpus
# source, so a shard is traceable back to the pool it was cut from; --programs
# can only record the string "--programs". The header pins the pool digest, so a
# machine on an older data/candidates.json is caught at merge time rather than
# contributing a shard whose indices mean something else. (Also: zsh word-splits
# an unquoted $(...) but not a $VAR, so a 130-name variable arrives as one arg.)
python3 - "$POOL" "$FROM" "$TO" "$SHARD" <<'PY'
import hashlib, json, pathlib, sys
pool, lo, hi, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
names = [c["name"] for c in json.loads(pathlib.Path(pool).read_text())["candidates"]]
digest = hashlib.sha256("\n".join(names).encode()).hexdigest()
chunk = names[lo - 1:hi]
pathlib.Path(out).write_text(
    f"# shard {lo}-{hi} of {len(names)} (1-based, inclusive)\n"
    f"# pool: {pool}\n"
    f"# candidate_order_sha256: {digest}\n"
    + "\n".join(chunk) + "\n"
)
print(f"{len(chunk)} programs -> {out}")
PY

N_TASKS=$(( TO - FROM + 1 ))
cat <<EOF

shard        $FROM-$TO of $N_POOL  ($N_TASKS tasks)
protocol     model=$MODEL  temperature=$TEMPERATURE  seed=$SEED
             context=$CONTEXT_LENGTH  max_examples=$MAX_EXAMPLES  sandbox_timeout=$SANDBOX_TIMEOUT_SEC
draws        $CALLS per task  ($(( N_TASKS * CALLS )) total, cached ones replay free)
report       $OUT
ledger       $LEDGER
EOF
echo

if (( DRY_RUN )); then
  echo "--dry-run: nothing started. Shard list is at $SHARD"
  exit 0
fi

# ── the model server ────────────────────────────────────────────────────────
# Reuse one already listening on this port, or start our own. Which of the two
# happened does NOT decide whether the run is trustworthy - the /api/ps check
# below does, and it runs either way. An earlier version refused a server it had
# not started, on the theory that only a self-started one has a known context
# window. That was the wrong guard: the window is knowable by asking, and
# refusing a hand-started server only forces a needless reload of 4.7 GB between
# consecutive shards.
#
# What the distinction does decide is teardown. We stop what we started and
# leave alone what we did not: unloading someone else's warm model is exactly
# what they started it by hand to avoid.
command -v ollama >/dev/null || { echo "ollama not on PATH" >&2; exit 2; }
command -v curl   >/dev/null || { echo "curl not on PATH" >&2; exit 2; }

HOST="127.0.0.1:${PORT}"
URL="http://${HOST}"
SERVE_PID=""

if curl -sf "${URL}/api/tags" >/dev/null 2>&1; then
  echo "reusing the ollama server already on ${HOST} (leaving it up on exit)"
else
  echo "starting ollama on ${HOST} with OLLAMA_CONTEXT_LENGTH=${CONTEXT_LENGTH}"
  OLLAMA_HOST="$HOST" \
  OLLAMA_CONTEXT_LENGTH="$CONTEXT_LENGTH" \
  OLLAMA_NUM_PARALLEL=1 \
  OLLAMA_KEEP_ALIVE=60m \
    ollama serve >>"$LOG" 2>&1 &
  SERVE_PID=$!
fi

cleanup() {
  local rc=$?
  echo
  # Unloading the weights and shutting the server down are two different things,
  # and the first is the one that matters on a shared machine: the server process
  # is a few MB, the resident model is 6.4 GB. So it happens by default on both
  # paths - including when the server was already there and we are only borrowing
  # it, because a shard that walks away leaving 6.4 GB pinned on a machine it did
  # not start is a bad guest. --no-stop-model opts out, for chaining shards warm.
  if (( STOP_MODEL )); then
    echo "unloading $MODEL"
    OLLAMA_HOST="$HOST" ollama stop "$MODEL" >/dev/null 2>&1 || true
  else
    echo "--no-stop-model: leaving $MODEL loaded"
  fi
  if [[ -z "$SERVE_PID" ]]; then
    echo "leaving the pre-existing server on ${HOST} up"
  elif (( KEEP_SERVING )); then
    echo "--keep-serving: leaving our server up (pid $SERVE_PID)"
  else
    echo "stopping the server we started (pid $SERVE_PID)"
    kill "$SERVE_PID" 2>/dev/null || true
    wait "$SERVE_PID" 2>/dev/null || true
  fi
  [[ -f "$OUT" ]] && echo "report: $OUT  (merge with scripts/consolidate_screens.py)"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for _ in $(seq 60); do
  curl -sf "${URL}/api/tags" >/dev/null 2>&1 && break
  sleep 1
done
curl -sf "${URL}/api/tags" >/dev/null 2>&1 || {
  echo "server did not come up within 60 s - see $LOG" >&2; exit 1; }

# Present already? Then nothing is downloaded. Asked of the server this run will
# use, not of the `ollama list` CLI: that CLI is itself a client and dies with
# "could not connect" on a machine where no server is up yet - which is every
# machine this script is meant to bootstrap.
#
# An id with no tag is stored as ":latest", so match that form too rather than
# re-pulling 4.7 GB over a name that is already on disk.
if ! curl -sf "${URL}/api/tags" | python3 -c "
import json, sys
want = '$MODEL' if ':' in '$MODEL' else '$MODEL:latest'
have = {m.get('model') for m in json.load(sys.stdin).get('models', [])}
sys.exit(0 if want in have else 1)"; then
  echo "$MODEL is not on this server - pulling it"
  OLLAMA_HOST="$HOST" ollama pull "$MODEL"
else
  echo "$MODEL already present - nothing to download"
fi

# Load the weights, then read back what the server is ACTUALLY serving. The
# window is not in the cache key and Ollama truncates rather than refuses, so
# this assertion is the only thing standing between a wrong OLLAMA_CONTEXT_LENGTH
# and a shard of silently head-cropped prompts that merges in looking fine.
echo "loading $MODEL and verifying the served window"
curl -sf "${URL}/api/generate" -d "{\"model\":\"${MODEL}\",\"prompt\":\"ok\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null

RUNTIME="$(python3 - "$URL" "$MODEL" "$CONTEXT_LENGTH" <<'PY'
import json, sys, urllib.request
url, model, want = sys.argv[1], sys.argv[2], int(sys.argv[3])
ps = json.load(urllib.request.urlopen(f"{url}/api/ps"))
version = json.load(urllib.request.urlopen(f"{url}/api/version"))["version"]
loaded = [m for m in ps.get("models", []) if m.get("model") == model]
if not loaded:
    sys.exit(f"{model} is not loaded on {url} - see the log")
m = loaded[0]
got = m.get("context_length")
if got != want:
    sys.exit(
        f"served context is {got}, not {want}.\n"
        f"Ollama picks its default from available VRAM and truncates an over-long "
        f"prompt silently, so this run would measure a different instrument than "
        f"the other shards. Refusing to start.\n\n"
        f"If you started this server by hand, restart it with the window pinned:\n"
        f"  OLLAMA_HOST={url.removeprefix('http://')} "
        f"OLLAMA_CONTEXT_LENGTH={want} OLLAMA_NUM_PARALLEL=1 ollama serve\n"
        f"or stop it and let this script start its own."
    )
print(json.dumps({
    "backend": "ollama",
    "ollama_version": version,
    "model_digest": m.get("digest", "")[:16],
    "quantization": m.get("details", {}).get("quantization_level"),
    "parameter_size": m.get("details", {}).get("parameter_size"),
    "context_length": got,
    "num_parallel": 1,
}, sort_keys=True))
PY
)"
echo "  ok: $RUNTIME"

# ── run ─────────────────────────────────────────────────────────────────────
# measure_pi.py rewrites $OUT after every task, so an interrupted shard leaves a
# valid partial and re-running the identical command resumes it from cache.
# Never hand-trim the shard list to "skip what is done": the report holds only
# what that run walked, so a trimmed re-run drops the rest.
[[ -f "$OUT" ]] && cp "$OUT" "$OUT.bak" && echo "kept the previous report as $OUT.bak"

# python-dotenv does not override an existing environment variable, so these win
# over .env without touching it.
export MODEL TEMPERATURE
export LLM_BASE_URL="${URL}/v1"
export LLM_API_KEY="unused"            # Ollama ignores it; the SDK demands one
export LLM_CONTEXT_TOKENS="$CONTEXT_LENGTH"
export LLM_TIMEOUT_SEC="1800"
export SANDBOX_TIMEOUT_SEC
export CACHE_DIR="cache"
export SCREEN_RUNTIME="$RUNTIME"
# One ledger per shard: src.llm appends to CALLS_LOG and llm.spent() sums it, so
# two machines sharing one file would interleave lines and mis-total. Separate
# files concatenate cleanly at merge time.
export CALLS_LOG="$LEDGER"
# Local backend, so the calls are free. The ledger still records tokens,
# finish_reason and seconds - the token profile PLAN.md's rate card needs.
export PRICE_IN_PER_MTOK="0"
export PRICE_OUT_PER_MTOK="0"
export BUDGET_USD_CAP="1"

[[ -d .venv ]] && source .venv/bin/activate

python3 scripts/measure_pi.py \
    --programs-from "$SHARD" \
    --calls-per-program "$CALLS" \
    --seed "$SEED" \
    --max-examples "$MAX_EXAMPLES" \
    --model "$MODEL" \
    --out "$OUT" 2>&1 | tee -a "$LOG"
