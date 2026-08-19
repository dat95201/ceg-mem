#!/usr/bin/env bash
#
# One shard of one experiment: start the local proposer, run the grid over an
# index range of the corpus, tear the server back down. Reported data, not a
# smoke test.
#
#   bash scripts/eval_shard.sh --exp trial                    # 3 tasks, 1 seed, B=5
#   bash scripts/eval_shard.sh --exp E1 --from 1 --to 30
#   bash scripts/eval_shard.sh --exp E2                       # whole corpus, one machine
#   bash scripts/eval_shard.sh --exp E4-k8 --from 1 --to 12
#
# Writes, for a shard covering positions FROM..TO of that experiment's universe:
#
#   data/eval_shards/<exp>_<from>_<to>.txt    the exact program list, with digests
#   data/episodes_eval_<exp>_<from>_<to>.jsonl  this shard's rounds
#   data/overfit_eval_<exp>_<from>_<to>.jsonl   this shard's overfit verdicts
#   data/calls_eval_<exp>_<from>_<to>.jsonl     this shard's call ledger
#   logs/eval_<exp>_<from>_<to>.log             the trace
#
# Merge them with scripts/consolidate_evals.py, which is also where a shard that
# was interrupted, or run under a different protocol, is caught.
#
# Why the shard order is not data/tasks.json's order
# --------------------------------------------------
# data/tasks.json is grouped by stratum - twenty `dead` tasks, then `medium`,
# then `easy`, then `too_easy`. Cutting index ranges out of that would hand one
# machine every `dead` task (twenty rounds each, every cell, by construction)
# and another every `too_easy` one (usually one round), so the shards would
# differ ~10x in wall clock and, worse, a run that finished three shards of four
# would hold a stratum-biased grid rather than a smaller one. So each band is
# spaced evenly over the whole universe first, deterministically, and the result
# written to data/eval_order.txt with the corpus digest that produced it. Every
# prefix and every suffix of that order is then proportional.
#
# Incremental by construction
# ---------------------------
# Two independent mechanisms, and they cover different costs. src.llm caches on
# (model, temperature, max_tokens, nonce, prompt) and src.loop nonces a draw
# `<task>|seed<S>|r<round>`, so a re-run replays every model call it already
# bought. The oracle is not cached at all: re-running a finished cell re-executes
# the candidate against the test pool, which is most of the wall clock here. That
# is what --resume-from is for, and why this script passes the merged history
# (data/episodes.jsonl) by default - a cell finished by any earlier shard is
# skipped rather than re-walked.
#
# Long job: run under tmux, or `nohup bash scripts/eval_shard.sh ... &`.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── PROTOCOL - in src.llm's cache key, or in what the metrics mean. Identical
#    on every machine and for the life of the grid, or the shards do not merge.
#    Same values the screen ran under: pi is a property of the model, and the
#    corpus is banded on pi measured with exactly these.
MODEL="${MODEL:-qwen2.5-coder:7b}"
TEMPERATURE="${TEMPERATURE:-1.0}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-32768}"
SANDBOX_TIMEOUT_SEC="${SANDBOX_TIMEOUT_SEC:-30.0}"
GRANULARITY="${GRANULARITY:-fine}"
# ── knobs that are free to differ per machine ───────────────────────────────
PORT="${PORT:-11435}"                        # own port, not the desktop app's
TASKS="data/tasks.json"
MERGED="data/episodes.jsonl"

EXP=""; FROM=""; TO=""; SEEDS=""; BUDGET=""; DRY_RUN=0
KEEP_SERVING=0; STOP_MODEL=1; RESUME_MERGED=1

