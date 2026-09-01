#!/usr/bin/env bash
# Where this run's artifacts live - the bash half of src/paths.py.
#
# Source it immediately after `cd "$ROOT"`, before any variable that names a
# path under data/ or logs/:
#
#     ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
#     cd "$ROOT"
#     . scripts/run_dir_paths.sh
#
# and then write "$RUN_DATA/tasks.json" instead of "data/tasks.json".
#
# Defines exactly three names plus one function:
#
#   RUN_SLUG   ""                     or  "official-2026-09-01"
#   RUN_DATA   "data"                 or  "data/official-2026-09-01"
#   RUN_LOGS   "logs"                 or  "logs/official-2026-09-01"
#   run_banner <stage>                one line to stderr saying where output goes
#
# Relative, not absolute, because every entry point cd's to $ROOT first and the
# shard headers, provenance blobs and log lines that carry these strings are all
# nicer to read - and to diff between machines - relative.
#
# RUN_DIR is NOT exported here; it is read from the environment the caller
# already has, and python children read the same variable through src/paths.py.
# That is the whole coupling: one env var, two readers, no argument plumbing.
# Keep this file and src/paths.py in agreement - tests/test_run_dir.py asserts
# they resolve identically for a handful of values.

RUN_SLUG="${RUN_DIR:-}"
RUN_SLUG="${RUN_SLUG#"${RUN_SLUG%%[![:space:]]*}"}"   # ltrim
RUN_SLUG="${RUN_SLUG%"${RUN_SLUG##*[![:space:]]}"}"   # rtrim
while [[ "$RUN_SLUG" == /* ]]; do RUN_SLUG="${RUN_SLUG#/}"; done
while [[ "$RUN_SLUG" == */ ]]; do RUN_SLUG="${RUN_SLUG%/}"; done

if [[ -n "$RUN_SLUG" ]]; then
  RUN_DATA="data/$RUN_SLUG"
  RUN_LOGS="logs/$RUN_SLUG"
else
  RUN_DATA="data"
  RUN_LOGS="logs"
fi

# Deliberately NO mkdir here. Sourcing a path resolver should not touch the
# filesystem: every stage already creates the directories it writes into
# (oracle_gate.sh, screen_shard.sh and eval_shard.sh each have their own
# `mkdir -p`), and a resolver that creates a directory just to answer a
# question litters data/ with a folder per typo - and per test case.

run_banner() {
  local stage="${1:-stage}"
  local run="${RUN_SLUG:-(default - RUN_DIR unset)}"
  printf '[%s] run=%s  ->  %s/\n' "$stage" "$run" "$RUN_DATA" >&2
}
