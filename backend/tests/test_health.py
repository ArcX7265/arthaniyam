from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def teardown_module() -> None:
    client.close()


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_capabilities_keep_real_money_disabled() -> None:
    response = client.get("/api/v1/system/capabilities")

    assert response.status_code == 200
    assert response.json()["real_money_enabled"] is False
    assert response.json()["live_keys_accepted"] is False
    assert "webhook_configured" in response.json()


def test_dashboard_is_served_from_root() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "ArthaNiyam" in response.text
    assert "run-attack-button" in response.text


def test_dashboard_assets_are_available() -> None:
    stylesheet = client.get("/assets/styles.css")
    script = client.get("/assets/app.js")

    assert stylesheet.status_code == 200
    assert "--acid" in stylesheet.text
    assert script.status_code == 200
    assert "runAttack" in script.text
    assert "confirmWithRazorpay" in script.text
    assert "replayProof" in script.text
    assert "compilePolicy" in script.text
    assert "approveChallenge" in script.text
    assert "runDelegationAttack" in script.text
    assert "runRefundAttack" in script.text
    assert "runAdversarialEvaluation" in script.text
    assert "runBoundaryCampaign" in script.text
    assert "runPolicyRollout" in script.text


def test_policy_schema_rejects_transaction_limit_above_budget() -> None:
    response = client.post(
        "/api/v1/policies/validate",
        json={
            "policy_id": "procurement-demo",
            "version": 1,
            "name": "Procurement Demo",
            "currency": "INR",
            "budget": {
                "monthly_limit": 5_000_000,
                "per_transaction_limit": 6_000_000,
            },
            "approval": {"required_above": 1_000_000, "approver_count": 1},
        },
    )

    assert response.status_code == 422
