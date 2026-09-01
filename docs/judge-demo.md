# ArthaNiyam judge demo

## The 90-second path

1. Open the dashboard and scroll to **Buildathon judge mode**.
2. Click **Generate judge scorecard**.
3. Explain that three independent mechanisms are running: Z3 searches for an
   unsafe sequence, fixed scenarios exercise known attacks and benign controls,
   and seeded boundary generation probes values immediately around policy
   limits.
4. Point to the six checks, the combined test-case count, attack recall,
   false-positive rate, and the canonical evidence hash.
5. Point to **Honest boundary**. ArthaNiyam deliberately distinguishes measured
   prototype evidence from claims it has not established.

## The core sentence

"A normal gateway asks whether this request is allowed; ArthaNiyam asks whether
this request, combined with everything already authorized, can violate the
financial invariant."

## What the scorecard proves

- A bounded solver can construct a split-payment counterexample to local checks.
- Seven known attacks are stopped while four benign controls remain available.
- Generated cases just above and below two policy boundaries match an
  independent arithmetic oracle.
- A simultaneous in-process burst cannot reserve beyond the shared budget.
- Identical seeded runs produce identical canonical evidence hashes.

## What it does not prove

- Safety outside the solver's tested action bound.
- Accuracy on real production fraud distributions.
- Multi-process or distributed transaction safety.
- Authorization for live money movement.
