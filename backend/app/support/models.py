from typing import Literal

from pydantic import Field

from app.policy.models import StrictModel


class ComplaintRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=100)
    payment_id: str = Field(min_length=3, max_length=100)
    kind: Literal["duplicate_payment", "cancelled_order", "refund_request", "refund_status", "other"]
    amount: int | None = Field(default=None, strict=True, gt=0, le=100_000_000)
    message: str = Field(min_length=3, max_length=2000)


class ApprovalRequest(StrictModel):
    evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=3, max_length=80)


class TakeoverRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class InvestigationRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=100)
    message: str = Field(min_length=3, max_length=2000)
    payment_id: str | None = Field(default=None, min_length=3, max_length=100)
    amount: int | None = Field(default=None, strict=True, gt=0, le=100_000_000)


class InformationRequest(InvestigationRequest):
    """Typed operator input, never model-supplied authority."""


class ConfirmProposalRequest(StrictModel):
    proposal_id: str = Field(min_length=8, max_length=100)
