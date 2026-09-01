from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from random import Random
from tempfile import mkstemp
from time import perf_counter
from typing import Literal
from uuid import uuid4

from pydantic import Field

from app.policy.models import PolicyDefinition, StrictModel
from app.runtime.guard import RuntimeGuard
from app.runtime.models import FinancialAction, RuntimeEvaluationRequest
from app.runtime.storage import SQLiteRuntimeRepository


class BoundaryCampaignRequest(StrictModel):
    seed: int = Field(default=42, ge=0, le=4_294_967_295)
    samples_per_class: int = Field(default=20, ge=5, le=50)


class BoundaryCaseResult(StrictModel):
    case_id: str
    family: Literal["correlated-approval", "monthly-budget"]
    case_type: Literal["attack", "benign"]
    expected_blocked: bool
    actual_blocked: bool
    passed: bool
    boundary_amount: int
    observed_amount: int
    delta: int
    decision: str
    reason_code: str


class BoundaryCampaignReport(StrictModel):
    campaign_id: str
    created_at: datetime
    seed: int
    samples_per_class: int
    total_cases: int
    passed_cases: int
    attacks: int
    benign_controls: int
    false_negatives: int
    false_positives: int
    attack_recall_percent: float
    false_positive_rate_percent: float
    accuracy_percent: float
    duration_ms: float
    throughput_per_second: float
    evidence_hash: str
    cases: list[BoundaryCaseResult]


class BoundaryCampaignService:
    """Generate deterministic cases around policy boundaries and score the guard."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def run(self, request: BoundaryCampaignRequest) -> BoundaryCampaignReport:
        descriptor, database_name = mkstemp(
            prefix="arthaniyam-campaign-", suffix=".sqlite3"
        )
        os.close(descriptor)
        database_path = Path(database_name)
        started = perf_counter()
        try:
            guard = RuntimeGuard(SQLiteRuntimeRepository(database_path))
            random = Random(request.seed)
            cases: list[BoundaryCaseResult] = []
            for index in range(request.samples_per_class):
                cases.extend(
                    (
                        self._correlation_case(guard, random, index, attack=True),
                        self._correlation_case(guard, random, index, attack=False),
                        self._budget_case(guard, random, index, attack=True),
                        self._budget_case(guard, random, index, attack=False),
                    )
                )
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{database_path}{suffix}").unlink(missing_ok=True)

        duration_ms = round((perf_counter() - started) * 1_000, 2)
        attacks = [case for case in cases if case.case_type == "attack"]
        benign = [case for case in cases if case.case_type == "benign"]
        false_negatives = sum(not case.actual_blocked for case in attacks)
        false_positives = sum(case.actual_blocked for case in benign)
        passed = sum(case.passed for case in cases)
        report = BoundaryCampaignReport(
            campaign_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            seed=request.seed,
            samples_per_class=request.samples_per_class,
            total_cases=len(cases),
            passed_cases=passed,
            attacks=len(attacks),
            benign_controls=len(benign),
            false_negatives=false_negatives,
            false_positives=false_positives,
            attack_recall_percent=round(
                (len(attacks) - false_negatives) / len(attacks) * 100, 1
            ),
            false_positive_rate_percent=round(
                false_positives / len(benign) * 100, 1
            ),
            accuracy_percent=round(passed / len(cases) * 100, 1),
            duration_ms=duration_ms,
            throughput_per_second=round(
                len(cases) / max(duration_ms / 1_000, 0.001), 1
            ),
            evidence_hash=self._hash(request, cases),
            cases=cases,
        )
        self.repository.save_boundary_campaign(
            report.campaign_id,
            report.model_dump_json(),
            report.evidence_hash,
            report.created_at,
        )
        return report

    def get(self, campaign_id: str) -> BoundaryCampaignReport:
        stored = self.repository.get_boundary_campaign(campaign_id)
        if stored is None:
            raise KeyError(campaign_id)
        return BoundaryCampaignReport.model_validate_json(stored)

    def _correlation_case(
        self,
        guard: RuntimeGuard,
        random: Random,
        index: int,
        *,
        attack: bool,
    ) -> BoundaryCaseResult:
        threshold = random.randint(600_000, 1_200_000)
        first_amount = random.randint(200_000, threshold - 150_000)
        distance = random.randint(1, 50_000)
        target = threshold + distance if attack else threshold - distance
        second_amount = target - first_amount
        case_type = "attack" if attack else "benign"
        prefix = f"campaign-correlation-{case_type}-{index}"
        policy = self._policy(prefix, monthly=3_000_000, threshold=threshold)
        guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy,
                action=self._action(f"{prefix}-first", first_amount, "shared-purpose"),
            )
        )
        result = guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy,
                action=self._action(f"{prefix}-second", second_amount, "shared-purpose"),
            )
        )
        return self._case(
            prefix,
            "correlated-approval",
            attack,
            threshold,
            result.correlated_amount,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
        )

    def _budget_case(
        self,
        guard: RuntimeGuard,
        random: Random,
        index: int,
        *,
        attack: bool,
    ) -> BoundaryCaseResult:
        monthly = random.randint(1_000_000, 2_000_000)
        first_amount = random.randint(300_000, monthly - 200_000)
        distance = random.randint(1, 50_000)
        target = monthly + distance if attack else monthly - distance
        second_amount = target - first_amount
        case_type = "attack" if attack else "benign"
        prefix = f"campaign-budget-{case_type}-{index}"
        policy = self._policy(prefix, monthly=monthly, threshold=monthly)
        guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy,
                action=self._action(f"{prefix}-first", first_amount, "hardware"),
            )
        )
        result = guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy,
                action=self._action(f"{prefix}-second", second_amount, "furniture"),
            )
        )
        return self._case(
            prefix,
            "monthly-budget",
            attack,
            monthly,
            target,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
        )

    @staticmethod
    def _policy(policy_id: str, *, monthly: int, threshold: int) -> PolicyDefinition:
        return PolicyDefinition(
            policy_id=policy_id,
            version=1,
            name=f"Boundary campaign {policy_id}",
            currency="INR",
            budget={"monthly_limit": monthly, "per_transaction_limit": monthly},
            approval={"required_above": threshold, "approver_count": 1},
            correlation={"window_hours": 24, "group_by": ["vendor", "purpose"]},
        )

    @staticmethod
    def _action(action_id: str, amount: int, purpose: str) -> FinancialAction:
        return FinancialAction(
            action_id=action_id,
            agent_id="campaign-agent",
            amount=amount,
            vendor_id="campaign-vendor",
            category="procurement",
            purpose=purpose,
            invoice_id=f"invoice-{action_id}",
            approval_ids=[],
        )

    @staticmethod
    def _case(
        case_id: str,
        family: Literal["correlated-approval", "monthly-budget"],
        attack: bool,
        boundary: int,
        observed: int,
        decision: str,
        reason: str,
    ) -> BoundaryCaseResult:
        actual_blocked = decision in {"deny", "require_approval"}
        return BoundaryCaseResult(
            case_id=case_id,
            family=family,
            case_type="attack" if attack else "benign",
            expected_blocked=attack,
            actual_blocked=actual_blocked,
            passed=actual_blocked == attack,
            boundary_amount=boundary,
            observed_amount=observed,
            delta=observed - boundary,
            decision=decision,
            reason_code=reason,
        )

    @staticmethod
    def _hash(
        request: BoundaryCampaignRequest, cases: list[BoundaryCaseResult]
    ) -> str:
        canonical = json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "cases": [case.model_dump(mode="json") for case in cases],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + sha256(canonical).hexdigest()