usage() {
  cat <<'USAGE'
usage: bash scripts/eval_shard.sh --exp NAME [--from N --to M] [options]

  --exp NAME        which experiment. One of:
                      trial           all three arms, 3 tasks, 1 seed, B=5
                      E1              no-memory arm, --force-full-budget
                      E2              untyped + typed, --check-overfit
                      E3-guard-only   typed with --steer off
                      E3-steer-only   typed with --guard off
                      E4-k20 E4-k8 E4-k3      typed at --max-examples K
                      E5-c90 E5-c75 E5-c50    typed at --typing-noise-c C
  --from N --to M   1-based inclusive range over that experiment's universe
                    (default: all of it). E1/E2/E3 walk the 85-task corpus,
                    E4/E5 the 24-task stratified sweep subset.
  --seeds "1 2 3"   override the preset's seeds
  --budget B        override the preset's budget (default 20; trial 5)
  --port P          ollama port (default 11435). A server already listening
                    there is reused and left running; otherwise one is started
                    and torn down at the end. Either way the served context
                    window is verified before anything is spent.
  --no-resume-merged  do not consult data/episodes.jsonl for finished cells
  --stop-model      unload the model from memory on exit (default)
  --no-stop-model   leave it loaded, to warm-start the next shard
  --keep-serving    leave our own server up on exit (the process, not the
                    weights; pair with --no-stop-model to chain shards)
  --dry-run         print the plan and the shard list, start nothing
  -h, --help        this

Pinned, and not overridable per run without also changing every other machine:
  MODEL=qwen2.5-coder:7b  TEMPERATURE=1.0  CONTEXT_LENGTH=32768
  SANDBOX_TIMEOUT_SEC=30.0  GRANULARITY=fine

Merge the shards with:
  python3 scripts/consolidate_evals.py
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --exp)     EXP="$2"; shift 2 ;;
    --from)    FROM="$2"; shift 2 ;;
    --to)      TO="$2"; shift 2 ;;
    --seeds)   SEEDS="$2"; shift 2 ;;
    --budget)  BUDGET="$2"; shift 2 ;;
    --port)    PORT="$2"; shift 2 ;;
    --no-resume-merged) RESUME_MERGED=0; shift ;;
    --stop-model)    STOP_MODEL=1; shift ;;
    --no-stop-model) STOP_MODEL=0; shift ;;
    --keep-serving)  KEEP_SERVING=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$EXP" ]] || { echo "--exp is required" >&2; usage >&2; exit 2; }
[[ -f "$TASKS" ]] || { echo "$TASKS missing - freeze the corpus first (CORPUS.md)" >&2; exit 2; }

# ── the presets ─────────────────────────────────────────────────────────────
# One driver, many experiments: PLAN.md steps 6-9 are this same grid with
# different flags. Encoded here rather than retyped per shard, because a flag
# retyped wrong on shard 3 of 4 is a cell that never joins the others - it lands
# in a different cell key and analysis simply reports it missing, days later.
UNIVERSE="corpus"; MODES=""; EXTRA=""; DEF_SEEDS="1 2 3"; DEF_BUDGET=20
EPISODES=""; MERGEABLE=1
case "$EXP" in
  trial)
    MODES="no_memory untyped typed"; EXTRA="--check-overfit"
    UNIVERSE="trial"; DEF_SEEDS="1"; DEF_BUDGET=5; MERGEABLE=0
    # A rehearsal that skips cells because another machine already ran them
    # rehearses nothing. This is the one preset that ignores the merged history.
    RESUME_MERGED=0 ;;
  E1)
    MODES="no_memory"; EXTRA="--force-full-budget"; DEF_SEEDS="1 2 3 4 5" ;;
  E2)
    MODES="untyped typed"; EXTRA="--check-overfit"; DEF_SEEDS="1 2 3 4 5" ;;
  E3-guard-only)
    MODES="typed"; EXTRA="--steer off" ;;
  E3-steer-only)
    MODES="typed"; EXTRA="--guard off" ;;
  E4-k20) MODES="typed"; EXTRA="--max-examples 20 --check-overfit"; UNIVERSE="sweep" ;;
  E4-k8)  MODES="typed"; EXTRA="--max-examples 8  --check-overfit"; UNIVERSE="sweep" ;;
  E4-k3)  MODES="typed"; EXTRA="--max-examples 3  --check-overfit"; UNIVERSE="sweep" ;;
  E5-c90) MODES="typed"; EXTRA="--typing-noise-c 0.9";  UNIVERSE="sweep" ;;
  E5-c75) MODES="typed"; EXTRA="--typing-noise-c 0.75"; UNIVERSE="sweep" ;;
  E5-c50) MODES="typed"; EXTRA="--typing-noise-c 0.5";  UNIVERSE="sweep" ;;
  *) echo "unknown --exp: $EXP" >&2; usage >&2; exit 2 ;;
esac
SEEDS="${SEEDS:-$DEF_SEEDS}"
BUDGET="${BUDGET:-$DEF_BUDGET}"

# ── the universe, and the shard cut out of it ───────────────────────────────
# Written to files, never held in a shell variable: run_eval.py records the
# *path*, so a shard is traceable back to the corpus it was cut from, and zsh
# word-splits an unquoted $(...) but not an unquoted $VAR - which is how an
# 85-name variable arrives at argparse as one unknown program.
mkdir -p data/eval_shards logs
python3 - "$TASKS" <<'PY'
import hashlib, json, pathlib, sys

