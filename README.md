# ArthaNiyam

**An autonomous financial-control teammate for merchant support.**

ArthaNiyam checks financial actions against shared payment history and merchant policy. It identifies unsafe combinations of otherwise valid requests, requires human approval where necessary, and records evidence for each decision.

The Paytm hackathon direction extends this working policy engine into a teammate that investigates merchant refund-support requests, proposes a resolution, obtains the required authority, executes through a simulator, and follows the request to a verified outcome.

**Intended submission:** Paytm Build for India AI Hackathon, Mumbai — Autonomous AI Teammates.

> Status: the support workflow includes a **local Ollama investigator**, an optional OpenAI investigator, and a labelled offline keyword reference. It reads scoped evidence, asks for information and proposes a resolution. The operator confirms intent; deterministic checks and finance approvals retain authority. Local Llama 3.2 evaluation results are recorded below. Support payments remain simulated; the existing Razorpay Test Mode adapter remains in the technical labs. No Paytm integration or real-money refunds are enabled. ArthaNiyam does not depend on Resora.

## The problem

A support or purchasing agent can make an individually valid request that becomes unsafe when combined with earlier actions. Two INR 9,000 purchases can cross an INR 10,000 approval threshold. Two refund requests can each fit the original capture while exceeding it together. Concurrent agents can compete for the same remaining budget.

ArthaNiyam evaluates actions against shared commitments, captures, refunds, approvals, and delegated authority. The comparison gateway in its demo is an intentionally simplified stateless baseline; it is not a representation of Paytm's internal controls.

## Hackathon workflow

The teammate workflow focuses on merchant refund support:

1. A merchant submits a request such as “Check this customer's refund and process the eligible balance.”
2. The investigator asks for an exact payment and operator-entered refund amount when missing. It reads the selected order, capture, prior refunds and applicable policy through scoped tools; it cannot guess another payment.
3. It proposes a complaint classification with an evidence fingerprint. After the operator confirms that the proposal matches their intent, deterministic server checks decide whether to allow it, require finance approval, or deny it.
4. The teammate executes an allowed action, waits for a bound human approval, or escalates a denied or ambiguous request.
5. The simulator receipt worker confirms the synthetic outcome, updates the support request, and attaches the action and audit evidence. This is not real bank settlement.

The original split-payment demonstration remains available as a technical example. A customer-service workflow provides a closer fit to the supplied track description than policy inspection alone. The exact event rules, reuse eligibility, and integration requirements still need confirmation from the organisers.

## Current capabilities and remaining work

### Features to adapt from Resora

The first support slice adapts Resora's complaint intake, searchable inbox, linked order/payment/refund evidence, approval invalidation when records change, human takeover, and persistent simulated follow-ups into Python and a minimal interface. No Resora runtime or database is imported.

Implemented synthetic scenarios cover duplicate payments, cancelled orders, accepted returns/partial refunds, and checking existing support refunds. Missing evidence goes to human review. This does not yet handle delayed captures, arbitrary transaction complaints, or real provider disputes.

### Capability status

| Capability                  | Current status                                                                                                      |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Policy authoring            | Constrained offline parser; optional model-based structured extraction                                              |
| Symbolic verification       | Z3 search for bounded split-payment counterexamples                                                                 |
| Runtime enforcement         | Shared budget reservations, invoice checks, correlation, and action replay protection                               |
| Approvals                   | Expiring simulator approvals bound to policy version and action contents                                            |
| Delegation                  | Simulator authority graph with conservation checks                                                                  |
| Refunds                     | Atomic cumulative-refund admission and audit recording across shared-SQLite instances; idempotent retries          |
| Provider execution          | Offline simulator and Razorpay Test Mode adapter; live keys rejected                                                |
| Evidence                    | Persisted proof replay, audit hash chains, portable exports, and standalone verifiers                               |
| Evaluation                  | Fixed adversarial scenarios, benign controls, seeded boundary campaigns, and judge scorecards                       |
| Reproducibility             | Windows launch/verification scripts, Docker Compose, and GitHub Actions configuration                               |
| Support workflow            | Reference-mode intake, evidence, cumulative approval threshold, expiring review, takeover and durable synthetic receipts |
| AI support investigator     | Local Ollama structured tool loop and optional OpenAI Responses loop; clarification, evidence-bound proposals, timeout and handoff |
| Paytm integration           | Not implemented; depends on permitted product APIs and test access                                                  |

Policy extraction and support investigation are separate optional AI paths. The investigator can read and propose, but has no refund-execution, finance-approval or policy-editing tools. Generated rationales are labelled proposals, not authoritative payment facts. Financial authorization remains deterministic in all modes.

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

