"""Statistics: bootstrap CIs, Wilcoxon signed-rank, Vargha-Delaney A12,
Benjamini-Hochberg correction - the protocol the paper already specifies
(Section 5), applied to data/results_real.json instead of the synthetic
model. Produces the Table-2/3-style comparison (oracle calls to repair,
redundant attempts) across the three memory conditions, per stratum and
overall, paired by task.

The four statistical primitives below are pure functions (no file I/O), kept
separate from compare_conditions() so scripts/check_consistency.py can call
the same functions directly on freshly-loaded data instead of trusting this
script's own output file.
"""
from __future__ import annotations

import argparse
import itertools
import json
import pathlib
import sys

import numpy as np
from scipy import stats as scipy_stats

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.select_corpus import BANDS, PRIMARY_BANDS  # noqa: E402

from src.paths import DATA_DIR, announce  # noqa: E402
# The ChatRepair `transcript` arm (E6) was removed unrun on 2026-08-29: it
# tested no surviving claim, and the "typed index is flat, transcript grows
# linearly" claim it existed for is already falsified by the typed arm alone
# (80.7 tokens/round against untyped's 3.5). docs/DIAGNOSIS.md.
MODES = ("no_memory", "untyped", "typed")
def _strata_in_use() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(all strata, strata the BH family corrects across), read off the freeze.

    Hardcoding these was wrong: the corpus can be frozen two ways and they carry
    different labels. scripts/select_corpus.py bands on measured pi and marks
    two of five bands as controls; a rating-floor corpus has no pi measurement
    at all and is split into three proxy terciles, every one of which carries
    the same prediction. Reading data/strata.json keeps the analysis honest
    about which corpus it is analysing instead of asserting one.
    """
    path = DATA_DIR / "strata.json"
    if not path.exists():
        return tuple(name for name, _, _ in BANDS), PRIMARY_BANDS
    blob = json.loads(path.read_text())
    seen = []
    for t in blob.get("tasks", []):
        if t["stratum"] not in seen:
            seen.append(t["stratum"])
    primary = tuple(blob.get("primary_bands") or seen)
    return tuple(seen), primary


STRATA, BH_STRATA = _strata_in_use()


def bootstrap_ci(values: list[float], *, n_resamples: int = 10_000, alpha: float = 0.05, seed: int = 0) -> dict:
    """95% CI on the mean, resampling `values` (one value per task) with replacement.

    `values` is sorted first, and that is load-bearing, not tidiness. The
    resampler draws *indices*, so permuting the input - a dict's .values() built
    in a different insertion order, say - yields a different realization of the
    same bootstrap distribution, and so an interval that differs in the 4th
    decimal, even with the seed pinned. scripts/check_consistency.py recomputes
    these numbers from episodes it re-sorted on the way in; without the sort the
    frozen and recomputed CIs disagree and the consistency check fails on data
    that is perfectly correct. Sorting picks a canonical realization; the
    multiset, hence the distribution being sampled, is untouched.
    """
    arr = np.sort(np.asarray(values, dtype=float))
    if arr.size == 0:
        return {"mean": None, "lo": None, "hi": None, "n": 0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_resamples, arr.size))
    resample_means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(resample_means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"mean": float(arr.mean()), "lo": float(lo), "hi": float(hi), "n": int(arr.size)}


def vargha_delaney_a12(xs: list[float], ys: list[float]) -> float | None:
    """P(X > Y) + 0.5*P(X == Y). 0.5 = no effect; >0.5 means xs tends larger.

    None, not NaN, when either side is empty: this value is written straight
    into data/analysis.json, and json.dumps renders a NaN as a bare `NaN`
    token that only Python's own parser will read back.
    """
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if xs.size == 0 or ys.size == 0:
        return None
    gt = (xs[:, None] > ys[None, :]).sum()
    eq = (xs[:, None] == ys[None, :]).sum()
    return float((gt + 0.5 * eq) / (xs.size * ys.size))


def wilcoxon_paired(xs: list[float], ys: list[float]) -> dict:
    """scipy's paired Wilcoxon signed-rank test; degenerates to p=1.0 when
    every pair is tied (scipy raises on an all-zero difference vector).

    Reports `n_effective` next to `n`, and that distinction is not cosmetic.
    scipy's default zero_method='wilcox' DISCARDS tied pairs before ranking, so
    the test's real sample size is the number of pairs that actually differ -
    while `n` counts every task fed in. DESIGN.md SS7 pre-registers a typed arm
    expected at exactly 0, which makes ties the common case rather than the
    rare one: on redundancy the two arms can agree on most tasks and disagree
    on a handful, and quoting `n` there overstates the evidence by an order of
    magnitude. Both are reported; `n_effective` is the one to put in the paper.
    """
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    n_effective = int(np.count_nonzero(xs - ys)) if xs.size else 0
    if xs.size == 0 or np.allclose(xs, ys):
        return {"statistic": None, "pvalue": 1.0, "n": int(xs.size), "n_effective": n_effective}
    stat, p = scipy_stats.wilcoxon(xs, ys, zero_method="wilcox")
    return {"statistic": float(stat), "pvalue": float(p),
            "n": int(xs.size), "n_effective": n_effective}


def paired_rate_ratio(xs: list[float], ys: list[float], *, n_resamples: int = 10_000,
                      seed: int = 0) -> dict:
    """sum(xs)/sum(ys) with a paired cluster bootstrap over tasks.

    DESIGN.md SS7's pre-registered primary estimand, and it exists because the
    rank tests degenerate against an arm expected at exactly 0: A12 saturates at
    1.00 for any nonzero comparator and carries no magnitude, and a bootstrap CI
    on the zero arm is [0, 0]. A ratio of totals still has a scale, and
    resampling *tasks* (not pairs) respects the fact that seeds within a task
    are not independent.

    Returns ratio=0.0 when the numerator is zero and the denominator is not -
    that is the result, not a failure - and None when the denominator is zero,
    where the ratio is undefined rather than infinite.
    """
    xs, ys = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if xs.size == 0:
        return {"ratio": None, "lo": None, "hi": None, "n": 0}
    order = np.argsort(ys, kind="stable")   # canonical realization; see bootstrap_ci
    xs, ys = xs[order], ys[order]
    denom = ys.sum()
    point = float(xs.sum() / denom) if denom else None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, xs.size, size=(n_resamples, xs.size))
    num_r, den_r = xs[idx].sum(axis=1), ys[idx].sum(axis=1)
    ok = den_r > 0
    if not ok.any():
        return {"ratio": point, "lo": None, "hi": None, "n": int(xs.size)}
    ratios = num_r[ok] / den_r[ok]
    lo, hi = np.percentile(ratios, [2.5, 97.5])
    return {"ratio": point, "lo": float(lo), "hi": float(hi), "n": int(xs.size),
            "n_resamples_usable": int(ok.sum())}


def benjamini_hochberg(pvalues: list[float], *, alpha: float = 0.05) -> list[bool]:
    """Standard BH step-up procedure. Returns a reject/accept flag per input
    p-value, in the original order (not the sorted order used internally)."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    max_rank_ok = -1
    for rank, i in enumerate(order):
        if pvalues[i] <= (rank + 1) / m * alpha:
            max_rank_ok = rank
    reject = [False] * m
    for rank in range(max_rank_ok + 1):
        reject[order[rank]] = True
    return reject


