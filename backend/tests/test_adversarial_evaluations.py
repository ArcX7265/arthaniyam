from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def test_mixed_suite_catches_attacks_without_blocking_benign_controls() -> None:
    response = client.post("/api/v1/evaluations/run")

    assert response.status_code == 200
    report = response.json()
    assert report["total_scenarios"] == 10
    assert report["attack_scenarios"] == 6
    assert report["benign_scenarios"] == 4
    assert report["attacks_caught"] == 6
    assert report["benign_allowed"] == 4
    assert report["false_positives"] == 0
    assert report["naive_gateway_misses"] == 6
    assert report["coverage_percent"] == 100.0
    assert report["attack_recall_percent"] == 100.0
    assert report["false_positive_rate_percent"] == 0.0
    assert report["accuracy_percent"] == 100.0
    assert report["evidence_hash"].startswith("sha256:")
    assert {scenario["scenario_id"] for scenario in report["scenarios"]} == {
        "split-payment",
        "budget-exhaustion",
        "duplicate-invoice",
        "approval-spoof",
        "authority-multiplication",
        "cumulative-refund",
        "independent-payments",
        "within-budget",
        "conservative-delegation",
        "bounded-refunds",
    }
    assert all(scenario["passed"] for scenario in report["scenarios"])
    assert sum(
        scenario["scenario_type"] == "attack" for scenario in report["scenarios"]
    ) == 6
    assert sum(
        scenario["scenario_type"] == "benign" for scenario in report["scenarios"]
    ) == 4

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
