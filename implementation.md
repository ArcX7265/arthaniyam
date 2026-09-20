# ArthaNiyam — Paytm Hackathon Implementation Plan

> Build an autonomous financial-control teammate that completes merchant support work under enforceable financial policies.

This plan evolves the existing ArthaNiyam repository. It is a standalone product; no Resora application or service is required. The README describes what runs today. All proposed tools, entities, endpoints, and states below are targets unless explicitly marked existing.

## Build checkpoint — 20 September 2026

The support slice and **bounded model-driven investigator are implemented**. Offline reference mode runs without credentials. Live-model validation is pending because no OpenAI API key is configured locally; neither mode enables production payments.

- `/` serves the minimal support inbox; `/labs` preserves the policy demonstration.
- `backend/app/support/` supplies typed intake, stored evidence, server-selected support policy, exact evidence-bound approval, takeover, metrics and synthetic receipt jobs.
- Four idempotently seeded payments demonstrate duplicate payment, cancelled order and accepted-return scenarios. Status enquiries inspect existing support refunds; uncertain evidence goes to review.
- Support approvals expire after five minutes. Cumulative refunds per capture above ₹5,000 require review, including split requests. Changed policy, prior refunds or evidence invalidate a reviewed proposal without executing it.
- Refund admission, cumulative check, refund record, refund audit event, approval consumption, case transition and receipt job are committed together. All accepted simulator refunds count against the capture immediately, including those waiting for a receipt. Independent service instances serialize through SQLite, not a process-local lock.
- The API lifespan worker reconciles due synthetic receipts every two seconds, in batches of up to 25. A standalone worker is optional. Human takeover blocks further actions but permits receipt reconciliation without automatic closure.
- The new UI renders untrusted strings as text. The legacy labs' dynamic rendering remains a separate hardening task.
- The investigator adds 26 regression cases, bringing the total to 115. Mocked model-contract tests are not evidence of live-model accuracy. Provider dispatch recovery and real bank settlement remain outside the implemented scope.

### Investigator implementation

`support/agent.py` implements a Responses function-calling loop with strict, validated schemas and only five tools: `get_request`, `get_payment_evidence`, `propose_resolution`, `ask_for_information`, and `escalate_to_human`. Both read tools must run before a proposal; the proposal must cite the exact snapshot fingerprint. The model cannot choose payment scope, amount, policy, approval, execution outcome or arbitrary tools. The operator supplies the payment and amount and confirms the proposed complaint type before the deterministic support service runs.

`support/investigations.py` persists typed intake, bounded follow-up history, proposal records and 60-second investigation leases in existing case storage. Network calls run outside database transactions. Concurrent runs, changed evidence, human takeover, replaced input and late results are checked before accepting a proposal. Confirmation rechecks expiry and evidence atomically; repeated confirmation returns the same result. Finance review remains a separate transition. Unexpected model behavior fails closed with a useful visible reason and no offline fallback.

Model budget: six requests, 1,800 output tokens per request, 45-second overall timeout. Conversations accept at most ten follow-ups. Encrypted reasoning/output items are kept only in memory for API continuation; the UI persists tool names and concise proposals, not private reasoning. The reference path is explicitly a narrow keyword implementation. All model/provider claims require subsequent live evaluation.

Implemented API prefix: `/api/v1/support`. Endpoints include `POST /demo/seed`, `GET /payments`, `GET /metrics`, `GET|POST /requests`, `GET /requests/{id}`, `POST /requests/{id}/investigate|approve|takeover`, `GET /investigator/capabilities`, `POST /investigations`, `POST /requests/{id}/messages`, and `POST /requests/{id}/confirm-proposal`. All support endpoints are disabled outside simulator mode. Cross-origin browser mutations are rejected; this is not authentication. Keep the app loopback-only.

**Next validation:** configure credentials and run a live-model evaluation of paraphrases, ambiguous intent, missing details and injection attempts. Still pending: trusted identity/tenant scope, approval rejection and resume, real provider outbox/reconciliation, an explicit support evidence export, solver witness correction and legacy lab rendering hardening. Case/evidence JSON and a synthetic receipt worker are an MVP, not the complete normalized production model below. The core refund journal calls simulator acceptance `executed`; the support case stays `refund_pending` until a synthetic receipt arrives. No model or customer text directly authorizes money movement.

## 1. Product and track fit

Intended event: Paytm Build for India AI Hackathon, Mumbai. Intended track: Autonomous AI Teammates.

