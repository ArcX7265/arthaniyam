from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import mkstemp
from uuid import uuid4

from app.payments.gateway import SimulatedRazorpayGateway
from app.payments.models import (
    OrderExecutionRequest,
    PaymentConfirmationRequest,
    RefundEvaluationRequest,
)
from app.payments.refunds import RefundService
from app.payments.service import PaymentExecutionService
from app.policy.models import PolicyDefinition, StrictModel
from app.runtime.guard import RuntimeGuard
from app.runtime.models import (
    DelegationEvaluationRequest,
    DelegationGrant,
    FinancialAction,
    RuntimeEvaluationRequest,
)
from app.runtime.storage import SQLiteRuntimeRepository


class AdversarialScenarioResult(StrictModel):
    scenario_id: str
    invariant: str
    attack: str
    naive_decision: str
    arthaniyam_decision: str
    passed: bool
    reason_code: str
    evidence: dict[str, int | str | bool]


class AdversarialEvaluationReport(StrictModel):
    run_id: str
    created_at: datetime
    total_scenarios: int
    attacks_caught: int
    naive_gateway_misses: int
    coverage_percent: float
    evidence_hash: str
    scenarios: list[AdversarialScenarioResult]


class AdversarialEvaluationService:
    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def run(self) -> AdversarialEvaluationReport:
        descriptor, database_name = mkstemp(
            prefix="arthaniyam-eval-", suffix=".sqlite3"
        )
        os.close(descriptor)
        database_path = Path(database_name)
        try:
            guard = RuntimeGuard(SQLiteRuntimeRepository(database_path))
            scenarios = [
                self._split_payment(guard),
                self._budget_exhaustion(guard),
                self._duplicate_invoice(guard),
                self._approval_spoof(guard),
                self._authority_multiplication(guard),
                self._cumulative_refund(guard),
            ]
        finally:
            for suffix in ("", "-wal", "-shm"):
                Path(f"{database_path}{suffix}").unlink(missing_ok=True)
        caught = sum(result.passed for result in scenarios)
        digest = self._evidence_hash(scenarios)
        report = AdversarialEvaluationReport(
            run_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            total_scenarios=len(scenarios),
            attacks_caught=caught,
            naive_gateway_misses=sum(
                result.naive_decision == "allow" for result in scenarios
            ),
            coverage_percent=round(caught / len(scenarios) * 100, 1),
            evidence_hash=digest,
            scenarios=scenarios,
        )
        self.repository.save_evaluation_suite(
            report.run_id, report.model_dump_json(), report.evidence_hash, report.created_at
        )
        return report

    def get(self, run_id: str) -> AdversarialEvaluationReport:
        stored = self.repository.get_evaluation_suite(run_id)
        if stored is None:
            raise KeyError(run_id)
        return AdversarialEvaluationReport.model_validate_json(stored)

    def _split_payment(self, guard: RuntimeGuard) -> AdversarialScenarioResult:
        policy = self._policy("eval-split")
        guard.evaluate(RuntimeEvaluationRequest(policy=policy, action=self._action("split-1")))
        result = guard.evaluate(
            RuntimeEvaluationRequest(policy=policy, action=self._action("split-2"))
        )
        return self._scenario(
            "split-payment",
            "Correlated commitments require approval",
            "Two related payments individually below the threshold",
            result.naive_gateway.decision,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
            {"correlated_amount": result.correlated_amount, "threshold": 1_000_000},
        )

    def _budget_exhaustion(self, guard: RuntimeGuard) -> AdversarialScenarioResult:
        policy = self._policy(
            "eval-budget", monthly=1_500_000, threshold=1_500_000
        )
        guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy, action=self._action("budget-1", amount=900_000)
            )
        )
        result = guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy,
                action=self._action("budget-2", amount=700_000, purpose="chairs"),
            )
        )
        return self._scenario(
            "budget-exhaustion",
            "Reservations plus commitments stay within budget",
            "Concurrent-looking requests exceed the shared monthly budget",
            result.naive_gateway.decision,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
            {"attempted_total": 1_600_000, "budget": 1_500_000},
        )

    def _duplicate_invoice(self, guard: RuntimeGuard) -> AdversarialScenarioResult:
        policy = self._policy("eval-invoice", threshold=5_000_000)
        first = self._action("invoice-1", invoice="invoice-shared")
        guard.evaluate(RuntimeEvaluationRequest(policy=policy, action=first))
        guard.commit(policy.policy_id, policy.version, first.action_id)
        result = guard.evaluate(
            RuntimeEvaluationRequest(
                policy=policy,
                action=self._action(
                    "invoice-2", invoice="invoice-shared", purpose="different-purpose"
                ),
            )
        )
        return self._scenario(
            "duplicate-invoice",
            "One invoice cannot create two successful payments",
            "A new action ID reuses an already committed invoice",
            result.naive_gateway.decision,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
            {"invoice_id": "invoice-shared"},
        )

    def _approval_spoof(self, guard: RuntimeGuard) -> AdversarialScenarioResult:
        policy = self._policy("eval-approval")
        guard.evaluate(
            RuntimeEvaluationRequest(policy=policy, action=self._action("approval-1"))
        )
        spoofed = self._action("approval-2").model_copy(
            update={"approval_ids": ["approval-untrusted-string"]}
        )
        result = guard.evaluate(RuntimeEvaluationRequest(policy=policy, action=spoofed))
        return self._scenario(
            "approval-spoof",
            "Approvals are exact-action capabilities",
            "An agent submits an arbitrary approval-shaped string",
            result.naive_gateway.decision,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
            {"approval_count": 1, "valid_approval_count": 0},
        )

    def _authority_multiplication(
        self, guard: RuntimeGuard
    ) -> AdversarialScenarioResult:
        policy = self._policy("eval-delegation", threshold=5_000_000)
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        guard.evaluate_delegation(
            DelegationEvaluationRequest(
                policy=policy,
                grant=DelegationGrant(
                    grant_id="authority-1",
                    parent_agent_id="root-agent",
                    child_agent_id="buyer-a",
                    authority_limit=3_000_000,
                    expires_at=expiry,
                ),
            )
        )
        result = guard.evaluate_delegation(
            DelegationEvaluationRequest(
                policy=policy,
                grant=DelegationGrant(
                    grant_id="authority-2",
                    parent_agent_id="root-agent",
                    child_agent_id="buyer-b",
                    authority_limit=3_000_000,
                    expires_at=expiry,
                ),
            )
        )
        return self._scenario(
            "authority-multiplication",
            "Delegated authority cannot multiply",
            "Two sibling grants allocate 120% of parent authority",
            result.naive_gateway.decision,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
            {
                "parent_authority": result.parent_authority,
                "attempted_delegation": result.delegated_total,
            },
        )

    def _cumulative_refund(self, guard: RuntimeGuard) -> AdversarialScenarioResult:
        policy = self._policy("eval-refund", threshold=5_000_000)
        action = self._action("refund-payment")
        guard.evaluate(RuntimeEvaluationRequest(policy=policy, action=action))
        payments = PaymentExecutionService(guard, SimulatedRazorpayGateway())
        payments.create_order(
            OrderExecutionRequest(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                action_id=action.action_id,
            )
        )
        payments.confirm_payment(
            PaymentConfirmationRequest(
                policy_id=policy.policy_id,
                policy_version=policy.version,
                action_id=action.action_id,
                simulated_outcome="success",
            )
        )
        refunds = RefundService(guard)
        for sequence in (1, 2):
            result = refunds.evaluate(
                RefundEvaluationRequest(
                    policy_id=policy.policy_id,
                    policy_version=policy.version,
                    action_id=action.action_id,
                    refund_id=f"refund-{sequence}",
                    amount=540_000,
                    reason="adversarial evaluation",
                )
            )
        return self._scenario(
            "cumulative-refund",
            "Refunds cannot exceed captured funds",
            "Two individually valid refunds total 120% of capture",
            result.naive_gateway.decision,
            result.arthaniyam.decision,
            result.arthaniyam.reason_codes[0],
            {"captured": result.captured_amount, "attempted_refund": 1_080_000},
        )

    @staticmethod
    def _policy(
        policy_id: str,
        *,
        monthly: int = 5_000_000,
        threshold: int = 1_000_000,
    ) -> PolicyDefinition:
        return PolicyDefinition(
            policy_id=policy_id,
            version=1,
            name=f"Evaluation {policy_id}",
            currency="INR",
            budget={"monthly_limit": monthly, "per_transaction_limit": 1_000_000},
            approval={"required_above": threshold, "approver_count": 1},
            correlation={"window_hours": 24, "group_by": ["vendor", "purpose"]},
        )

    @staticmethod
    def _action(
        action_id: str,
        *,
        amount: int = 900_000,
        purpose: str = "laptops",
        invoice: str | None = None,
    ) -> FinancialAction:
        return FinancialAction(
            action_id=action_id,
            agent_id="root-agent",
            amount=amount,
            vendor_id="vendor-001",
            category="hardware",
            purpose=purpose,
            invoice_id=invoice or f"invoice-{action_id}",
            approval_ids=[],
        )

    @staticmethod
    def _scenario(
        scenario_id: str,
        invariant: str,
        attack: str,
        naive: str,
        arthaniyam: str,
        reason: str,
        evidence: dict[str, int | str | bool],
    ) -> AdversarialScenarioResult:
        return AdversarialScenarioResult(
            scenario_id=scenario_id,
            invariant=invariant,
            attack=attack,
            naive_decision=naive,
            arthaniyam_decision=arthaniyam,
            passed=naive == "allow" and arthaniyam in {"deny", "require_approval"},
            reason_code=reason,
            evidence=evidence,
        )

    @staticmethod
    def _evidence_hash(scenarios: list[AdversarialScenarioResult]) -> str:
        canonical = json.dumps(
            [scenario.model_dump(mode="json") for scenario in scenarios],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + sha256(canonical).hexdigest()
