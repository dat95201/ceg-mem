#!/usr/bin/env bash
#
# One shard of one experiment: start the local proposer, run the grid over an
# index range of the corpus, tear the server back down. Reported data, not a
# smoke test.
#
#   bash scripts/eval_shard.sh --exp trial                    # 3 bands x 1 task, 1 seed, B=5
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
# data/tasks.json is grouped by stratum - every `dead` task, then every `hard`,
# then `medium`, `easy`, `too_easy`, in the band order select_corpus.py froze.
# Cutting index ranges out of that would hand one machine every `dead` task
# (the full budget in every cell, by construction - nothing there ever accepts)
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

# ── BACKEND - which proposer this shard talks to. Everything below keys off it.
#    ollama: a local server this script starts, pins and verifies (the default,
#            and what the pi screen and the reported grid run under).
#    cloud:  an OpenAI-compatible endpoint. No server lifecycle, no window to
#            verify, and calls cost money - so the prices and the cap become
#            real numbers instead of the zero/tripwire pair the local path uses.
#    Set it per run; never edit this file to switch.
BACKEND="${BACKEND:-ollama}"

# ── PROTOCOL - in src.llm's cache key, or in what the metrics mean. Identical
#    on every machine and for the life of the grid, or the shards do not merge.
#    Same values the screen ran under: pi is a property of the model, and the
#    corpus is banded on pi measured with exactly these.
MODEL="${MODEL:-qwen2.5-coder:7b}"
TEMPERATURE="${TEMPERATURE:-1.0}"
CONTEXT_LENGTH="${CONTEXT_LENGTH:-32768}"
SANDBOX_TIMEOUT_SEC="${SANDBOX_TIMEOUT_SEC:-30.0}"
GRANULARITY="${GRANULARITY:-fine}"
# o-series only, and empty for everything else. In src.llm's cache key and in
# run_eval's cell key, because it changes the proposal distribution.
REASONING_EFFORT="${REASONING_EFFORT:-}"
# Cloud only. Zero on the local path, where calls are free and the cap is a
# tripwire (see the export block near the bottom).
PRICE_IN_PER_MTOK="${PRICE_IN_PER_MTOK:-}"
PRICE_OUT_PER_MTOK="${PRICE_OUT_PER_MTOK:-}"
BUDGET_USD_CAP="${BUDGET_USD_CAP:-}"
LLM_BASE_URL_CLOUD="${LLM_BASE_URL:-}"       # empty = api.openai.com
# ── knobs that are free to differ per machine ───────────────────────────────
PORT="${PORT:-11435}"                        # own port, not the desktop app's
TASKS="data/tasks.json"
MERGED="data/episodes.jsonl"

EXP=""; FROM=""; TO=""; SEEDS=""; BUDGET=""; DRY_RUN=0
CHECK_REGRESSION=0; REGRESSION_CAP=; UNIVERSE_OVERRIDE=; FREE_GUARDED=0; FREE_GUARD_CAP=
KEEP_SERVING=0; STOP_MODEL=1; RESUME_MERGED=1

# ── the universes ───────────────────────────────────────────────────────────
# The one place a list path is written down: usage() reports these sizes and the
# shard cut below reads its own list from the same table, so a help text that
# disagrees with what the script actually walks is not expressible. The lists
# themselves are written from data/tasks.json by the interleave below - keep the
# keys here in step with the names it writes.
universe_list() {
  case "$1" in
    corpus) echo "data/eval_order.txt" ;;
    sweep)  echo "data/sweep_programs.txt" ;;
    trial)  echo "data/trial_programs.txt" ;;
    # A subset drawn on purpose - a demo, a rehearsal on ten tasks per band, the
    # programs one reviewer asked about. Written by whoever drew it, not by the
    # interleave below, so it is the one universe this script does not generate
    # and the one whose digest header is checked on READ instead of on write.
    # sweep minus the dead band: the tasks where pi_hat > 0, i.e. the only ones
    # where handing the search more attempts can convert into a repair. E9's
    # universe. Derived, not drawn - scripts/build_live_universe.py writes it
    # (with the corpus digest header the read-time check below requires).
    live)   echo "data/live_programs.txt" ;;
    demo)   echo "data/demo_programs.txt" ;;
    *)      echo "" ;;
  esac
}

