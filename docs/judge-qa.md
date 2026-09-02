# Judge Q&A

## What is ArthaNiyam in one sentence?

It is a financial policy control plane that checks an agent’s proposed action
against shared financial history and invariants before allowing a payment API
to execute it.

## Why is this not just a collection of if/else statements?

An `if` statement can check the current request. The central failures here are
properties of sequences and conserved quantities: correlated payments,
reserved plus committed spend, delegated authority, cumulative refunds, and
single-use approvals. ArthaNiyam combines durable state, transactional
admission, formal counterexample search, and replayable evidence to reason
about those properties across requests.

## Where is AI used meaningfully?

AI translates constrained policy language into a typed candidate and explains
counterexamples and runtime decisions. The model is deliberately outside the
authorization boundary: its output must pass deterministic schema validation,
and payments are governed by formal and stateful checks. This uses AI for the
ambiguous language layer while keeping financial enforcement deterministic.

## Could the product work without an LLM?

Yes—the reference compiler, verifier, runtime guard, simulator, and evidence
system all work offline. That is a safety property, not a weakness. An LLM
improves policy authoring and explanation, but the system never requires a
probabilistic model to decide whether money may move.

## What is technically novel about the project?

The contribution is the combination of design-time bounded verification and
runtime enforcement over the same typed financial policy. A counterexample is
not merely shown in a notebook: the runtime guard remembers the related state,
atomically reserves permitted funds, binds approvals to exact actions, and
emits independently verifiable evidence.

## What happens in the main demo?

Two INR 9,000 purchases share a vendor and purpose. A local gateway allows both
because each is below the INR 10,000 threshold. ArthaNiyam allows and reserves
the first, then requires approval for the second because correlated exposure is
INR 18,000.

## How do you know it is not blocking everything?

The fixed benchmark includes four benign controls as well as seven attacks. It
reports attack recall and false-positive rate separately. The current included
suite catches all seven attacks and allows all four controls. Seeded boundary
generation also tests balanced values immediately above and below policy
limits.

## Are 100% recall and 0% false positives realistic?

They are results for the included synthetic suite, not a production fraud
claim. The dashboard and submission state this explicitly. The purpose of the
suite is to make the prototype falsifiable and reproducible, then expose any
failure instead of presenting selected successful examples.

## How does it scale beyond one process?

The current benchmark creates twelve independently locked runtime instances
sharing one SQLite ledger. Final admission is rechecked inside a serialized
write transaction, so they cannot independently consume the same budget. A
production deployment would replace the repository with PostgreSQL and use
row or advisory locks while retaining the typed policy and decision contracts.

## Is it distributed-safe today?

No. It demonstrates coordination between instances that share one SQLite
database on one host. Multi-host consensus, failover, and partition handling
are outside the prototype and are listed as limitations.

## Why use formal verification if it is bounded?

A found counterexample is concrete proof that a policy permits an unsafe
sequence. When none is found, ArthaNiyam only reports that no counterexample was
found within the tested bound. The result is useful because it searches action
combinations systematically while stating exactly what it did and did not
establish.

## Can a generated policy directly authorize money?

No. Model output becomes an untrusted typed candidate. Schema validation,
policy review, deterministic runtime checks, approval verification, and provider
confirmation remain separate gates.

## How are approvals protected from replay?

An approval is bound to the policy version and exact action contents, including
amount, vendor, purpose, category, and invoice. It expires, requires distinct
approvers according to policy, and is consumed after the reservation succeeds.

## Can this move real money?

The prototype intentionally rejects live Razorpay keys. It uses a deterministic
offline simulator by default and can use Razorpay Test Mode for a checkout
demonstration.

## How can a judge trust the evidence export?

Audit events form a SHA-256 hash chain with a persisted head checkpoint. Judge
scorecards include canonical hashes over their constituent evidence. Exported
bundles can be checked by API or by standard-library Python scripts without
running or trusting the ArthaNiyam server.

## What would you build next for production?

Authenticated organization and approver identities, a PostgreSQL repository,
external audit-checkpoint notarization, observability, rate limiting, secret
management, provider reconciliation, failure recovery, and compliance review.
