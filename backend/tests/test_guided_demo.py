from fastapi.testclient import TestClient

from app.main import app, runtime_guard


client = TestClient(app)


def setup_function() -> None:
    runtime_guard.reset()


def teardown_module() -> None:
    client.close()


def test_guided_demo_tells_complete_reproducible_story() -> None:
    response = client.post("/api/v1/demo/guided-run")

    assert response.status_code == 200
    report = response.json()
    assert report["outcome"] == "unsafe_sequence_blocked"
    assert len(report["steps"]) == 2
    assert [step["naive_decision"] for step in report["steps"]] == [
        "allow",
        "allow",
    ]
    assert [step["arthaniyam_decision"] for step in report["steps"]] == [
        "allow_and_reserve",
        "require_approval",
    ]
    assert report["steps"][1]["correlated_amount"] == 1_800_000
    assert report["scorecard_verdict"] == "ready"
    assert report["scorecard_checks"] == "6/6"
    assert report["scorecard_test_cases"] == 31
    assert report["evidence_hash"].startswith("sha256:")

    stored = client.get(f'/api/v1/demo/guided-runs/{report["demo_id"]}')
    assert stored.status_code == 200
    assert stored.json() == report


def test_guided_demo_evidence_is_stable_across_runs() -> None:
    first = client.post("/api/v1/demo/guided-run").json()
    second = client.post("/api/v1/demo/guided-run").json()

    assert first["demo_id"] != second["demo_id"]
    assert first["evidence_hash"] == second["evidence_hash"]


def test_unknown_guided_demo_returns_not_found() -> None:
    response = client.get("/api/v1/demo/guided-runs/missing-demo")

    assert response.status_code == 404
