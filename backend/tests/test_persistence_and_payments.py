import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.payments.gateway import (
    PaymentGatewayError,
    RazorpayTestGateway,
    SimulatedRazorpayGateway,
)
from app.runtime.guard import RuntimeGuard, runtime_guard
from app.runtime.models import RuntimeEvaluationRequest
from app.runtime.storage import SQLiteRuntimeRepository


client = TestClient(app)


def teardown_module() -> None:
    client.close()


@pytest.fixture(autouse=True)
def reset_default_runtime() -> None:
    runtime_guard.reset()


def policy_payload(policy_id: str = "persistent-demo") -> dict:
    return {
        "policy_id": policy_id,
        "version": 1,
        "name": "Persistent Demo",
        "currency": "INR",
        "budget": {
            "monthly_limit": 5_000_000,
            "per_transaction_limit": 1_000_000,
        },
        "approval": {"required_above": 1_000_000, "approver_count": 1},
        "correlation": {
            "window_hours": 24,
            "group_by": ["vendor", "purpose"],
        },
    }


def action_payload(action_id: str = "persistent-payment-001") -> dict:
    return {
        "action_id": action_id,
        "agent_id": "procurement-agent",
        "amount": 900_000,
        "vendor_id": "vendor-001",
        "category": "hardware",
        "purpose": "office-laptops",
        "invoice_id": f"invoice-{action_id}",
        "approval_ids": [],
    }


def test_runtime_state_survives_guard_reconstruction() -> None:
    database = Path(os.environ["ARTHANIYAM_DATABASE_PATH"]).with_name(
        f"runtime-reconstruction-{uuid4()}.sqlite3"
    )
    first_guard = RuntimeGuard(SQLiteRuntimeRepository(database))
    request = RuntimeEvaluationRequest.model_validate(
        {"policy": policy_payload(), "action": action_payload()}
    )

    first_guard.evaluate(request)
    reconstructed_guard = RuntimeGuard(SQLiteRuntimeRepository(database))
    state = reconstructed_guard.state("persistent-demo", 1)

    assert state.state.reserved_amount == 900_000
    assert state.actions[0]["action_id"] == "persistent-payment-001"
    assert state.audit_trail[0].event_type == "evaluation"


def test_simulated_order_creation_is_idempotent() -> None:
    evaluate = client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy_payload(), "action": action_payload()},
    )
    request = {
        "policy_id": "persistent-demo",
        "policy_version": 1,
        "action_id": "persistent-payment-001",
    }

    first = client.post("/api/v1/executions/orders", json=request)
    second = client.post("/api/v1/executions/orders", json=request)

    assert evaluate.status_code == 200
    assert first.status_code == 200
    assert first.json()["order"]["provider"] == "simulator"
    assert first.json()["order"]["amount"] == 900_000
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert second.json()["order"]["order_id"] == first.json()["order"]["order_id"]


def test_order_execution_requires_active_reservation() -> None:
    client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy_payload(), "action": action_payload()},
    )
    client.post(
        "/api/v1/runtime/actions/persistent-payment-001/release",
        json={"policy_id": "persistent-demo", "policy_version": 1},
    )

    response = client.post(
        "/api/v1/executions/orders",
        json={
            "policy_id": "persistent-demo",
            "policy_version": 1,
            "action_id": "persistent-payment-001",
        },
    )

    assert response.status_code == 409
    assert "active reservation" in response.json()["detail"]


def test_order_creation_appears_in_audit_trail() -> None:
    client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy_payload(), "action": action_payload()},
    )
    client.post(
        "/api/v1/executions/orders",
        json={
            "policy_id": "persistent-demo",
            "policy_version": 1,
            "action_id": "persistent-payment-001",
        },
    )

    state = client.get(
        "/api/v1/runtime/policies/persistent-demo/state?version=1"
    ).json()

    assert state["audit_trail"][-1]["event_type"] == "order_created"
    assert "PROVIDER_SIMULATOR" in state["audit_trail"][-1]["reason_codes"]


def test_simulator_returns_deterministic_order() -> None:
    action = RuntimeEvaluationRequest.model_validate(
        {"policy": policy_payload(), "action": action_payload()}
    ).action
    gateway = SimulatedRazorpayGateway()

    assert gateway.create_order(action) == gateway.create_order(action)


def test_live_razorpay_keys_are_rejected() -> None:
    with pytest.raises(PaymentGatewayError, match="Test Mode"):
        RazorpayTestGateway("rzp_live_forbidden", "secret")
