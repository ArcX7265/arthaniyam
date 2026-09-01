from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import mkstemp
from typing import Literal
from uuid import uuid4

from app.judge import JudgeScorecardRequest, JudgeScorecardService
from app.policy.models import PolicyDefinition, StrictModel
from app.runtime.guard import RuntimeGuard
from app.runtime.models import FinancialAction, RuntimeEvaluationRequest
from app.runtime.storage import SQLiteRuntimeRepository


class GuidedDemoStep(StrictModel):
    sequence: int
    action_id: str
    amount: int
    naive_decision: str
    arthaniyam_decision: str
    correlated_amount: int
    reason_code: str
    finding: str


class GuidedDemoReport(StrictModel):
    demo_id: str
    created_at: datetime
    outcome: Literal["unsafe_sequence_blocked", "unexpected_result"]
    problem: str
    insight: str
    steps: list[GuidedDemoStep]
    scorecard_id: str
    scorecard_verdict: str
    scorecard_checks: str
    scorecard_test_cases: int
    scorecard_evidence_hash: str
    evidence_hash: str


class GuidedDemoService:
    """Produce a deterministic judge narrative from real runtime decisions."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.scorecards = JudgeScorecardService(repository)

    def run(self) -> GuidedDemoReport:
        descriptor, database_name = mkstemp(
            prefix="arthaniyam-guided-demo-", suffix=".sqlite3"
        )
        os.close(descriptor)
        database_path = Path(database_name)
        try:
            guard = RuntimeGuard(SQLiteRuntimeRepository(database_path))
            policy = self._policy()
            first = guard.evaluate(
                RuntimeEvaluationRequest(policy=policy, action=self._action(1))
            )
            second = guard.evaluate(
                RuntimeEvaluationRequest(policy=policy, action=self._action(2))
            )
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{database_path}{suffix}").unlink(missing_ok=True)

        steps = [
            GuidedDemoStep(
                sequence=1,
                action_id=first.action_id,
                amount=900_000,
                naive_decision=first.naive_gateway.decision,
                arthaniyam_decision=first.arthaniyam.decision,
                correlated_amount=first.correlated_amount,
                reason_code=first.arthaniyam.reason_codes[0],
                finding="The first payment fits every local and shared constraint.",
            ),
            GuidedDemoStep(
                sequence=2,
                action_id=second.action_id,
                amount=900_000,
                naive_decision=second.naive_gateway.decision,
                arthaniyam_decision=second.arthaniyam.decision,
                correlated_amount=second.correlated_amount,
                reason_code=second.arthaniyam.reason_codes[0],
                finding=(
                    "The second request still looks safe alone, but the related "
                    "commitment is now above the approval threshold."
                ),
            ),
        ]
        blocked = (
            first.naive_gateway.decision == "allow"
            and second.naive_gateway.decision == "allow"
            and first.arthaniyam.decision == "allow_and_reserve"
            and second.arthaniyam.decision == "require_approval"
        )
        scorecard = self.scorecards.run(
            JudgeScorecardRequest(seed=2026, samples_per_class=5)
        )
        evidence_hash = self._hash(
            {
                "steps": [step.model_dump(mode="json") for step in steps],
                "scorecard_evidence_hash": scorecard.evidence_hash,
            }
        )
        report = GuidedDemoReport(
            demo_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            outcome=("unsafe_sequence_blocked" if blocked else "unexpected_result"),
            problem=(
                "A stateless gateway approves two INR 9,000 payments because each "
                "is below the INR 10,000 approval threshold."
            ),
            insight=(
                "ArthaNiyam evaluates the financial outcome across requests, so the "
                "second payment is gated when correlated exposure reaches INR 18,000."
            ),
            steps=steps,
            scorecard_id=scorecard.scorecard_id,
            scorecard_verdict=scorecard.verdict,
            scorecard_checks=f"{scorecard.checks_passed}/{scorecard.total_checks}",
            scorecard_test_cases=scorecard.total_test_cases,
            scorecard_evidence_hash=scorecard.evidence_hash,
            evidence_hash=evidence_hash,
        )
        self.repository.save_guided_demo(
            report.demo_id,
            report.model_dump_json(),
            report.evidence_hash,
            report.created_at,
        )
        return report

    def get(self, demo_id: str) -> GuidedDemoReport:
        stored = self.repository.get_guided_demo(demo_id)
        if stored is None:
            raise KeyError(demo_id)
        return GuidedDemoReport.model_validate_json(stored)

    @staticmethod
    def _policy() -> PolicyDefinition:
        return PolicyDefinition.model_validate(
            {
                "policy_id": "guided-demo-policy",
                "version": 1,
                "name": "Guided split-payment policy",
                "budget": {
                    "monthly_limit": 5_000_000,
                    "per_transaction_limit": 1_000_000,
                },
                "approval": {"required_above": 1_000_000},
                "vendors": {
                    "require_approved_vendor": True,
                    "allowed_vendor_ids": ["vector-systems"],
                    "allowed_categories": ["hardware"],
                },
                "correlation": {
                    "window_hours": 24,
                    "group_by": ["vendor", "purpose"],
                },
            }
        )

    @staticmethod
    def _action(sequence: int) -> FinancialAction:
        return FinancialAction(
            action_id=f"guided-payment-{sequence}",
            agent_id="procurement-agent",
            amount=900_000,
            vendor_id="vector-systems",
            category="hardware",
            purpose="office-laptops",
            invoice_id=f"guided-invoice-{sequence}",
        )

    @staticmethod
    def _hash(value: dict[str, object]) -> str:
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + sha256(canonical).hexdigest()
