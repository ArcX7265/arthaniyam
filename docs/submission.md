# ArthaNiyam — buildathon submission

## One-line pitch

ArthaNiyam is a financial policy control plane that verifies what autonomous
agents intend to do, detects unsafe action sequences that ordinary API checks
miss, and permits money movement only after shared financial invariants hold.

## Track

Open Innovation.

## Problem

Autonomous agents can call payment APIs correctly and still create an unsafe
financial outcome. Two payments may each sit below an approval threshold while
their combined purpose exceeds it. Multiple agents may reserve the same budget,
reuse an invoice, multiply delegated authority, replay an approval, or refund
more than was captured.

Traditional gateways mostly evaluate one request at a time. The dangerous
property often belongs to a sequence of individually valid requests.

## Solution

ArthaNiyam adds a verification and enforcement layer before payment execution:

1. A finance owner describes a constrained policy in plain language.
2. The policy becomes a typed, reviewable model.
3. Z3 searches bounded action sequences for a counterexample.
4. The runtime guard compares each action with durable shared state.
5. Permitted money is atomically reserved before provider execution.
6. Sensitive actions require an expiring approval bound to the exact action.
7. Every decision enters a tamper-evident audit chain.
8. Judges can download and independently verify the resulting evidence.

## Why it is more than an if/else gateway

An ordinary condition answers, “Is this request smaller than INR 10,000?”

ArthaNiyam answers, “If this request is added to everything already reserved,
committed, delegated, approved, captured, and refunded, can any financial
invariant be violated?”

That requires temporal correlation, conserved quantities, atomic admission,
formal counterexample search, and replayable evidence—not only additional
conditions.

## Standout features

- Bounded formal verification with concrete, replayable counterexamples
- Stateful correlation across vendor, purpose, and invoice dimensions
- Atomic multi-instance reservations against a shared ledger
- Conserved delegated authority graph
- Single-use, exact-action approval capabilities
- Captured-funds and cumulative-refund conservation
- Counterfactual policy rollout before deployment
- Tamper-evident audit chain and portable offline verification
- Mixed adversarial/benign evaluation and seeded boundary fuzzing
- One-click guided demo and persisted judge scorecard

## Measured prototype evidence

| Measurement | Result | Scope |
|---|---:|---|
| Automated tests | 71 passing | Backend, API, persistence, security invariants, evidence, and UI contracts |
| Judge readiness checks | 6/6 | Symbolic, fixed, generated, concurrency, and evidence checks |
| One-click judge cases | 31 | 11 fixed scenarios plus 20 generated boundary cases |
| Fixed attack recall | 100% | Seven synthetic attacks |
| Fixed false-positive rate | 0% | Four synthetic benign controls |
| Multi-instance budget burst | 5 admitted, 7 denied | Twelve simultaneous INR 10,000 requests competing for INR 50,000 |
| Portable evidence | Valid | API verifier and Python standard-library verifier |

These measurements describe the included synthetic suite. They are evidence of
prototype behavior, not estimates of performance on real fraud distributions.

## Real-world impact

The pattern applies anywhere software agents can create financial commitments:
procurement, accounts payable, subscription recovery, marketplace refunds,
treasury operations, and agent-to-agent commerce. The immediate value is not
another agent that recommends an action; it is an enforceable boundary shared
by every agent that can move money.

## Scalability

The current prototype coordinates independently locked runtime instances using
transactional SQLite admission. Its typed policy, runtime decision, audit, and
portable evidence formats do not depend on SQLite. A scaled deployment can move
the repository boundary to PostgreSQL or another transactional ledger while
keeping the verification and policy semantics unchanged.

## Safety boundary

- Live Razorpay keys are rejected.
- All demos use the offline simulator or Razorpay Test Mode.
- Model-generated policy output never directly authorizes money.
- Formal verification is bounded and reported as such.
- Authenticated human identity, production compliance, multi-host consensus,
  monitoring, and external audit notarization remain future work.

## Technology

- FastAPI and Pydantic for typed API and policy contracts
- Z3 for bounded counterexample search
- SQLite WAL transactions for durable prototype state and atomic admission
- Razorpay-compatible simulator and Test Mode adapter
- Vanilla HTML, CSS, and JavaScript for the zero-build dashboard
- Pytest for deterministic and adversarial verification
- Docker, Compose, and GitHub Actions for reproducibility

## Demo

Run `scripts/start-demo.ps1`, open `http://127.0.0.1:8000`, and click **Start
90-second demo**. The guided flow shows the stateless failure, ArthaNiyam’s
stateful decision, and the independent scorecard. The detailed walkthrough is
in [judge-demo.md](judge-demo.md).
