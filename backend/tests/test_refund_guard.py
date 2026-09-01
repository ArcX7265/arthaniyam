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
        "policy_id": "refund-demo",
        "version": 1,
        "name": "Refund Demo",
        "currency": "INR",
        "budget": {"monthly_limit": 5_000_000, "per_transaction_limit": 1_000_000},
        "approval": {"required_above": 5_000_000, "approver_count": 1},
    }


def action() -> dict:
    return {
        "action_id": "captured-payment-001",
        "agent_id": "procurement-agent",
        "amount": 900_000,
        "vendor_id": "vendor-001",
        "category": "hardware",
        "purpose": "office-laptops",
        "invoice_id": "invoice-captured-001",
        "approval_ids": [],
    }


def capture_payment() -> None:
    client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy(), "action": action()},
    )
    client.post(
        "/api/v1/executions/orders",
        json={"policy_id": "refund-demo", "action_id": "captured-payment-001"},
    )
    confirmed = client.post(
        "/api/v1/executions/confirm",
        json={
            "policy_id": "refund-demo",
            "action_id": "captured-payment-001",
            "simulated_outcome": "success",
        },
    )
    assert confirmed.json()["status"] == "verified_and_committed"


def refund(refund_id: str, amount: int):
    return client.post(
        "/api/v1/demo/refunds/evaluate",
        json={
            "policy_id": "refund-demo",
            "action_id": "captured-payment-001",
            "refund_id": refund_id,
            "amount": amount,
            "reason": "customer return",
        },
    )


def test_cumulative_refunds_cannot_exceed_captured_payment() -> None:
    capture_payment()

    first = refund("refund-001", 540_000).json()
    second = refund("refund-002", 540_000).json()

    assert first["status"] == "executed"
    assert first["remaining_refundable"] == 360_000
    assert second["naive_gateway"]["decision"] == "allow"
    assert second["arthaniyam"]["decision"] == "deny"
    assert "CUMULATIVE_REFUND_EXCEEDS_CAPTURE" in second["arthaniyam"][
        "reason_codes"
    ]
    assert second["refunded_before"] == 540_000
    assert second["refunded_after"] == 540_000
    assert second["remaining_refundable"] == 360_000


def test_refund_idempotency_does_not_refund_twice() -> None:
    capture_payment()

    first = refund("refund-001", 400_000).json()
    replay = refund("refund-001", 400_000).json()
    conflict = refund("refund-001", 300_000)

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert replay["refunded_after"] == 400_000
    assert conflict.status_code == 409


def test_uncaptured_action_cannot_be_refunded() -> None:
    client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy(), "action": action()},
    )

    response = refund("refund-001", 100_000)

    assert response.status_code == 404
    assert "captured payment" in response.json()["detail"]
