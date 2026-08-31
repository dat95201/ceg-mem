# Why typed loses to untyped — diagnosis and fix

Branch `fixed-pipeline` · 31 Aug 2026 · computed from `data/episodes.jsonl`, main grid only
(`max_examples=100`, `c=1.0`, not random, not `force_full_budget`, not `free_guarded_rounds`).
Scripts: `/tmp/guardsim.py`, `/tmp/regime.py`, `/tmp/miss.py` (reproduced in §7).

---

## 1. The finding, in one number

Of the **7,349** typed rounds that reached the oracle and were refuted, **5,060 (68.9%)**
came back with a counterexample **that was already in memory**.

That is **3.41 wasted oracle calls per episode**. The same measurement on the untyped arm:
**1 round out of 1,004 (0.1%)**.

| arm | episodes | oracle rounds refuted | counterexample already in memory | per episode |
|---|---|---|---|---|
| **typed** | 1,484 | 7,349 | **5,060 — 68.9%** | 3.41 |
| untyped | 848 | 1,004 | 1 — 0.1% | 0.00 |

(Episode counts differ because the filter above is the mechanism filter, looser than
`analyze.py`'s `_is_main_grid`, which reports 530 cells per arm. Every rate is computed within
its own arm, so the comparison stands.)

This is not an estimate. If case *C* is in memory (stored with reference value *V*) and this
round's oracle returns *C* as the counterexample, then by definition the candidate's output on
*C* differs from *V* — so `_still_refutes` on that stored attempt returns `True`. The flat scan
would have blocked the round. `_still_refutes` is deterministic apart from sandbox timeouts,
which `docs/DIAGNOSIS.md` §5 already bounds at 3 rounds across the whole log.

**Typed is not slightly worse than untyped. It leaks 69% of its oracle calls to counterexamples
it was already holding.** At 11.14 sandbox runs per oracle call that is ≈ 38 wasted program
executions per episode, against a total of 60.23.

---

## 2. The mechanism: the index key has nothing to do with what causes a refutation

`TypedMemory.guard` (`src/memory.py:225`, key computed at `:230`) is a **partition**, not an
index:

```python
guess  = WHOLE_PROGRAM if self.granularity == "coarse" else edit_location(buggy_source, candidate_source)
bucket = self._by_location.get(guess, [])
for attempt in bucket:            # <- and nothing else, ever
    ...
```

The key is `edit_location(buggy, candidate)` — **where the patch edits**. What actually decides
whether a stored counterexample refutes a new candidate is **which input the candidate gets
wrong**. Those two are nearly independent: a counterexample that breaks on input *X* breaks any
candidate that mishandles *X*, no matter which line it touched.

The log says exactly how independent:

| per episode, typed arm | |
|---|---|
| distinct **fine** types (location × property) | **3.82** |
| distinct **coarse** types | 1.06 |
| distinct **counterexample cases** | **1.53** |
| candidate's edit location already in memory | **25.4%** |

θ_fine shatters ~1.5 real behavioural classes into ~3.8 buckets. Three times out of four the new
candidate lands in a bucket that has never been seen, so the guard looks in an empty bucket and
waves the candidate through — while the case that is about to fail sits in a neighbouring bucket.

Note also that at `granularity="coarse"` the key is the constant `WHOLE_PROGRAM`, so the typed
guard degenerates **exactly** into the untyped flat scan. There is no middle setting. The arm is
all-or-nothing between "one bucket" and "a bucket per line range".

---

## 3. The regime: m ≈ 1.2, so there is no Θ(m) to beat

Replaying the untyped guard from the log:

| | |
|---|---|
| live memory size when the guard fired | median **1.0**, mean **1.42** |
| position of the counterexample that fired | median **1.0**, mean 1.15 |
| memory size at end of episode, untyped | mean **1.18**, median 1.0, max 6 |
| rounds blocked, untyped | **81.5%** |

**Untyped's memory is tiny because untyped works.** One stored counterexample blocks four out of
five subsequent proposals — the model keeps re-proposing patches that die on the same input
(`duplicate_patch_rate` 0.217). Typed's memory is *larger* (mean 4.86) purely because it blocks
less and therefore keeps paying the oracle to refile things it already had.