tasks = json.loads(pathlib.Path(sys.argv[1]).read_text())["tasks"]
# Name AND stratum: the stratum decides where a task lands in the interleave
# below, so a re-freeze that kept every name but re-banded one task would leave
# this digest unchanged while every shard index shifted underneath it.
digest = hashlib.sha256(
    "\n".join(f"{t['name']}\t{t['stratum']}" for t in tasks).encode()).hexdigest()

# Interleave the strata, in the frozen order within each. Deterministic, so
# every machine cuts the same shard from the same index range - and balanced, so
# any contiguous range holds every stratum in roughly its corpus proportion.
#
# Positional, not a plain round-robin cycle: the bands have very different sizes
# (30 easy, 15 too_easy), and cycling exhausts the small ones first, leaving the
# tail all-easy - so the last shard would be the one band the effect is smallest
# in. Spacing each band evenly over [0,1) instead keeps every prefix AND every
# suffix proportional.
bands = ("dead", "hard", "medium", "easy", "too_easy")
by = {b: [t["name"] for t in tasks if t["stratum"] == b] for b in bands}


def interleave(groups):
    placed = [((i + 0.5) / len(names), bi, names[i])
              for bi, (_, names) in enumerate(groups.items()) if names
              for i in range(len(names))]
    return [name for _, _, name in sorted(placed)]


order = interleave(by)

# The E4/E5 subset: six per band, by name, per PLAN.md SS 9 - then put through
# the same round-robin so a sweep shard is balanced too. Frozen to a file
# because scripts/freeze_results.py --sweep-programs-from must receive the
# identical list days later, and its own default is a different set.
sweep = interleave({b: sorted(by[b])[:6] for b in bands})

# The trial: one task from each of three bands that behave differently - `dead`
# never accepts, `easy` usually does on the first or second round. An arm that
# looks identical on all three is an arm whose flags did not take.
trial = [by[b][0] for b in ("dead", "medium", "easy") if by[b]]

for name, names in (("eval_order", order), ("sweep_programs", sweep),
                    ("trial_programs", trial)):
    path = pathlib.Path(f"data/{name}.txt")
    body = (f"# {name}: {len(names)} programs, strata interleaved evenly\n"
            f"# corpus: data/tasks.json\n"
            f"# corpus_sha256: {digest}\n" + "\n".join(names) + "\n")
    if path.exists():
        prev = [l for l in path.read_text().splitlines()
                if l.startswith("# corpus_sha256: ")]
        if prev and prev[0].split(": ", 1)[1] != digest:
            sys.exit(
                f"{path} was cut from a different data/tasks.json "
                f"({prev[0].split(': ', 1)[1][:12]}... vs {digest[:12]}...).\n"
                "Every shard index would mean a different task, and episodes\n"
                "already collected were run against the old list. Move the old\n"
                "data/*_programs.txt, data/eval_order.txt and the episode logs\n"
                "aside deliberately, or restore the corpus they belong to.")
        if path.read_text() == body:
            continue
    path.write_text(body)
PY

case "$UNIVERSE" in
  corpus) LIST_SRC="data/eval_order.txt" ;;
  sweep)  LIST_SRC="data/sweep_programs.txt" ;;
  trial)  LIST_SRC="data/trial_programs.txt" ;;
esac

# `|| true`: grep exits 1 on a zero count, which under `set -e` would kill the
# script with no message at all.
N_UNIVERSE="$(grep -cve '^#' -e '^$' "$LIST_SRC" || true)"
[[ "$N_UNIVERSE" =~ ^[1-9][0-9]*$ ]] || {
  echo "$LIST_SRC holds no programs - the corpus freeze is empty or the file is corrupt" >&2
  exit 2; }
