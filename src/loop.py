"""A7 - the repair loop (Algorithm 1).

  for t in 1..B:
      p = propose(evidence, exclusions)   <- steering acts here
      if guard(p): continue               <- guard acts here
      r = oracle(p)
      if r is accept: return p
      store(p, r.counterexample, theta(p))
"""