Two consequences:

1. **Prop. 4.5 is being tested at m ≈ 1.4.** "Θ(m) versus O(1)" has no observable regime here.
   The measured gap is 10.55 → 5.76 guard evaluations per episode, i.e. **1.05 → 0.60 sandbox
   runs per round**. Typed saves 0.45 runs per round and pays 11.14 runs every time it misses.
   The trade is roughly **1 : 25 against it**.
2. **Reordering the flat scan buys nothing.** I checked, expecting recency to help: the refuter
   is at FIFO position 1.15 on average, so LIFO is *worse* (1.27) and total guard work rises 9%.
   There is nothing to optimise in a scan of length one.

---

## 4. The fix

### 4.1 Primary — the index says where to look **first**, never where to **stop**

One change, in `TypedMemory.guard`. Keep the type bucket as the first probe (that *is*
Prop. 4.5), then fall back to the rest of history, deduplicated by counterexample case:

```python
def _case_key(attempt):
    """Two attempts whose counterexample is the same shipped case give the same
    verdict against any candidate, so the guard need only run one of them."""
    ft = attempt.fine_type or attempt.coarse_type
    args = attempt.result.args
    return (ft.task, args[0]) if (ft and args) else None


def guard(self, candidate_source: str, buggy_source: str) -> GuardResult:
    guess = WHOLE_PROGRAM if self.granularity == "coarse" else edit_location(
        buggy_source, candidate_source)
    bucket = self._by_location.get(guess, [])
    evaluations = 0
    seen_cases: set = set()

    # Phase 1 - the type-indexed bucket. This is Prop. 4.5's O(1) expected hit,
    # and it is the only phase that runs on the 25% of rounds where theta guesses
    # the bucket correctly.
    for attempt in bucket:
        k = _case_key(attempt)
        if k in seen_cases:
            continue
        seen_cases.add(k)
        evaluations += 1
        if _still_refutes(attempt, candidate_source):
            return GuardResult(True, evaluations, attempt)

    # Phase 2 - everything else. Without this the guard is a partition, and a
    # partition of a memory of size ~1 can only lose: 68.9% of typed's refuting
    # oracle calls returned a case already in memory (docs/TYPED-VS-UNTYPED.md).
    for attempt in self.history:
        if attempt.result.accept:
            continue
        k = _case_key(attempt)
        if k is None or k in seen_cases:
            continue
        seen_cases.add(k)
        evaluations += 1
        if _still_refutes(attempt, candidate_source):
            return GuardResult(True, evaluations, attempt)

    return GuardResult(False, evaluations)
```

**What it changes for the claims.**

- **Recall becomes exactly untyped's.** Phase 2 covers every distinct stored case, so the typed
  guard can no longer miss anything the flat scan catches. The 5,060 leaked rounds get blocked.
- **Prop. 4.5 survives, honestly.** The claim becomes *the index reduces expected evaluations at
  equal recall* — testable, and true whenever θ guesses right (25.4% of rounds today, and it
  should rise once memory stops being starved).
- **Thm 4.3(a) should flip from "equal" to "typed strictly cheaper".** First-order estimate:
  60.23 − (3.41 × 11.14) + (3.41 × ~1.5 extra probes) ≈ **27 sandbox runs/episode** against
  untyped's 43.21. Treat that as a bound, not a prediction — blocking those rounds changes the
  trajectory, memory stops growing to 4.86, and later proposals differ. The safe statement is
  **typed lands at or below untyped on both axes, which it does not today.**

The dedup by case is worth having on `UntypedMemory` too. It saves nothing at m = 1.18, but it
is the difference between Θ(m) and Θ(distinct cases) when m does grow.

**Risk: none to soundness.** `_still_refutes` genuinely re-runs the stored counterexample, so a
block is always a verified refutation, never a guess. Widening the search can only find more true
refutations — it cannot create a false block. That is why this fix does not depend on the
cross-refutation rate that `measure_coherence.py` has never finished computing.

