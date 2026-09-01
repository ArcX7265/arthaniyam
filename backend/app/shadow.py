from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import mkstemp
from typing import Literal
from uuid import uuid4

from pydantic import Field

from app.policy.models import PolicyDefinition, StrictModel
from app.runtime.guard import RuntimeGuard
from app.runtime.models import FinancialAction, RuntimeEvaluationRequest, StateSnapshot
from app.runtime.storage import SQLiteRuntimeRepository


NormalizedDecision = Literal["allow", "review", "deny"]


class PolicyImpactRequest(StrictModel):
    current_policy: PolicyDefinition
    candidate_policy: PolicyDefinition
    actions: list[FinancialAction] = Field(min_length=1, max_length=200)


class ActionPolicyImpact(StrictModel):
    action_id: str
    amount: int
    current_decision: NormalizedDecision
    candidate_decision: NormalizedDecision
    transition: str
    changed: bool
    current_reason_codes: list[str]
    candidate_reason_codes: list[str]


class PolicyImpactReport(StrictModel):
    simulation_id: str
    created_at: datetime
    total_actions: int
    unchanged_actions: int
    escalated_actions: int
    relaxed_actions: int
    new_reviews: int
    new_denials: int
    current_final_state: StateSnapshot
    candidate_final_state: StateSnapshot
    evidence_hash: str
    impacts: list[ActionPolicyImpact]


class PolicyImpactService:
    """Replay one action stream in two isolated policy worlds."""

    def __init__(self, repository: SQLiteRuntimeRepository) -> None:
        self.repository = repository

    def simulate(self, request: PolicyImpactRequest) -> PolicyImpactReport:
        current_path = self._temporary_database("current")
        candidate_path = self._temporary_database("candidate")
        try:
            current_guard = RuntimeGuard(SQLiteRuntimeRepository(current_path))
            candidate_guard = RuntimeGuard(SQLiteRuntimeRepository(candidate_path))
            impacts: list[ActionPolicyImpact] = []
            for action in request.actions:
                current = current_guard.evaluate(
                    RuntimeEvaluationRequest(
                        policy=request.current_policy,
                        action=action,
                    )
                )
                candidate = candidate_guard.evaluate(
                    RuntimeEvaluationRequest(
                        policy=request.candidate_policy,
                        action=action,
                    )
                )
                current_decision = self._normalize(current.arthaniyam.decision)
                candidate_decision = self._normalize(candidate.arthaniyam.decision)
                impacts.append(
                    ActionPolicyImpact(
                        action_id=action.action_id,
                        amount=action.amount,
                        current_decision=current_decision,
                        candidate_decision=candidate_decision,
                        transition=f"{current_decision}_to_{candidate_decision}",
                        changed=current_decision != candidate_decision,
                        current_reason_codes=current.arthaniyam.reason_codes,
                        candidate_reason_codes=candidate.arthaniyam.reason_codes,
                    )
                )
            current_state = current_guard.state(
                request.current_policy.policy_id,
                request.current_policy.version,
            ).state
            candidate_state = candidate_guard.state(
                request.candidate_policy.policy_id,
                request.candidate_policy.version,
            ).state
        finally:
            self._remove_database(current_path)
            self._remove_database(candidate_path)

        rank = {"allow": 0, "review": 1, "deny": 2}
        escalated = sum(
            rank[impact.candidate_decision] > rank[impact.current_decision]
            for impact in impacts
        )
        relaxed = sum(
            rank[impact.candidate_decision] < rank[impact.current_decision]
            for impact in impacts
        )
        report = PolicyImpactReport(
            simulation_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            total_actions=len(impacts),
            unchanged_actions=sum(not impact.changed for impact in impacts),
            escalated_actions=escalated,
            relaxed_actions=relaxed,
            new_reviews=sum(
                impact.candidate_decision == "review"
                and impact.current_decision != "review"
                for impact in impacts
            ),
            new_denials=sum(
                impact.candidate_decision == "deny"
                and impact.current_decision != "deny"
                for impact in impacts
            ),
            current_final_state=current_state,
            candidate_final_state=candidate_state,
            evidence_hash=self._hash(request, impacts),
            impacts=impacts,
        )
        self.repository.save_policy_impact(
            report.simulation_id,
            report.model_dump_json(),
            report.evidence_hash,
            report.created_at,
        )
        return report

    def get(self, simulation_id: str) -> PolicyImpactReport:
        stored = self.repository.get_policy_impact(simulation_id)
        if stored is None:
            raise KeyError(simulation_id)
        return PolicyImpactReport.model_validate_json(stored)

    @staticmethod
    def _normalize(decision: str) -> NormalizedDecision:
        if decision == "allow_and_reserve" or decision == "allow":
            return "allow"
        if decision == "require_approval":
            return "review"
        return "deny"

    @staticmethod
    def _temporary_database(label: str) -> Path:
        descriptor, name = mkstemp(
            prefix=f"arthaniyam-shadow-{label}-", suffix=".sqlite3"
        )
        os.close(descriptor)
        return Path(name)

    @staticmethod
    def _remove_database(path: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    @staticmethod
    def _hash(
        request: PolicyImpactRequest,
        impacts: list[ActionPolicyImpact],
    ) -> str:
        canonical = json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "impacts": [impact.model_dump(mode="json") for impact in impacts],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + sha256(canonical).hexdigest()