# A size is a property of the corpus that is frozen, not of this script, so it
# is counted rather than asserted. A list that is absent prints "-" instead of a
# number it cannot know: the first shard of that universe writes it.
# `|| true`: grep exits 1 on a zero count, which under `set -e` would kill the
# script with no message at all.
universe_size() {
  local f n
  f="$(universe_list "$1")"
  [[ -n "$f" && -f "$f" ]] || { echo "-"; return; }
  n="$(grep -cve '^#' -e '^$' "$f" || true)"
  echo "${n:-0}"
}

# The universe sizes are read off the frozen lists rather than written down -
# but the text around them carries backticks, a $ and line continuations in the
# cloud examples, and an expanded heredoc would run `model` as a command and
# swallow the backslashes. So the heredoc stays QUOTED and the three counts are
# substituted afterwards, through @N_...@ placeholders.
usage() {
  sed -e "s/@N_CORPUS@/$(printf '%-5s' "$(universe_size corpus)")/" \
      -e "s/@N_SWEEP@/$(printf  '%-5s' "$(universe_size sweep)")/" \
      -e "s/@N_TRIAL@/$(printf  '%-5s' "$(universe_size trial)")/" <<'USAGE'
usage: bash scripts/eval_shard.sh --exp NAME [--from N --to M] [options]

  --exp NAME        which experiment. One of:
                      trial           all three arms, 3 bands x 1 task, 1 seed
                      E1              no-memory arm, --force-full-budget
                      E2              untyped + typed, --check-overfit
                      E3-guard-only   typed with --steer off
                      E3-steer-only   typed with --guard off
                      E4-k20 E4-k8 E4-k3      typed at --max-examples K
                      E5-c90 E5-c75 E5-c50    typed at --typing-noise-c C
                      E5-c25 E5-c00           two more c levels, for the
                                              crossover c* and the slope
                      E5-random       the c axis's NULL: classes assigned at
                                      random. c=0.00 is not this - see the
                                      preset's comment for why
                      E8-audit        untyped+typed with --audit-guarded: pays
                                      the oracle on guarded rounds so their
                                      failure type is on the record. Subset
                                      only - it defeats the saving E2 measures
                      E8-corpus       E8-audit over all 106 tasks instead of
                                      the 30-task sweep. No model calls. Until
                                      it runs, the steered typed arm's type-keyed
                                      metrics are valid only on those 30
                      E9-freeguard    E2's three arms under the other budget
                                      accounting: a guarded round is free, so
                                      the attempts the guard saves are handed
                                      back to the search. Cor. 4.4 is only
                                      testable here - charged to the budget the
                                      guard cannot change an outcome at all
  --free-guard-draw-cap N  with the flag above, stop after N x --budget draws.
                    NOT in the cell key - a re-run at a higher N tops the same
                    episode up rather than forking a second one.
  --universe NAME   run a preset over a different universe than its own default:
                    corpus | sweep | live | trial | demo.  `live` is the sweep
                    minus the dead band - the tasks where extra attempts can
                    actually convert; scripts/build_live_universe.py writes it. `demo` reads
                    data/demo_programs.txt, which YOU write - the other three are
                    generated from data/tasks.json, this one is a draw. It must
                    carry the same '# corpus_sha256:' header they do.
                    The universe is deliberately NOT in the cell key: the same
                    task/mode/seed is the same cell whichever list named it, so a
                    demo over 30 tasks and the full grid share their episodes and
                    their cache rather than paying for both.
  --from N --to M   1-based inclusive range over that experiment's universe
                    (default: all of it). E1/E2/E3/E8-corpus walk the whole
                    frozen corpus, E4/E8-audit the stratified sweep subset and
                    E9 the live subset of it.
                    Neither size belongs to this script - a re-freeze with
                    different quotas, or a band that under-filled, changes
                    both - so they are read off the lists, never written here:
                      corpus  data/eval_order.txt       @N_CORPUS@
                      sweep   data/sweep_programs.txt   @N_SWEEP@
                      trial   data/trial_programs.txt   @N_TRIAL@
                    A "-" means that list has not been cut yet; the first
                    shard of that universe writes it from data/tasks.json.
  --seeds "1 2 3"   override the preset's seeds
  --budget B        override the preset's budget (default 20; trial 5)
  --backend B       ollama (default) or cloud. See the block below.
  --model ID        proposer id. Must match the model the corpus was banded
                    under, unless you are deliberately running a second-proposer
                    arm - `model` is in the cell key, so the two never pool.
  --reasoning-effort E   low|medium|high, o-series only (o4-mini, o3, ...)
  --free-guarded-rounds  a guarded round costs a model call but NO unit of the
                    attempt budget, so the loop draws until it has spent --budget
                    real attempts (capped at 10x --budget draws). IS in the cell
                    key: success@B under this flag is a curve of a different B,
                    and pooling the two puts two experiments on one axis. Without
                    it the guard is outcome-neutral - it only blocks candidates
                    already known to fail - so untyped reproduces no_memory
                    exactly at every budget. See docs/DIAGNOSIS.md.
  --check-regression     #22: score every accepted patch on both halves of the
                    shipped pool - the cases the faulty version fails, and the
                    ones it passes that nothing in the loop ever checks. Sandbox
                    time only, no model calls, and NOT in the cell key, so it
                    can be switched on without invalidating finished cells.
  --regression-cap N     cases per program for the above; 0 = the whole pool.
                    Recorded in every row, because a capped audit is a weaker
                    measurement than an uncapped one.
  --port P          ollama port (default 11435). A server already listening
                    there is reused and left running; otherwise one is started
                    and torn down at the end. Either way the served context
                    window is verified before anything is spent. Ignored by
                    --backend cloud.
  --no-resume-merged  do not consult data/episodes.jsonl for finished cells
  --stop-model      unload the model from memory on exit (default)
  --no-stop-model   leave it loaded, to warm-start the next shard
  --keep-serving    leave our own server up on exit (the process, not the
                    weights; pair with --no-stop-model to chain shards)
  --dry-run         print the plan and the shard list, start nothing
  -h, --help        this

Pinned for the reported grid, and not overridable per run without also changing
every other machine:
  MODEL=qwen2.5-coder:7b  TEMPERATURE=1.0  CONTEXT_LENGTH=32768
  SANDBOX_TIMEOUT_SEC=30.0  GRANULARITY=fine

--backend cloud
  No server is started, stopped or verified; LLM_BASE_URL is passed through
  (empty = api.openai.com) and LLM_API_KEY must be set. Three variables become
  required rather than optional, because on this path the calls cost money and
  src.llm's cap is the only thing that stops a runaway grid:

    PRICE_IN_PER_MTOK  PRICE_OUT_PER_MTOK  BUDGET_USD_CAP

  The cap is PER PROCESS: llm.spent() sums only this shard's own ledger, and
  every shard gets its own. Running N shards at once therefore needs
  BUDGET_USD_CAP = total/N, or the real ceiling is N times what you set.

  gpt-4o-mini, whole corpus, E1+E2:
    LLM_API_KEY=sk-... PRICE_IN_PER_MTOK=0.15 PRICE_OUT_PER_MTOK=0.60 \
    BUDGET_USD_CAP=25 CONTEXT_LENGTH=128000 \
    bash scripts/eval_shard.sh --exp E2 --backend cloud --model gpt-4o-mini

  o4-mini (reasoning; src.llm switches to max_completion_tokens and drops
  temperature on its own, and src.proposer raises the output ceiling):
    LLM_API_KEY=sk-... PRICE_IN_PER_MTOK=1.10 PRICE_OUT_PER_MTOK=4.40 \
    BUDGET_USD_CAP=60 CONTEXT_LENGTH=200000 \
    bash scripts/eval_shard.sh --exp E2 --backend cloud --model o4-mini \
         --reasoning-effort medium

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
    --backend) BACKEND="$2"; shift 2 ;;
    --model)   MODEL="$2"; shift 2 ;;
    --reasoning-effort) REASONING_EFFORT="$2"; shift 2 ;;
    --universe)          UNIVERSE_OVERRIDE="$2"; shift 2 ;;
    --free-guarded-rounds) FREE_GUARDED=1; shift ;;
    --free-guard-draw-cap) FREE_GUARD_CAP="$2"; shift 2 ;;
    --check-regression)  CHECK_REGRESSION=1; shift ;;
    --regression-cap)    REGRESSION_CAP="$2"; shift 2 ;;
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
[[ -f "$TASKS" ]] || { echo "$TASKS missing - freeze the corpus first (RUNBOOK.md)" >&2; exit 2; }
[[ "$BACKEND" == "ollama" || "$BACKEND" == "cloud" ]] || {
  echo "--backend must be ollama or cloud, got '$BACKEND'" >&2; exit 2; }

