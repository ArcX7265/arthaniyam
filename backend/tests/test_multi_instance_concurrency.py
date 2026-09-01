from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from tempfile import mkstemp

import pytest

from app.policy.models import PolicyDefinition
from app.runtime.guard import RuntimeGuard
from app.runtime.models import FinancialAction, RuntimeEvaluationRequest
from app.runtime.storage import SQLiteRuntimeRepository


@pytest.fixture
def database_path():
    descriptor, name = mkstemp(prefix="arthaniyam-multi-instance-", suffix=".sqlite3")
    os.close(descriptor)
    path = Path(name)
    yield path
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def policy(policy_id: str, *, threshold: int = 5_000_000) -> PolicyDefinition:
    return PolicyDefinition.model_validate(
        {
            "policy_id": policy_id,
            "version": 1,
            "name": "Multi-instance concurrency policy",
            "budget": {
                "monthly_limit": 5_000_000,
                "per_transaction_limit": 1_000_000,
            },
            "approval": {"required_above": threshold},
        }
    )


def action(sequence: int, *, purpose: str, invoice: str | None = None) -> FinancialAction:
    return FinancialAction(
        action_id=f"multi-action-{sequence}",
        agent_id="multi-agent",
        amount=1_000_000,
        vendor_id="multi-vendor",
        category="hardware",
        purpose=purpose,
        invoice_id=invoice or f"multi-invoice-{sequence}",
    )


def test_shared_sqlite_budget_is_atomic_across_runtime_instances(database_path) -> None:
    database = database_path
    runtime_policy = policy("multi-budget")
    guards = [RuntimeGuard(SQLiteRuntimeRepository(database)) for _ in range(12)]
    requests = [
        RuntimeEvaluationRequest(
            policy=runtime_policy,
            action=action(index, purpose=f"purpose-{index}"),
        )
        for index in range(12)
    ]

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(
            executor.map(
                lambda item: item[0].evaluate(item[1]),
                zip(guards, requests),
            )
        )

    allowed = sum(
        result.arthaniyam.decision == "allow_and_reserve" for result in results
    )
    denied = sum(result.arthaniyam.decision == "deny" for result in results)
    final_state = guards[0].state(runtime_policy.policy_id, 1).state

    assert allowed == 5
    assert denied == 7
    assert final_state.reserved_amount == runtime_policy.budget.monthly_limit
    assert final_state.available_budget == 0


def test_atomic_recheck_catches_concurrent_correlation_threshold(database_path) -> None:
    database = database_path
    runtime_policy = policy("multi-correlation", threshold=1_000_000)
    guards = [RuntimeGuard(SQLiteRuntimeRepository(database)) for _ in range(2)]
    requests = [
        RuntimeEvaluationRequest(
            policy=runtime_policy,
            action=action(index, purpose="same-purpose").model_copy(
                update={"amount": 600_000}
            ),
        )
        for index in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: item[0].evaluate(item[1]),
                zip(guards, requests),
            )
        )

    decisions = sorted(result.arthaniyam.decision for result in results)
    assert decisions == ["allow_and_reserve", "require_approval"]
    assert guards[0].state(runtime_policy.policy_id, 1).state.reserved_amount == 600_000


def test_atomic_recheck_prevents_concurrent_duplicate_invoice(database_path) -> None:
    database = database_path
    runtime_policy = policy("multi-invoice")
    guards = [RuntimeGuard(SQLiteRuntimeRepository(database)) for _ in range(2)]
    requests = [
        RuntimeEvaluationRequest(
            policy=runtime_policy,
            action=action(index, purpose=f"purpose-{index}", invoice="same-invoice"),
        )
        for index in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: item[0].evaluate(item[1]),
                zip(guards, requests),
            )
        )

    decisions = sorted(result.arthaniyam.decision for result in results)
    assert decisions == ["allow_and_reserve", "deny"]
    denied = next(result for result in results if result.arthaniyam.decision == "deny")
    assert "DUPLICATE_INVOICE" in denied.arthaniyam.reason_codes
