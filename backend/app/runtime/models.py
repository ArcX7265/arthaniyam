from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.policy.models import PolicyDefinition, StrictModel


NaiveDecision = Literal["allow", "deny"]
ArthaniyamDecision = Literal["allow_and_reserve", "require_approval", "deny"]
ActionStatus = Literal["reserved", "committed", "released"]


class FinancialAction(StrictModel):
    action_id: str = Field(min_length=3, max_length=120)
    agent_id: str = Field(min_length=3, max_length=120)
    amount: int = Field(gt=0, description="Payment amount in paise")
    vendor_id: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=240)
    invoice_id: str = Field(min_length=1, max_length=120)
    approval_ids: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def approvals_must_be_unique(self) -> "FinancialAction":
        if len(self.approval_ids) != len(set(self.approval_ids)):
            raise ValueError("approval_ids must be unique")
        return self


class RuntimeEvaluationRequest(StrictModel):
    policy: PolicyDefinition
    action: FinancialAction


class DecisionDetail(StrictModel):
    decision: NaiveDecision | ArthaniyamDecision
    reason_codes: list[str]
    explanation: str


class StateSnapshot(StrictModel):
    policy_id: str
    policy_version: int
    reserved_amount: int
    committed_amount: int
    available_budget: int
    active_reservations: int
    committed_actions: int
    audit_events: int


class AuditEvent(StrictModel):
    event_id: str
    action_id: str
    event_type: Literal[
        "evaluation", "idempotent_replay", "commit", "release", "order_created",
        "payment_verified", "payment_failed", "webhook_received"
    ]
    decision: str
    occurred_at: datetime
    reason_codes: list[str]


class RuntimeComparison(StrictModel):
    action_id: str
    naive_gateway: DecisionDetail
    arthaniyam: DecisionDetail
    correlated_amount: int
    replayed: bool = False
    state: StateSnapshot
    audit_event: AuditEvent


class ActionTransitionRequest(StrictModel):
    policy_id: str = Field(min_length=3, max_length=100)
    policy_version: int = Field(default=1, ge=1)


class ActionTransitionResult(StrictModel):
    action_id: str
    status: ActionStatus
    state: StateSnapshot
    audit_event: AuditEvent


class RuntimeStateResponse(StrictModel):
    state: StateSnapshot
    actions: list[dict[str, str | int]]
    audit_trail: list[AuditEvent]
