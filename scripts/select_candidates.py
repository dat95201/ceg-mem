"""Stage 0 - build the screening candidate pool from ConDefects. No model calls.

This is the step that stands between "the benchmark" and "the tasks we screen".
It spends no API budget: every gate is a pure function of `faultyVersion.py`,
the shipped test-directory listing, and `date.txt`.

Why filter a benchmark at all
-----------------------------
Not because of difficulty. A task is dropped here when the quantity the paper
measures is undefined or unrecoverable on it - a property of the program, fixed
before any agent runs, that no arm of the experiment can change. Filtering on
that is dose-range selection, not outcome selection.

There is exactly one thing every claim in the paper needs, and it is not a large
type space. Writing D = sum_tau q_tau/(q_tau + pi), the formal core gives

  Thm 4.3(a)  E[oracle calls]       = 1/pi (no memory), 1 + D (either memory)
  Thm 4.3(b)  E[redundant attempts] = 0 (typed), R := 1/pi - 1 - D (untyped/none)
  Thm 4.2     typed memory halts within K+1 oracle calls
  Prop 4.5    guard cost Theta(m) untyped vs O(1) typed, m <= K

and R has the closed form R = (1/pi) * sum_tau q_tau^2 / (q_tau + pi), which is
*decreasing* in K: the primary typed-vs-untyped gap is LARGEST at K = 1 and
shrinks as failure mass spreads over more classes. An earlier version of this
file asserted the opposite - that K -> 1 makes the two memories coincide - and
justified a K_proxy floor with it. That was wrong: at K = 1 and pi = 0.10 the
untyped arm still pays 8.10 redundant proposals against typed's 0, because
without a class notion it re-proposes the one refuted class until it happens to
draw the correct one. The floor has been removed.

What killed the previous 60-task corpus was therefore not K. It was (i) a
bimodal pi - 16 of 60 tasks at exactly 0.00 and 13 at >= 0.90, so almost nothing
sat where refutations get stored - and (ii) program size: at a median of six
lines, every candidate patch differs from the faulty source across more than
_MAX_HUNK_FRACTION of the file, so src/typer.py cannot recover an edit location
at all and theta's location axis collapses to the constant `wholesale`. That is
a failure of theta to be *computable*, not of K to be *large*.

What each gate is for
---------------------
G1 is the floor: the coding task must ship at least one expected output. With
none, there is no ground truth at all and Eq. (1)'s oracle is undefined rather
than weak. It names AtCoder's interactive problems, whose in/ files hold the
judge's data in the judge's format and whose out/ does not exist - the reference
cannot even parse them, so src/oracle.py's run-the-reference fallback fails too
and the fault stalls the screen with NoUsableTestCase. Only this half of G1 is
checkable here; the rest (does the reference actually pass, is the faulty
version actually refuted) needs to run code and belongs to
scripts/validate_oracle.py.

G3 carries the real weight, and its justification is (ii) above: a program long
enough that a diff-recovered edit location means something, and short enough to
fit a prompt. G4 is Definition 3.1's rho - too few shipped cases and the
sampling oracle stops being informative about the class. G2 is not about K; it
only requires that all three fault families have a site, so that
scripts/validate_oracle.py can plant one mutant of each and score the oracle
rather than score a property of the program. G5 is pool size, declared as such.
G6 is independence: several submissions can fail the same coding task and
near-duplicates would correlate cells the paired analysis assumes independent.

K_proxy is still computed, and the traversal order still stratifies on it, but
it is a covariate and not a gate. Its job is to make the screened pool span a
range of K so that the K-dependence R(K) above can be *estimated and tested*
rather than assumed. Note also that it is largely a size measure -
Spearman(K_proxy, lines) is about +0.89 over the archive - which is a further
reason not to let it gate anything; the diagnostic is printed on every run.

Not a gate: nothing here reads `correctVersion.py`. Selection never inspects the
reference fix, so it cannot be selecting on the answer. (The reference is used
downstream, by the oracle and by scripts/validate_oracle.py, which is a
different stage with a different job.)

Output
------
data/candidates.json - the ordered pool, plus every fault examined and the first
gate it failed, so the filter is auditable end to end and the reported figure
"N examined -> M candidates" can be reproduced from the artifact alone.
"""
from __future__ import annotations

