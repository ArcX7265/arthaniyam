import json
import sqlite3

from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def evaluation_body() -> dict:
    return {
        "policy": {
            "policy_id": "audit-integrity-policy",
            "version": 1,
            "name": "Audit integrity policy",
            "currency": "INR",
            "budget": {
                "monthly_limit": 5_000_000,
                "per_transaction_limit": 1_000_000,
            },
            "approval": {"required_above": 1_000_000, "approver_count": 1},
        },
        "action": {
            "action_id": "audit-action-1",
            "agent_id": "audit-agent",
            "amount": 500_000,
            "vendor_id": "audit-vendor",
            "category": "hardware",
            "purpose": "laptops",
            "invoice_id": "audit-invoice-1",
            "approval_ids": [],
        },
    }


def test_audit_chain_verifies_untouched_events() -> None:
    evaluated = client.post("/api/v1/runtime/evaluate", json=evaluation_body())
    assert evaluated.status_code == 200

    response = client.get(
        "/api/v1/runtime/policies/audit-integrity-policy/audit-integrity?version=1"
    )

    assert response.status_code == 200
    report = response.json()
    assert report["valid"] is True
    assert report["total_events"] == 1
    assert report["chained_events"] == 1
    assert report["head_hash"].startswith("sha256:")
    assert report["first_broken_sequence"] is None
    assert report["issues"] == []


def test_audit_chain_detects_changed_event_content() -> None:
    client.post("/api/v1/runtime/evaluate", json=evaluation_body())
    database_path = runtime_guard.repository.database_path
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT event_id, event_json FROM audit_events LIMIT 1"
        ).fetchone()
        event = json.loads(row[1])
        event["decision"] = "tampered"
        connection.execute(
            "UPDATE audit_events SET event_json = ? WHERE event_id = ?",
            (json.dumps(event, separators=(",", ":")), row[0]),
        )

    report = client.get(
        "/api/v1/runtime/policies/audit-integrity-policy/audit-integrity?version=1"
    ).json()

    assert report["valid"] is False
    assert report["first_broken_sequence"] == 1
    assert "event content hash does not match" in report["issues"][0]


def test_audit_chain_detects_deleted_tail_against_checkpoint() -> None:
    client.post("/api/v1/runtime/evaluate", json=evaluation_body())
    database_path = runtime_guard.repository.database_path
    with sqlite3.connect(database_path) as connection:
        event_id = connection.execute(
            "SELECT event_id FROM audit_chain_entries LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM audit_chain_entries WHERE event_id = ?", (event_id,)
        )
        connection.execute("DELETE FROM audit_events WHERE event_id = ?", (event_id,))

    report = client.get(
        "/api/v1/runtime/policies/audit-integrity-policy/audit-integrity?version=1"
    ).json()

    assert report["valid"] is False
    assert any("checkpoint count" in issue for issue in report["issues"])
