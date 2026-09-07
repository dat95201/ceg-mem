# A COLAB CELL, not a module. It contains IPython `!` magics, so it does not
# import and `python3 -m py_compile` on it will fail - that is expected. It
# lives in the repository because it is the only paid-API launch path we have
# and it was previously kept nowhere but a chat window, which is how it was run
# twice with the wrong model id. Paste it into the notebook; keep the two in
# sync by pasting, not by editing one of them.
#
# ────────────────────────────────────────────────────────────────────────────
# STAGE   eval - the remaining arms, CLOUD proposer. Uses the model, costs money
# READS   data/<RUN_DIR>/sweep_programs.txt
# WRITES  data/<RUN_DIR>/episodes_eval_<exp>_<model>_*.jsonl
# TIME    hours per arm; sharded, resumable
# SKIP?   E2 no; E3-steer-only is what makes the steering claim scale-checkable
# ────────────────────────────────────────────────────────────────────────────
import os, sys; os.chdir(WORKDIR); sys.path.insert(0, ".")

# ── the two things this cell decides, both literals, both read before running ─
#
# MODEL is hardcoded HERE and section 1's CLOUD_MODEL is deliberately NOT used.
# Editing a Colab cell does not change the kernel variable until that cell is
# RUN, and two launches went out as gpt-4o-mini after section 1 had already been
# edited to say o4-mini. A literal on the command line cannot drift from the
# line you are reading.
#
#   the id is        o4-mini
#   NOT              gpt-o4-mini   (does not exist)
#   NOT              gpt-4o-mini   (a different, much weaker model - and the one
#                                   that actually went out twice. Note the
#                                   transposition: gpt-4o vs o4.)
MODEL = "o4-mini"

# Change EXP and re-run this cell. ONE FLEET AT A TIME - cmd_launch returns as
# soon as the shards are up, and the next call dies on "a fleet is still
# running". Watch it with the status cell, then come back and change EXP.
#
#   RUN IN THIS ORDER
#     E1              no_memory. Run it FIRST: proposal_nonce omits the mode, so
#                     round 1 is one draw shared by every arm - E1 pays for it
#                     and the rest replay it from the cache, which is also what
#                     keeps the arms paired under common random numbers.
#     E2              untyped + typed, two modes in one preset
#     E3-steer-only   typed with the guard off. The arm that isolates prompt
#                     steering - the one claim the paper predicts may be
#                     scale-dependent, and the reason this run exists at all.
#
#   DO NOT RUN on the cloud: E4/E5 sweeps, E8, E9, E10-E12. They are mechanism
#   internals already measured at 7B, and each one multiplies a real bill.
EXP = "E2"

# Sandbox-only extras cost no model calls. [] for none.
EXTRA = []

# ── the protocol for this arm, in one place ─────────────────────────────────
# --universe sweep   30 tasks, not the 99-task corpus E1's preset defaults to.
#                    fleet.sh forwards this to eval_shard.sh --dry-run when it
#                    sizes the universe, so --from/--to are not needed.
# --seeds "1 2 3"    what the qwen ablation arms ran. e3_steer_only pairs on 297
#                    cells, not 495, so the steering comparison already lives at
#                    3 seeds; running 5 here would be paid for and then cut back.
# --reasoning-effort in the cache key AND the cell key. Change it and every cell
#                    re-keys and is paid for twice. Decide once.
# no --port          preflight_backend() returns immediately on cloud; there is
#                    no local server to probe.
SHARDS_CLOUD = 4        # NOT {SHARDS}: that 6 was sized for OLLAMA_NUM_PARALLEL=1.
                        # On cloud the shards really do generate in parallel, so
                        # the limits are the API's rate limit and the cap split.
EFFORT = "medium"

# ── guards, in the order a mistake gets expensive ───────────────────────────
# The routing predicate is the repository's own, not a copy: src.llm dispatches
# on the LEADING TOKEN of the id, so "o4-mini" and "o4-mini-2025-04-16" take
# max_completion_tokens and refuse temperature, while anything starting "gpt"
# does not. gpt-4o-mini would therefore be sent temperature + max_tokens and
# would RUN - a complete, plausible, wrongly-labelled arm - if --reasoning-effort
# did not happen to reject it first. Do not rely on that accident.
from src.llm import _REASONING_PREFIXES, _is_reasoning

if not _is_reasoning(MODEL):
    raise SystemExit(
        f"MODEL={MODEL!r} is not an o-series id: src.llm matches the leading "
        f"token against {_REASONING_PREFIXES}, and {MODEL.split('-', 1)[0]!r} is "
        f"not in it. If you meant the reasoning model, the id is 'o4-mini'.")
assert CLOUD_API_KEY, "CLOUD_API_KEY rong - dien vao §1 va CHAY LAI cell §1"
assert RUN_DIR and RUN_DIR != "official-2026-09-01", \
    "doi RUN_DIR, dung ghi de len run qwen"

print(f"{EXP}  ·  model={MODEL}  ·  effort={EFFORT}  ·  {RUN_DIR}")
print(f"cap ${CLOUD_BUDGET} / {SHARDS_CLOUD} shard = "
      f"${CLOUD_BUDGET/SHARDS_CLOUD:.2f} moi shard")
print("doc lai dong tren TRUOC KHI cell ket thuc - do la lan cuoi con mien phi\n")

env = (f"LLM_API_KEY={CLOUD_API_KEY} "
       f"PRICE_IN_PER_MTOK={CLOUD_PRICE_IN} PRICE_OUT_PER_MTOK={CLOUD_PRICE_OUT} "
       f"BUDGET_USD_CAP={CLOUD_BUDGET} CONTEXT_LENGTH={CLOUD_CONTEXT}")
extra = " ".join(EXTRA)

!{env} bash scripts/fleet.sh eval --exp {EXP} --shards {SHARDS_CLOUD} \
    -- --backend cloud --model {MODEL} --universe sweep \
       --seeds "1 2 3" --reasoning-effort {EFFORT} {extra}
