# Merchant support architecture

```mermaid
flowchart TD
    UI[Merchant support inbox] --> API[FastAPI support endpoints]
    API --> Claim[Persist case and claim investigation]
    Claim --> Gate[Deterministic preflight]
    Gate -->|Recognised status enquiry| Read[Read scoped payment snapshot]
    Gate -->|Recognised bypass wording| Human[Needs review / human takeover]
    Gate -->|Other complaints| LLM[Local Llama 3.2 via Ollama]
    LLM --> Tools[Validated read and proposal tools]
    Tools --> Read
    Read --> Proposal[Evidence-bound proposal or clarification]
    Proposal --> Confirm[Operator confirms intent]
    Confirm --> Guard[Eligibility and cumulative-refund checks]
    Guard -->|Approval required| Approval[Separate simulated finance approval]
    Approval --> Guard
    Guard -->|Admitted| Refund[Atomic simulator refund and durable job]
    Guard -->|Denied or uncertain| Human
    Refund --> Worker[Receipt worker]
    Worker --> Receipt[Confirmed synthetic receipt and case update]
    Claim <--> DB[(SQLite shared state and audit records)]
    Guard <--> DB
    Worker <--> DB
```

## Boundaries

- Customer text and model output are untrusted. Five named tools are available; none executes a refund, edits policy or grants finance approval.
- The operator selects payment and amount. Both evidence tools must run before a proposal; its fingerprint must match the scoped snapshot.
- Inference runs outside database transactions. Claims, changed evidence, follow-ups and human takeover govern acceptance of late results.
- Confirmation revalidates proposals. Server checks account for captures, prior/pending refunds and cumulative approval limits. Transactions and idempotency protect local concurrent instances.
- A receipt worker simulates confirmation. Accepted refunds remain pending until a synthetic receipt is recorded.

## Runtime

Python/FastAPI, Pydantic, SQLite, vanilla HTML/CSS/JavaScript and Ollama's loopback chat API. OpenAI is optional and explicitly selected. Earlier Z3/control-plane demonstrations remain at `/labs`; see [their architecture](architecture.md).

## Proposed scale-up

The current app is local, single-merchant and unauthenticated. Production needs authenticated tenant scope, trusted roles, a transactional production database, queued workers, provider reconciliation, observability and independent quality evaluation. These are planned components, not deployed capabilities.
