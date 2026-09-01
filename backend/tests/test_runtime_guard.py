from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runtime.guard import runtime_guard


client = TestClient(app)


def teardown_module() -> None:
    client.close()


@pytest.fixture(autouse=True)
def reset_runtime_guard() -> None:
    runtime_guard.reset()


def policy(
    *,
    monthly_limit: int = 5_000_000,
    per_transaction_limit: int = 1_000_000,
    approval_threshold: int = 1_000_000,
) -> dict:
    return {
        "policy_id": "runtime-demo",
        "version": 1,
        "name": "Runtime Procurement Demo",
        "currency": "INR",
        "budget": {
            "monthly_limit": monthly_limit,
            "per_transaction_limit": per_transaction_limit,
        },
        "approval": {
            "required_above": approval_threshold,
            "approver_count": 1,
        },
        "vendors": {
            "require_approved_vendor": True,
            "allowed_vendor_ids": ["vendor-001"],
            "allowed_categories": ["hardware"],
        },
        "correlation": {
            "window_hours": 24,
            "group_by": ["vendor", "purpose"],
        },
    }


def action(
    action_id: str,
    *,
    amount: int = 900_000,
    invoice_id: str | None = None,
    purpose: str = "office-laptops",
    approval_ids: list[str] | None = None,
) -> dict:
    return {
        "action_id": action_id,
        "agent_id": "procurement-agent",
        "amount": amount,
        "vendor_id": "vendor-001",
        "category": "hardware",
        "purpose": purpose,
        "invoice_id": invoice_id or f"invoice-{action_id}",
        "approval_ids": approval_ids or [],
    }


def evaluate(policy_payload: dict, action_payload: dict):
    return client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy_payload, "action": action_payload},
    )


def test_related_payments_bypass_naive_gateway_but_not_runtime_guard() -> None:
    first = evaluate(policy(), action("payment-001"))
    second = evaluate(policy(), action("payment-002"))

    assert first.status_code == 200
    assert first.json()["naive_gateway"]["decision"] == "allow"
    assert first.json()["arthaniyam"]["decision"] == "allow_and_reserve"

    assert second.status_code == 200
    result = second.json()
    assert result["naive_gateway"]["decision"] == "allow"
    assert result["arthaniyam"]["decision"] == "require_approval"
    assert result["correlated_amount"] == 1_800_000
    assert "CORRELATED_APPROVAL_THRESHOLD_EXCEEDED" in result["arthaniyam"][
        "reason_codes"
    ]
    assert result["state"]["reserved_amount"] == 900_000


def test_idempotent_replay_does_not_reserve_twice() -> None:
    payload = action("payment-001")

    first = evaluate(policy(), payload).json()
    second = evaluate(policy(), payload).json()

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["state"]["reserved_amount"] == 900_000
    assert second["state"]["active_reservations"] == 1


def test_reusing_action_id_with_different_contents_is_denied() -> None:
    original = action("payment-001")
    changed = deepcopy(original)
    changed["amount"] = 800_000

    evaluate(policy(), original)
    conflict = evaluate(policy(), changed).json()

    assert conflict["arthaniyam"]["decision"] == "deny"
    assert "IDEMPOTENCY_KEY_CONFLICT" in conflict["arthaniyam"]["reason_codes"]
    assert conflict["state"]["reserved_amount"] == 900_000


def test_committed_invoice_cannot_be_paid_again() -> None:
    evaluate(policy(), action("payment-001", invoice_id="invoice-shared"))
    commit = client.post(
        "/api/v1/runtime/actions/payment-001/commit",
        json={"policy_id": "runtime-demo", "policy_version": 1},
    )
    duplicate = evaluate(
        policy(),
        action(
            "payment-002",
            invoice_id="invoice-shared",
            purpose="unrelated-purpose",
        ),
    ).json()

    assert commit.status_code == 200
    assert commit.json()["status"] == "committed"
    assert duplicate["naive_gateway"]["decision"] == "allow"
    assert duplicate["arthaniyam"]["decision"] == "deny"
    assert "DUPLICATE_INVOICE" in duplicate["arthaniyam"]["reason_codes"]


def test_active_reservations_are_counted_against_budget() -> None:
    limited_policy = policy(
        monthly_limit=1_500_000,
        per_transaction_limit=1_000_000,
        approval_threshold=1_500_000,
    )
    evaluate(limited_policy, action("payment-001", amount=900_000))
    second = evaluate(
        limited_policy,
        action("payment-002", amount=700_000, purpose="office-chairs"),
    ).json()

    assert second["naive_gateway"]["decision"] == "allow"
    assert second["arthaniyam"]["decision"] == "deny"
    assert "BUDGET_EXCEEDED" in second["arthaniyam"]["reason_codes"]


def test_required_approval_allows_related_payment_and_reserves_it() -> None:
    evaluate(policy(), action("payment-001"))
    evaluate(policy(), action("payment-002"))
    challenge = client.post(
        "/api/v1/demo/approvals/challenges",
        json={
            "policy_id": "runtime-demo",
            "policy_version": 1,
            "action_id": "payment-002",
        },
    ).json()
    decision = client.post(
        f"/api/v1/demo/approvals/challenges/{challenge['challenge_id']}/decide",
        json={"approver_id": "finance-controller", "decision": "approve"},
    ).json()
    approved = evaluate(
        policy(),
        action(
            "payment-002",
            approval_ids=[decision["grants"][0]["approval_id"]],
        ),
    ).json()

    assert approved["arthaniyam"]["decision"] == "allow_and_reserve"
    assert "REQUIRED_APPROVAL_PRESENT" in approved["arthaniyam"]["reason_codes"]
    assert approved["state"]["reserved_amount"] == 1_800_000


def test_arbitrary_approval_string_cannot_bypass_review() -> None:
    evaluate(policy(), action("payment-001"))
    result = evaluate(
        policy(),
        action("payment-002", approval_ids=["approval-made-up"]),
    ).json()

    assert result["arthaniyam"]["decision"] == "require_approval"
    assert "APPROVAL_INVALID_OR_EXPIRED" in result["arthaniyam"]["reason_codes"]


def test_releasing_reservation_returns_budget() -> None:
    evaluate(policy(), action("payment-001"))
    released = client.post(
        "/api/v1/runtime/actions/payment-001/release",
        json={"policy_id": "runtime-demo", "policy_version": 1},
    )

    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert released.json()["state"]["reserved_amount"] == 0
    assert released.json()["state"]["available_budget"] == 5_000_000


def test_runtime_state_exposes_actions_and_audit_trail() -> None:
    evaluate(policy(), action("payment-001"))

    response = client.get("/api/v1/runtime/policies/runtime-demo/state?version=1")

    assert response.status_code == 200
    state = response.json()
    assert state["actions"][0]["status"] == "reserved"
    assert state["audit_trail"][0]["event_type"] == "evaluation"
