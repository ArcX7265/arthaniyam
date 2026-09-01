# ArthaNiyam judge demo

## The 90-second path

1. Open the dashboard and click **Start 90-second demo**.
2. Show how both INR 9,000 requests pass the local gateway, while ArthaNiyam
   gates the second after observing INR 18,000 of correlated exposure.
3. Use **Inspect full evidence** to continue to Buildathon judge mode.
4. Explain that three independent mechanisms are running: Z3 searches for an
   unsafe sequence, fixed scenarios exercise known attacks and benign controls,
   and seeded boundary generation probes values immediately around policy
   limits.
5. Point to the six checks, the combined test-case count, attack recall,
   false-positive rate, and the canonical evidence hash.
6. Point to **Honest boundary**. ArthaNiyam deliberately distinguishes measured
   prototype evidence from claims it has not established.
7. Download the verifiable JSON and run `python scripts/verify_scorecard.py
   <downloaded-file>` to validate it independently from the server.

## The core sentence

"A normal gateway asks whether this request is allowed; ArthaNiyam asks whether
this request, combined with everything already authorized, can violate the
financial invariant."

## What the scorecard proves

- A bounded solver can construct a split-payment counterexample to local checks.
- Seven known attacks are stopped while four benign controls remain available.
- Generated cases just above and below two policy boundaries match an
  independent arithmetic oracle.
- Twelve independently locked runtime instances cannot reserve beyond their
  shared SQLite budget.
- Identical seeded runs produce identical canonical evidence hashes.

## What it does not prove

- Safety outside the solver's tested action bound.
- Accuracy on real production fraud distributions.
- Multi-host distributed consensus or failover safety.
- Authorization for live money movement.