import argparse
import ast
import collections
import dataclasses
import json
import os
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.mutants import FAULT_TYPES, candidate_mutants  # noqa: E402
from src.adapter import CODE_DIR, LANGUAGE_DIR, TEST_DIR  # noqa: E402

from src.paths import DATA_DIR, ROOT, announce  # noqa: E402

# NOTE: `data` is also a python package (data/mutants.py, imported above).
# That import always resolves to ROOT/data/mutants.py and is unaffected by
# RUN_DIR - source code in data/ stays put, only run OUTPUT moves.
DATE_FILE = pathlib.Path(os.environ.get("CONDEFECTS_ROOT", "external/ConDefects")) / "date.txt"

# ── the gates ────────────────────────────────────────────────────────────
# Every constant here is an argument, not a tuning knob, and the docstring
# above says which claim each one protects. The one exception is DATE_MIN,
# which is a pool-size choice and is documented as such: see `--date-min`.

LINES_MIN = 15       # under ~15 lines every candidate differs from the faulty
                     # source across more than _MAX_HUNK_FRACTION of the file,
                     # so src/typer.py cannot recover an edit location and
                     # theta's location axis collapses to `wholesale`
LINES_MAX = 120      # prompt budget
STMTS_MIN = 12       # same floor as LINES_MIN, expressed on the AST so that a
                     # long file of imports and blank lines does not qualify
NCASE_MIN = 15       # Definition 3.1's rho: too few cases and the sampling
                     # oracle stops being informative about the class
N_FAMILIES = 3       # all three fault families must have a site, else the
                     # oracle-validation stage would score the program on a
                     # property of the program rather than of the oracle

# Deliberately absent: any gate on K_proxy. See the module docstring - the
# primary typed-vs-untyped gap R is decreasing in K, so a floor would select
# against the effect, and empirically a floor of 6 removes only ~6 coding tasks
# that G3 does not already remove. K_proxy stratifies the traversal order and is
# reported as a covariate; it gates nothing.

# Contamination control. This is the one threshold set by pool size rather than
# by theory, and the paper says so: it is the LATEST cut that still yields the
# required number of candidates. Block 1 is screened first and Block 2 is a
# pre-declared reserve, so the tasks actually used carry the strongest recency
# available and any relaxation is visible in the artifact.
BLOCK1_DATE = "2023-01-01"
DATE_MIN = "2022-04-01"

SEED = 20260717


@dataclasses.dataclass
class Fault:
    task: str            # coding task, e.g. "abc301_d"
    program: str         # submission id
    name: str            # "<task>/<program>" - the identity src/adapter.py uses
    contest: str
    family: str          # abc | arc | agc
    letter: str
    date: str
    lines: int
    stmts: int
    branches: int
    ncase: int          # inputs shipped under Test/<contest>/<L>/in
    nscored: int        # of those, how many have a matching out/ file
    k_proxy: int
    n_families: int
    cap: dict[str, int]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _structure(source: str) -> tuple[int, int] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    stmts = sum(1 for n in ast.walk(tree) if isinstance(n, ast.stmt))
    branches = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, (ast.If, ast.For, ast.While, ast.Compare, ast.IfExp))
    )
    return stmts, branches


