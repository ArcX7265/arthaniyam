from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from app.approvals.binding import approval_binding
from app.approvals.models import (
    ApprovalChallenge,
    ApprovalChallengeRequest,
    ApprovalDecisionRequest,
    ApprovalGrant,
)
from app.runtime.guard import RuntimeActionNotFoundError, RuntimeGuard, RuntimeTransitionError
from app.runtime.models import AuditEvent, FinancialAction


class ApprovalService:
    def __init__(self, guard: RuntimeGuard, lifetime_minutes: int = 15) -> None:
        self.guard = guard
        self.lifetime_minutes = lifetime_minutes

    def create_challenge(self, request: ApprovalChallengeRequest) -> ApprovalChallenge:
        repository = self.guard.repository
        existing = repository.find_approval_challenge(
            request.policy_id, request.policy_version, request.action_id
        )
        if existing:
            return self._challenge(existing)
        evaluation = repository.get_evaluation(
            request.policy_id, request.policy_version, request.action_id
        )
        if evaluation is None:
            raise RuntimeActionNotFoundError("approval action evaluation was not found")
        if evaluation.response.arthaniyam.decision != "require_approval":
            raise RuntimeTransitionError("only a require_approval decision can create a challenge")
        action = FinancialAction.model_validate_json(evaluation.fingerprint)
        policy = repository.get_policy(request.policy_id, request.policy_version)
        if policy is None:
            raise RuntimeActionNotFoundError("approval policy was not found")
        now = datetime.now(timezone.utc)
        challenge = ApprovalChallenge(
            challenge_id=str(uuid4()),
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            action_id=request.action_id,
            amount=action.amount,
            vendor_id=action.vendor_id,
            purpose=action.purpose,
            binding_hash=approval_binding(request.policy_id, request.policy_version, action),
            required_approvers=policy.approval.approver_count,
            status="pending",
            created_at=now,
            expires_at=now + timedelta(minutes=self.lifetime_minutes),
            grants=[],
        )
        repository.save_approval_challenge(challenge.model_dump_json())
        repository.append_audit(
            request.policy_id,
            request.policy_version,
            AuditEvent(
                event_id=str(uuid4()),
                action_id=request.action_id,
                event_type="approval_challenge",
                decision="pending",
                occurred_at=now,
                reason_codes=["ACTION_BOUND_APPROVAL_REQUIRED"],
            ),
        )
        return challenge

    def decide(
        self, challenge_id: str, request: ApprovalDecisionRequest
    ) -> ApprovalChallenge:
        stored = self.guard.repository.get_approval_challenge(challenge_id)
        if stored is None:
            raise RuntimeActionNotFoundError("approval challenge was not found")
        challenge = self._challenge(stored)
        now = datetime.now(timezone.utc)
        if challenge.status == "expired":
            return challenge
        if challenge.status in {"rejected", "consumed"}:
            raise RuntimeTransitionError(f"approval challenge is already {challenge.status}")
        if challenge.status == "approved":
            return challenge
        if request.decision == "reject":
            rejected = challenge.model_copy(update={"status": "rejected"})
            self.guard.repository.update_approval_challenge(rejected.model_dump_json())
            return rejected
        if any(grant.approver_id == request.approver_id for grant in challenge.grants):
            return challenge

        approval_id = "approval_" + sha256(
            f"{challenge_id}|{request.approver_id}".encode("utf-8")
        ).hexdigest()[:24]
        grants = [
            *challenge.grants,
            ApprovalGrant(
                approval_id=approval_id,
                approver_id=request.approver_id,
                granted_at=now,
            ),
        ]
        status = "approved" if len(grants) >= challenge.required_approvers else "pending"
        approved = challenge.model_copy(update={"grants": grants, "status": status})
        self.guard.repository.update_approval_challenge(approved.model_dump_json())
        self.guard.repository.save_approval_grant(
            approval_id,
            challenge_id,
            challenge.binding_hash,
            request.approver_id,
            challenge.expires_at,
        )
        self.guard.repository.append_audit(
            challenge.policy_id,
            challenge.policy_version,
            AuditEvent(
                event_id=str(uuid4()),
                action_id=challenge.action_id,
                event_type="approval_granted",
                decision=status,
                occurred_at=now,
                reason_codes=["APPROVAL_BOUND_TO_EXACT_ACTION", "APPROVER_RECORDED"],
            ),
        )
        return approved

    def _challenge(self, value: str) -> ApprovalChallenge:
        challenge = ApprovalChallenge.model_validate_json(value)
        if (
            challenge.status in {"pending", "approved"}
            and challenge.expires_at <= datetime.now(timezone.utc)
        ):
            challenge = challenge.model_copy(update={"status": "expired"})
            self.guard.repository.update_approval_challenge(challenge.model_dump_json())
        return challenge