The supplied track brief asks for measurable outcomes in sales or customer service, with contextual decisions, action across systems, and human collaboration. ArthaNiyam's existing financial policy infrastructure supports that direction, but relabelling an enforcement dashboard is insufficient. The submission should demonstrate a merchant refund-support request from intake through confirmed resolution.

Primary user: a merchant support operator who needs an eligible refund investigated and completed. A finance owner defines limits and reviews exceptions. The customer receives a clear outcome through a simulated internal update feed for the MVP.

Proposed pitch: “ArthaNiyam investigates merchant refund requests, completes permitted actions, and brings finance teams in when approval is needed—with evidence for every decision.”

Before final submission, confirm the official event edition, deadline, reuse policy for an existing project, judging criteria, and any required APIs. Describe pre-existing work and new hackathon work accurately. The supplied screenshot does not establish these rules, and track fit is a proposal rather than organiser approval.

## 2. Baseline before the support build

| Area             | Existing implementation                                     | Remaining work                                                          |
| ---------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------- |
| Policy authoring | Reference parser and optional model extraction              | Validate live extraction; review policy assumptions before activation   |
| Verification     | Bounded Z3 split-payment search and replay                  | Fix witness identifiers and ensure witnesses obey modeled scope         |
| Runtime          | Budget, correlation, invoice, and authority checks          | Broaden concurrency and transition recovery coverage                    |
| Approvals        | Expiring action-bound simulator grants                      | Add support workflow linkage and atomic consumption with admission      |
| Refunds          | Sequential capture conservation and replay handling         | Atomic shared-database admission and pending refund reservations        |
| Payments         | Simulator, test checkout, signature verification            | Recover uncertain execution and conflicting/out-of-order events         |
| Evidence         | Audit chains and portable verifiers                         | Link request, evidence, policy decision, approval, and provider outcome |
| Delivery         | Dashboard, 71-test suite, CI configuration, Docker, scripts | Support workspace, integrated demo, updated submission materials        |

This table records the pre-build baseline (71 tests). See the checkpoint above for completed support and investigator work. Model-generated proposals are separate from authoritative server-generated execution outcomes.

## 3. MVP boundaries

Implement three outcomes for one merchant refund workflow:

- **Allow:** a supported request with verified capture, sufficient refundable balance, and authority is executed once.
- **Require approval:** an eligible request exceeds the configured automatic threshold; it waits for an approval bound to the exact action.
- **Deny or escalate:** a request exceeds refundable funds, conflicts with policy, lacks evidence, or has an uncertain record match. No new refund is sent.

Keep the existing split-payment, delegation, shadow-policy, and solver demonstrations as supporting technical evidence. Do not make the main demo dependent on navigating every lab.

Outside this MVP: live money movement, arbitrary bank-transfer reversal, fraud adjudication, chargeback decisions, external customer messaging, voice, and a full banking platform. Public deployment requires authenticated tenant and operator boundaries; the initial demo remains local with synthetic identities.

## 4. Responsibilities and architecture

### Resora features to adapt

The existing Resora code is a reference for tested behaviors and fixtures. Port selected behavior into ArthaNiyam; do not require Resora to run or duplicate ArthaNiyam's financial policy engine.

| Resora feature                             | Priority           | ArthaNiyam adaptation                                                           | Acceptance check                                                       |
| ------------------------------------------ | ------------------ | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Complaint intake and conversation history  | First slice        | Store request messages and missing-information prompts in support records       | Repeated messages retain context and scoped references                 |
| Searchable inbox and case detail drawer    | First slice        | Add a simple requests list and evidence detail view to the existing frontend    | Operator can identify the next action without entering a technical lab |
| Linked order, payment, and refund evidence | First slice        | Add read tools and source-linked snapshots                                      | Claims are distinct from provider facts; ambiguous matches escalate    |
| Duplicate-payment investigation            | First slice        | Verify two distinct captures for one purchase and propose one eligible refund   | Repeated complaints cannot create a second action                      |
| Existing-refund tracking                   | First slice        | Resume the original refund rather than issue another                            | Request stays pending until confirmation and survives restart          |
| Approval fingerprint invalidation          | First slice        | Combine material evidence versions with ArthaNiyam's exact-action grant         | Changed amount, policy, capture, or refund state requires fresh review |
| Human takeover and explicit resume         | First slice        | Pause new actions while retaining provider reconciliation                       | A worker cannot dispatch a new refund for a human-owned request        |
| Delayed second payment                     | After first slice  | Observe late capture, reopen investigation, and apply the same refund guard     | One verified duplicate produces at most one refund                     |
| Payment after cancellation                 | After first slice  | Preserve cancellation, verify fulfillment eligibility, propose refund or review | No cancelled order is silently confirmed                               |
| Scenario lab and record-derived metrics    | Demo preparation   | Seed reproducible support fixtures and display actual outcomes                  | Metrics match persisted requests and actions                           |
| Paid-but-unconfirmed order correction      | Optional extension | Add an operational order adapter with its own permissions                       | Confirmation is idempotent and blocked by cancellation                 |

