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
