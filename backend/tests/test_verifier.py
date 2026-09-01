from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def teardown_module() -> None:
    client.close()


def demo_policy(*, threshold: int = 1_000_000, monthly_limit: int = 5_000_000) -> dict:
    return {
        "policy_id": "procurement-demo",
        "version": 1,
        "name": "Procurement Demo",
        "currency": "INR",
        "budget": {
            "monthly_limit": monthly_limit,
            "per_transaction_limit": threshold,
        },
        "approval": {"required_above": threshold, "approver_count": 1},
        "correlation": {
            "window_hours": 24,
            "group_by": ["vendor", "purpose", "invoice"],
        },
    }


def test_verifier_finds_split_payment_that_naive_gateway_allows() -> None:
    response = client.post(
        "/api/v1/policies/verify",
        json={"policy": demo_policy(), "max_actions": 4},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "counterexample_found"
    assert result["counterexample"]["attack"] == "correlated_split_payment"
    assert len(result["counterexample"]["actions"]) == 2
    assert all(
        action["naive_gateway_decision"] == "allow"
        for action in result["counterexample"]["actions"]
    )
    assert result["counterexample"]["arthaniyam_decision"] == "require_approval"
    assert len(
        {action["invoice_id"] for action in result["counterexample"]["actions"]}
    ) == 1
    assert result["counterexample"]["correlated_total"] > 1_000_000
    assert all(
        action["amount"] <= 1_000_000
        for action in result["counterexample"]["actions"]
    )


def test_verifier_is_honest_when_budget_cannot_cross_threshold() -> None:
    response = client.post(
        "/api/v1/policies/verify",
        json={
            "policy": demo_policy(threshold=1_000_000, monthly_limit=1_000_000),
            "max_actions": 4,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "no_counterexample_within_bound"
    assert result["counterexample"] is None
    assert "not an unbounded proof" in result["limitation"]


def test_verification_run_id_is_replayable() -> None:
    payload = {"policy": demo_policy(), "max_actions": 4}

    first = client.post("/api/v1/policies/verify", json=payload).json()
    second = client.post("/api/v1/policies/verify", json=payload).json()

    assert first["proof_run_id"] == second["proof_run_id"]
    assert first["evidence_hash"].startswith("sha256:")


def test_persisted_proof_can_be_retrieved_and_replayed() -> None:
    verified = client.post(
        "/api/v1/policies/verify",
        json={"policy": demo_policy(), "max_actions": 4},
    ).json()

    stored = client.get(f"/api/v1/proofs/{verified['proof_run_id']}")
    listed = client.get("/api/v1/proofs?limit=10")
    replayed = client.post(
        f"/api/v1/proofs/{verified['proof_run_id']}/replay"
    )

    assert stored.status_code == 200
    assert stored.json()["evidence_hash"] == verified["evidence_hash"]
    assert verified["proof_run_id"] in {
        proof["proof_run_id"] for proof in listed.json()
    }
    assert replayed.status_code == 200
    assert replayed.json()["status"] == "verified"
    assert replayed.json()["stored_integrity_verified"] is True
    assert replayed.json()["replay_matches_original"] is True
    assert replayed.json()["replayed_evidence_hash"] == verified["evidence_hash"]


def test_unknown_proof_returns_not_found() -> None:
    assert client.get("/api/v1/proofs/missing-proof").status_code == 404
    assert client.post("/api/v1/proofs/missing-proof/replay").status_code == 404


def test_verification_run_id_changes_when_policy_changes() -> None:
    first = client.post(
        "/api/v1/policies/verify",
        json={"policy": demo_policy(threshold=1_000_000), "max_actions": 4},
    ).json()
    second = client.post(
        "/api/v1/policies/verify",
        json={"policy": demo_policy(threshold=900_000), "max_actions": 4},
    ).json()

    assert first["proof_run_id"] != second["proof_run_id"]