def _is_main_grid(ep: dict) -> bool:
    """The E2 comparison grid.

    force_full_budget has to match the arm: the no-memory arm *is* E1's
    full-budget corpus (see scripts/run_eval.py and scripts/fit_theory.py),
    while the memory arms stop at their first accept. Pinning it here keeps a
    stray second no-memory run - `run_eval.py --modes no_memory` without
    --force-full-budget, say - out of the averages instead of quietly pooling
    two arms that were produced under different stopping rules.
    """
    return (
        ep["guard_on"] and ep["steer_on"]
        and ep["max_examples"] == 100 and ep["typing_noise_c"] == 1.0
        and ep.get("force_full_budget", False) == (ep["mode"] == "no_memory")
        # E8-audit and E5-random run every knob above at its
        # main-grid value, so without these three they pooled straight into the
        # typed/untyped task means - and E8's whole point is that its guarded
        # rounds carry a type the main grid's do not, so one task mean ended up
        # averaging censored episodes with uncensored ones.
        and not ep.get("audit_guarded", False)
        and not ep.get("typing_random", False)
        # free_guarded_rounds redefines what one unit of budget buys, so a cell
        # run under it is a different experiment with a different success@B
        # curve. Same class of leak as typing_random: a key that is never
        # written reads False, and the arm pools into the charged grid.
        and not ep.get("free_guarded_rounds", False)
    )


