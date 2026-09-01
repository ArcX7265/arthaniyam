from datetime import datetime
from typing import Literal

from pydantic import Field

from app.policy.models import StrictModel


class ApprovalChallengeRequest(StrictModel):
    policy_id: str = Field(min_length=3, max_length=100)
    policy_version: int = Field(default=1, ge=1)
    action_id: str = Field(min_length=3, max_length=120)


class ApprovalDecisionRequest(StrictModel):
    approver_id: str = Field(min_length=3, max_length=120)
    decision: Literal["approve", "reject"]


class ApprovalGrant(StrictModel):
    approval_id: str
    approver_id: str
    granted_at: datetime


class ApprovalChallenge(StrictModel):
    challenge_id: str
    policy_id: str
    policy_version: int
    action_id: str
    amount: int
    vendor_id: str
    purpose: str
    binding_hash: str
    required_approvers: int
    status: Literal["pending", "approved", "rejected", "expired", "consumed"]
    created_at: datetime
    expires_at: datetime
    grants: list[ApprovalGrant]
