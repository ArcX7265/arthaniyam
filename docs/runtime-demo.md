# Runtime Guard Demo

Start the API and open `http://127.0.0.1:8000/docs`. Use
`POST /api/v1/runtime/evaluate` twice with the policy below.

The first action uses `payment-001` and `invoice-001`. The second uses
`payment-002` and `invoice-002`. Keep the vendor and purpose unchanged.

```json
{
  "policy": {
    "policy_id": "runtime-demo",
    "version": 1,
    "name": "Runtime Procurement Demo",
    "currency": "INR",
    "budget": {
      "monthly_limit": 5000000,
      "per_transaction_limit": 1000000
    },
    "approval": {
      "required_above": 1000000,
      "approver_count": 1
    },
    "vendors": {
      "require_approved_vendor": true,
      "allowed_vendor_ids": ["vendor-001"],
      "allowed_categories": ["hardware"]
    },
    "correlation": {
      "window_hours": 24,
      "group_by": ["vendor", "purpose"]
    }
  },
  "action": {
    "action_id": "payment-001",
    "agent_id": "procurement-agent",
    "amount": 900000,
    "vendor_id": "vendor-001",
    "category": "hardware",
    "purpose": "office-laptops",
    "invoice_id": "invoice-001",
    "approval_ids": []
  }
}
```

The naive gateway allows both INR 9,000 requests. ArthaNiyam reserves the
first and requires approval for the second because the correlated total is
INR 18,000.
