# ArthaNiyam

ArthaNiyam is a buildathon prototype for verifying financial policies before autonomous systems are allowed to move money.

The MVP asks: can several individually valid agent actions combine into an invalid financial outcome?

The system will translate a constrained natural-language policy into a typed policy, search for counterexamples with a solver, enforce the verified policy at runtime, and execute permitted actions through Razorpay test mode.

## Planned applications

- `backend/` - FastAPI API, policy schema, model checker, runtime guard, ledger, and Razorpay adapter.
- `frontend/` - Policy Studio, Attack Lab, Gateway Comparison, Payment Console, and Proof Explorer.
- `docs/` - MVP specification, invariants, threat model, and demo script.

## MVP invariants

1. Delegated authority cannot multiply.
2. Reserved plus committed spending cannot exceed the budget.
3. Correlated payments cannot bypass an approval threshold.
4. An invoice cannot be successfully paid twice.
5. Transfers and refunds cannot exceed captured money.

See `docs/mvp-spec.md` for the implementation boundary.

## Current runnable slice

The first vertical slice uses Z3 to search for a correlated split-payment
sequence that a request-by-request gateway would allow even though the combined
commitment requires approval. The response contains the concrete actions, both
decisions, the violated invariant, an honest bound statement, and a stable
replay ID derived from the complete policy.

Every verification run is persisted as a proof record with a canonical SHA-256
evidence hash. `GET /api/v1/proofs/{proof_run_id}` retrieves its exact input and
result, while `POST /api/v1/proofs/{proof_run_id}/replay` reruns the solver and
checks both stored-record integrity and deterministic replay. Policy Studio
exposes this through a one-click **Replay proof** control.

## Policy compiler

Policy Studio accepts constrained finance rules in plain language through
`POST /api/v1/policies/compile`. Compilation is deliberately separated from
enforcement: the compiler creates a typed candidate, source mappings,
assumptions, warnings, and blocking ambiguities. Only a schema-valid candidate
can be sent to the deterministic verifier or runtime guard.

The safe default `POLICY_COMPILER_MODE=reference` is a transparent offline
compiler for the demo grammar. Set `POLICY_COMPILER_MODE=openai`,
`OPENAI_API_KEY`, and `OPENAI_MODEL` to use strict structured extraction through
the OpenAI Responses API. Model output is still validated by the same typed
policy boundary and never directly authorizes a payment.

## Bounded approval demo

When correlated spend requires review, the offline simulator creates an
expiring challenge bound to the exact policy version, action, amount, vendor,
purpose, category, and invoice. A grant is accepted only for that binding,
requires distinct approvers according to policy, and is consumed after the
reservation succeeds. Arbitrary approval strings, expired grants, cross-action
reuse, and duplicate approver votes do not bypass the guard.

The interactive endpoints live under `/api/v1/demo/approvals/*` and are
hard-disabled whenever `RAZORPAY_MODE=test`. They intentionally simulate the
human step for the buildathon walkthrough; a real deployment must replace them
with authenticated approver identity and authorization.

## Conserved delegation authority

The Delegation Lab models authority as a stateful graph rather than an
independent flag on each request. It rejects sibling grants whose total exceeds
their parent's authority, cycles, multiple active parents, expired grants, and
paths beyond the configured depth. Delegated authority also constrains the
child's combined committed spend and active reservations, while outbound grants
reduce the parent's remaining spending authority.

The interactive `/api/v1/demo/delegations/evaluate` endpoint is restricted to
the offline simulator. Razorpay Test Mode keeps authority administration
disabled until an authenticated administrative integration is provided.

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000` for the interactive ArthaNiyam dashboard, or
`http://127.0.0.1:8000/docs` for the raw API. The dashboard includes Policy
Studio, bounded counterexample search, a live two-payment Attack Lab, a shared
budget view, and an inspectable runtime audit trail. A ready-to-paste API
request is also available in `docs/demo-request.json`.

The stateful runtime slice is also available at `POST /api/v1/runtime/evaluate`.
It compares request-local gateway logic with cross-request enforcement,
atomically reserves permitted amounts, and protects against correlated payment
splitting, duplicate invoices, action-ID conflicts, and budget races. See
`docs/runtime-demo.md` for the two-request demonstration.

Runtime policies, reservations, commitments, evaluations, audit events, and
provider executions are persisted in SQLite. The database defaults to
`backend/arthaniyam.sqlite3` and can be changed with
`ARTHANIYAM_DATABASE_PATH`.

Payment execution defaults to a deterministic offline Razorpay simulator. To
connect a Razorpay Test Mode account, copy `.env.example` to `.env`, set
`RAZORPAY_MODE=test`, and provide test-only credentials. The adapter rejects
live keys by design. A policy-approved active reservation is required before
`POST /api/v1/executions/orders` will create an order.

## Payment lifecycle

An approved action is reserved first; creating a provider order does not count
as payment. The reservation becomes committed only after a trusted payment
confirmation:

1. `POST /api/v1/executions/orders` creates an idempotent simulator or Razorpay
   Test Mode order.
2. In Test Mode, the dashboard opens Razorpay Checkout. The browser sends the
   returned payment ID, order ID, and signature to
   `POST /api/v1/executions/confirm`.
3. The server verifies the HMAC against its stored order ID, fetches the payment
   from Razorpay, checks amount and currency, and commits only a captured
   payment. The key secret is never sent to the browser.
4. `POST /api/v1/webhooks/razorpay` provides the asynchronous path. It verifies
   the signature against the unmodified request body, deduplicates
   `X-Razorpay-Event-Id`, and safely handles repeated or out-of-order delivery.

For webhooks, configure a separate `RAZORPAY_WEBHOOK_SECRET` and point the
Razorpay Test Mode webhook URL at `/api/v1/webhooks/razorpay`. The simulator
needs no credentials and automatically produces a deterministic successful
confirmation for the dashboard demo.

This prototype never enables live money movement. Test credentials beginning
with `rzp_test_` are accepted; live credentials are rejected.
