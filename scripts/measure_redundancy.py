"""Redundancy, budget curves, and the repeated-sampling baseline - all of it
computed from data/episodes.jsonl alone. **No model calls, no sandbox runs.**

Every number here was already implicit in the round log; nothing new is bought.
That is the point: the metrics the related-work survey asks for as CEGMem's
flagship (duplicate-patch rate, novel-class discovery, success@B as a curve)
need no extra experiment, only a reader.

What is measured, and why each one is here
------------------------------------------

**Duplicate-Patch Rate (DPR)** - the fraction of rounds whose patch is, after
AST normalisation, one this episode already proposed. It is the honest proxy
for "redundant attempt" on a real benchmark, and it has one property none of
the type-based counts have: it is defined on **guarded rounds too**, because
src.loop logs the patch of a round it blocked. Every theta-based count is
censored exactly where an arm guards, so an arm that guards a lot looks less
redundant for procedural reasons; DPR does not have that problem and needs no
--audit-guarded run to be comparable.

Normalisation is `ast.dump(ast.parse(src))` with docstrings dropped, so
whitespace, comments and formatting do not create spurious "new" patches. It
deliberately does NOT alpha-rename locals: two patches that differ only in a
variable name are different patches to a reviewer, and calling them duplicates
would overstate the effect being claimed. Patches that do not parse are counted
separately rather than pooled - a syntactically broken patch is a harness
signal, not a proposal.

**Failure-Signature Revisit Rate / Novel-Class Discovery / Elimination Yield /
Effective Proposal Ratio** - the theta-based family. Theorem 4.2(i) says a
typed memory never revisits an eliminated class, which is the statement
NCDR = 1.0; these are the measured versions of it. All four are censored on
guarded rounds unless the log came from an --audit-guarded cell, so the report
carries `type_metrics_censored` per arm and says what fraction of rounds it
could not type. Read them next to that number or not at all.

**Class-revisit distance** - how many rounds pass before a type reappears,
as a distribution rather than a mean. Rounds after the last occurrence are
right-censored at the budget and reported as such.

**success@b, both budgets** - as a curve over b = 1..B, not one point at B.
Two budgets, because they are two different resources and the paper's own
theorems predict different things about them:
  proposal budget - b counts every round, guarded or not. One model call each.
  oracle budget   - b counts only rounds that reached the oracle. Theorem
                    4.3(a) puts typed and untyped level here, so a gap in this
                    curve is a finding about the *guard*, not about typing.
AUC over each curve summarises it without cherry-picking a B.

**pass@k, the repeated-sampling baseline** - Large Language Monkeys' claim is
that coverage keeps rising with independent samples, which is the strongest
theoretical objection to this whole line of work. The E1 arm *is* that
experiment: no_memory under --force-full-budget draws B independent proposals
from an unchanging prompt. So pass@k comes out of data already collected, both
empirically (did any of the first k rounds accept) and as the 1-(1-pi)^k curve
its own pi_hat implies. Plot it under the typed arm's success@b: if the two
curves coincide, the contribution is not there, and it is better to know.

Writes data/redundancy.json.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.metrics import (DEFAULT_METRICS_LOG, build_crn_type_index,  # noqa: E402
                         group_by_episode, load_rounds)

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
# The ChatRepair `transcript` arm (E6) was removed unrun on 2026-08-29: it
# tested no surviving claim, and the "typed index is flat, transcript grows
# linearly" claim it existed for is already falsified by the typed arm alone
# (80.7 tokens/round against untyped's 3.5). docs/DIAGNOSIS.md.
MODES = ("no_memory", "untyped", "typed")


class _StripDocstrings(ast.NodeTransformer):
    """Drop docstring expressions so a reworded comment is not a new patch."""

    def _strip(self, node):
        self.generic_visit(node)
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_Module = _strip
    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip


def normalize_patch(source: str) -> str | None:
    """Canonical form of a patch, or None if it does not parse.

    None is a distinct outcome, not a failure to swallow: an unparseable patch
    is a harness signal (a truncated reply, most often - see
    src.proposer.TruncatedResponse) and pooling it with real proposals would
    let two different broken replies count as a duplicate of each other.
    """
    if not source or not source.strip():
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return None
    return ast.dump(ast.fix_missing_locations(_StripDocstrings().visit(tree)))


def _effective_rounds(rows: list[dict]) -> list[dict]:
    """Rounds up to and including the first accept.

    The same truncation src.metrics.summarize_episode applies, and for the same
    reason: E1 runs its arm past the first accept on purpose, and counting
    those extra rounds would inflate exactly the redundancy metric the arms are
    being compared on.
    """
    rows = sorted(rows, key=lambda r: r["round_index"])
    accepted = [r for r in rows if r["accept"] and not r.get("guarded", False)]
    if not accepted:
        return rows
    cut = accepted[0]["round_index"]
    return [r for r in rows if r["round_index"] <= cut]


def episode_redundancy(rows: list[dict], crn_types: dict[tuple, str] | None = None) -> dict:
    """Per-episode redundancy and diversity, from the round log alone.

    crn_types recovers the failure type of a guarded round from the paired
    no-memory draw (src.metrics.build_crn_type_index). Without it every metric
    keyed on `fine_type` below - FSRR, the revisit distances, the type entropy -
    is censored in exactly the arms that guard, and by the amount they guard:
    51% of the flat arm's rounds and 30% of the typed arm's carry no type of
    their own. The arm that guards most is then measured least, which inverts
    the comparison these metrics exist to make.
    """
    rows = _effective_rounds(rows)
    if crn_types:
        # Copy, never mutate the caller's rows: the same episode is also read by
        # the arm-level pass below and by scripts/analyze.py's frozen summary.
        rows = [dict(r, fine_type=(r.get("fine_type") or crn_types.get(
                    (r["task"], r.get("seed", 0), r["round_index"], r.get("patch") or ""))))
                if r.get("guarded", False) and not r.get("fine_type") else r
                for r in rows]
    n = len(rows)

    seen_patches: set[str] = set()
    duplicates = unparseable = 0
    for r in rows:
        norm = normalize_patch(r.get("patch", ""))
        if norm is None:
            unparseable += 1
            continue
        if norm in seen_patches:
            duplicates += 1
        seen_patches.add(norm)
    parseable = n - unparseable

    # Two populations, kept apart on purpose. `typed_rows` is every round that
    # carries a type, which under --audit-guarded includes guarded rounds - the
    # oracle was paid on them for the record. `oracle_typed` is the rounds whose
    # type came from an oracle call the loop actually spent. Mixing them put
    # audited guarded rounds in NCDR's numerator while n_oracle excluded them
    # from the denominator, so an E8 episode could report ncdr = 4.0 against
    # Theorem 4.2(i)'s predicted 1.0 - and scored a round the guard BLOCKED as
    # an effective proposal.
    typed_rows = [r for r in rows if r.get("fine_type")]
    oracle_typed = [r for r in typed_rows if not r.get("guarded", False)]
    seen_types: list[str] = []
    revisits = 0
    revisit_distances: list[int] = []
    last_seen: dict[str, int] = {}
    for r in typed_rows:
        key, idx = r["fine_type"], r["round_index"]
        if key in last_seen:
            revisits += 1
            revisit_distances.append(idx - last_seen[key])
        else:
            seen_types.append(key)
        last_seen[key] = idx
    # Every class's LAST appearance is a censored observation, not an absent
    # one: we know its next revisit distance exceeds however many rounds the
    # episode had left. Those remainders are what make the survival curve an
    # estimate of "how long until a class comes back" rather than of "how long
    # until a class that did come back came back".
    max_round = max((r["round_index"] for r in rows), default=0)
    revisit_censored = [max_round - idx for idx in last_seen.values() if max_round > idx]

    n_oracle = sum(1 for r in rows if not r.get("guarded", False))
    n_distinct_types = len(seen_types)
    # NCDR and the elimination yield are about what the ORACLE bought, so they
    # count only classes first seen on a non-guarded round.
    oracle_first_seen: list[str] = []
    for r in oracle_typed:
        if r["fine_type"] not in oracle_first_seen:
            oracle_first_seen.append(r["fine_type"])
    n_oracle_distinct = len(oracle_first_seen)
    # A round with an oracle verdict: the FSRR denominator the plan specifies.
    # len(typed_rows) is not it - an inconclusive round or an accept has a
    # verdict and no type, so using the typed rows inflated the rate, and the
    # inflation factor differs per arm with its accept and error rates.
    #
    # UPDATED for the CRN recovery above. `revisits` is counted over typed_rows,
    # which now includes guarded rounds whose signature was recovered from the
    # paired no-memory draw. A denominator that still excluded guarded rounds put
    # the numerator and denominator on different populations and drove the flat
    # arm to FSRR = 3.23 - a rate above 1.0, which is how the mismatch announced
    # itself. The right denominator is every round that produced a failure
    # signature: exactly the population `revisits` is counted over, so the rate
    # is bounded by 1 again. Accepts and inconclusive rounds carry no signature
    # and are excluded by having no type, which is what the older comment here
    # was reaching for before recovery made guarded rounds typable.
    n_verdicts = sum(1 for r in typed_rows
                     if not r["accept"] and not r.get("oracle_error")
                     and not r.get("proposal_error"))
    counts = collections.Counter(r["fine_type"] for r in typed_rows)
    total = sum(counts.values())
    entropy = (-sum((c / total) * np.log2(c / total) for c in counts.values())
               if total else None)

    accepted_rows = [r for r in rows if r["accept"] and not r.get("guarded", False)]
    # "Useful" = the first round to exhibit a type, or the round that repaired
    # it. Everything else spent a model call to learn nothing new.
    useful = n_oracle_distinct + (1 if accepted_rows else 0)

    return {
        "episode_id": rows[0]["episode_id"], "task": rows[0]["task"],
        "mode": rows[0]["mode"], "seed": rows[0].get("seed", 0),
        "n_rounds": n, "n_oracle_calls": n_oracle,
        "n_guarded": sum(1 for r in rows if r.get("guarded", False)),
        "accepted": bool(accepted_rows),
        "first_accept_round": accepted_rows[0]["round_index"] if accepted_rows else None,
        # #2 Duplicate-Patch Rate. Denominator excludes unparseable replies.
        "duplicate_patches": duplicates,
        "unparseable_patches": unparseable,
        "duplicate_patch_rate": (duplicates / parseable) if parseable else None,
        # #3 Failure-Signature Revisit Rate, over rounds that reached a verdict.
        "type_revisits": revisits,
        "fsrr": (revisits / n_verdicts) if n_verdicts else None,
        # #4 Novel-Class Discovery Rate; Theorem 4.2(i) predicts 1.0 for typed.
        # Numerator and denominator now agree about guarded rounds, so this is
        # bounded by 1.0 by construction rather than by hope.
        "ncdr": (n_oracle_distinct / n_oracle) if n_oracle else None,
        # Thm 4.2(i)'s actual claim, with a denominator that can reach it.
        # `n_oracle` above counts EVERY unguarded round, including the accept
        # that ends the episode and every inconclusive or truncated one - none
        # of which carries a type, so none can ever enter the numerator. That
        # ceiling is what held the typed arm at 0.618 against a predicted 1.0;
        # over the rounds that actually reached a refutation it is 0.966, and
        # the flat arm 0.964. Both reported: the old one is the share of oracle
        # CALLS that bought a new class, this is the share of REFUTATIONS that
        # were novel, and only the second is what the theorem is about.
        "ncdr_refutations": (n_oracle_distinct / len(oracle_typed)) if oracle_typed else None,
        # #5 Elimination Yield: the same numerator against the proposal budget.
        # None, not 0.0, when no round could carry a type at all - an episode the
        # guard blocked end to end discovered nothing because nothing was
        # measurable, which is not the same claim as "discovered nothing".
        "elimination_yield": ((n_oracle_distinct / n) if n_oracle else None) if n else None,
        # #8 Effective Proposal Ratio, same treatment.
        "epr": ((useful / n) if n_oracle else None) if n else None,
        # #7 raw distances and the censored remainders; the KM curve is built
        # pooled, below. Both travel or the curve is conditioned on revisiting.
        "revisit_distances": revisit_distances,
        "revisit_censored": revisit_censored,
        # #33 diversity of the failure classes actually produced.
        "type_entropy_bits": float(entropy) if entropy is not None else None,
        "n_distinct_types": n_distinct_types,
        # How much of this episode could not be typed at all - the censoring
        # figure the theta-based rates above have to be read against.
        "untyped_rounds": n - len(typed_rows),
        "type_censoring_rate": ((n - len(typed_rows)) / n) if n else None,
    }


def budget_curves(episodes: list[list[dict]], budget: int) -> dict:
    """success@b for b = 1..budget, under both budgets, plus the AUCs.

    proposal budget: b counts rounds, so a guarded round spends one.
    oracle budget:   b counts only rounds that reached the oracle.
    """
    by_proposal = np.zeros(budget, dtype=float)
    by_oracle = np.zeros(budget, dtype=float)
    n = 0
    for rows in episodes:
        rows = sorted(rows, key=lambda r: r["round_index"])
        n += 1
        accepted = next((r for r in rows if r["accept"] and not r.get("guarded", False)), None)
        if accepted is None:
            continue
        p_at = accepted["round_index"]
        o_at = sum(1 for r in rows
                   if r["round_index"] <= p_at and not r.get("guarded", False))
        if p_at <= budget:
            by_proposal[p_at - 1:] += 1
        if 1 <= o_at <= budget:
            by_oracle[o_at - 1:] += 1
    if not n:
        return {"n_episodes": 0}
    prop, orac = by_proposal / n, by_oracle / n
    return {
        "n_episodes": n,
        "budget": budget,
        "success_at_b_proposal": [float(x) for x in prop],
        "success_at_b_oracle": [float(x) for x in orac],
        # #18 AUC-Budget: one number for the whole curve, so no single B is
        # cherry-picked. Normalised to [0,1] by dividing by the budget.
        "auc_proposal": float(prop.mean()),
        "auc_oracle": float(orac.mean()),
    }


def repeated_sampling_baseline(no_memory_rows: list[list[dict]], budget: int) -> dict:
    """pass@k from the E1 arm - the Large Language Monkeys comparison.

    Two estimates, deliberately both:
      empirical  - did any of the first k rounds of this episode accept. This
                   is what the arm actually did.
      analytic   - the plan's 1 - (1 - pi_task)^k, averaged over TASKS, with
                   pi_task each task's own per-round accept rate. Pooling pi
                   first and exponentiating once is a different quantity and a
                   much larger one: 1-(1-p)^k is concave in p, so by Jensen a
                   pooled pi overstates coverage, and it overstates it most on a
                   bimodal corpus - which this one is, over half the pool at
                   pi_hat exactly 0. On a heterogeneous 106-task corpus with
                   rounds independent BY CONSTRUCTION, pooled pass@20 came out
                   0.935 against an empirical 0.560. That is not a small bias in
                   a diagnostic; it is a 67% inflation of the repeated-sampling
                   baseline this arm exists to be compared against, in the
                   direction that flatters us.

                   Because of that, the empirical-vs-analytic gap is only an
                   independence check when the analytic curve is the per-task
                   one. `pass_at_k_analytic_pooled` is kept beside it, clearly
                   named, because the pooled pi is still the right thing to
                   quote as a single-number corpus summary.

    Only episodes run with force_full_budget contribute: an episode that
    stopped at its first accept has no rounds after it, so counting it would
    bias pi_hat downwards for exactly the tasks that succeeded.
    """
    full = [rows for rows in no_memory_rows
            if rows and rows[0].get("force_full_budget", False)]
    if not full:
        return {"n_episodes": 0,
                "note": "no force_full_budget no_memory episodes - E1 has not run, "
                        "or ran without the flag (scripts/analyze.py refuses to pool "
                        "the two, and so does this)"}

    empirical = np.zeros(budget, dtype=float)
    accepts = rounds = 0
    per_task: dict[str, list[int]] = {}
    for rows in full:
        rows = sorted(rows, key=lambda r: r["round_index"])
        scored = [r for r in rows if not r.get("oracle_error") and not r.get("proposal_error")]
        accepts += sum(1 for r in scored if r["accept"])
        rounds += len(scored)
        # Same population as pi: an accept on a round the scoring excluded is
        # not an accept this curve may claim.
        first = next((r["round_index"] for r in scored if r["accept"]), None)
        if first is not None and first <= budget:
            empirical[first - 1:] += 1
        t = rows[0]["task"]
        acc, tot = per_task.setdefault(t, [0, 0])
        per_task[t] = [acc + sum(1 for r in scored if r["accept"]), tot + len(scored)]
    empirical /= len(full)
    pi_hat = accepts / rounds if rounds else 0.0
    pis = [a / t for a, t in per_task.values() if t]
    analytic = [float(np.mean([1.0 - (1.0 - p) ** k for p in pis])) if pis else 0.0
                for k in range(1, budget + 1)]
    analytic_pooled = [float(1.0 - (1.0 - pi_hat) ** k) for k in range(1, budget + 1)]
    return {
        "n_episodes": len(full), "n_tasks": len(per_task), "budget": budget,
        "pi_hat_pooled": pi_hat, "n_scored_rounds": rounds, "n_accepts": accepts,
        "pi_task_median": float(np.median(pis)) if pis else None,
        "pass_at_k_empirical": [float(x) for x in empirical],
        "pass_at_k_analytic": analytic,
        "pass_at_k_analytic_pooled": analytic_pooled,
        "auc_empirical": float(empirical.mean()),
        "auc_analytic": float(np.mean(analytic)),
        "auc_analytic_pooled": float(np.mean(analytic_pooled)),
    }


def _survival(distances: list[int], budget: int, censored: list[int] | None = None) -> dict:
    """Kaplan-Meier P(a class has NOT been revisited within d rounds), d = 1..budget.

    A class seen once and never again is the majority case and it is CENSORED,
    not absent: we know its revisit distance exceeds the rounds that remained
    after it appeared, and `censored` carries exactly those remainders. Dropping
    them - which is what taking the mean over observed distances alone does -
    conditions the curve on being revisited at all and biases it hard toward
    short distances, i.e. toward the paper's own conclusion.

    Estimated the standard way: at each d, S(d) = prod over event times t <= d
    of (1 - deaths_t / at_risk_t), where at_risk counts every class whose
    observation window still reaches t.
    """
    if not distances and not censored:
        return {"n_revisits": 0, "n_censored": 0, "survival": None,
                "median_distance": None, "mean_distance": None}
    obs = list(distances)
    cen = list(censored or [])
    surv, s = [], 1.0
    for d in range(1, budget + 1):
        at_risk = sum(1 for x in obs if x >= d) + sum(1 for x in cen if x >= d)
        deaths = sum(1 for x in obs if x == d)
        if at_risk:
            s *= (1.0 - deaths / at_risk)
        surv.append(float(s))
    arr = np.asarray(obs, dtype=float) if obs else None
    return {"n_revisits": len(obs), "n_censored": len(cen),
            # Of the OBSERVED revisits, and labelled as such: the KM median is
            # the d where the curve crosses 0.5, and with this much censoring it
            # is often not reached inside the budget at all.
            "median_distance": float(np.median(arr)) if arr is not None else None,
            "mean_distance": float(arr.mean()) if arr is not None else None,
            "km_median": next((d for d, v in enumerate(surv, 1) if v <= 0.5), None),
            "survival": surv}


def _mean(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return float(np.mean(vals)) if vals else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes-path", type=pathlib.Path, default=DEFAULT_METRICS_LOG)
    parser.add_argument("--budget", type=int, default=20,
                        help="B, for the curves. Must match the grid's budget")
    parser.add_argument("--out", type=pathlib.Path, default=DATA_DIR / "redundancy.json")
    args = parser.parse_args()

    rows = load_rounds(args.episodes_path)
    if not rows:
        raise SystemExit(f"{args.episodes_path} is empty or missing - run the grid first")

    by_episode = group_by_episode(rows)
    # Main grid only, same predicate scripts/analyze.py uses - an ablation or a
    # sweep cell is a different experiment and must not be pooled into these
    # means. Kept local rather than imported so this script has no dependency on
    # the strata freeze existing yet.
    def is_main(rows_: list[dict]) -> bool:
        r = rows_[0]
        return (r.get("guard_on", True) and r.get("steer_on", True)
                and r.get("max_examples", 100) == 100
                and r.get("typing_noise_c", 1.0) == 1.0
                and r.get("force_full_budget", False) == (r["mode"] == "no_memory")
                # E8-audit, a windowed E6 and E5-random run every knob above at
                # its main-grid value. Without these three, E8's episodes - the
                # only ones whose guarded rounds carry a type - averaged into the
                # same task mean as the main grid's, mixing censored with
                # uncensored inside one number.
                and not r.get("audit_guarded", False)
                and not r.get("typing_random", False)
                and not r.get("free_guarded_rounds", False))

    main_grid = {eid: rs for eid, rs in by_episode.items() if is_main(rs)}
    print(f"{len(rows)} rounds | {len(by_episode)} episodes "
          f"| {len(main_grid)} in the main grid", flush=True)

    # Built over every row, not just the main grid: the join source is the
    # no-memory arm and the E8 audit, and an episode being outside the main grid
    # does not stop it from being the twin that types a guarded round.
    crn_types = build_crn_type_index(rows)
    per_episode = [episode_redundancy(rs, crn_types) for rs in main_grid.values()]

    arms: dict = {}
    for mode in MODES:
        eps = [e for e in per_episode if e["mode"] == mode]
        if not eps:
            continue
        ep_rows = [main_grid[e["episode_id"]] for e in eps]
        distances = [d for e in eps for d in e["revisit_distances"]]
        censored = [d for e in eps for d in e.get("revisit_censored", [])]
        censoring = _mean([e["type_censoring_rate"] for e in eps])
        arms[mode] = {
            "n_episodes": len(eps),
            "duplicate_patch_rate": _mean([e["duplicate_patch_rate"] for e in eps]),
            "duplicate_patches_total": sum(e["duplicate_patches"] for e in eps),
            "unparseable_patches_total": sum(e["unparseable_patches"] for e in eps),
            "fsrr": _mean([e["fsrr"] for e in eps]),
            "ncdr": _mean([e["ncdr"] for e in eps]),
            "ncdr_refutations": _mean([e["ncdr_refutations"] for e in eps]),
            "elimination_yield": _mean([e["elimination_yield"] for e in eps]),
            "epr": _mean([e["epr"] for e in eps]),
            "type_entropy_bits": _mean([e["type_entropy_bits"] for e in eps]),
            "class_revisit": _survival(distances, args.budget, censored),
            # Read every theta-based rate above against this. It is the share of
            # rounds with no type at all - guarded rounds outside an
            # --audit-guarded cell, plus inconclusive and truncated ones.
            "type_metrics_censored": censoring,
            **budget_curves(ep_rows, args.budget),
        }

    no_memory_rows = [rs for rs in by_episode.values() if rs[0]["mode"] == "no_memory"]
    report = {
        "episodes_path": str(args.episodes_path),
        "budget": args.budget,
        "n_rounds": len(rows), "n_episodes": len(by_episode), "n_main_grid": len(main_grid),
        "arms": arms,
        "repeated_sampling": repeated_sampling_baseline(no_memory_rows, args.budget),
        "per_episode": [{k: v for k, v in e.items()
                         if k not in ("revisit_distances", "revisit_censored")}
                        for e in per_episode],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")

    head = f"{'arm':12s} {'n':>4s} {'DPR':>7s} {'FSRR':>7s} {'NCDR':>7s} {'EPR':>7s} {'AUC-p':>7s} {'censor':>7s}"
    print("\n" + head)
    print("-" * len(head))
    for mode, a in arms.items():
        def f(x, spec=".3f"):
            return format(x, spec) if isinstance(x, float) else "-"
        print(f"{mode:12s} {a['n_episodes']:>4d} {f(a['duplicate_patch_rate']):>7s} "
              f"{f(a['fsrr']):>7s} {f(a['ncdr']):>7s} {f(a['epr']):>7s} "
              f"{f(a.get('auc_proposal')):>7s} {f(a['type_metrics_censored']):>7s}")
    rs = report["repeated_sampling"]
    if rs.get("n_episodes"):
        print(f"\nrepeated sampling (E1): pi_hat={rs['pi_hat_pooled']:.4f}  "
              f"pass@1={rs['pass_at_k_empirical'][0]:.3f}  "
              f"pass@{args.budget}={rs['pass_at_k_empirical'][-1]:.3f}  "
              f"AUC={rs['auc_empirical']:.3f}")
    else:
        print(f"\nrepeated sampling: {rs.get('note', 'unavailable')}")
    # `all(... > 0.05)` was the wrong gate: the harmful case is asymmetry, and
    # asymmetry is exactly when some arm is BELOW the threshold. An E1 episode
    # that accepts at round 20 is censored 1/20 = 0.05 and one that never
    # accepts is censored 0.0, so on the real grid `all` essentially never
    # fired - while {no_memory: 0.05, typed: 0.45}, the case the note is for,
    # was silent.
    cens = [a["type_metrics_censored"] for a in arms.values()
            if a["type_metrics_censored"] is not None]
    if cens and (max(cens) > 0.05 or (max(cens) - min(cens)) > 0.05):
        print("\nNOTE: every arm has >5% of rounds with no failure type. FSRR, NCDR,\n"
              "elimination yield and EPR are censored by that much and are NOT\n"
              "comparable between arms that guard at different rates. Run the\n"
              "E8-audit cell (--audit-guarded) for a comparable version; DPR is\n"
              "unaffected and can be read as it stands.")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
