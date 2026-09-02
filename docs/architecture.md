# ArthaNiyam architecture

## System view

```mermaid
flowchart LR
    Human[Finance owner] -->|constrained policy language| Compiler[Policy compiler]
    Compiler -->|typed, reviewable policy| Schema[Policy schema]
    Schema --> Verifier[Z3 bounded verifier]
    Verifier -->|counterexample + evidence hash| Proofs[(Proof records)]

    Agent[Autonomous agent] -->|financial action| API[FastAPI control plane]
    API --> Local[Request-local comparison]
    API --> Guard[Stateful runtime guard]
    Schema --> Guard

    Guard -->|BEGIN IMMEDIATE| Ledger[(Shared SQLite ledger)]
    Ledger --> Guard
    Guard -->|review required| Approval[Action-bound approval]
    Approval --> Guard
    Guard -->|reserved action only| Payments[Razorpay adapter]
    Payments -->|simulator or Test Mode confirmation| Ledger

    Guard --> Audit[Tamper-evident audit chain]
    Payments --> Audit
    Audit --> Bundle[Portable evidence bundle]

    Verifier --> Scorecard[Judge scorecard]
    Guard --> Scorecard
    Fuzzer[Seeded boundary campaign] --> Scorecard
    Scorecard --> Judge[Guided demo / independent verifier]
```

The language model, when enabled, proposes a typed policy candidate. It never
authorizes payments. Deterministic schema validation, the solver, and the
stateful runtime guard remain the enforcement boundary.

## Why a stateless gateway is insufficient

```mermaid
sequenceDiagram
    participant A as Purchasing agent
    participant G as Local gateway
    participant N as ArthaNiyam
    participant L as Shared ledger

    A->>G: Pay INR 9,000 for office laptops
    G-->>A: Allow: below INR 10,000 threshold
    A->>N: Same payment
    N->>L: Read correlated commitments
    L-->>N: INR 0
    N->>L: Atomically reserve INR 9,000
    N-->>A: Allow and reserve

    A->>G: Pay another INR 9,000 for office laptops
    G-->>A: Allow: below INR 10,000 threshold
    A->>N: Same payment
    N->>L: Read correlated commitments
    L-->>N: INR 9,000 already reserved
    N-->>A: Require approval: exposure is INR 18,000
```

An `if` statement can validate the current request. ArthaNiyam evaluates the
current request against durable commitments, delegated authority, prior
invoices, approvals, refunds, and correlated intent.

## Components and guarantees

| Component | Responsibility | Demonstrated guarantee |
|---|---|---|
| Policy compiler | Converts constrained prose into typed candidate policy | Ambiguities remain reviewable and cannot directly authorize money |
| Z3 verifier | Searches bounded action sequences | Produces a minimal split-payment counterexample and honest bound statement |
| Runtime guard | Makes stateful authorization decisions | Applies budget, correlation, invoice, delegation, approval, and refund invariants |
| Shared ledger | Coordinates reservations | Serializes final admission across independently locked runtime instances |
| Approval binding | Represents human authorization | Binds approval to exact policy version and action contents; consumes it after use |
| Razorpay adapter | Executes approved reservations | Supports offline simulation and Razorpay Test Mode; rejects live keys |
| Audit chain | Records decisions and transitions | Detects changed content, broken links, missing events, and deleted tails |
| Evaluation system | Measures safety and availability | Runs attacks, benign controls, boundary generation, and concurrency bursts |
| Evidence verifier | Validates exported artifacts | Recomputes hashes without trusting the running API |

## Trust boundaries

- All financial values are integers in paise.
- The reference compiler and simulator work completely offline.
- OpenAI model output is treated as untrusted policy input.
- Provider confirmation is trusted only after server-side verification.
- Interactive approvals, delegation administration, and refunds are simulator
  demonstrations until authenticated operator identity is integrated.
- SQLite coordinates instances sharing one database on one host. Multi-host
  consensus and failover are outside the prototype.
- Evaluation metrics describe synthetic benchmark cases, not production fraud
  rates.

## Production scaling path

The policy and evidence contracts are storage-independent. A production system
would replace SQLite with a transactional database such as PostgreSQL, use row
or advisory locks for policy-level admission, place runtime instances behind a
load balancer, externalize audit checkpoints, and connect approvals to an
authenticated identity provider. None of those changes require financial
authorization to be delegated to a language model.