Reference locations in the separate Resora repository: `src/lib/domain.ts` for reconciliation, fingerprints, takeover, and simulated jobs; `src/lib/types.ts` for case records; `src/components/workspace.tsx` for UI behavior; and `tests/` for fixtures and invariants. These are reference paths, not new files in ArthaNiyam.

Resora currently routes partial existing refunds to human review and uses a coarse per-payment refund key. Supporting multiple legitimate partial refunds in ArthaNiyam requires explicit action identities and cumulative reservations; copying that key unchanged would block valid work. Its provider jobs are synthetic confirmations, not a production reconciliation scheduler. Port the user behavior while implementing the required transactional and recovery guarantees in Python.

The intended product remains **ArthaNiyam**: Resora contributes the support experience, and ArthaNiyam supplies the policy enforcement and evidence infrastructure. A separate Next.js app, database snapshot format, and competing approval engine are unnecessary dependencies.

### Service flow

```text
Merchant support request
          |
          v
Request orchestrator <----> AI evidence and proposal tools
          |
          v
Deterministic eligibility + policy guard
          |
          +---- deny / missing evidence ----> escalation or clarification
          |
          +---- approval required ----------> finance review ----+
          |                                                     |
          +<-------------------- revalidate exact action --------+
          |
          v
Atomic admission + refund reservation + execution job
          |
          v
Provider adapter ----> verified completion / pending / unknown
          |                              |
          +---------- durable follow-up -+
                         |
                         v
Request outcome + merchant/customer update + evidence
```

Retain FastAPI, Pydantic, SQLite WAL, Z3, and the existing HTML/CSS/JavaScript frontend. Add services within this repository; a second application is unnecessary.

The model interprets the request, chooses evidence to inspect, proposes an action, and writes a short explanation grounded in returned records. Deterministic services own matching constraints, arithmetic, eligibility, authority, approval, and execution. A model proposal can never make a denied action executable.

Use the existing repository layer for persistence. Add a durable job/outbox table for external actions and follow-ups. Do not hold a database write transaction open during a provider or model network request. A worker claims bounded leases and resumes work after restart.

## 5. Data contracts

Continue storing amounts as integer paise with an explicit currency. New operational records carry merchant scope and timestamps.

| Entity            | Required information                                                                                  |
| ----------------- | ----------------------------------------------------------------------------------------------------- |
| SupportRequest    | ID, merchant, customer/reference, request text, status, owner, next action, version                   |
| EvidenceSnapshot  | Request ID, source record, observation time, authoritative/claim classification, relevant versions    |
| ActionProposal    | Stable action ID, request, payment, action type, amount, reason, evidence fingerprint, policy version |
| PolicyDecision    | Action ID, actual guard result, reason codes, policy identity, decision time, evidence reference      |
| ApprovalChallenge | Exact binding, required reviewers, expiry, votes, status; reuse existing structures where possible    |
| RefundReservation | Capture/payment ID, action ID, reserved amount, state, unique logical execution key                   |
| ExecutionAttempt  | Action ID, provider mode/reference, stable idempotency key, pending/unknown/final outcome             |
| Job / OutboxEntry | Subject, due time, lease, attempts, deduplication key, last error                                     |
| RequestUpdate     | Audience, evidence-backed message, timestamp, internal delivery status                                |

Bindings include merchant, policy version, action identity, amount, payment, reason, and material evidence versions. Server code selects the merchant's configured policy. Clients and the model cannot submit a more permissive policy to gain authorization.

Define refund-specific policy fields deliberately. Procurement spending budgets and refund limits describe different quantities; a refund must not silently be represented as another purchase to reuse the existing guard.

## 6. Agent tools and operating loop

Proposed read tools:

- `get_support_request`
- `get_order_and_payments`
- `get_capture_and_refunds`
- `get_applicable_policy`
- `get_action_history`

Proposed workflow tools:

- `ask_for_missing_information`
- `propose_refund`
- `request_bound_approval`
- `schedule_follow_up`
- `escalate_to_human`
- `publish_verified_update`

`propose_refund` invokes server validation and the guard. It cannot directly invoke a provider or select a decision outcome. An allowed proposal creates durable execution work; the executor rechecks authority and current records before dispatch.

