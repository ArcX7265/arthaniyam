# ArthaNiyam

**An autonomous financial-control teammate for merchant support.**

ArthaNiyam checks financial actions against shared payment history and merchant policy. It identifies unsafe combinations of otherwise valid requests, requires human approval where necessary, and records evidence for each decision.

The Paytm hackathon direction extends this working policy engine into a teammate that investigates merchant refund-support requests, proposes a resolution, obtains the required authority, executes through a simulator, and follows the request to a verified outcome.

**Intended submission:** Paytm Build for India AI Hackathon, Mumbai — Autonomous AI Teammates.

> Status: the policy engine and technical demo work today. The autonomous support workflow described below is planned. Payments use an offline simulator or the existing Razorpay Test Mode adapter; there is no Paytm integration yet. ArthaNiyam is the standalone submission and does not depend on Resora.

## The problem

A support or purchasing agent can make an individually valid request that becomes unsafe when combined with earlier actions. Two INR 9,000 purchases can cross an INR 10,000 approval threshold. Two refund requests can each fit the original capture while exceeding it together. Concurrent agents can compete for the same remaining budget.

ArthaNiyam evaluates actions against shared commitments, captures, refunds, approvals, and delegated authority. The comparison gateway in its demo is an intentionally simplified stateless baseline; it is not a representation of Paytm's internal controls.

## Hackathon workflow

The first complete teammate workflow will focus on merchant refund support:

1. A merchant submits a request such as “Check this customer's refund and process the eligible balance.”
2. The AI retrieves the associated order, capture, prior refunds, and applicable policy, and asks for clarification if the match is uncertain.
3. It proposes an action with evidence references. The deterministic policy engine decides whether to allow it, require approval, or deny it.
4. The teammate executes an allowed action, waits for a bound human approval, or escalates a denied or ambiguous request.
5. It verifies provider completion, updates the support request, and attaches the action and audit evidence.

The original split-payment demonstration remains available as a technical example. A customer-service workflow provides a closer fit to the supplied track description than policy inspection alone. The exact event rules, reuse eligibility, and integration requirements still need confirmation from the organisers.

## Current capabilities and remaining work

### Features to adapt from Resora

Resora provides useful reference implementations for complaint intake, a searchable case inbox, linked order/payment/refund evidence, approval invalidation when records change, human takeover, and persistent simulated follow-ups. Adapt these behaviors into ArthaNiyam's backend and minimal interface; they are not yet ported.

Start with duplicate-payment complaints and tracking existing refunds. Add payment-after-cancellation and delayed duplicate-payment cases once the first workflow is complete. Keep ArthaNiyam's policy engine as the financial authorization boundary. Resora's tests and scenario fixtures can guide new Python tests, while its Next.js runtime and workspace database remain separate.

### Capability status

| Capability                  | Current status                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Policy authoring            | Constrained offline parser; optional model-based structured extraction                                              |
| Symbolic verification       | Z3 search for bounded split-payment counterexamples                                                                 |
| Runtime enforcement         | Shared budget reservations, invoice checks, correlation, and action replay protection                               |
| Approvals                   | Expiring simulator approvals bound to policy version and action contents                                            |
| Delegation                  | Simulator authority graph with conservation checks                                                                  |
| Refunds                     | Sequential cumulative-refund and replay checks in the simulator; cross-instance atomic admission still needs fixing |
| Provider execution          | Offline simulator and Razorpay Test Mode adapter; live keys rejected                                                |
| Evidence                    | Persisted proof replay, audit hash chains, portable exports, and standalone verifiers                               |
| Evaluation                  | Fixed adversarial scenarios, benign controls, seeded boundary campaigns, and judge scorecards                       |
| Reproducibility             | Windows launch/verification scripts, Docker Compose, and GitHub Actions configuration                               |
| Autonomous support teammate | Planned: intake, evidence tools, bounded model loop, durable follow-up, and verified closure                        |
| Paytm integration           | Not implemented; depends on permitted product APIs and test access                                                  |

The current AI path extracts policy fields. Generated investigations and explanations are part of the planned teammate work, not an existing capability. Financial authorization remains deterministic in both modes.

## Run locally

Requires Python 3.11+; Node.js is used by the verification script for frontend syntax checks.

From the repository root in Windows PowerShell:

```powershell
.\scripts\start-demo.ps1
```

The launcher creates a virtual environment on first use, installs dependencies, and forces the offline compiler and payment simulator. If an environment already exists and dependencies have changed, update it with:

```powershell
.\.venv\Scripts\python.exe -m pip install -e './backend[dev]'
```

Open the [dashboard](http://127.0.0.1:8000) or [API documentation](http://127.0.0.1:8000/docs). Click **Start 90-second demo** to run the existing split-payment and scorecard demonstration. It does not yet run an autonomous customer-support investigation.

Alternatively, with Docker available:

```sh
docker compose up --build
```

Compose uses a persistent SQLite volume and exposes the simulator on loopback. The local default database is `backend/arthaniyam.sqlite3`. To choose a different path, set `ARTHANIYAM_DATABASE_PATH` in the process environment before starting the server.

## Optional policy AI and provider modes

The repository-root `.env.example` documents the existing settings. For model-based policy extraction, configure a root `.env` with `POLICY_COMPILER_MODE=openai`, `OPENAI_API_KEY`, and an accessible `OPENAI_MODEL`. Keep `RAZORPAY_MODE=simulate` for the offline payment demo.

Start the API directly when using this mode, because the demo launcher overrides compiler mode:

```powershell
$env:POLICY_COMPILER_MODE = 'openai'
$env:RAZORPAY_MODE = 'simulate'
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Keep credentials out of Git. A real-model run must be validated separately before claiming it in the submission.

The optional existing payment adapter uses Razorpay test credentials. Its approvals, delegation administration, and refund-demo endpoints are disabled outside simulator mode. Preserve accurate provider labels; changing event branding does not turn this adapter into a Paytm integration.

## Architecture

| Component                        | Responsibility                                           |
| -------------------------------- | -------------------------------------------------------- |
| FastAPI / Pydantic               | Typed API, validation, policy contracts                  |
| Policy compiler                  | Extract a reviewable candidate from constrained language |
| Z3 verifier                      | Search the model for a bounded counterexample            |
| Runtime guard / SQLite WAL       | Apply rules and atomically admit spending reservations   |
| Approval and delegation services | Represent bounded human permission and agent authority   |
| Payment adapter                  | Simulator or verified test-provider execution            |
| Evidence services                | Persist, hash, export, and replay records                |
| HTML / CSS / JavaScript          | Dashboard, existing labs, and future support workspace   |

The planned teammate will call these services through scoped tools. It will not directly write ledger state, select its own permissions, or override a denial. See [implementation.md](implementation.md) for the build sequence and acceptance criteria.

## Verify the project

```powershell
.\scripts\verify-project.ps1
```

The last local verification before this documentation update passed 71 backend tests and frontend JavaScript syntax validation. That result covers the current prototype, not the planned support workflow or live integrations.

The included fixed benchmark has seven attack scenarios and four benign controls. The guided scorecard combines those with 20 generated boundary cases. Report results with their fixture counts; synthetic measurements do not establish production fraud accuracy or customer impact.

Downloaded evidence can be checked independently:

```sh
python scripts/verify_evidence.py path/to/arthaniyam-evidence.json
python scripts/verify_scorecard.py path/to/arthaniyam-judge-scorecard.json
```

Hashes help detect changes against a trusted record. They do not independently prove that the inputs were true, that the operator was authorized, or that the service generating the record was trustworthy.

## Next delivery milestones

1. Fix counterexample fidelity, concurrent refund admission, and unsafe dynamic HTML rendering; add regression coverage.
2. Add the merchant support request model, evidence tools, and bounded AI investigation loop.
3. Connect human approval, one-time execution, durable follow-up, and verified closure into one workflow.
4. Present a minimal inbox, request detail, approval queue, and evidence view; retain technical labs under an advanced section.
5. Adapt the UI and submission materials for the intended event, validate actual model behavior, and record an end-to-end demo.
6. Confirm event eligibility and any Paytm integration requirements before submission.

## Scope and documentation

The prototype is intended for synthetic local demonstrations. Authentication, tenant isolation, trusted approver identities, complete crash recovery, and production provider operations remain future work. SQLite admission tests cover shared-database runtime instances, not multi-host consensus. The solver currently covers a bounded split-payment model, not formal proofs of every runtime rule.

Existing [architecture](docs/architecture.md), [demo script](docs/judge-demo.md), [submission narrative](docs/submission.md), [judge Q&A](docs/judge-qa.md), and [checklist](docs/submission-checklist.md) describe the earlier buildathon package. Some wording and UI branding still reference that event; revising them is an explicit milestone in the new [implementation plan](implementation.md).
