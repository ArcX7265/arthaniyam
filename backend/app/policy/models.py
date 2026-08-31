from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Currency = Literal["INR"]
CorrelationKey = Literal["vendor", "purpose", "invoice"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BudgetPolicy(StrictModel):
    monthly_limit: int = Field(gt=0, description="Monthly budget in paise")
    per_transaction_limit: int = Field(gt=0, description="Single-action limit in paise")

    @model_validator(mode="after")
    def transaction_limit_fits_budget(self) -> "BudgetPolicy":
        if self.per_transaction_limit > self.monthly_limit:
            raise ValueError("per_transaction_limit cannot exceed monthly_limit")
        return self


class ApprovalPolicy(StrictModel):
    required_above: int = Field(ge=0, description="Approval threshold in paise")
    approver_count: int = Field(default=1, ge=1, le=5)


class VendorPolicy(StrictModel):
    require_approved_vendor: bool = True
    allowed_vendor_ids: list[str] = Field(default_factory=list)
    allowed_categories: list[str] = Field(default_factory=list)


class DelegationPolicy(StrictModel):
    enabled: bool = True
    conserve_authority: bool = True
    maximum_depth: int = Field(default=3, ge=0, le=10)


class CorrelationPolicy(StrictModel):
    window_hours: int = Field(default=24, ge=1, le=720)
    group_by: list[CorrelationKey] = Field(default_factory=lambda: ["vendor", "purpose"])


class PolicyDefinition(StrictModel):
    policy_id: str = Field(min_length=3, max_length=100)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=3, max_length=200)
    currency: Currency = "INR"
    budget: BudgetPolicy
    approval: ApprovalPolicy
    vendors: VendorPolicy = Field(default_factory=VendorPolicy)
    delegation: DelegationPolicy = Field(default_factory=DelegationPolicy)
    correlation: CorrelationPolicy = Field(default_factory=CorrelationPolicy)

    @model_validator(mode="after")
    def approval_threshold_fits_budget(self) -> "PolicyDefinition":
        if self.approval.required_above > self.budget.monthly_limit:
            raise ValueError("approval threshold cannot exceed monthly budget")
        return self