# Cost-to-repair metrics: an unrepaired episode has no cost-to-repair, so it is
# excluded rather than counted as zero or as its truncated total. Every other
# metric averages over all episodes, accepted or not.
_ACCEPTED_ONLY = frozenset({"oracle_calls_to_accept", "sandbox_runs_to_accept"})


def _per_task_means(episodes: list[dict], mode: str, stratum: str | None, metric: str) -> dict[str, float]:
    """task -> mean of `metric` over that task's seeds, for one mode/stratum.

    metric == "oracle_calls_to_accept": averaged only over accepted episodes
    (unrepaired episodes have no verification-round count to report, exactly
    as Table 2's "oracle calls to repair" column is defined). This is the one
    metric the conditioning applies to, and it is why the proposal's Hard band
    needs a co-primary that survives non-acceptance.
    every other metric: averaged over every episode, accepted or not -
    redundant_attempts, guard_evaluations (Prop. 4.5), proposals, and
    success_at_b (Cor. 4.4, the budgeted-success rate).
    """
    by_task: dict[str, list[float]] = {}
    for ep in episodes:
        if ep["mode"] != mode or not _is_main_grid(ep):
            continue
        if stratum is not None and ep.get("stratum") != stratum:
            continue
        if metric in _ACCEPTED_ONLY and not ep["accepted"]:
            continue
        # .get, not [...]: a results file frozen before the token/redundancy
        # fields existed simply has no key, and that should read as "not
        # measured on this artifact" rather than crash the whole report.
        value = ep.get(metric)
        if value is None:
            continue
        by_task.setdefault(ep["task"], []).append(value)
    return {t: float(np.mean(v)) for t, v in by_task.items() if v}


def compare_conditions(episodes: list[dict], metric: str) -> dict:
    out: dict = {"metric": metric, "strata": {}}
    pending_bh: dict[tuple[str, str], list[tuple[str, float]]] = {}  # (comparison) -> [(stratum, pvalue)]

    for stratum_label in (*STRATA, "overall"):
        stratum = None if stratum_label == "overall" else stratum_label
        per_mode = {mode: _per_task_means(episodes, mode, stratum, metric) for mode in MODES}

        summary = {}
        for mode in MODES:
            # by task name, so the input order does not depend on how the caller
            # happened to order its episodes - see bootstrap_ci's docstring
            summary[mode] = bootstrap_ci([per_mode[mode][t] for t in sorted(per_mode[mode])])

        comparisons = {}
        for a, b in itertools.combinations(MODES, 2):
            shared_tasks = sorted(set(per_mode[a]) & set(per_mode[b]))
            xs = [per_mode[a][t] for t in shared_tasks]
            ys = [per_mode[b][t] for t in shared_tasks]
            wilcoxon = wilcoxon_paired(xs, ys)
            a12 = vargha_delaney_a12(xs, ys)
            comparisons[f"{a}_vs_{b}"] = {
                "n_tasks": len(shared_tasks), "a12": a12, **wilcoxon,
                # The pre-registered estimand (DESIGN.md SS7). Only meaningful for
                # a count metric, which is why it is skipped for the two rates.
                "rate_ratio": (paired_rate_ratio(xs, ys)
                               if metric not in ("success_at_b",) else None),
            }
            if stratum_label in BH_STRATA:
                pending_bh.setdefault((a, b), []).append((stratum_label, wilcoxon["pvalue"]))

        out["strata"][stratum_label] = {"summary": summary, "comparisons": comparisons}

    for (a, b), entries in pending_bh.items():
        strata_labels = [s for s, _ in entries]
        pvalues = [p for _, p in entries]
        rejected = benjamini_hochberg(pvalues)
        for stratum_label, is_significant in zip(strata_labels, rejected):
            out["strata"][stratum_label]["comparisons"][f"{a}_vs_{b}"]["bh_significant"] = is_significant

    return out


