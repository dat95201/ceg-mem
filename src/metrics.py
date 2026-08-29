"""Data schema for the repair loop (src/loop.py).

One RoundRecord is written per round - one proposal plus, if it wasn't
guarded away, one oracle call - not per episode, so an interrupted run still
leaves usable rows. Appended as JSON Lines, same convention as
data/calls.jsonl in src/llm.py.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

DEFAULT_METRICS_LOG = pathlib.Path("data/episodes.jsonl")


@dataclasses.dataclass(frozen=True)
class RoundRecord:
    """One round within one episode of the repair loop."""

    episode_id: str
    task: str
    mode: str            # "no_memory" | "untyped" | "typed"
    round_index: int      # 1-based
    patch: str
    accept: bool
    counterexample_args: list | None
    reason: str | None
    examples_tried: int
    coarse_type: str | None   # FailureType.key, or None once accept=True
    fine_type: str | None
    model: str | None
    seed: int = 0
    guarded: bool = False        # True: candidate blocked before the oracle ran (Eq. (2))
    guard_evaluations: int = 0   # stored counterexamples re-run this round (Proposition 4.5 cost)
    guard_on: bool = True        # ablation (E3): guard disabled entirely when False
    steer_on: bool = True        # ablation (E3): exclusion_block suppressed when False
    max_examples: int = 100      # oracle informativeness sweep (E4)
    typing_noise_c: float = 1.0  # typing coherence sweep (E5)
    # The c axis's null, and deliberately a separate field rather than a
    # sentinel value of typing_noise_c: c=0.0 is NOT random assignment
    # (src.memory.TypedMemory explains why), so a reader who saw only c would
    # conclude the sweep already contained its own control.
    typing_random: bool = False
    granularity: str = "fine"    # which theta() the memory indexed by
    # E1 ran the no-memory arm past its first accept on purpose, for an unbiased
    # pi_hat/q_hat corpus. That changes what a round *means*, so it belongs in
    # the cell identity (scripts/run_eval.py, scripts/freeze_results.py) and in
    # summarize_episode's decision about which rounds are comparable.
    force_full_budget: bool = False
    # The key memory actually filed this attempt under, at the active
    # granularity. Equal to coarse_type/fine_type except under typing noise,
    # where TypedMemory re-files a refutation at a different location (Def. 3.1
    # coherence c). Logging both is what lets scripts/measure_anchoring.py tell
    # "the correct class was excluded because we mistyped" from "the correct
    # class was excluded because theta cannot separate it from a wrong one",
    # without re-deriving the loop's typing-noise RNG from outside.
    stored_type: str | None = None
    # Set when the oracle could not reach a verdict (src.oracle.OracleResult):
    # the round consumed budget but is not a refutation and carries no type.
    oracle_error: str | None = None
    # Set when the *proposer* failed to return a patch at all
    # (src.proposer.TruncatedResponse). Same treatment as oracle_error: the
    # round spent a proposal, but nothing was refuted, so no type is recorded
    # and memory is left untouched. Kept as a separate field because the two
    # have different causes and different fixes - one is a property of the
    # test pool, the other of the response budget (paper SS VI-D-a).
    proposal_error: str | None = None

    # ---- redundancy, one definition for every arm -------------------------
    # The failure type of the stored counterexample that blocked this round
    # (src.memory.GuardResult.blocked_by), or None if the round was not guarded.
    # This closes DESIGN.md SS6's first open item: without it, `redundant_attempts`
    # counted type repeats in the typed arm and guard firings in the untyped one,
    # while Theorem 4.3(b) assigned both the same R. With it, a guarded round in
    # either arm carries the type of the thing it repeated.
    blocked_by_type: str | None = None
    # The same label as theta assigned it, before TypedMemory re-filed it under
    # the c-sweep's noised location. Equal to blocked_by_type in every arm and
    # at c=1.0; different only where memory was wrong about the class. Both are
    # needed or the c-sweep's per-type breakdown compares the typed arm's
    # beliefs against the other arms' facts.
    blocked_by_type_true: str | None = None

    # ---- cost: the join to data/calls.jsonl -------------------------------
    # cache_key is the join key (the draw nonce is NOT - src.loop.proposal_nonce
    # omits the mode so the unconditioned arms share a draw, which means one
    # nonce maps to several ledger rows once the arms diverge). The token and
    # second counts are copied here so a metric can be computed without the
    # ledger at all; reasoning_tokens is 0 on a chat model and non-zero only on
    # the o-series (src.llm).
    cache_key: str | None = None
    # "usage" = the server reported these counts. "tokenizer"/"heuristic" = they
    # were reconstructed on a cache hit from a blob written before the ledger
    # join existed (src.llm._recover_counts). Carried per round because the
    # reconstruction is not uniform across arms: the unconditioned arms replay
    # E1's draws and are the reconstructed ones. Pooling reconstructed counts
    # with reported ones without saying so is how a token comparison between
    # arms becomes a comparison between measurement regimes.
    tokens_method: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    reasoning_effort: str | None = None
    # Wall clock, split three ways because the three are paid to different
    # parties: the model, the sandbox running the oracle, and the sandbox
    # re-running stored counterexamples for the guard (Prop. 4.5's cost in
    # seconds rather than in evaluations).
    llm_sec: float | None = None
    oracle_sec: float | None = None
    guard_sec: float | None = None

    # ---- arm identity for the new conditions ------------------------------
    # The oracle was run on guarded rounds too, for the record only - it did not
    # reach memory and did not change the loop. Changes what a round *is*
    # (a guarded round now carries a true type), so it is in the cell key.
    audit_guarded: bool = False
    # A guarded round costs a model call but not a unit of the attempt budget.
    # Off by default, because it changes what `budget` MEANS and therefore what
    # every success@B curve is a curve of - the two accountings are different
    # experiments and must never be pooled. Part of the cell key for that reason.
    #
    # Why it exists: with guarded rounds charged to the budget, the guard is
    # provably outcome-neutral. It only ever blocks a candidate some stored
    # counterexample already refutes - i.e. one that was going to fail anyway -
    # so under common random numbers the round at which the first ACCEPTED patch
    # appears is invariant to the guard, and `untyped` reproduces `no_memory`
    # to the last decimal at every budget (docs/DIAGNOSIS.md SS1). Corollary 4.4
    # cannot be tested at all in that accounting. Free guarded rounds are what
    # convert a blocked proposal into a retained attempt and make it testable.
    free_guarded_rounds: bool = False
    # Which unit of the attempt budget this round consumed, or None for a round
    # that consumed none (only possible under free_guarded_rounds). Logged rather
    # than re-derived: round_index counts DRAWS and no longer equals the attempt
    # count once the two come apart, and every success@B curve needs the latter.
    attempt_index: int | None = None
    # Program executions this round: the oracle's `examples_tried` on an oracle
    # round, `guard_evaluations` on a guarded one. THE primary cost unit.
    #
    # Oracle calls are not a cost unit. src.memory._still_refutes runs the
    # candidate in the sandbox exactly as the oracle does, so counting oracle
    # calls while ignoring guard evaluations charges one arm for work the other
    # also performs - and that single choice is the difference between the 2.50x
    # saving the abstract claims for `untyped` and the 1.19x it actually buys.
    # Recorded explicitly rather than summed downstream because --audit-guarded
    # fills examples_tried on guarded rounds for bookkeeping the loop never used,
    # and a downstream sum would bill the arm for it.
    sandbox_runs: int = 0

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def append_round(record: RoundRecord, path: pathlib.Path = DEFAULT_METRICS_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record.to_json()) + "\n")


def load_rounds(path: pathlib.Path = DEFAULT_METRICS_LOG, *, dedupe: bool = True) -> list[dict[str, Any]]:
    """Read the round log, last write wins per (episode_id, round_index).

    src.loop derives episode_id deterministically from the experiment cell, so a
    cell that died halfway and was re-run appends a *second* copy of its early
    rounds under the same id. Collapsing on read is what makes that rewrite mean
    what it looks like; without it those rounds would be counted twice and the
    re-run would look like a longer episode than it was.

    Order is deterministic: first appearance of each key, carrying the last
    value written for it.
    """
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not dedupe:
        return rows
    by_round: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        by_round[(row["episode_id"], row["round_index"])] = row
    return list(by_round.values())


def group_by_episode(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_episode: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_episode.setdefault(row["episode_id"], []).append(row)
    return by_episode


def _sum_or_none(rows: list[dict], *fields: str) -> float | None:
    """Sum of `fields` over `rows`, or None if not one row measured any of them.

    The distinction is the whole point: 0.0 means "measured, and it was free",
    None means "nobody looked". Summing `or 0.0` conflates them, and conflates
    them hardest on the arms that replay a cache.
    """
    if not any(r.get(f) is not None for r in rows for f in fields):
        return None
    return sum(r.get(f) or 0.0 for r in rows for f in fields)


def build_crn_type_index(rows: list[dict[str, Any]]) -> dict[tuple, str]:
    """(task, seed, round_index) -> the true fine_type of that draw.

    Built from the no-memory arm, and this is not a convenience - it is the only
    way the type-based redundancy counts mean the same thing in every arm.

    A guarded round never reaches the oracle, so it carries no type. In the flat
    arm 81.6% of rounds are guarded, so 81.6% of that arm's rounds are invisible
    to any count keyed on `fine_type` - and the arm that guards MOST looks least
    redundant, for a purely procedural reason. --audit-guarded exists to fill
    those in, but it costs the oracle time the whole method exists to save and it
    only ever ran on the 30-task sweep.

    Common random numbers make it free instead. src.loop.proposal_nonce keys a
    draw on (task, seed, round) and omits the mode, so every arm whose prompt is
    unconditioned - no_memory, untyped, and typed with steer off - draws the
    BYTE-IDENTICAL patch at the same round index. Measured on the frozen log:
    4,269 of 4,269 guarded untyped rounds and 1,641 of 1,641 guarded guard-only
    rounds have a paired no-memory round whose patch matches exactly and whose
    type the oracle already paid for.

    It does NOT cover the steered typed arm - its exclusion block changes the
    prompt, so its draws diverge after the first exclusion and only 13.6% still
    pair. That arm needs --audit-guarded, and the caller is told which rounds
    stayed unknown rather than being handed a silently partial count.
    """
    index: dict[tuple, str] = {}
    for r in rows:
        ft = r.get("fine_type")
        if ft is None:
            continue
        # Two sources, and the patch text is part of the key in both, so a draw
        # that did NOT match is never silently filled from a different patch.
        #
        #   no_memory, unguarded  - the CRN join above. Covers every arm whose
        #     prompt is unconditioned.
        #   --audit-guarded, any mode - E8 paid the oracle on guarded rounds for
        #     exactly this, and the audit does not change the prompt, so a
        #     steered typed round pairs with its own audit twin. That is the only
        #     way to reach the steered arm, and it reaches it only over E8's
        #     universe: 439 of 439 censored typed rounds on the 30 sweep tasks at
        #     seeds 1-3, and none of the 2,176 outside it.
        from_no_memory = r.get("mode") == "no_memory" and not r.get("guarded", False)
        from_audit = r.get("audit_guarded", False)
        if not (from_no_memory or from_audit):
            continue
        index[(r["task"], r.get("seed", 0), r["round_index"], r.get("patch") or "")] = ft
    return index


def _round_runs(row: dict[str, Any]) -> int:
    """Program executions this round, back-compatible with pre-`sandbox_runs` logs.

    The stored field is authoritative when present. For older rows it is
    reconstructed: a guarded round only ever ran its guard evaluations, and an
    oracle round ran those plus the oracle's examples. --audit-guarded rows are
    the one case the reconstruction gets deliberately different from a naive sum -
    they carry examples_tried on a *guarded* round for bookkeeping the loop never
    acted on, and billing the arm for it would make the audit cell incomparable
    with the grid cell it exists to explain.
    """
    stored = row.get("sandbox_runs")
    if stored:
        return stored
    evals = row.get("guard_evaluations") or 0
    if row.get("guarded", False):
        return evals
    return evals + (row.get("examples_tried") or 0)


def summarize_episode(rows: list[dict[str, Any]],
                      crn_types: dict[tuple, str] | None = None) -> dict[str, Any]:
    """Roll up one episode's RoundRecord rows into the per-episode summary
    shared by scripts/freeze_results.py, scripts/analyze.py,
    scripts/fit_theory.py and scripts/check_consistency.py - one
    implementation so every consumer agrees on what "oracle calls to repair"
    and "redundant attempt" mean (Section 4/5 of the paper).

    Two failure-taxonomy quantities (Table 6 in the paper) fall out directly:
      - every guarded round is, by construction, a redundant attempt: its
        type matched a bucket memory had already eliminated (that's the only
        way to be guarded), so the guard did its job of avoiding an oracle
        call for it.
      - a "guard miss" is a round that reached the oracle anyway (not
        guarded) but whose type turns out (post-hoc, from theta()) to match
        a type an *earlier* round in this same episode already had refuted -
        i.e. a round the guard should have caught but its cheap a-priori
        type guess (src.typer.edit_location) missed.
    redundant_attempts = n_guarded + guard_miss follows from that directly.

    **Rounds after the first accept are excluded from every one of those
    counts.** E1 runs the no-memory arm to the full budget on purpose
    (force_full_budget, so pi_hat/q_hat are estimated from an unbiased corpus -
    see scripts/fit_theory.py) and E2 reuses those same episodes as its
    no-memory arm. The memory arms stop at their first accept. Counting the
    no-memory arm's extra rounds would inflate exactly the redundancy metric
    Table 2/3 compares the arms on - a purely procedural difference showing up
    as an effect. So the summary reports what a loop that stopped at the first
    accept would have done; the untruncated counts stay available as
    n_rounds_logged / n_oracle_calls_logged for audit. For an episode that
    already stopped at its first accept the two are identical.
    """
    rows = sorted(rows, key=lambda r: r["round_index"])
    oracle_rows_logged = [r for r in rows if not r.get("guarded", False)]
    accepted_rows = [r for r in oracle_rows_logged if r["accept"]]
    accepted = bool(accepted_rows)
    first_accept_round = accepted_rows[0]["round_index"] if accepted else None

    effective = rows if not accepted else [r for r in rows if r["round_index"] <= first_accept_round]
    oracle_rows = [r for r in effective if not r.get("guarded", False)]
    oracle_calls_to_accept = len(oracle_rows) if accepted else None

    # Type repeats, measured on every round that carries a type. A guarded round
    # normally carries none - the oracle never ran - so it can only join this
    # count under --audit-guarded, which runs the oracle on guarded rounds for
    # the record. That is the whole point of the flag: without it the guarded
    # rounds are censored, and an arm that guards a lot looks less redundant
    # than an arm that guards none purely because its evidence went unmeasured.
    guard_miss = 0
    type_repeats = 0
    eliminated_before: set[str] = set()
    for r in effective:
        if r["accept"]:
            continue
        tkey = r.get("fine_type")
        if tkey is None:
            continue  # accepted, inconclusive, or guarded without an audit
        if tkey in eliminated_before:
            type_repeats += 1
            if not r.get("guarded", False):
                guard_miss += 1
        # Guarded rounds are TESTED against what memory holds but never SEED
        # it. Their type exists only because --audit-guarded paid the oracle to
        # find out, and that verdict is deliberately never stored
        # (src.loop.py), so a later round matching it is not a repeat the guard
        # could have caught. Seeding from it made an --audit-guarded cell report
        # strictly more redundancy than its own non-audit twin - the censoring
        # artefact P0-1(b) exists to remove, with the sign flipped.
        if not r.get("guarded", False):
            eliminated_before.add(tkey)

    # ---- redundancy, measured the same way in every arm ------------------
    # Three defects in the block above, all of which made Thm 4.3(b) unreadable
    # and none of which is the force_full_budget truncation (that one is right:
    # `effective` clips the no-memory arm at its first accept, and the arms come
    # out to the same 9.92 rounds).
    #
    #   1. CENSORING. A guarded round carries no type, so it is skipped both as
    #      a possible repeat and as a seed. 81.6% of the flat arm's rounds are
    #      guarded, so the arm that guards most is measured least.
    #   2. SUMMING. `redundant_attempts = n_guarded + guard_miss` adds a repeat
    #      the guard CAUGHT to one it MISSED. Those have opposite sign: catching
    #      is the saving, missing is the failure. And no_memory's n_guarded is 0
    #      by construction, so its total is a different quantity entirely.
    #   3. ARM-DEPENDENT REFERENCE SET. `eliminated_before` seeds only from
    #      unguarded rounds, so an arm that guards more has fewer types on record
    #      and therefore fewer DETECTABLE repeats. Byte-identical draw sequences
    #      scored 4.82 (no_memory) against 3.08 (untyped) for that reason alone.
    #
    # The fix is one reference set, shared by every arm: the types of all draws
    # so far, recovered through common random numbers where the round itself was
    # censored. Redundancy PRESENT is then a property of the proposer and is
    # invariant across arms - verified at 530/530 cells - so it doubles as a
    # falsifier for the pairing. What memory changes is where that redundancy is
    # paid: `redundancy_caught` by the guard, `redundancy_paid` by the oracle.
    seen_any: set[str] = set()
    redundancy_caught = redundancy_paid = redundancy_unknown = 0
    for r in effective:
        if r["accept"]:
            continue
        tkey = r.get("fine_type")
        if tkey is None and r.get("guarded", False) and crn_types is not None:
            tkey = crn_types.get(
                (r["task"], r.get("seed", 0), r["round_index"], r.get("patch") or ""))
        if tkey is None:
            # Only reachable for the steered typed arm, whose draws diverge from
            # the no-memory sequence. Counted, never silently dropped: a count
            # with 4.27 unknown rounds per episode is not a count.
            redundancy_unknown += 1
            continue
        if tkey in seen_any:
            if r.get("guarded", False):
                redundancy_caught += 1
            else:
                redundancy_paid += 1
        seen_any.add(tkey)

    n_guarded = len(effective) - len(oracle_rows)
    # The arm-neutral redundancy count (DESIGN.md SS6, first open item). A guarded
    # round is redundant because it provably reproduced a counterexample already
    # in memory - `blocked_by_type` names which one - and that holds for the flat
    # untyped scan exactly as it does for the type-indexed bucket. Reported
    # alongside the old n_guarded + guard_miss, which is kept under its own name
    # so nothing that already reads it silently changes meaning.
    n_blocked_known = sum(1 for r in effective if r.get("blocked_by_type"))
    first = rows[0]
    tokens_in = sum(r.get("prompt_tokens") or 0 for r in effective)
    tokens_out = sum(r.get("completion_tokens") or 0 for r in effective)
    tokens_reasoning = sum(r.get("reasoning_tokens") or 0 for r in effective)
    have_tokens = any(r.get("prompt_tokens") is not None for r in effective)
    # #16 context tokens per round. The plan calls this "the place a typed index
    # beats a flat log most clearly", and it is a *shape*, not a total. Measured,
    # it does not: typed grows 80.7 tokens/round against untyped's 3.5, so the
    # totals can be close while the curves are nothing alike. Aligned to
    # round_index so an arm's curve survives averaging across episodes of
    # different length; None where the round replayed a cache entry written
    # before the ledger join existed (scripts/backfill_cache_tokens.py).
    by_round = {r["round_index"]: r.get("prompt_tokens") for r in effective}
    prompt_tokens_by_round = [by_round.get(i + 1) for i in range(len(effective))]
    # #6 redundant-token share: of the completion tokens this episode spent, how
    # many went on rounds that reproduced something memory already had. Uses
    # P0-1(a)'s arm-neutral definition - blocked_by_type is set in every arm -
    # so it is not the typed-only "type repeat" count. None, not 0.0, when the
    # tokens were never measured.
    redundant_out = sum(r.get("completion_tokens") or 0 for r in effective
                        if r.get("blocked_by_type"))
    have_out = any(r.get("completion_tokens") is not None for r in effective)
    return {
        "episode_id": first["episode_id"], "task": first["task"], "mode": first["mode"],
        "seed": first.get("seed", 0),
        "guard_on": first.get("guard_on", True), "steer_on": first.get("steer_on", True),
        "max_examples": first.get("max_examples", 100), "typing_noise_c": first.get("typing_noise_c", 1.0),
        "granularity": first.get("granularity", "fine"),
        "force_full_budget": first.get("force_full_budget", False),
        "audit_guarded": first.get("audit_guarded", False),
        # THIS LINE WAS MISSING, and its absence was silently contaminating every
        # typed figure in the study. analyze._is_main_grid, freeze._cell_key and
        # measure_redundancy.is_main all read the arm's identity off the FROZEN
        # episode and all test `not ep.get("typing_random", False)` - a key that
        # is never written reads False, so E5-random's 90 episodes passed every
        # one of those filters and pooled into `typed`, which is why that arm had
        # 620 episodes against untyped's 530. c=0.0 is not random assignment
        # (src.memory.TypedMemory says why), so those 90 are a different arm.
        "typing_random": first.get("typing_random", False),
        # Must travel for the same reason typing_random must: analyze._is_main_grid,
        # freeze_results._cell_key and measure_redundancy.is_main all read the arm's
        # identity OFF THE FROZEN EPISODE, and a key that is never written reads
        # False - which would pool a free-guarded arm straight into the charged one
        # and put two different success@B curves on one axis.
        "free_guarded_rounds": first.get("free_guarded_rounds", False),
        "reasoning_effort": first.get("reasoning_effort"),
        "n_rounds": len(effective), "n_oracle_calls": len(oracle_rows), "n_guarded": n_guarded,
        # ---- the primary cost unit ----------------------------------------
        # Program executions, not oracle calls. src.memory._still_refutes runs the
        # candidate in the sandbox exactly as the oracle does, so an accounting that
        # counts oracle calls and ignores guard evaluations bills one arm for work
        # the other also performs. Measured over the same `effective` prefix as every
        # other count here, so it is comparable across arms with different stopping
        # behaviour. Falls back to the per-round sum for rows logged before
        # RoundRecord.sandbox_runs existed, where a guarded round's only executions
        # were its guard evaluations and an oracle round's were its examples.
        "sandbox_runs": sum(_round_runs(r) for r in effective),
        "sandbox_runs_to_accept": (sum(_round_runs(r) for r in oracle_rows)
                                   + sum(_round_runs(r) for r in effective
                                         if r.get("guarded", False))) if accepted else None,
        "attempts": sum(1 for r in effective
                        if r.get("attempt_index") is not None) or len(effective),
        "n_rounds_logged": len(rows), "n_oracle_calls_logged": len(oracle_rows_logged),
        "guard_evaluations": sum(r.get("guard_evaluations", 0) for r in effective),
        "n_inconclusive": sum(1 for r in effective if r.get("oracle_error")),
        "n_proposal_errors": sum(1 for r in effective if r.get("proposal_error")),
        "accepted": accepted, "first_accept_round": first_accept_round,
        "oracle_calls_to_accept": oracle_calls_to_accept,
        "guard_miss": guard_miss, "redundant_attempts": n_guarded + guard_miss,
        # Thm 4.3(b), measured on one arm-neutral reference set. `redundancy_paid`
        # is the outcome variable the theorem is about - redundant attempts that
        # reached the oracle. `redundancy_present` is invariant under common
        # random numbers and is there to be CHECKED, not compared: if two
        # unconditioned arms disagree on it, the pairing is broken.
        "redundancy_present": redundancy_caught + redundancy_paid,
        "redundancy_caught": redundancy_caught,
        "redundancy_paid": redundancy_paid,
        # Rounds whose type could be recovered from neither the round itself nor
        # the paired no-memory draw. Non-zero only in the steered typed arm.
        "redundancy_unknown": redundancy_unknown,
        # The arm-neutral pair. `type_repeats` needs --audit-guarded to be
        # comparable across arms; `blocked_known_counterexample` never does.
        "type_repeats": type_repeats,
        "blocked_known_counterexample": n_blocked_known,
        # Cost. None rather than 0 when no round carried token counts, so a
        # freeze produced before the ledger join existed reads as "not measured"
        # instead of as "cost nothing".
        "tokens_in": tokens_in if have_tokens else None,
        "tokens_out": tokens_out if have_tokens else None,
        "tokens_reasoning": tokens_reasoning if have_tokens else None,
        "tokens_total": (tokens_in + tokens_out) if have_tokens else None,
        # #16 (curve) and #6 (share). Both None-safe: an unmeasured episode must
        # not read as a cheap one.
        "prompt_tokens_by_round": prompt_tokens_by_round if have_tokens else None,
        # Which regime produced this episode's counts. "usage" throughout is the
        # clean case; a mix means some rounds replayed an old cache entry and
        # their counts were reconstructed.
        "tokens_methods": sorted({r.get("tokens_method") for r in effective
                                  if r.get("tokens_method")}) or None,
        "redundant_token_share": ((redundant_out / tokens_out) if tokens_out else 0.0)
                                 if have_out else None,
        # Same None-vs-0.0 discipline as the token fields above, which these
        # did not have: an episode that replayed entirely from cache has no
        # llm_sec on any round, and summing `or 0.0` reported #17 wall-clock as
        # a measured 0.0 - "this cost nothing" - for exactly the arms that share
        # E1's draws.
        "llm_sec": _sum_or_none(effective, "llm_sec"),
        "oracle_sec": _sum_or_none(effective, "oracle_sec"),
        "guard_sec": _sum_or_none(effective, "guard_sec"),
        "wall_sec": _sum_or_none(effective, "llm_sec", "oracle_sec", "guard_sec"),
        # Corollary 4.4: a repair found within the budget, as a 0/1 per episode
        # so a per-task mean is the budgeted-success *rate*. Unlike
        # oracle_calls_to_accept this is defined on every episode, which is what
        # makes the low-pi strata - where the corollary's "whenever B binds"
        # actually holds - measurable at all.
        "success_at_b": 1.0 if accepted else 0.0,
        # Proposals = model calls, the proposal's second budget. Every round
        # spends exactly one, guarded or not.
        "proposals": len(effective),
    }


if __name__ == "__main__":
    demo = RoundRecord(
        episode_id="demo", task="gcd", mode="typed", round_index=1,
        patch="def gcd(a, b):\n    return a\n", accept=False,
        counterexample_args=[10, 4], reason="expected 2, got 10",
        examples_tried=3, coarse_type=None, fine_type=None, model=None,
    )
    print(json.dumps(demo.to_json(), indent=2))
