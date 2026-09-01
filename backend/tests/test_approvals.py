import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runtime.guard import runtime_guard


client = TestClient(app)


def teardown_module() -> None:
    client.close()


@pytest.fixture(autouse=True)
def reset_runtime() -> None:
    runtime_guard.reset()


def policy() -> dict:
    return {
        "policy_id": "approval-demo",
        "version": 1,
        "name": "Approval Demo",
        "currency": "INR",
        "budget": {"monthly_limit": 5_000_000, "per_transaction_limit": 1_000_000},
        "approval": {"required_above": 1_000_000, "approver_count": 1},
        "correlation": {"window_hours": 24, "group_by": ["vendor", "purpose"]},
    }


def action(action_id: str, *, vendor: str = "vendor-001", approvals=None) -> dict:
    return {
        "action_id": action_id,
        "agent_id": "procurement-agent",
        "amount": 900_000,
        "vendor_id": vendor,
        "category": "hardware",
        "purpose": "office-laptops",
        "invoice_id": f"invoice-{action_id}",
        "approval_ids": approvals or [],
    }


def evaluate(action_payload: dict):
    return client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy(), "action": action_payload},
    )


def approved_grant_for_second_action() -> str:
    evaluate(action("payment-001"))
    assert evaluate(action("payment-002")).json()["arthaniyam"]["decision"] == "require_approval"
    challenge = client.post(
        "/api/v1/demo/approvals/challenges",
        json={"policy_id": "approval-demo", "action_id": "payment-002"},
    ).json()
    approved = client.post(
        f"/api/v1/demo/approvals/challenges/{challenge['challenge_id']}/decide",
        json={"approver_id": "finance-controller", "decision": "approve"},
    ).json()
    return approved["grants"][0]["approval_id"]


def test_approval_is_bound_to_exact_action_and_consumed_once() -> None:
    approval_id = approved_grant_for_second_action()

    accepted = evaluate(action("payment-002", approvals=[approval_id])).json()
    attempted_reuse = evaluate(
        action("payment-003", approvals=[approval_id])
    ).json()

    assert accepted["arthaniyam"]["decision"] == "allow_and_reserve"
    assert "REQUIRED_APPROVAL_PRESENT" in accepted["arthaniyam"]["reason_codes"]
    assert attempted_reuse["arthaniyam"]["decision"] == "require_approval"
    assert "APPROVAL_INVALID_OR_EXPIRED" in attempted_reuse["arthaniyam"]["reason_codes"]


def test_same_approver_decision_is_idempotent() -> None:
    evaluate(action("payment-001"))
    evaluate(action("payment-002"))
    challenge = client.post(
        "/api/v1/demo/approvals/challenges",
        json={"policy_id": "approval-demo", "action_id": "payment-002"},
    ).json()
    request = {"approver_id": "finance-controller", "decision": "approve"}

    first = client.post(
        f"/api/v1/demo/approvals/challenges/{challenge['challenge_id']}/decide",
        json=request,
    ).json()
    second = client.post(
        f"/api/v1/demo/approvals/challenges/{challenge['challenge_id']}/decide",
        json=request,
    ).json()

    assert first["grants"] == second["grants"]
    assert len(second["grants"]) == 1