# Checked here, before a shard list is written or a server touched, because
# every one of these is unrecoverable after the fact: an unpriced ledger cannot
# be repriced (the token counts are there but which rate applied is not), and a
# cap left at the local tripwire stops a paid grid three calls in.
if [[ "$BACKEND" == "cloud" ]]; then
  : "${LLM_API_KEY:?--backend cloud needs LLM_API_KEY (export it; do not commit it)}"
  for v in PRICE_IN_PER_MTOK PRICE_OUT_PER_MTOK BUDGET_USD_CAP; do
    [[ -n "${!v}" ]] || {
      echo "--backend cloud needs $v set - these calls cost money and src.llm's" >&2
      echo "cap is what stops a runaway grid. See --help for the two rate cards." >&2
      exit 2; }
  done
  # The window check is an ollama-specific defence (it truncates rather than
  # refusing); a hosted endpoint returns a context_length_exceeded error
  # instead. But LLM_CONTEXT_TOKENS still has to be right, because src.llm uses
  # it to refuse an over-long prompt *before* sending it, so a run reports the
  # overflow rather than paying for a call the backend will reject.
  [[ "$CONTEXT_LENGTH" != "32768" ]] || {
    echo "--backend cloud with CONTEXT_LENGTH=32768 (the local default)." >&2
    echo "Set it to the hosted model's real window - 128000 for gpt-4o-mini," >&2
    echo "200000 for o4-mini - or src.llm will refuse prompts that fit." >&2
    exit 2; }
