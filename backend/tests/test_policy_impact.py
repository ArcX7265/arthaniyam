from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def policy(version: int, threshold: int) -> dict:
    return {
        "policy_id": "rollout-policy",
        "version": version,
        "name": f"Rollout policy v{version}",
        "currency": "INR",
        "budget": {
            "monthly_limit": 5_000_000,
            "per_transaction_limit": 1_500_000,
        },
        "approval": {"required_above": threshold, "approver_count": 1},
        "vendors": {
            "require_approved_vendor": True,
            "allowed_vendor_ids": ["vector-systems"],
            "allowed_categories": ["hardware"],
        },
        "correlation": {"window_hours": 24, "group_by": ["vendor", "purpose"]},
    }


def action(sequence: int, amount: int, purpose: str) -> dict:
    return {
        "action_id": f"history-{sequence}",
        "agent_id": "procurement-agent",
        "amount": amount,
        "vendor_id": "vector-systems",
        "category": "hardware",
        "purpose": purpose,
        "invoice_id": f"history-invoice-{sequence}",
        "approval_ids": [],
    }


def request_body() -> dict:
    return {
        "current_policy": policy(1, 1_000_000),
        "candidate_policy": policy(2, 800_000),
        "actions": [
            action(1, 900_000, "laptops"),
            action(2, 700_000, "chairs"),
            action(3, 850_000, "monitors"),
        ],
    }


def test_shadow_mode_reports_policy_rollout_impact() -> None:
    response = client.post("/api/v1/policies/impact/simulate", json=request_body())

    assert response.status_code == 200
    report = response.json()
    assert report["total_actions"] == 3
    assert report["unchanged_actions"] == 1
    assert report["escalated_actions"] == 2
    assert report["relaxed_actions"] == 0
    assert report["new_reviews"] == 2
    assert report["new_denials"] == 0
    assert [impact["transition"] for impact in report["impacts"]] == [
        "allow_to_review",
        "allow_to_allow",
        "allow_to_review",
    ]
    assert report["current_final_state"]["reserved_amount"] == 2_450_000
    assert report["candidate_final_state"]["reserved_amount"] == 700_000
    assert report["evidence_hash"].startswith("sha256:")

    stored = client.get(f'/api/v1/policies/impact/{report["simulation_id"]}')
    assert stored.status_code == 200
    assert stored.json() == report


def test_shadow_mode_is_deterministic_for_same_input() -> None:
    first = client.post("/api/v1/policies/impact/simulate", json=request_body()).json()
    second = client.post("/api/v1/policies/impact/simulate", json=request_body()).json()

    assert first["simulation_id"] != second["simulation_id"]
    assert first["evidence_hash"] == second["evidence_hash"]


def test_shadow_mode_requires_a_bounded_action_stream() -> None:
    body = request_body()
    body["actions"] = []
    empty = client.post("/api/v1/policies/impact/simulate", json=body)

    body["actions"] = [action(index, 100, f"purpose-{index}") for index in range(201)]
    oversized = client.post("/api/v1/policies/impact/simulate", json=body)

    assert empty.status_code == 422
    assert oversized.status_code == 422


def test_unknown_policy_impact_returns_not_found() -> None:
    response = client.get("/api/v1/policies/impact/not-a-simulation")

    assert response.status_code == 404
