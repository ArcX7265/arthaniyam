from datetime import datetime, timedelta, timezone

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


def policy(*, maximum_depth: int = 3) -> dict:
    return {
        "policy_id": "delegation-demo",
        "version": 1,
        "name": "Delegation Demo",
        "currency": "INR",
        "budget": {"monthly_limit": 5_000_000, "per_transaction_limit": 1_000_000},
        "approval": {"required_above": 5_000_000, "approver_count": 1},
        "delegation": {
            "enabled": True,
            "conserve_authority": True,
            "maximum_depth": maximum_depth,
        },
    }


def grant(grant_id: str, parent: str, child: str, amount: int) -> dict:
    return {
        "grant_id": grant_id,
        "parent_agent_id": parent,
        "child_agent_id": child,
        "authority_limit": amount,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }


def delegate(policy_payload: dict, grant_payload: dict):
    return client.post(
        "/api/v1/demo/delegations/evaluate",
        json={"policy": policy_payload, "grant": grant_payload},
    )


def test_sibling_grants_cannot_multiply_parent_authority() -> None:
    first = delegate(policy(), grant("grant-001", "root-agent", "buyer-a", 3_000_000))
    second = delegate(policy(), grant("grant-002", "root-agent", "buyer-b", 3_000_000))

    assert first.status_code == 200
    assert first.json()["arthaniyam"]["decision"] == "allow"
    assert second.json()["naive_gateway"]["decision"] == "allow"
    assert second.json()["arthaniyam"]["decision"] == "deny"
    assert "DELEGATED_AUTHORITY_MULTIPLICATION" in second.json()["arthaniyam"][
        "reason_codes"
    ]
    assert second.json()["delegated_total"] == 6_000_000
    assert second.json()["remaining_authority"] == 2_000_000


def test_delegation_cycle_is_rejected() -> None:
    delegate(policy(), grant("grant-001", "root-agent", "buyer-a", 2_000_000))
    result = delegate(
        policy(), grant("grant-002", "buyer-a", "root-agent", 1_000_000)
    ).json()

    assert result["arthaniyam"]["decision"] == "deny"
    assert "DELEGATION_CYCLE" in result["arthaniyam"]["reason_codes"]


def test_maximum_delegation_depth_is_enforced() -> None:
    limited = policy(maximum_depth=1)
    delegate(limited, grant("grant-001", "root-agent", "buyer-a", 2_000_000))
    result = delegate(
        limited, grant("grant-002", "buyer-a", "sub-agent", 1_000_000)
    ).json()

    assert result["arthaniyam"]["decision"] == "deny"
    assert "DELEGATION_DEPTH_EXCEEDED" in result["arthaniyam"]["reason_codes"]


def test_child_spending_cannot_exceed_delegated_limit() -> None:
    delegate(policy(), grant("grant-001", "root-agent", "buyer-a", 1_000_000))

    def action(action_id: str, amount: int, purpose: str) -> dict:
        return {
            "action_id": action_id,
            "agent_id": "buyer-a",
            "amount": amount,
            "vendor_id": "vendor-001",
            "category": "hardware",
            "purpose": purpose,
            "invoice_id": f"invoice-{action_id}",
            "approval_ids": [],
        }

    first = client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy(), "action": action("payment-001", 900_000, "laptops")},
    ).json()
    second = client.post(
        "/api/v1/runtime/evaluate",
        json={"policy": policy(), "action": action("payment-002", 200_000, "chairs")},
    ).json()

    assert first["arthaniyam"]["decision"] == "allow_and_reserve"
    assert second["arthaniyam"]["decision"] == "deny"
    assert "DELEGATED_AUTHORITY_EXCEEDED" in second["arthaniyam"]["reason_codes"]