def _load_truly_correct(path: pathlib.Path | None = None) -> dict[str, bool]:
    """episode_id -> truly_correct, from data/overfit_checks.jsonl.

    Keyed on episode_id and nothing else. (task, mode, seed) is NOT unique: E2
    and E4-k20/k8/k3 all run mode="typed" over the sweep subset at seeds 1-3, so
    four legitimate audits collapse to one arbitrary survivor.
    """
    path = path or DATA_DIR / "overfit_checks.jsonl"
    if not path.exists():
        return {}
    out: dict[str, bool] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "episode_id" in row and "truly_correct" in row:
            out[row["episode_id"]] = bool(row["truly_correct"])
    return out


def cost_of_pass(episodes: list[dict], *, price_in: float | None = None,
                 price_out: float | None = None) -> dict:
    """v = C / R, the expected spend per correct patch, per arm.

    The formula is *Efficient Agents*' cost-of-pass, used as published rather
    than reinvented: C is the mean cost of one episode and R the fraction of
    episodes that produced a correct patch, so v is dollars per success and
    diverges - reported as None - for an arm that never succeeds.

    Two honesty requirements are built in:

      R uses `truly_correct` where the overfit audit ran, not `accepted`.
      Accepting is not repairing (DESIGN.md SS4): the loop accepts when no
      *sampled* case refutes. Scoring an overfitted patch as a pass would
      reward exactly the arm that produces more of them.

      The audit lives in data/overfit_checks.jsonl, NOT on the frozen episode -
      scripts/freeze_results.py builds episodes from summarize_episode alone and
      neither writes `truly_correct`, so this used to read it off a key that was
      never there and fall through to the accept rate on every run, silently.
      It is joined here, on episode_id, and the denominator is EVERY episode:
      the audit only exists for episodes that accepted, so dividing by the
      audited ones would compute the correct/plausible ratio and call it a
      resolve rate. On 100 episodes / 40 accepts / 30 correct that is 0.75
      instead of 0.30, understating v by 2.5x.

      The prices are the caller's, defaulting to $PRICE_IN_PER_MTOK /
      $PRICE_OUT_PER_MTOK. Both zero is the local backend, where the honest
      answer is that v was NOT measured: v is reported as None rather than as a
      confident $0.00 per correct patch. `repriced` says a real rate card was
      applied to a local run's token counts, so a caption can say so.
    """
    import os
    price_in = float(os.environ.get("PRICE_IN_PER_MTOK", 0)) if price_in is None else price_in
    price_out = float(os.environ.get("PRICE_OUT_PER_MTOK", 0)) if price_out is None else price_out
    # Was: `price_in == 0 and price_out == 0`, which was True in exactly the case
    # where nothing had been repriced - a caption generator reading the flag got
    # the opposite of the truth.
    unpriced = price_in == 0 and price_out == 0

    out: dict = {"price_in_per_mtok": price_in, "price_out_per_mtok": price_out,
                 "unpriced": unpriced, "repriced": not unpriced, "arms": {}}
    audit = _load_truly_correct()
    for mode in MODES:
        rows = [e for e in episodes if e["mode"] == mode and _is_main_grid(e)]
        priced = [e for e in rows if e.get("tokens_in") is not None]
        if not rows:
            continue
        # truly_correct when the audit ran on this episode, else fall back to
        # accepted and say so, so a missing audit never silently inflates R.
        # Denominator is every episode, numerator is the audited successes.
        n_audited = sum(1 for e in rows if e["episode_id"] in audit)
        if n_audited:
            successes = sum(1 for e in rows if audit.get(e["episode_id"]) is True)
            r = successes / len(rows)
            basis = f"truly_correct, over all {len(rows)} episodes ({n_audited} audited)"
        else:
            r = sum(1.0 if e["accepted"] else 0.0 for e in rows) / len(rows)
            basis = "accepted (no overfit audit found - R is an upper bound)"
        per_episode = [e["tokens_in"] / 1e6 * price_in + e["tokens_out"] / 1e6 * price_out
                       for e in priced]
        c = float(np.mean(per_episode)) if per_episode else None
        # None, not 0.0: with both prices at zero nothing was measured, and a
        # table that prints $0.00 per correct patch has invented a result.
        v = None if (unpriced or c is None or r <= 0) else float(c / r)
        out["arms"][mode] = {
            "n_episodes": len(rows), "n_priced": len(priced), "n_audited": n_audited,
            "resolve_rate": r, "resolve_basis": basis,
            "usd_per_episode": c,
            "cost_of_pass": v,
            # The plan asks for a bootstrap CI on v and bootstrap_ci is right
            # here; resampling the per-episode cost carries C's uncertainty at
            # a fixed R, which is the dominant term.
            "usd_per_episode_ci": (bootstrap_ci(per_episode) if per_episode else None),
            "cost_of_pass_ci": (
                {k: (None if v_ is None else v_ / r) for k, v_ in
                 bootstrap_ci(per_episode).items() if k in ("lo", "hi")}
                if per_episode and r > 0 and not unpriced else None),
        }
    return out