fi
if [[ -n "$REASONING_EFFORT" && "$BACKEND" != "cloud" ]]; then
  echo "REASONING_EFFORT is set but --backend is $BACKEND. o-series models are" >&2
  echo "cloud-only; src.llm would refuse the combination anyway." >&2
  exit 2
fi

# ── the presets ─────────────────────────────────────────────────────────────
# One driver, many experiments: DESIGN.md steps 6-9 are this same grid with
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
  # Two more c levels. Four points (1.0 from E2, then .9/.75/.5) give a slope
  # but not a crossover: c* is where typed stops beating untyped, and nothing
  # in that range crosses. These reach far enough down that it should.
  E5-c25) MODES="typed"; EXTRA="--typing-noise-c 0.25"; UNIVERSE="sweep" ;;
  E5-c00) MODES="typed"; EXTRA="--typing-noise-c 0.0";  UNIVERSE="sweep" ;;
  # The c axis's NULL, which c=0.00 is not. TypedMemory.store noises only the
  # location half of a type and the first store of an episode has nowhere else
  # to file itself, so the bottom of the sweep is a lower bound on the damage
  # rather than random assignment. This arm files every refutation under a
  # location drawn uniformly from those seen so far, its own included: memory
  # still partitions the evidence and the guard is still O(1), but the partition
  # carries no information about failure type. Without it, "typing helps because
  # the classes are right" is not separable from "any partition helps" - which
  # is the first question a reviewer asks about a typed index.
  E5-random)
    MODES="typed"; EXTRA="--typing-random"; UNIVERSE="sweep" ;;
  # The ChatRepair baseline. Full seeds, whole corpus - it is reported beside
  # Redundancy audit: pay the oracle on guarded rounds too, so a guarded round
  # carries the failure type it would have had. Without it every type-based
  # redundancy count is censored in exactly the arms that guard, and an arm
  # that guards often looks less redundant for procedural reasons. Sweep subset
  # only - it spends the oracle time E2 exists to show can be saved.
  E8-audit)
    MODES="untyped typed"; EXTRA="--audit-guarded"; UNIVERSE="sweep" ;;
  # E9: the same three arms as E2 under the OTHER budget accounting, where a
  # guarded round is free. E2 measures what memory saves per attempt; E9 measures
  # what it buys when the attempts it saves are handed back to the search. The
  # two are separate cells by construction (--free-guarded-rounds is in the cell
  # key), so this never overwrites or pools with E2.
  # E8 over the whole corpus, not the 30-task sweep. Same flag, different
  # universe, and the reason is a measurement one rather than a cost one: the
  # CRN join recovers a guarded round's failure type for free in every arm whose
  # prompt is unconditioned, but the STEERED typed arm diverges from the
  # no-memory draw sequence and only 13.6% of its guarded rounds pair outside
  # E8's own 30 tasks. Until this runs, every type-keyed metric for that arm -
  # FSRR, type entropy, the revisit curve - is valid only on those 30.
  # Replays E2's cached draws: no model calls, ~2.3 h across 6 shards.
  E8-corpus)
    MODES="untyped typed"; EXTRA="--audit-guarded"; UNIVERSE="corpus"
    DEF_SEEDS="1 2 3" ;;
  E9-freeguard)
    MODES="no_memory untyped typed"
    # cap 3, not the default 10: on this universe the untyped guard blocks ~54%
    # of candidates, so 3x20 = 60 draws already reaches a 20-attempt budget, and
    # 10x would spend 46 GPU-hours proving that dead tasks stay dead.
    EXTRA="--free-guarded-rounds --free-guard-draw-cap 3 --check-overfit"
    UNIVERSE="live"; DEF_SEEDS="1 2 3" ;;
  *) echo "unknown --exp: $EXP" >&2; usage >&2; exit 2 ;;
