from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def test_fixed_attack_suite_catches_all_known_scenarios() -> None:
    response = client.post("/api/v1/evaluations/run")

    assert response.status_code == 200
    report = response.json()
    assert report["total_scenarios"] == 6
    assert report["attacks_caught"] == 6
    assert report["naive_gateway_misses"] == 6
    assert report["coverage_percent"] == 100.0
    assert report["evidence_hash"].startswith("sha256:")
    assert {scenario["scenario_id"] for scenario in report["scenarios"]} == {
        "split-payment",
        "budget-exhaustion",
        "duplicate-invoice",
        "approval-spoof",
        "authority-multiplication",
        "cumulative-refund",
    }
    assert all(scenario["passed"] for scenario in report["scenarios"])

    stored = client.get(f'/api/v1/evaluations/{report["run_id"]}')
    assert stored.status_code == 200
    assert stored.json() == report


def test_attack_evidence_is_deterministic_across_runs() -> None:
    first = client.post("/api/v1/evaluations/run").json()
    second = client.post("/api/v1/evaluations/run").json()

    assert first["run_id"] != second["run_id"]
    assert first["evidence_hash"] == second["evidence_hash"]


def test_unknown_evaluation_returns_not_found() -> None:
    response = client.get("/api/v1/evaluations/not-a-run")

    assert response.status_code == 404
