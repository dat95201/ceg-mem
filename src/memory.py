"""A6 - the three memory stores.

Common interface: store, guard, evidence, exclusions.
UntypedMemory.guard must genuinely re-run every stored counterexample; that
cost is the subject of Proposition 4.5. Do not optimise it away.
"""
