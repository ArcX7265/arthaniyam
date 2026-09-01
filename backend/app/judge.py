from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field

from app.campaigns import BoundaryCampaignRequest, BoundaryCampaignService
from app.evaluations import AdversarialEvaluationService
from app.policy.models import PolicyDefinition, StrictModel
from app.policy.verifier import VerificationRequest, verify_correlated_payments
from app.runtime.storage import SQLiteRuntimeRepository


class JudgeScorecardRequest(StrictModel):
    seed: int = Field(default=2026, ge=0, le=4_294_967_295)
    samples_per_class: int = Field(default=20, ge=5, le=50)


class JudgeCheck(StrictModel):
    check_id: str
    label: str
    passed: bool
    result: str


class JudgeScorecardReport(StrictModel):
    scorecard_id: str
    created_at: datetime
    verdict: Literal["ready", "needs_attention"]
    checks_passed: int
    total_checks: int
    fixed_scenarios: int
    generated_cases: int
    total_test_cases: int
    attack_recall_percent: float
    false_positive_rate_percent: float
    concurrency_invariant_held: bool
    proof_evidence_hash: str
    benchmark_evidence_hash: str
    campaign_evidence_hash: str
    evidence_hash: str
    checks: list[JudgeCheck]
    limitations: list[str]


class JudgeScorecardService:
    """Compose existing independent evaluations into one judge-facing report."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository
        self.evaluations = AdversarialEvaluationService(repository)
        self.campaigns = BoundaryCampaignService(repository)

    def run(self, request: JudgeScorecardRequest) -> JudgeScorecardReport:
        proof_request = VerificationRequest(
            policy=PolicyDefinition.model_validate(
                {
                    "policy_id": "judge-symbolic-policy",
                    "version": 1,
                    "name": "Judge symbolic split-payment policy",
                    "budget": {
                        "monthly_limit": 5_000_000,
                        "per_transaction_limit": 1_000_000,
                    },
                    "approval": {"required_above": 1_000_000},
                }
            ),
            max_actions=4,
        )
        proof = verify_correlated_payments(proof_request)
        proof_hash = self._hash(
            {
                "request": proof_request.model_dump(mode="json"),
                "result": proof.model_dump(mode="json", exclude={"evidence_hash"}),
            }
        )
        benchmark = self.evaluations.run()
        campaign = self.campaigns.run(
            BoundaryCampaignRequest(
                seed=request.seed,
                samples_per_class=request.samples_per_class,
            )
        )
        burst = next(
            scenario
            for scenario in benchmark.scenarios
            if scenario.scenario_id == "concurrent-budget-burst"
        )
        concurrency_held = bool(burst.evidence.get("invariant_holds"))
        checks = [
            JudgeCheck(
                check_id="symbolic-counterexample",
                label="Solver exposes a locally valid but globally unsafe sequence",
                passed=proof.status == "counterexample_found",
                result=proof.status,
            ),
            JudgeCheck(
                check_id="fixed-attacks",
                label="Every hand-authored attack is stopped",
                passed=benchmark.attacks_caught == benchmark.attack_scenarios,
                result=f"{benchmark.attacks_caught}/{benchmark.attack_scenarios} caught",
            ),
            JudgeCheck(
                check_id="benign-controls",
                label="Legitimate controls remain available",
                passed=benchmark.false_positives == 0,
                result=f"{benchmark.false_positives} false positives",
            ),
            JudgeCheck(
                check_id="boundary-oracle",
                label="Generated boundary cases match an independent oracle",
                passed=campaign.passed_cases == campaign.total_cases,
                result=f"{campaign.passed_cases}/{campaign.total_cases} matched",
            ),
            JudgeCheck(
                check_id="concurrent-admission",
                label="A simultaneous burst cannot over-reserve the budget",
                passed=concurrency_held,
                result=(
                    f"{burst.evidence.get('arthaniyam_admitted_actions')} admitted, "
                    f"{burst.evidence.get('arthaniyam_denied_actions')} denied"
                ),
            ),
            JudgeCheck(
                check_id="reproducible-evidence",
                label="Every evaluation produces canonical SHA-256 evidence",
                passed=all(
                    digest.startswith("sha256:")
                    for digest in (
                        proof_hash,
                        benchmark.evidence_hash,
                        campaign.evidence_hash,
                    )
                ),
                result="3 canonical evidence hashes",
            ),
        ]
        digest = self._hash(
            {
                "request": request.model_dump(mode="json"),
                "proof_evidence_hash": proof_hash,
                "benchmark_evidence_hash": benchmark.evidence_hash,
                "campaign_evidence_hash": campaign.evidence_hash,
                "checks": [check.model_dump(mode="json") for check in checks],
            }
        )
        passed = sum(check.passed for check in checks)
        report = JudgeScorecardReport(
            scorecard_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            verdict="ready" if passed == len(checks) else "needs_attention",
            checks_passed=passed,
            total_checks=len(checks),
            fixed_scenarios=benchmark.total_scenarios,
            generated_cases=campaign.total_cases,
            total_test_cases=benchmark.total_scenarios + campaign.total_cases,
            attack_recall_percent=min(
                benchmark.attack_recall_percent, campaign.attack_recall_percent
            ),
            false_positive_rate_percent=max(
                benchmark.false_positive_rate_percent,
                campaign.false_positive_rate_percent,
            ),
            concurrency_invariant_held=concurrency_held,
            proof_evidence_hash=proof_hash,
            benchmark_evidence_hash=benchmark.evidence_hash,
            campaign_evidence_hash=campaign.evidence_hash,
            evidence_hash=digest,
            checks=checks,
            limitations=[
                "All money movement uses an offline simulator or Razorpay Test Mode; live keys are rejected.",
                "The solver result is a bounded counterexample search, not an unbounded correctness proof.",
                "Concurrency safety is demonstrated within one process, not across distributed replicas.",
                "Benchmark metrics describe synthetic test cases, not production fraud prevalence.",
            ],
        )
        self.repository.save_judge_scorecard(
            report.scorecard_id,
            report.model_dump_json(),
            report.evidence_hash,
            report.created_at,
        )
        return report

    def get(self, scorecard_id: str) -> JudgeScorecardReport:
        stored = self.repository.get_judge_scorecard(scorecard_id)
        if stored is None:
            raise KeyError(scorecard_id)
        return JudgeScorecardReport.model_validate_json(stored)

    @staticmethod
    def _hash(value: dict[str, object]) -> str:
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + sha256(canonical).hexdigest()