esac
SEEDS="${SEEDS:-$DEF_SEEDS}"
BUDGET="${BUDGET:-$DEF_BUDGET}"
# After the preset, so it overrides the preset's own universe rather than being
# overwritten by it.
if [[ -n "$UNIVERSE_OVERRIDE" ]]; then
  [[ -n "$(universe_list "$UNIVERSE_OVERRIDE")" ]] || {
    echo "unknown --universe '$UNIVERSE_OVERRIDE' - one of: corpus sweep trial demo" >&2
    exit 2; }
  UNIVERSE="$UNIVERSE_OVERRIDE"
fi

if [[ -n "$REASONING_EFFORT" ]]; then
  EXTRA="$EXTRA --reasoning-effort $REASONING_EFFORT"
fi
# #22. Appended, not folded into a preset: the F2P/P2P audit is orthogonal to
# which arm is running and it is not in the cell key, so it composes with any
# --exp instead of needing one of its own. The cap rides along whenever it is
# set, so the shard's own log says which measurement this was.
if (( FREE_GUARDED )); then
  EXTRA="$EXTRA --free-guarded-rounds"
  [[ -n "$FREE_GUARD_CAP" ]] && EXTRA="$EXTRA --free-guard-draw-cap $FREE_GUARD_CAP"
fi
if (( CHECK_REGRESSION )); then
  EXTRA="$EXTRA --check-regression"
  [[ -n "${REGRESSION_CAP:-}" ]] && EXTRA="$EXTRA --regression-cap $REGRESSION_CAP"
fi

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
# Positional, not a plain round-robin cycle: the quotas are unequal by design
# and a band can under-fill besides, so cycling exhausts the small bands first
# and leaves a tail drawn from whichever band is largest - making the last shard
# the least representative one, and quite possibly the band the effect is
# smallest in. Spacing each band evenly over [0,1) instead keeps every prefix
# AND every suffix proportional, whatever the counts turn out to be.
bands = ("dead", "hard", "medium", "easy", "too_easy")
by = {b: [t["name"] for t in tasks if t["stratum"] == b] for b in bands}


def interleave(groups):
    placed = [((i + 0.5) / len(names), bi, names[i])
              for bi, (_, names) in enumerate(groups.items()) if names
              for i in range(len(names))]
    return [name for _, _, name in sorted(placed)]


order = interleave(by)

# The E4/E5 subset: six per band, by name, per DESIGN.md - then put through
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