Bound model turns, tool calls, elapsed time, and retries per run. Stop with a useful escalation when the budget is exhausted. Keep deterministic reference mode for offline fixtures and label it clearly. Model failures must be visible; do not report scripted fallback behavior as live AI.

Treat request text and provider descriptions as untrusted data. They cannot change permissions or tool instructions. Show retrieved evidence and concise rationale, rather than private model reasoning.

## 7. Request and execution lifecycle

```text
OPEN -> INVESTIGATING -> POLICY_CHECK -> READY -> EXECUTING -> VERIFYING -> RESOLVED
             |              |                       |            |
      WAITING_INFORMATION   WAITING_APPROVAL        WAITING_PROVIDER

Any active stage -> HUMAN_OWNED / ESCALATED
Changed evidence -> fresh investigation and authorization
```

Separate request status from action status. An authorized action is not a completed refund. Mark a request resolved only after authoritative provider evidence establishes the intended outcome.

Human takeover blocks new autonomous actions while permitting status reconciliation for actions already sent. Expired or rejected approvals cannot authorize execution. Resume requires an explicit operator action and fresh validation.

After an uncertain provider response, retain the reservation and query the same action. Release funds only after a verified terminal failure or cancellation. Unknown outcomes require reconciliation or human review, never a new logical refund.

## 8. Correctness work before integration

### Counterexample fidelity

The current solver witness repeats one invoice ID. Generate distinct invoices for a split-payment example, honor the policy's vendor/category restrictions, and state exactly which fields are modeled. Replaying the witness should demonstrate the intended correlation failure rather than fail earlier for a duplicate invoice or disallowed vendor. “No counterexample within the bound” must remain scoped to the tested model.

### Concurrent refunds — simulator admission implemented

The simulator now performs cumulative checks, refund insertion and audit recording in one shared-database write transaction. Support admission also records the case transition and durable synthetic receipt job atomically. Accepted simulator refunds count toward conservation even before the receipt. A future real provider adapter still needs explicit reservations and uncertain-outcome recovery:

```text
completed refunds + active refund reservations + proposed amount <= verified capture
```

Idempotency conflicts must reject changed request contents. Test separate service instances against one database with simultaneous requests. Process-local locks alone cannot establish this property.

### Approval and execution atomicity

Validate and consume an approval in the same transaction as admission and creation of execution work. Protect exact action binding and distinct reviewer requirements. Recover cleanly if a process stops between authorization, provider dispatch, and result persistence. The existing simulator approval endpoints remain demo-only until trusted identity is implemented.

### Provider event reconciliation

Handle failed attempts followed by a successful capture on the same order, repeated events, late confirmations, and disagreement between browser and webhook paths. Do not assume a failed attempt proves the entire order can never be paid. Provider-order creation also needs a durable execution claim to prevent concurrent duplicate dispatch.

### Safe rendering and evidence claims

Replace unescaped API/model string interpolation into `innerHTML` with text nodes or explicit escaping. Test representative untrusted values at the rendering boundary.

An evidence hash commits to recorded bytes; it is not a proof of real-world truth or operator authorization. A checkpoint stored beside the chain can also be rewritten by a sufficiently privileged attacker. Explain this boundary and reserve external signing/notarization for later work.

## 9. Minimal interface

Use a restrained layout with four primary views:

1. **Requests:** status, age, amount, owner, and next action.
2. **Request detail:** original request, verified evidence, proposed action, policy result, and progress.
3. **Approvals:** reason, amount, affected payment, expiry, and approve/reject controls.
4. **Activity and evidence:** execution status, updates, and export links.

Keep Policy Studio and technical labs under an advanced section. Show plain-language reasons such as “INR 600 remains refundable” before internal reason codes. Display provider/compiler mode accurately, and make pending and unknown states visible.

Replace previous-event hero/footer and submission labels with the intended Paytm event and track wording. Keep factual Razorpay adapter labels wherever that adapter is used. Do not imply endorsement or a Paytm connection from branding alone.

## 10. Provider and API plan

Keep the offline simulator as the first implementation target. Introduce or extend provider contracts for payment lookup, refund submission, refund lookup, and event verification. Add Paytm only after checking the applicable product documentation, credentials, supported operations, and organiser requirements. Checkout collection APIs must not be described as arbitrary payouts or bank-transfer reversals.

Proposed support API additions:

