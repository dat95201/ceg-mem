# Project status — CEGMem

**State: clean. No results are held.** `data/` contains source only
(`mutants.py`, the planted-mutant operators). Every candidate list, gate report,
screen, corpus freeze, episode log, call ledger and analysis output was removed,
so nothing in this checkout was measured under a superseded design.

The response cache (`cache/`, untracked) is deliberately kept. It is
content-addressed on `(model, temperature, max_tokens, nonce, prompt)`, so a
re-run does not *reuse* an old result — it gets the identical completion the same
request would have produced, without paying for it again. To force a cold run,
`rm -rf cache` first.

## Where to look

| | |
|---|---|
| `bash scripts/pipeline.sh` | which stage has run, and what runs next |
| [PLAN.md](PLAN.md) | the ordered plan: which command serves which paper claim, and what it costs |
| [SELECTION.md](SELECTION.md) | why the benchmark is filtered at all, and on what |
| [CORPUS.md](CORPUS.md) | the oracle gate and the corpus freeze |
| [SCREENING.md](SCREENING.md) | the π̂ screen, sharded across machines |
| [EXPERIMENT.md](EXPERIMENT.md) | E1–E5: protocol, shards, merge audit, failure modes |

## Which numbers are synthetic

All numbers **in the paper** are synthetic. §5 states it plainly: the proposer
and the oracle are simulated, no model is prompted and no test suite is executed.
That is deliberate — soundness and non-repetition are only checkable against a
known candidate space and a known set of correct patches, which no real benchmark
provides. §9 names the next step, and this repository is that step: instantiate
the type function and the counterexample oracle on a real benchmark, and measure
how coherent real failure types actually are.

No number in this repository is real either, because it holds no results. When it
does, `scripts/check_consistency.py` rebuilds every frozen file from
`data/episodes.jsonl` and deep-diffs it against what is on disk, so a reported
number cannot drift from the artifact that produced it.

## What a run has to decide before it starts

These are choices, not defaults to accept silently. Each is argued where it is
made; they are collected here because all four are cheap to fix beforehand and
expensive to fix afterwards.

1. **Screen depth K.** π̂ lives on a grid of `1/K`, so a band is fillable only if
   some multiple of `1/K` falls inside it. Check the band counts after the freeze
   and before E1 — EXPERIMENT.md §2 has the one-liner. A primary band that comes
   out empty is a measurement limit, and the fix is a deeper screen, not a
   smaller claim.
2. **The proposer.** π is a property of the model, so the screen and E1–E5 must
   run under the same one. `data/tasks.json` records which model it was banded
   under; `eval_shard.sh` pins it and verifies the served context window.
3. **Budget B and the seed counts.** PLAN.md §0 pre-registers both deviations
   from the paper and the reason for each.
4. **Whether to report the transcript condition.** PLAN.md's fidelity caveats
   explain why the paper's untyped baseline shows the proposer nothing, and what
   a transcript-in-the-prompt arm would measure instead. It is a fourth mode, not
   a relabelling of `untyped`.

## Known gaps against the paper's §10

Recorded here rather than silently carried:

- **`scripts/gen_synthetic.py` does not exist.** It produced every number in the
  paper. This repo replaced the synthetic corpus with the real-benchmark chain,
  so the paper's own four-command reproduce sequence cannot be run here.
- **`data/results.json` is never produced** — the frozen artifact is
  `data/results_real.json`. §10's re-derivability guarantee names the former.
- **`data/schema.md` does not exist**, though §10 promises it documents the task
  and result formats.
- `requirements.txt` pins `openai` and `python-dotenv`; §10 states the artifact
  depends *solely* on numpy, scipy and matplotlib. That claim describes the
  synthetic artifact and does not survive the move to a real LLM proposer.