LIST_SRC="$(universe_list "$UNIVERSE")"
[[ -n "$LIST_SRC" ]] || {
  echo "no list for universe '$UNIVERSE' - add it to universe_list()" >&2; exit 2; }

# `|| true`: grep exits 1 on a zero count, which under `set -e` would kill the
# script with no message at all.
[[ -f "$LIST_SRC" ]] || {
  echo "$LIST_SRC does not exist." >&2
  [[ "$UNIVERSE" == demo ]] && echo "A demo universe is a list you draw yourself - see RUNBOOK.md." >&2
  exit 2; }
# The three generated lists are digest-checked when they are WRITTEN, above. A
# hand-drawn one never passes through that, so it is checked here, on read: a
# list cut from a different data/tasks.json names different tasks at the same
# indices, and then every shard boundary means something else. eval_order.txt
# was just written or validated against the corpus frozen now, so its own header
# is the authority and the digest is not recomputed here.
CORPUS_DIGEST="$(sed -n 's/^# corpus_sha256: //p' data/eval_order.txt | head -1)"
LIST_DIGEST="$(sed -n 's/^# corpus_sha256: //p' "$LIST_SRC" | head -1)"
if [[ -z "$LIST_DIGEST" ]]; then
  echo "$LIST_SRC has no '# corpus_sha256: <digest>' header - add the digest of" >&2
  echo "the data/tasks.json it was drawn from, or its indices trace to nothing." >&2
  exit 2
elif [[ -n "$CORPUS_DIGEST" && "$LIST_DIGEST" != "$CORPUS_DIGEST" ]]; then
  echo "$LIST_SRC was cut from a different data/tasks.json" >&2
  echo "  (${LIST_DIGEST:0:12}... vs ${CORPUS_DIGEST:0:12}...). Re-draw it." >&2
  exit 2
fi

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

# The model id joins the tag whenever it is not the pinned local one. Without
# it a second proposer's E2 shard writes to the same episodes file, ledger and
# meta as the local E2 shard of the same range - two grids appending to one
# log, which freeze_results.py would later refuse to freeze without being able
# to say which rows came from where. `model` is already in the cell key; this
# keeps the *files* apart too.
SLUG=""
if [[ "$MODEL" != "qwen2.5-coder:7b" ]]; then
  SLUG="_$(printf '%s' "$MODEL" | tr -c 'A-Za-z0-9' '-' | sed 's/-\{1,\}/-/g;s/^-//;s/-$//')"
fi
TAG="$(printf '%s%s_%03d_%03d' "$EXP" "$SLUG" "$FROM" "$TO")"
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
backend      $BACKEND$([[ "$BACKEND" == "cloud" ]] && echo "  ·  \$${PRICE_IN_PER_MTOK}/\$${PRICE_OUT_PER_MTOK} per Mtok  ·  cap \$${BUDGET_USD_CAP} (this process only)")
protocol     model=$MODEL  temperature=$TEMPERATURE  context=$CONTEXT_LENGTH
             granularity=$GRANULARITY  sandbox_timeout=$SANDBOX_TIMEOUT_SEC$([[ -n "$REASONING_EFFORT" ]] && echo "  reasoning_effort=$REASONING_EFFORT")
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
STARTED_SERVER=0
if [[ "$BACKEND" == "ollama" ]]; then
  command -v curl >/dev/null || { echo "curl not on PATH" >&2; exit 2; }
  URL="http://127.0.0.1:${PORT}"
  curl -sf "${URL}/api/tags" >/dev/null 2>&1 || STARTED_SERVER=1
else
  URL=""            # src.llm's own default, or LLM_BASE_URL if the caller set one
  STOP_MODEL=0      # nothing local to unload
fi

