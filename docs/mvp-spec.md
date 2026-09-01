# ArthaNiyam MVP Specification

## Objective

Build a working multi-agent financial policy verifier that accepts a constrained procurement policy, searches for unsafe transaction sequences, renders solver-generated counterexamples, enforces the corrected policy at runtime, executes permitted actions through Razorpay test mode, and produces a replayable proof trail.

## Primary demonstration

A naive gateway approves three related payments of INR 9,000 because each is below an INR 10,000 approval threshold. ArthaNiyam identifies their shared purpose, treats them as one INR 27,000 commitment, and requires approval.

## Policy surface

- monthly budget;
- per-transaction limit;
- approval threshold;
- approved vendors and categories;
- correlation window and grouping keys;
- parent-child delegation; and
- authority expiration.

## Verified invariants

1. Parent authority plus delegated descendant authority cannot exceed the original grant.
2. Committed spend plus active reservations cannot exceed the budget.
3. Correlated commitments above the threshold require valid approval.
4. One invoice cannot lead to multiple successful payments.
5. Transfers and cumulative refunds cannot exceed captured money.

## Honest verification language

The model checker is bounded. When it cannot find an attack, the UI must say: "No counterexample found within the tested model."

The mixed evaluation benchmark reports attack recall across six known attacks
and false-positive rate across four known benign controls. These fixed-suite
metrics must not be described as production fraud accuracy.

A seeded boundary campaign may generate 20–200 balanced cases immediately
above and below the approval and monthly-budget limits. Its expected outcomes
come from an independent arithmetic oracle, and its hash excludes runtime-only
metadata so an identical seed can be replayed deterministically.

Counterfactual rollout simulation replays at most 200 actions in isolated
current-policy and candidate-policy ledgers. It measures decision changes and
state divergence without creating provider orders or moving money.

## Non-goals

- production money movement;
- replacing a payment gateway;
- arbitrary natural-language theorem proving;
- production AP2, x402, or UPI adapters;
- blockchain settlement or zero-knowledge proofs; and
- multi-region banking infrastructure.
