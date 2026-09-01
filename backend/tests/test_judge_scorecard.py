from pathlib import Path
import runpy

from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def test_judge_scorecard_runs_complete_evidence_path() -> None:
    response = client.post(
        "/api/v1/evaluations/judge-scorecards/run",
        json={"seed": 2026, "samples_per_class": 5},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["verdict"] == "ready"
    assert report["campaign_seed"] == 2026
    assert report["samples_per_class"] == 5
    assert report["checks_passed"] == report["total_checks"] == 6
    assert report["fixed_scenarios"] == 11
    assert report["generated_cases"] == 20
    assert report["total_test_cases"] == 31
    assert report["attack_recall_percent"] == 100.0
    assert report["false_positive_rate_percent"] == 0.0
    assert report["concurrency_invariant_held"] is True
    assert report["evidence_hash"].startswith("sha256:")
    assert len(report["limitations"]) == 4
    assert all(check["passed"] for check in report["checks"])

    stored = client.get(
        f'/api/v1/evaluations/judge-scorecards/{report["scorecard_id"]}'
    )
    assert stored.status_code == 200
    assert stored.json() == report


def test_judge_scorecard_evidence_is_reproducible() -> None:
    payload = {"seed": 99, "samples_per_class": 5}
    first = client.post("/api/v1/evaluations/judge-scorecards/run", json=payload).json()
    second = client.post("/api/v1/evaluations/judge-scorecards/run", json=payload).json()

    assert first["scorecard_id"] != second["scorecard_id"]
    assert first["evidence_hash"] == second["evidence_hash"]


def test_unknown_judge_scorecard_returns_not_found() -> None:
    response = client.get("/api/v1/evaluations/judge-scorecards/not-a-scorecard")

    assert response.status_code == 404


def test_portable_judge_bundle_verifies_in_api_and_standalone() -> None:
    report = client.post(
        "/api/v1/evaluations/judge-scorecards/run",
        json={"seed": 2026, "samples_per_class": 5},
    ).json()
    bundle_response = client.get(
        f'/api/v1/evaluations/judge-scorecards/{report["scorecard_id"]}/evidence-bundle'
    )

    assert bundle_response.status_code == 200
    bundle = bundle_response.json()
    assert bundle["format_version"] == "arthaniyam.judge.v1"
    assert bundle["bundle_hash"].startswith("sha256:")
    verification = client.post(
        "/api/v1/evaluations/judge-scorecards/verify", json=bundle
    ).json()
    assert verification["valid"] is True
    assert verification["issues"] == []

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "verify_scorecard.py"
    standalone_verify = runpy.run_path(str(script_path))["verify"]
    assert standalone_verify(bundle) == []


def test_portable_judge_bundle_detects_tampering() -> None:
    report = client.post(
        "/api/v1/evaluations/judge-scorecards/run",
        json={"seed": 7, "samples_per_class": 5},
    ).json()
    bundle = client.get(
        f'/api/v1/evaluations/judge-scorecards/{report["scorecard_id"]}/evidence-bundle'
    ).json()
    bundle["scorecard"]["checks"][0]["passed"] = False

    verification = client.post(
        "/api/v1/evaluations/judge-scorecards/verify", json=bundle
    ).json()

    assert verification["valid"] is False
    assert any("evidence hash" in issue for issue in verification["issues"])
    assert any("manifest hash" in issue for issue in verification["issues"])