cleanup() {
  local rc=$?
  # Disarm first: this same function handles EXIT, INT and TERM, and the `exit`
  # at the bottom would otherwise re-enter it through EXIT.
  trap - EXIT INT TERM
  echo
  if [[ "$BACKEND" == "cloud" ]]; then
    echo "backend=cloud: nothing local to tear down. Spend is in $LEDGER."
  elif (( STOP_MODEL )); then
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
if [[ "$BACKEND" == "ollama" ]]; then
  MODEL="$MODEL" CONTEXT_LENGTH="$CONTEXT_LENGTH" RUNTIME_OUT="$RUNTIME_FILE" \
    bash scripts/serve_local.sh --port "$PORT"
else
  # The cloud path has no window to verify - but it still has to leave a runtime
  # record, because consolidate_evals.py hard-stops on shards whose runtimes
  # disagree and a shard with no record at all is unauditable. What is knowable
  # here is the endpoint, the id and the effort; the digest and quantisation
  # have no hosted analogue and are omitted rather than faked.
  python3 - "$RUNTIME_FILE" "$LLM_BASE_URL_CLOUD" "$CONTEXT_LENGTH" "$REASONING_EFFORT" <<'PY'
import json, pathlib, sys
out, base, context, effort = sys.argv[1:5]
pathlib.Path(out).write_text(json.dumps({
    "backend": "openai",
    "api_base": base or "https://api.openai.com/v1",
    "context_length": int(context),
    "reasoning_effort": effort or None,
}, sort_keys=True) + "\n")
PY
  echo "backend=cloud: ${LLM_BASE_URL_CLOUD:-https://api.openai.com/v1}, model=$MODEL"
fi

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
         "$GRANULARITY" "$BUDGET" "$SEEDS" "$MODES" "$EXTRA" "$EPISODES" \
         "$BACKEND" "$REASONING_EFFORT" "${PRICE_IN_PER_MTOK:-0}" \
         "${PRICE_OUT_PER_MTOK:-0}" "${BUDGET_USD_CAP:-1}" <<'PY'
import json, pathlib, sys
(meta, runtime_file, exp, lo, hi, universe, shard, model, temperature,
 context, sandbox, granularity, budget, seeds, modes, extra, episodes,
 backend, effort, price_in, price_out, cap) = sys.argv[1:23]
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
        # backend and reasoning_effort belong to the protocol, not the runtime:
        # they change what the proposer IS, and consolidate_evals.py must refuse
        # to pool two shards that disagree on either. The rate card is recorded
        # so a ledger can be re-priced later - the token counts survive, but
        # which rate applied does not, unless it is written down here.
        "backend": backend,
        "reasoning_effort": effort or None,
        "price_in_per_mtok": float(price_in),
        "price_out_per_mtok": float(price_out),
        "budget_usd_cap": float(cap),
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
export LLM_CONTEXT_TOKENS="$CONTEXT_LENGTH"
export CACHE_DIR="cache"
if [[ "$BACKEND" == "ollama" ]]; then
  export LLM_BASE_URL="${URL}/v1"
  export LLM_API_KEY="unused"          # Ollama ignores it; the SDK demands one
  export LLM_TIMEOUT_SEC="1800"        # a local 7B needs ~1000s for the longest budget
else
  export LLM_BASE_URL="$LLM_BASE_URL_CLOUD"   # empty = api.openai.com
  # LLM_API_KEY is already exported by the caller; asserted at the top.
  export LLM_TIMEOUT_SEC="${LLM_TIMEOUT_SEC:-300}"
  export REASONING_EFFORT
fi
# One ledger per shard: src.llm appends to CALLS_LOG and llm.spent() sums it, so
# two machines sharing one file would interleave lines and mis-total. Separate
# files concatenate cleanly at merge time.
export CALLS_LOG="$LEDGER"
if [[ "$BACKEND" == "ollama" ]]; then
  # Local backend, so the calls are free. The ledger still records tokens,
  # finish_reason and seconds - the token profile DESIGN.md's rate card needs. The
  # cap can then never bind; it is left low as a tripwire, so a client somehow
  # repointed at a paid endpoint stops instead of spending.
  export PRICE_IN_PER_MTOK="0"
  export PRICE_OUT_PER_MTOK="0"
  export BUDGET_USD_CAP="1"
else
  # Real rates, asserted non-empty at the top. The cap binds per process and
  # llm.spent() sums only this shard's ledger, so N parallel shards need
  # total/N each - see --help.
  export PRICE_IN_PER_MTOK PRICE_OUT_PER_MTOK BUDGET_USD_CAP
fi

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
