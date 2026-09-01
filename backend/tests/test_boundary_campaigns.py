from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def test_seeded_boundary_campaign_scores_both_sides_of_limits() -> None:
    response = client.post(
        "/api/v1/evaluations/boundary-campaigns/run",
        json={"seed": 2026, "samples_per_class": 5},
    )

    assert response.status_code == 200
    report = response.json()
    assert report["seed"] == 2026
    assert report["total_cases"] == 20
    assert report["passed_cases"] == 20
    assert report["attacks"] == 10
    assert report["benign_controls"] == 10
    assert report["false_negatives"] == 0
    assert report["false_positives"] == 0
    assert report["attack_recall_percent"] == 100.0
    assert report["false_positive_rate_percent"] == 0.0
    assert report["accuracy_percent"] == 100.0
    assert report["throughput_per_second"] > 0
    assert {case["family"] for case in report["cases"]} == {
        "correlated-approval",
        "monthly-budget",
    }
    assert all(case["passed"] for case in report["cases"])

    stored = client.get(
        f'/api/v1/evaluations/boundary-campaigns/{report["campaign_id"]}'
    )
    assert stored.status_code == 200
    assert stored.json() == report


def test_campaign_hash_replays_seed_and_changes_with_seed() -> None:
    def run(seed: int) -> dict:
        return client.post(
            "/api/v1/evaluations/boundary-campaigns/run",
            json={"seed": seed, "samples_per_class": 5},
        ).json()

    first = run(77)
    replay = run(77)
    different = run(78)

    assert first["campaign_id"] != replay["campaign_id"]
    assert first["evidence_hash"] == replay["evidence_hash"]
    assert first["evidence_hash"] != different["evidence_hash"]


def test_campaign_workload_is_bounded() -> None:
    too_small = client.post(
        "/api/v1/evaluations/boundary-campaigns/run",
        json={"seed": 1, "samples_per_class": 4},
    )
    too_large = client.post(
        "/api/v1/evaluations/boundary-campaigns/run",
        json={"seed": 1, "samples_per_class": 51},
    )

    assert too_small.status_code == 422
    assert too_large.status_code == 422