def _k_proxy(task: str, source: str) -> tuple[int, dict[str, int]]:
    """Distinct (line, fault family) signatures the faulty source admits.

    Sites are collapsed to their line, not their byte span, because theta's
    location axis is a line range recovered from a diff (src/typer.py): two
    mutations on one line are one location, and counting them separately would
    inflate K_proxy on dense one-liners.
    """
    sites: set[tuple[str, str]] = set()
    cap: dict[str, int] = {}
    for family in FAULT_TYPES:
        try:
            mutants = candidate_mutants(task, source, family)
        except Exception:                      # a source the operator cannot
            mutants = []                       # walk contributes no sites
        lines = {(family, m.site.split(":")[0]) for m in mutants}
        cap[family] = len(lines)
        sites |= lines
    return len(sites), cap


def _spearman(a: list[float], b: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order_ = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order_):
            j = i
            while j + 1 < len(order_) and values[order_[j + 1]] == values[order_[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order_[k]] = avg
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def _load_dates() -> dict[str, str]:
    path = ROOT / DATE_FILE if not DATE_FILE.is_absolute() else DATE_FILE
    dates: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            dates[parts[0]] = parts[1]
    return dates


def enumerate_faults() -> tuple[list[Fault], collections.Counter]:
    """Every ConDefects Python fault that has test data, with its metrics."""
    dates = _load_dates()
    contests_with_tests = set(os.listdir(TEST_DIR)) if TEST_DIR.is_dir() else set()
    if not contests_with_tests:
        raise SystemExit(
            f"{TEST_DIR} is empty or missing - the 63 GB Test archive must be "
            "unpacked before selection (scripts/fetch_condefects.py)."
        )

    faults: list[Fault] = []
    skipped: collections.Counter = collections.Counter()

    for task in sorted(os.listdir(CODE_DIR)):
        if "_" not in task:
            continue
        contest, letter = task.rsplit("_", 1)
        if contest not in contests_with_tests:
            skipped["no_test_archive"] += 1
            continue
        in_dir = TEST_DIR / contest / letter.upper() / "in"
        if not in_dir.is_dir():
            skipped["no_test_dir"] += 1
            continue
        inputs = set(os.listdir(in_dir))
        # The shipped expected outputs, matched by filename. src/adapter.py
        # pairs them exactly this way, and src/oracle.py falls back to *running
        # the reference* for an input with no out/ file - so this count is how
        # much of the pool is scored against AtCoder's own answer rather than
        # against a second run of the program under test.
        out_dir = TEST_DIR / contest / letter.upper() / "out"
        outputs = set(os.listdir(out_dir)) if out_dir.is_dir() else set()
        ncase = len(inputs)
        nscored = len(inputs & outputs)
        lang_dir = CODE_DIR / task / LANGUAGE_DIR
        if not lang_dir.is_dir():
            continue
        for program in sorted(os.listdir(lang_dir)):
            faulty = lang_dir / program / "faultyVersion.py"
            if not faulty.exists():
                continue
            source = faulty.read_text(encoding="utf-8", errors="ignore")
            structure = _structure(source)
            if structure is None:
                skipped["unparseable"] += 1
                continue
            k_proxy, cap = _k_proxy(task, source)
            faults.append(Fault(
                task=task, program=program, name=f"{task}/{program}",
                contest=contest, family=contest[:3], letter=letter,
                date=dates.get(task, "0000-00-00"),
                lines=len(source.splitlines()),
                stmts=structure[0], branches=structure[1],
                ncase=ncase, nscored=nscored, k_proxy=k_proxy, cap=cap,
                n_families=sum(1 for v in cap.values() if v > 0),
            ))
    return faults, skipped


def first_failed_gate(f: Fault, *, date_min: str) -> str | None:
    """The first gate a fault fails, in a fixed order, or None if it passes.

    Order is fixed so the funnel table in the paper is reproducible: a fault is
    attributed to the *first* reason it was dropped, never to whichever gate
    happened to run last.
    """
    # Ordered most-fundamental first, so the funnel table attributes a fault to
    # the deepest reason it is unusable rather than to whichever gate ran first.
    #
    # Nothing is deeper than this one: with no shipped expected output anywhere,
    # there is no ground truth to compare a patch against, so Eq. (1)'s oracle
    # is not merely weak but undefined. It is the static half of G1 - the only
    # half Stage 0 can check, since the rest of G1 requires running the
    # reference, which this stage must not do.
    #
    # In practice it names AtCoder's *interactive* problems. Those have no fixed
    # expected output at all, so ConDefects ships in/ without out/, and the in/
    # files hold the judge's own data in the judge's format rather than the
    # contestant's stdin. src/oracle.py's fallback - run the reference to
    # produce the expected value - therefore cannot rescue them either: the
    # reference is handed input it was never written to parse and dies on the
    # first line. That surfaces as src.oracle.NoUsableTestCase, which
    # scripts/measure_pi.py treats as fatal, so one such fault stalls a whole
    # shard of the screen.
    #
    # Deliberately `== 0`, not `< NCASE_MIN`. A task shipping *some* outputs has
    # a working oracle that is merely thinner, and the reference-run fallback is
    # the documented design for the rest. Gating those on partial coverage would
    # be a different and stronger claim; main() reports them instead so the
    # decision stays visible rather than buried in a threshold.
    if f.nscored == 0:
        return "G1_no_expected_output"
    if not (LINES_MIN <= f.lines <= LINES_MAX):
        return "G3_lines"
    if f.stmts < STMTS_MIN:
        return "G3_stmts"
    if f.ncase < NCASE_MIN:
        return "G4_cases"
    if f.n_families < N_FAMILIES:
        return "G2_families"
    if f.date < date_min:
        return "G5_date"
    return None


def dedup(passing: list[Fault], *, seed: int) -> tuple[list[Fault], int]:
    """G6 - one fault per coding task, chosen by seeded coin flip.

    Deliberately *not* "the fault with the largest K_proxy", which was the first
    version of this rule. Two things are wrong with it. It selects on the same
    statistic the traversal order stratifies on, so the pool's K_proxy
    distribution would be an artefact of the dedup rule rather than of the
    benchmark. And it selects *upward* on K, while Theorem 4.2's bound is
    K+1 <= B: preferring the largest capacity pushes tasks toward the regime
    where the round bound cannot be reached within budget, i.e. against the
    claim being tested.

    A seeded uniform choice is neutral on every axis, and is the only rule here
    that cannot be argued to encode a preference.
    """
    by_task: dict[str, list[Fault]] = collections.defaultdict(list)
    for f in passing:
        by_task[f.task].append(f)
    rng = random.Random(seed + 1)
    kept: list[Fault] = []
    for task in sorted(by_task):
        group = sorted(by_task[task], key=lambda f: f.name)
        kept.append(rng.choice(group))
    return kept, len(passing) - len(kept)


def control_sample(faults: list[Fault], *, seed: int, size: int) -> list[Fault]:
    """An unfiltered comparison arm: uniform random over G1-only survivors.

    The purpose is to bound what the filtered corpus is allowed to claim. Every
    gate below G1 is defended as dose-range selection, but that defence
    establishes only that the mechanism EXISTS where it has room to operate; it
    says nothing about how often a practitioner meets such a task. This sample
    is drawn with no capacity gate, no size gate, no date gate and no screening,
    one fault per coding task, and the typed-vs-untyped comparison is reported
    on it whatever it shows - including null, including underpowered.

    Without this arm the headline is "on 2% of the archive, selected so the
    mechanism is visible, the mechanism is visible." With it, the filtered
    result is an effect size and this one is its base rate.
    """
    by_task: dict[str, list[Fault]] = collections.defaultdict(list)
    for f in faults:
        by_task[f.task].append(f)
    rng = random.Random(seed + 2)
    picks = [rng.choice(sorted(by_task[t], key=lambda f: f.name))
             for t in sorted(by_task)]
    rng.shuffle(picks)
    return picks[:size]


def order(pool: list[Fault], *, seed: int) -> list[Fault]:
    """Deterministic traversal order for screening.

    Stratify on K_proxy terciles, shuffle inside each tercile with the one
    seeded RNG, then round-robin over (block, tercile). The interleave is what
    makes an interrupted screen usable: stop after any prefix and the screened
    set is still balanced on type-space capacity, so a partial screen is a
    smaller corpus rather than a skewed one.

    K_proxy is the only stratification variable. Contest family (abc/arc/agc)
    is deliberately *not* balanced: it is a nuisance variable, not a design
    factor, and interleaving on it would over-sample the small agc bucket into
    every prefix. Shuffling leaves it proportional, and no gate or ordering
    rule anywhere in this file references it.
    """
    k = sorted(f.k_proxy for f in pool)
    cut_lo, cut_hi = k[len(k) // 3], k[2 * len(k) // 3]

    def tercile(f: Fault) -> int:
        return 0 if f.k_proxy <= cut_lo else (1 if f.k_proxy <= cut_hi else 2)

    buckets: dict[tuple[int, int], list[Fault]] = collections.defaultdict(list)
    for f in pool:
        block = 0 if f.date >= BLOCK1_DATE else 1
        buckets[(block, tercile(f))].append(f)

    rng = random.Random(seed)
    for key in sorted(buckets):
        buckets[key].sort(key=lambda f: f.name)   # canonical before shuffling
        rng.shuffle(buckets[key])

    # Block 1 first, entirely, then Block 2: the reserve is only ever reached
    # after the primary pool is exhausted, which is what "pre-declared reserve"
    # has to mean operationally.
    ordered: list[Fault] = []
    for block in (0, 1):
        keys = sorted(key for key in buckets if key[0] == block)
        queues = [list(buckets[key]) for key in keys]
        while any(queues):
            for queue in queues:
                if queue:
                    ordered.append(queue.pop(0))
    return ordered, (cut_lo, cut_hi)


def main() -> None:
    announce('select_candidates')
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date-min", default=DATE_MIN,
                        help="G5 contamination cut. The default is the latest "
                             "cut that still yields ~500 candidates; raising it "
                             "trades pool size for recency.")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--control-size", type=int, default=40,
                        help="size of the unfiltered comparison arm drawn "
                             "uniformly over G1-only survivors")
    parser.add_argument("--out", type=pathlib.Path,
                        default=DATA_DIR / "candidates.json")
    args = parser.parse_args()

    faults, skipped = enumerate_faults()
    funnel: collections.Counter = collections.Counter()
    passing: list[Fault] = []
    for f in faults:
        gate = first_failed_gate(f, date_min=args.date_min)
        funnel[gate or "passed"] += 1
        if gate is None:
            passing.append(f)

    pool, dropped_dup = dedup(passing, seed=args.seed)
    ordered, cuts = order(pool, seed=args.seed)
    control = control_sample(faults, seed=args.seed, size=args.control_size)

    block1 = [f for f in ordered if f.date >= BLOCK1_DATE]
    block2 = [f for f in ordered if f.date < BLOCK1_DATE]

    print(f"faults with test data       {len(faults):6d}"
          f"   ({len({f.task for f in faults})} coding tasks)")
    for gate in ("G1_no_expected_output", "G3_lines", "G3_stmts", "G4_cases",
                 "G2_families", "G5_date"):
        print(f"  dropped at {gate:22} {funnel[gate]:6d}")
    print(f"  passed all gates            {funnel['passed']:6d}")
    print(f"  dropped at G6_dedup         {dropped_dup:6d}")
    print(f"CANDIDATES                  {len(ordered):6d}"
          f"   (block 1: {len(block1)}, block 2 reserve: {len(block2)})")
    print(f"UNFILTERED CONTROL ARM      {len(control):6d}   (G1 only, uniform)")
    print(f"K_proxy tercile cuts        {cuts}")
    if skipped:
        print(f"  not examined: {dict(skipped)}")

    # The diagnostic a reviewer will ask for unprompted: how much of K_proxy is
    # just program length? Printed here so it can never be quietly omitted.
    rho_all = _spearman([f.k_proxy for f in faults], [f.lines for f in faults])
    rho_pool = _spearman([f.k_proxy for f in ordered], [f.lines for f in ordered])
    print(f"Spearman(K_proxy, lines)     {rho_all:+.3f} over all examined,"
          f" {rho_pool:+.3f} within the pool")
    print("  -> K_proxy is largely a size measure, which is why it gates nothing."
          " It spreads the\n     screening order over a range of K; measured"
          " K-hat stratifies the frozen corpus,\n     and R(K) is estimated"
          " rather than assumed (see DESIGN.md).")

    # Partial out/ coverage is not gated (see first_failed_gate) but it is not
    # nothing either: on those cases src/oracle.py scores the candidate against
    # a fresh run of the reference rather than against AtCoder's answer. That is
    # a sound differential test only where the answer is unique - on a problem
    # accepting several valid answers it refutes correct patches, which depresses
    # pi_hat on exactly the tasks it touches. Printed so the threat is on the
    # record and the threshold stays a decision rather than a default.
    partial = sorted((f for f in ordered if f.nscored < f.ncase),
                     key=lambda f: f.nscored / f.ncase)
    if partial:
        print(f"\n{len(partial)} candidate(s) ship expected output for only part of"
              f" their test pool:")
        for f in partial:
            print(f"  {f.name:26s} {f.nscored:4d}/{f.ncase:<4d} scored against"
                  f" AtCoder's answer, the rest against a reference re-run")
        print("  -> not gated: the oracle still works, it is thinner. But a problem"
              " admitting\n     several valid answers will refute correct patches"
              " on the unscored cases.")

    payload = {
        "seed": args.seed,
        "gates": {
            "k_proxy_gate": None, "lines": [LINES_MIN, LINES_MAX],
            "stmts_min": STMTS_MIN, "ncase_min": NCASE_MIN,
            "nscored_min": 1,
            "n_families": N_FAMILIES, "date_min": args.date_min,
            "block1_date": BLOCK1_DATE,
        },
        # Reported, not gated - see the note printed by main() and the comment
        # in first_failed_gate.
        "partial_expected_output": [
            {"name": f.name, "ncase": f.ncase, "nscored": f.nscored}
            for f in ordered if f.nscored < f.ncase
        ],
        "test_dir": str(TEST_DIR),
        "funnel": dict(funnel),
        "dropped_at_G6_dedup": dropped_dup,
        "k_proxy_tercile_cuts": list(cuts),
        "n_examined": len(faults),
        "n_candidates": len(ordered),
        "n_block1": len(block1),
        "n_block2": len(block2),
        "spearman_kproxy_lines": {
            "all_examined": round(rho_all, 4), "within_pool": round(rho_pool, 4),
        },
        # The unfiltered arm. Screened and run exactly like the pool, reported
        # whatever it shows. See control_sample().
        "control_unfiltered": [
            {**f.as_dict(), "rank": i} for i, f in enumerate(control)
        ],
        # The ordered pool. `rank` is the screening order; `block` 1 is screened
        # first and block 2 is the pre-declared reserve.
        "candidates": [
            {**f.as_dict(), "rank": i,
             "block": 1 if f.date >= BLOCK1_DATE else 2}
            for i, f in enumerate(ordered)
        ],
        # Everything examined and why it was dropped - condition (2) of the
        # proposal's SS VI-A-c: report the full distribution, including what was
        # excluded.
        "examined": [
            {"name": f.name, "date": f.date, "lines": f.lines,
             "k_proxy": f.k_proxy, "ncase": f.ncase, "nscored": f.nscored,
             "gate": first_failed_gate(f, date_min=args.date_min) or "passed"}
            for f in faults
        ],
    }
    args.out.write_text(json.dumps(payload, indent=1))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
