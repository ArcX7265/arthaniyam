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