def context_tokens_by_round(episodes: list[dict]) -> dict:
    """#16: mean prompt tokens at round 1, 2, 3, ... per arm.

    This was built to show a typed index staying flat where a full log grows.
    Measured, it shows the opposite: 0.1 tokens/round for no_memory, 3.5 for
    untyped and 80.7 for typed, which is a 4x prompt over 20 rounds. The typed
    arm is the steepest of the three, and that is the finding - it is what the
    1.75x token cost in the totals is made of. Reported with the per-round n,
    because the tail of the curve is thin: only episodes that ran that long
    contribute, and those are the hard tasks.

    `slope_per_round` is an OLS fit over the rounds that have data - the single
    number the claim reduces to. Flat is the prediction for typed.
    """
    out: dict = {"note": "mean prompt tokens by 1-based round index, main grid only",
                 "arms": {}}
    for mode in MODES:
        curves = [e["prompt_tokens_by_round"] for e in episodes
                  if e["mode"] == mode and _is_main_grid(e)
                  and e.get("prompt_tokens_by_round")]
        if not curves:
            out["arms"][mode] = {"n_episodes": 0, "mean_by_round": None, "slope_per_round": None}
            continue
        width = max(len(c) for c in curves)
        means, counts = [], []
        for i in range(width):
            vals = [c[i] for c in curves if i < len(c) and c[i] is not None]
            means.append(float(np.mean(vals)) if vals else None)
            counts.append(len(vals))
        xs = [i + 1 for i, m in enumerate(means) if m is not None]
        ys = [m for m in means if m is not None]
        slope = float(np.polyfit(xs, ys, 1)[0]) if len(xs) >= 2 else None
        out["arms"][mode] = {
            "n_episodes": len(curves), "mean_by_round": means, "n_by_round": counts,
            "slope_per_round": slope,
            # How much of each curve is missing because the round replayed a
            # cache entry written before the ledger join existed. Reported, not
            # hidden: the arms that share E1's draws are the holed ones, so the
            # missingness is differential exactly across the comparison #16 is
            # for. scripts/backfill_cache_tokens.py closes it.
            "n_rounds_unmeasured": sum(1 for c in curves for v in c if v is None),
            "n_rounds_total": sum(len(c) for c in curves),
        }
    return out