Open the [support workspace](http://127.0.0.1:8000) or [API documentation](http://127.0.0.1:8000/docs). The earlier policy dashboard and **Start 90-second demo** remain under [Technical labs](http://127.0.0.1:8000/labs).

### Try the support workflow

1. Click **Load demo payments**, then **New request**.
2. Keep **Investigate my complaint**, select **Duplicate payment · ₹1,250**, enter `1250` and “Customer was charged twice; refund the duplicate.” Review the proposal and click **Confirm proposal & run checks**. It moves to **Refund pending**, then **Resolved** after a synthetic receipt (normally 8–10 seconds).
3. Omit payment or amount to try clarification. Use **Add information** to supply the requested fields. A cancelled-order complaint for the **₹7,500** payment requires **Approve demo refund** after intent confirmation because cumulative refunds would cross ₹5,000. Both proposals and finance reviews expire after five minutes.
4. Try another refund against a fully refunded capture: the guard blocks it. Split refunds also require approval once their cumulative total crosses the threshold.
5. Use **Take over** to stop autonomous actions. Already accepted refunds still receive receipts, but automation will not close a human-owned request.

Each capture has finite funds; loading fixtures again does not erase refunds. A new demo run can use a new `ARTHANIYAM_DATABASE_PATH`. Do not delete a database containing records you want to retain.

The API automatically polls durable receipt jobs every two seconds. Jobs survive restarts. An optional standalone worker runs from `backend` with `python -m app.support.worker` and the same database environment. All receipts are synthetic, not bank-confirmed settlements. Refund-status requests can be investigated again after the underlying receipt arrives; they do not initiate another refund.

The **Guided reference workflow** preserves the earlier explicitly classified intake. To run without any model, use `-InvestigatorMode reference`; its narrow keyword recognizer is not an LLM.

### Use local Llama 3.2 (no API credits)

Keep Ollama running and confirm `ollama list` contains `llama3.2:3b`. If Ollama is stopped, run `ollama serve` in another terminal. For a fresh installation only, download the model with `ollama pull llama3.2:3b`.

Set these values in the repository-root `.env`:

```dotenv
SUPPORT_INVESTIGATOR_MODE=ollama
OLLAMA_MODEL=llama3.2:3b
POLICY_COMPILER_MODE=reference
RAZORPAY_MODE=simulate
```

Start the app with `.\scripts\start-demo.ps1 -InvestigatorMode ollama` (also the launcher's default). No OpenAI key is needed. The separate policy compiler stays in reference mode.

Complaints and scoped evidence go to `http://127.0.0.1:11434/api/chat`. The adapter uses [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs) to select one tool per turn, then validates it through the same server tool guard. It never silently switches to OpenAI or keyword mode. Ollama must have the selected model installed; the UI's configured model label does not imply a live health check.

Allow up to three minutes per investigation, especially while loading the model. The limit is six model calls, 900 output tokens per call, 120 seconds per HTTP call and 180 seconds overall. Local speed and classification quality depend on your hardware/model. If Ollama is unavailable, the case goes to review with a retry message. The loopback endpoint is for native local startup; the existing Docker Compose setup remains in reference mode and does not connect to host Ollama.

### Enable the OpenAI investigator

Set `OPENAI_API_KEY` in the repository-root `.env` (never commit it or paste it into a complaint), and optionally set `OPENAI_MODEL` to a model available to your account. The existing default is `gpt-5-mini`. Then run:

```powershell
.\scripts\start-demo.ps1 -InvestigatorMode openai
```

For direct Uvicorn startup, set `SUPPORT_INVESTIGATOR_MODE=openai` and `RAZORPAY_MODE=simulate`. The launcher defaults to `-InvestigatorMode ollama`, regardless of the `.env` investigator mode; pass an explicit mode to override it. The UI shows the configured mode and whether an OpenAI key is missing.

OpenAI mode sends the complaint, follow-up messages and selected synthetic evidence to the Responses API. Use demo data only. The [official function-calling contract](https://developers.openai.com/api/docs/guides/function-calling) informs the strict schemas and tool-output loop. Limits: six model calls, 1,800 output tokens per call, a 45-second overall timeout and ten follow-up messages per case. Request storage is disabled (`store: false`); that is not a claim of zero provider retention. Private reasoning is neither persisted nor displayed. No model errors silently fall back to reference mode.

Investigation claims expire 15 seconds after the overall timeout: 195 seconds for Ollama, 60 seconds for OpenAI/reference. After a process interruption, click **Investigate again** after that lease expires. New information or human takeover invalidates an outstanding result. Expired proposals and changed evidence must be investigated again before confirmation. The API is a local simulator without trusted user identity or tenant isolation; do not expose it publicly.

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
| HTML / CSS / JavaScript          | Minimal support workspace and separate technical labs   |

The planned teammate will call these services through scoped tools. It will not directly write ledger state, select its own permissions, or override a denial. See [implementation.md](implementation.md) for the build sequence and acceptance criteria.

## Verify the project

```powershell
.\scripts\verify-project.ps1
```

The suite has 138 tests: the original 71, 18 support cases and 49 investigator cases. Coverage includes concurrency, restart recovery, stale proposals/approvals, clarification, malformed/unauthorized tool calls, timeouts, no-fallback behavior and provider error redaction. Ollama tests cover the HTTP contract, local provider failures, operator confirmation, longer investigation leases, status routing and safeguard-bypass handoff. Model transports are mocked for safety/contract tests; these are not live-model accuracy measurements.

Run the seven-fixture investigation evaluation separately (no proposals are confirmed and no refunds are sent):

```powershell
.\.venv\Scripts\python.exe scripts/evaluate-investigator.py --mode reference
.\.venv\Scripts\python.exe scripts/evaluate-investigator.py --mode ollama --model llama3.2:3b --max-cases 7
# Explicit opt-in to paid API calls after configuring a key:
.\.venv\Scripts\python.exe scripts/evaluate-investigator.py --mode openai --max-cases 7
```

The command prints per-fixture outcomes and exits nonzero on a failed expectation. Reference-mode results test the offline recognizer only. Record actual live results, including failures, before using them in the submission.

Local evaluation on 21 September 2026: `llama3.2:3b`, Ollama 0.34.2, CPU inference, **7/7 fixtures passed**. Duplicate payment, cancellation, missing payment, missing amount and ambiguous complaint passed. Clear status-only wording is routed to an evidence-bound status proposal before inference, and safeguard-bypass wording is handed to a human before inference. Model-backed cases took about 16–18 seconds; status, missing-payment and bypass cases completed in about 0.02–0.03 seconds. No proposals were confirmed and no refunds were sent. Earlier prompt-only versions scored 5/7, 4/7 and 2/7, showing why deterministic safety and intent boundaries surround the model. These are development fixtures used during tuning, not an independent accuracy benchmark; expand evaluation before the hackathon.

Regression checkpoint: 49/49 investigator tests passed. The prior full-suite checkpoint had one existing failure because `frontend/app.js` was moved to the root and `/assets/app.js` returns 404; the investigator change does not move that file. Support JavaScript syntax validation passed.

The included fixed benchmark has seven attack scenarios and four benign controls. The guided scorecard combines those with 20 generated boundary cases. Report results with their fixture counts; synthetic measurements do not establish production fraud accuracy or customer impact.

Downloaded evidence can be checked independently:

```sh
python scripts/verify_evidence.py path/to/arthaniyam-evidence.json
python scripts/verify_scorecard.py path/to/arthaniyam-judge-scorecard.json
```

Hashes help detect changes against a trusted record. They do not independently prove that the inputs were true, that the operator was authorized, or that the service generating the record was trustworthy.

## Next delivery milestones

1. Expand the local-model evaluation with unseen paraphrases, ambiguity and malicious instructions before claiming AI quality in the submission. API credits are optional.
2. Fix solver witness fidelity and audit dynamic rendering in the legacy technical labs. The new support workspace uses text nodes for untrusted values.
3. Add trusted operator identity, merchant isolation, approval rejection/resume, customer updates and production provider reconciliation.
4. Refresh the remaining submission materials, validate model behavior and record an end-to-end demo.
5. Confirm event eligibility and any Paytm integration requirements before submission.

## Scope and documentation

The prototype is intended for synthetic local demonstrations. Authentication, tenant isolation, trusted approver identities, complete crash recovery, and production provider operations remain future work. SQLite admission tests cover shared-database runtime instances, not multi-host consensus. The solver currently covers a bounded split-payment model, not formal proofs of every runtime rule.

Existing [architecture](docs/architecture.md), [demo script](docs/judge-demo.md), [submission narrative](docs/submission.md), [judge Q&A](docs/judge-qa.md), and [checklist](docs/submission-checklist.md) describe the earlier buildathon package. Their old event narrative still needs revision. The workspace and lab event labels have been updated; remaining work is tracked in the [implementation plan](implementation.md).