FROM="${FROM:-1}"
TO="${TO:-$N_UNIVERSE}"
[[ "$FROM" =~ ^[0-9]+$ && "$TO" =~ ^[0-9]+$ ]] || { echo "--from/--to must be integers" >&2; exit 2; }
# 10#: every file this script writes is zero-padded (E1_031_060.txt), so re-issuing
# a shard by copying its own tag is the natural move - and bash reads a leading-zero
# literal as OCTAL in (( )) and printf while the python heredoc reads it as decimal.
# `--from 031 --to 060` would otherwise walk tasks 31-60 while naming every artifact
# 025_048, and `--from 085` would die on "value too great for base" with a message
# blaming the range.
FROM=$((10#$FROM)); TO=$((10#$TO))
(( FROM >= 1 && TO >= FROM && TO <= N_UNIVERSE )) || {
  echo "range $FROM-$TO is outside 1-$N_UNIVERSE for --exp $EXP" >&2; exit 2; }

TAG="$(printf '%s_%03d_%03d' "$EXP" "$FROM" "$TO")"
SHARD="data/eval_shards/${TAG}.txt"
LOG="logs/eval_${TAG}.log"
if (( MERGEABLE )); then
  EPISODES="data/episodes_eval_${TAG}.jsonl"
  OVERFIT="data/overfit_eval_${TAG}.jsonl"
  LEDGER="data/calls_eval_${TAG}.jsonl"
else
  # The trial is a test of the flags, not data. Nothing it writes is named
  # `*_eval_*`, which is what consolidate_evals.py globs: a B=5 episode of a
  # cell the grid never re-ran would otherwise merge in as a truncated one, and
  # the rehearsal's calls would land in the reported token profile.
  EPISODES="data/episodes_trial.jsonl"
  OVERFIT="data/overfit_trial.jsonl"
  LEDGER="data/calls_trial.jsonl"
fi

python3 - "$LIST_SRC" "$FROM" "$TO" "$SHARD" "$EXP" <<'PY'
import pathlib, sys
src, lo, hi, out, exp = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
lines = pathlib.Path(src).read_text().splitlines()
header = [l for l in lines if l.startswith("#")]
names = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
chunk = names[lo - 1:hi]
pathlib.Path(out).write_text(
    f"# experiment {exp}, shard {lo}-{hi} of {len(names)} (1-based, inclusive)\n"
    f"# universe: {src}\n" + "\n".join(header[1:]) + "\n" + "\n".join(chunk) + "\n")
print(f"{len(chunk)} programs -> {out}")
PY

RESUME=""
if (( RESUME_MERGED )) && [[ -f "$MERGED" ]]; then
  RESUME="--resume-from $MERGED"
fi

N_TASKS=$(( TO - FROM + 1 ))
N_SEEDS="$(wc -w <<< "$SEEDS" | tr -d ' ')"
N_MODES="$(wc -w <<< "$MODES" | tr -d ' ')"
cat <<EOF

experiment   $EXP
shard        $FROM-$TO of $N_UNIVERSE ($N_TASKS tasks, universe $LIST_SRC)
grid         modes="$MODES"  seeds="$SEEDS"  budget=$BUDGET
             $(( N_TASKS * N_SEEDS * N_MODES )) cells$([[ -n "$EXTRA" ]] && echo "  ·  $EXTRA")
protocol     model=$MODEL  temperature=$TEMPERATURE  context=$CONTEXT_LENGTH
             granularity=$GRANULARITY  sandbox_timeout=$SANDBOX_TIMEOUT_SEC
episodes     $EPISODES$( (( MERGEABLE )) || echo "   (trial - never merged)")
ledger       $LEDGER
resume       ${RESUME:-none (--no-resume-merged, or no merged history yet)}
EOF
echo

if (( DRY_RUN )); then
  echo "--dry-run: nothing started. Shard list is at $SHARD"
  exit 0
fi

# ── the model server ────────────────────────────────────────────────────────
# Reuse one already listening on this port, or start our own. Which of the two
# happened does NOT decide whether the run is trustworthy - the context-window
# check inside serve_local.sh does, and it runs either way. What it decides is
# teardown: we stop what we started and leave alone what we did not, because
# unloading someone else's warm model is exactly what they started it by hand to
# avoid.
command -v curl >/dev/null || { echo "curl not on PATH" >&2; exit 2; }
URL="http://127.0.0.1:${PORT}"
STARTED_SERVER=0
curl -sf "${URL}/api/tags" >/dev/null 2>&1 || STARTED_SERVER=1

cleanup() {
  local rc=$?
  # Disarm first: this same function handles EXIT, INT and TERM, and the `exit`
  # at the bottom would otherwise re-enter it through EXIT.
  trap - EXIT INT TERM
  echo
  if (( STOP_MODEL )); then
    bash scripts/serve_local.sh --unload --port "$PORT" || true
  else
    echo "--no-stop-model: leaving $MODEL loaded"
  fi
  if (( STARTED_SERVER )) && (( ! KEEP_SERVING )); then
    bash scripts/serve_local.sh --stop --port "$PORT" >/dev/null 2>&1 || true
    echo "stopped the server this shard started"
  elif (( STARTED_SERVER )); then
    echo "--keep-serving: leaving our server up on 127.0.0.1:${PORT}"
  fi
  (( rc == 0 )) || echo "shard exited with status $rc - re-run the identical command to resume"
  exit $rc
}
trap cleanup EXIT INT TERM

RUNTIME_FILE="$(mktemp)"
MODEL="$MODEL" CONTEXT_LENGTH="$CONTEXT_LENGTH" RUNTIME_OUT="$RUNTIME_FILE" \
  bash scripts/serve_local.sh --port "$PORT"

# ── the shard's protocol record ─────────────────────────────────────────────
# RoundRecord carries model, granularity and max_examples, but NOT the served
# context window, the model digest, the temperature or the sandbox timeout - and
# those are precisely the half that changes the numbers while leaving no trace in
# the cache. The screen solves this by recording them in every shard report and
# refusing to merge across a disagreement (consolidate_screens.py); this is the
# same record, and consolidate_evals.py checks it the same way.
META="data/eval_shards/${TAG}.meta.json"
python3 - "$META" "$RUNTIME_FILE" "$EXP" "$FROM" "$TO" "$LIST_SRC" "$SHARD" \
         "$MODEL" "$TEMPERATURE" "$CONTEXT_LENGTH" "$SANDBOX_TIMEOUT_SEC" \
         "$GRANULARITY" "$BUDGET" "$SEEDS" "$MODES" "$EXTRA" "$EPISODES" <<'PY'
import json, pathlib, sys
(meta, runtime_file, exp, lo, hi, universe, shard, model, temperature,
 context, sandbox, granularity, budget, seeds, modes, extra, episodes) = sys.argv[1:18]
raw = pathlib.Path(runtime_file).read_text().strip()
if not raw:
    sys.exit("scripts/serve_local.sh did not report the runtime it verified.\n"
             "It writes $RUNTIME_OUT only if that variable reaches it; without the\n"
             "record this shard would be unauditable, so it stops here rather than\n"
             "spending hours producing episodes nobody can check the window on.")
runtime = json.loads(raw)
header = [l for l in pathlib.Path(shard).read_text().splitlines() if l.startswith("#")]
digest = next((l.split(": ", 1)[1] for l in header if l.startswith("# corpus_sha256: ")), None)
pathlib.Path(meta).write_text(json.dumps({
    "experiment": exp, "from": int(lo), "to": int(hi),
    "universe": universe, "shard_list": shard, "corpus_sha256": digest,
    "episodes_path": episodes,
    "protocol": {
        "model": model, "temperature": float(temperature),
        "context_length": int(context), "sandbox_timeout_sec": float(sandbox),
        "granularity": granularity,
    },
    "runtime": runtime,
    "grid": {"budget": int(budget), "seeds": [int(x) for x in seeds.split()],
             "modes": modes.split(), "extra": extra.split()},
}, indent=2, sort_keys=True) + "\n")
print(f"protocol record -> {meta}")
PY
rm -f "$RUNTIME_FILE"

# ── run ─────────────────────────────────────────────────────────────────────
# python-dotenv does not override an existing environment variable, so these win
# over .env without touching it - and pin the protocol even if .env drifts.
export MODEL TEMPERATURE SANDBOX_TIMEOUT_SEC
export LLM_BASE_URL="${URL}/v1"
export LLM_API_KEY="unused"            # Ollama ignores it; the SDK demands one
export LLM_CONTEXT_TOKENS="$CONTEXT_LENGTH"
export LLM_TIMEOUT_SEC="1800"
export CACHE_DIR="cache"
# One ledger per shard: src.llm appends to CALLS_LOG and llm.spent() sums it, so
# two machines sharing one file would interleave lines and mis-total. Separate
# files concatenate cleanly at merge time.
export CALLS_LOG="$LEDGER"
# Local backend, so the calls are free. The ledger still records tokens,
# finish_reason and seconds - the token profile PLAN.md's rate card needs. The
# cap can then never bind; it is left low as a tripwire, so a client somehow
# repointed at a paid endpoint stops instead of spending.
export PRICE_IN_PER_MTOK="0"
export PRICE_OUT_PER_MTOK="0"
export BUDGET_USD_CAP="1"

[[ -d .venv ]] && source .venv/bin/activate

# Unquoted on purpose: $MODES, $SEEDS, $EXTRA and $RESUME are word lists, and
# this is bash, which splits them. (zsh would not - which is why the program
# list goes through a file instead of a variable.)
python3 scripts/run_eval.py \
    --programs-from "$SHARD" \
    --modes $MODES \
    --seeds $SEEDS \
    --budget "$BUDGET" \
    --model "$MODEL" \
    --granularity "$GRANULARITY" \
    --episodes-path "$EPISODES" \
    --overfit-path "$OVERFIT" \
    $EXTRA $RESUME 2>&1 | tee -a "$LOG"