def main() -> None:
    announce('analyze')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-path", type=pathlib.Path, default=DATA_DIR / "results_real.json")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "analysis.json")
    parser.add_argument("--price-in", type=float, default=None,
                        help="USD per Mtok input, for cost-of-pass. Defaults to "
                             "$PRICE_IN_PER_MTOK. Set both explicitly to reprice a "
                             "local (free) run against a hosted rate card - the "
                             "report records which was used")
    parser.add_argument("--price-out", type=float, default=None,
                        help="USD per Mtok output, for cost-of-pass")
    args = parser.parse_args()

    if not args.results_path.exists():
        raise SystemExit(f"{args.results_path} missing - run scripts/freeze_results.py first")
    episodes = json.loads(args.results_path.read_text())["episodes"]

    report = {
        "results_path": str(args.results_path),
        # The proposal's SS5 metric list, in its order. The first two were the
        # only ones computed before; the other three are recoverable from the
        # episode summaries that were already being written, at no extra cost.
        #   oracle_calls_to_accept  Thm 4.3(a), primary cost. Accepted episodes only.
        #   redundant_attempts      Thm 4.3(b). Every episode.
        #   success_at_b            Cor. 4.4, budgeted success. Every episode -
        #                           the co-primary that keeps the low-pi strata
        #                           informative when the round count cannot be
        #                           defined because nothing accepted.
        #   guard_evaluations       Prop. 4.5, the Theta(m) vs O(1) guard cost.
        #                           Predicted to *grow* with difficulty, so it is
        #                           measured best exactly where the round count
        #                           is measured worst.
        #   proposals               model calls, the other budget a practitioner pays.
        #
        # Everything below the original five is recoverable from the same
        # episode summaries at no extra cost, and each answers a question a
        # reviewer asks:
        #   blocked_known_counterexample  the arm-neutral redundancy count
        #                           (DESIGN.md SS6): rounds blocked because they
        #                           provably reproduced a stored counterexample.
        #                           Unlike redundant_attempts it means the same
        #                           thing in the flat and the typed arm.
        #   type_repeats            rounds whose theta repeats an earlier one.
        #                           Comparable across arms only under
        #                           --audit-guarded (E8); otherwise censored in
        #                           whichever arm guards more.
        #   tokens_in/out/total     what a practitioner actually pays. Input
        #                           dominates agentic cost, and it is where a
        #                           typed index was expected to win and does not.
        #   wall_sec                model + oracle + guard seconds.
        "metrics": {
            # PRIMARY. Program executions to repair - the only unit that charges
            # both arms for the same work. src.memory._still_refutes runs the
            # candidate in the sandbox exactly as the oracle does, so counting
            # oracle calls while ignoring guard evaluations bills one arm for
            # work the other also performs; that single choice is the difference
            # between untyped's headline 2.50x saving and the 1.19x it actually
            # buys, and between "Thm 4.3(a) rejected at 1.48x" and "Thm 4.3(a)
            # holds at 1.00x". See docs/DIAGNOSIS.md SS2-3.
            "sandbox_runs_to_accept": compare_conditions(episodes, "sandbox_runs_to_accept"),
            "sandbox_runs": compare_conditions(episodes, "sandbox_runs"),
            # SECONDARY, and mislabelled if reported without the line above:
            # this is oracle calls only, i.e. the guard's own executions are
            # free in this number and in no other.
            "oracle_calls_to_accept": compare_conditions(episodes, "oracle_calls_to_accept"),
            # Thm 4.3(b). `redundancy_paid` is the outcome variable - redundant
            # attempts that reached the oracle. The old `redundant_attempts` is
            # kept below under its own name so nothing that reads it changes
            # meaning, but it sums a repeat the guard caught with one it missed
            # and is censored in whichever arm guards more; do not report it.
            "redundancy_paid": compare_conditions(episodes, "redundancy_paid"),
            "redundancy_caught": compare_conditions(episodes, "redundancy_caught"),
            "redundancy_present": compare_conditions(episodes, "redundancy_present"),
            "redundant_attempts": compare_conditions(episodes, "redundant_attempts"),
            "success_at_b": compare_conditions(episodes, "success_at_b"),
            "guard_evaluations": compare_conditions(episodes, "guard_evaluations"),
            "proposals": compare_conditions(episodes, "proposals"),
            "blocked_known_counterexample": compare_conditions(episodes, "blocked_known_counterexample"),
            "type_repeats": compare_conditions(episodes, "type_repeats"),
            "tokens_in": compare_conditions(episodes, "tokens_in"),
            "tokens_out": compare_conditions(episodes, "tokens_out"),
            "tokens_total": compare_conditions(episodes, "tokens_total"),
            "redundant_token_share": compare_conditions(episodes, "redundant_token_share"),
            "wall_sec": compare_conditions(episodes, "wall_sec"),
        },
        # #16, reported as a curve rather than through compare_conditions: the
        # claim is about the SHAPE, and two arms can have close totals with
        # nothing alike about their curves. It stays its own section now that it
        # has falsified the thing it was built to confirm - see the function.
        "context_tokens_by_round": context_tokens_by_round(episodes),
        "cost_of_pass": cost_of_pass(episodes, price_in=args.price_in, price_out=args.price_out),
    }
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    for metric, result in report["metrics"].items():
        print(f"\n=== {metric} ===")
        for stratum, data in result["strata"].items():
            means = {m: data["summary"][m]["mean"] for m in MODES}
            print(f"  [{stratum:8s}] " + "  ".join(f"{m}={v}" for m, v in means.items() if v is not None))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