### 4.2 Secondary — give θ a granularity between "one line range" and "the whole program"

Today `fine` = exact edited line range, `coarse` = `WHOLE_PROGRAM`, and nothing between. Add a
third: the **enclosing syntactic block** (function, loop, or branch containing the edit), from the
AST rather than the diff. Expected to take the 3.82 buckets per episode down toward the 1.53 real
classes, which raises phase 1's hit rate and makes the index actually earn its keep. This is a
change to `src/typer.py`, and it needs `measure_coherence.py` to be run at the new granularity
before it can be defended.

### 4.3 Separate the two things the `typed` arm changes at once

`typed` = type-indexed guard **+** the exclusion block in the prompt (`src/proposer.py:216`).
These are independent mechanisms and the design confounds them:

- the exclusion block costs **2.23× input tokens** (5,490 → 12,227),
- and it makes typed's draws diverge from the no-memory sequence, which is why
  `type_metrics_censored` is **0.543** for typed against 0.322 for the others — CRN pairing is
  half broken in exactly the arm the paper is about.

Two cheap arms fix this:

| arm | guard | prompt | isolates |
|---|---|---|---|
| `typed-noprompt` | type-indexed | none | Prop. 4.5, with CRN pairing restored |
| `untyped-steer` | flat scan | exclusion block | whether steering is what actually helps |

The E9 hard band already hints that steering, not indexing, is the live mechanism: 0.444 →
0.611 (untyped, guard only) → 0.722 (typed, guard + steering). If `untyped-steer` reaches 0.722,
the paper's contribution is the exclusion block and the index is a distraction.

### 4.4 If Prop. 4.5 is to be defended at all, m has to grow

At m ≈ 1.2 no indexing claim is interesting. The only honest ways to raise it:

- **Store more than one counterexample per refutation.** `differential_test` stops at the first
  divergence (~9.55 cases in). Continuing a bounded k cases further costs +k sandbox runs per
  refutation and multiplies memory by up to k.
- **A more diverse proposer.** `duplicate_patch_rate` 0.217 at temperature 1.0 on a 7B model:
  the model is looping, which is precisely why one counterexample suffices.
- **Harder tasks / longer budgets**, where more distinct classes accumulate.

Or accept the honest result: *at realistic memory sizes a type partition is a pessimization, and
the value of typed memory is steering, not indexing.* That is a more interesting paper than a
marginal indexing win, and §4.3 is what makes it defensible.

---

## 5. What this does **not** explain

The token cost. Typed's 2.23× input tokens come from the exclusion block, not the guard, and
§4.1 does not touch it. If §4.3 shows steering carries the outcome effect, the token cost is the
price of the contribution and must be reported as such — currently it is not reported at all.

---

## 6. Order to do this in

1. **§4.1 guard fix** — one function, no new experiment design, strictly sound, and it is what
   turns Thm 4.3(a) from a tie into a win. Add a regression test that stores one counterexample
   under location A and asserts the guard blocks a candidate that edits location B.
2. **§4.3 two extra arms** — cheap, and they are what a reviewer will ask for.
3. **Re-run the main grid** with the fixed guard. Everything in §1–§3 is measured on the current
   log and will change.
4. **§4.2 granularity** and **§4.4 memory growth** only if §4.1 leaves the index looking useful.

---

## 7. Reproducing the numbers

```python
# 1. the leak - docs/TYPED-VS-UNTYPED.md SS1
#    a refuting oracle round whose counterexample case was already in memory
for each episode, in round order:
    keep a set of counterexample case names stored so far
    for each non-guarded, non-accepting round with a fine_type:
        if round.counterexample_args[0] in stored_cases: count it as a leak
        add it to stored_cases

# 2. the regime - SS3
#    live memory size = count of non-guarded refuting rounds so far in the episode

# 3. the scan position - SS3
#    on a guarded round, guard_evaluations IS the 1-based FIFO position of the
#    counterexample that fired, so LIFO position = m - fifo + 1
```

Filters used throughout: `max_examples == 100`, `typing_noise_c == 1.0`, `not typing_random`,
`not force_full_budget`, `not free_guarded_rounds`.
