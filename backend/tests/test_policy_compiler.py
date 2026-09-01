import json

from fastapi.testclient import TestClient

from app.main import app
from app.policy.compiler import OpenAIExtractionBackend


client = TestClient(app)


def teardown_module() -> None:
    client.close()


def complete_policy_text() -> str:
    return (
        "The monthly budget is ₹50,000. The per-transaction limit is ₹10,000. "
        "Require approval above ₹10,000. Correlate related vendor and purpose "
        "payments for 24 hours. Approved vendors are Vector Systems. "
        "Allowed categories are hardware."
    )


def test_reference_compiler_produces_verification_ready_typed_policy() -> None:
    response = client.post(
        "/api/v1/policies/compile",
        json={"policy_id": "compiled-demo", "source_text": complete_policy_text()},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ready_for_verification"
    assert result["compiler_mode"] == "reference"
    assert result["policy"]["budget"]["monthly_limit"] == 5_000_000
    assert result["policy"]["budget"]["per_transaction_limit"] == 1_000_000
    assert result["policy"]["approval"]["required_above"] == 1_000_000
    assert result["policy"]["vendors"]["allowed_vendor_ids"] == ["vector-systems"]
    assert len(result["source_map"]) == 4


def test_compiler_refuses_to_invent_missing_money_rules() -> None:
    response = client.post(
        "/api/v1/policies/compile",
        json={
            "policy_id": "ambiguous-demo",
            "source_text": "Only approved vendors may be paid for business purchases.",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "needs_review"
    assert result["policy"] is None
    assert {issue["field"] for issue in result["issues"] if issue["severity"] == "blocker"} == {
        "budget.monthly_limit",
        "budget.per_transaction_limit",
        "approval.required_above",
    }


def test_compiler_blocks_internally_inconsistent_policy() -> None:
    source = (
        "Monthly budget is INR 10,000. Per-transaction limit is INR 20,000. "
        "Approval threshold is INR 5,000."
    )
    result = client.post(
        "/api/v1/policies/compile",
        json={"policy_id": "invalid-demo", "source_text": source},
    ).json()

    assert result["status"] == "needs_review"
    assert result["policy"] is None
    assert any(issue["code"] == "INCONSISTENT_POLICY" for issue in result["issues"])


def test_openai_backend_requests_strict_structured_extraction(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            extraction = {
                "name": "AI extracted policy",
                "monthly_limit_rupees": 50_000,
                "per_transaction_limit_rupees": 10_000,
                "approval_threshold_rupees": 10_000,
                "correlation_window_hours": 24,
                "allowed_vendor_ids": ["vector-systems"],
                "allowed_categories": ["hardware"],
                "assumptions": [],
                "ambiguities": [],
            }
            return {
                "output": [
                    {"content": [{"type": "output_text", "text": json.dumps(extraction)}]}
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("app.policy.compiler.httpx.post", fake_post)
    extraction, _ = OpenAIExtractionBackend("test-key", "test-model").extract(
        complete_policy_text()
    )

    assert extraction.monthly_limit_rupees == 50_000
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["json"]["text"]["format"]["type"] == "json_schema"
    assert captured["json"]["text"]["format"]["strict"] is True