- `POST /api/v1/support/requests` — create a scoped request.
- `GET /api/v1/support/requests/{id}` — retrieve status and evidence.
- `POST /api/v1/support/requests/{id}/investigate` — start a bounded investigation.
- `POST /api/v1/support/requests/{id}/messages` — supply missing information.
- `POST /api/v1/support/requests/{id}/takeover` and `/resume` — manage ownership.

Reuse existing policy, approval, execution, and evidence services behind these endpoints after strengthening their boundaries. Do not duplicate financial rules inside UI code. Existing low-level commit/release and administration paths must be restricted before any public or trusted integration is enabled.

## 11. Build milestones and acceptance

| Milestone             | Work                                                                           | Exit evidence                                                                               |
| --------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| 1. Correctness        | Witness fidelity, atomic refund admission, approval transition, safe rendering | Regression tests prove the specific failure cases are handled                               |
| 2. Request foundation | Support records, synthetic fixtures, evidence tools, minimal inbox             | One request retrieves scoped capture/refund evidence and records a proposal                 |
| 3. AI investigation   | Bounded tool loop, missing-information handling, grounded explanation          | Real-model runs handle paraphrases and ambiguity without bypassing the guard                |
| 4. Completion loop    | Approval, execution job, reconciliation, customer update, takeover             | Allow/approval/deny paths complete correctly; restarts and retries do not duplicate refunds |
| 5. Paytm presentation | Event copy, support-first demo, refreshed supporting documents                 | Reproducible demo and accurately labelled evidence package                                  |
| 6. Submission         | Confirm rules, capture results, prepare video and links                        | All official fields completed with eligible, verifiable claims                              |

Complete these within ArthaNiyam. Avoid optional channels and extra financial domains until the request-to-outcome loop works.

## 12. Evaluation

Retain the existing tests and add fixtures with expected actions, prohibited actions, and terminal or waiting states. Cover:

- Eligible refund, threshold exception, cumulative over-refund, and repeated complaint.
- Missing capture, mismatched amount, ambiguous identity, and altered evidence after approval.
- Simultaneous refunds through independent instances and repeated execution jobs.
- Provider timeout after acceptance, restart, duplicate event, and failed-attempt/late-capture ordering.
- Human takeover, rejected/expired approval, and prompt injection in request text.
- Safe rendering of model/provider text and isolation of request scope.

Run deterministic service tests separately from model evaluations and provider tests. Measure correct outcomes, unsafe executions, duplicate executions, benign requests incorrectly blocked, human interventions, model/tool latency, provider waiting time, and cost per evaluated request.

The existing 71-test result and synthetic scorecard are baseline evidence. Recalculate after changes. A scorecard marked ready describes its checks; it is not production certification. Do not claim universal safety, autonomous completion rates, or real savings without a measured evaluation supporting them.

## 13. Target judge demonstration

1. Submit a merchant request to refund INR 400 from a verified INR 1,000 capture that already has INR 400 refunded.
2. Show the teammate retrieve the records and calculate INR 600 remaining. The guard permits the eligible INR 400 request under the demo policy.
3. Execute once through the simulator, visibly wait for confirmation, and publish the verified outcome. INR 200 remains refundable.
4. Replay the original request: the stored action is reused. Then propose a distinct INR 400 refund: the cumulative guard denies it.
5. On a separate verified capture, demonstrate a refund above the configured automatic limit. Obtain an approval for that exact action, revalidate, execute, and confirm.
6. Open the evidence timeline and export. Optionally show the existing split-payment solver example to explain the technical foundation.

Keep this sequence reproducible from synthetic fixtures. Label approvals, provider events, and any deterministic AI fallback. The current 90-second guided demo remains the earlier technical flow until this milestone is implemented.

## 14. Submission and growth path

Update `docs/submission.md`, `docs/judge-demo.md`, `docs/judge-qa.md`, `docs/architecture.md`, and `docs/submission-checklist.md` after the workflow exists. These files and parts of the UI currently describe the earlier Razorpay/Open Innovation package. Correct claims about AI explanations, provider coverage, evidence integrity, and concurrency scope as part of that update.

Submission deliverables: runnable repository, accurate setup, architecture, fresh test/evaluation results, short demo video, screenshots, team details, and the links required by the official form. Confirm repository visibility and deployed/CI status through actual runs; a configuration file alone does not prove success.

The potential next deployment is merchant support teams with repeat refund investigations. Validate the frequency, handling time, data access, and approval policies with real users before asserting impact. A larger deployment would need tenant authentication, trusted approvals, transactional PostgreSQL storage, provider reconciliation, observability, and externally anchored audit checkpoints. Keep these as measured future work rather than current scalability guarantees.
