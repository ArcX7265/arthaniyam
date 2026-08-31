from typing import Literal

from app.policy.models import StrictModel


class OrderExecutionRequest(StrictModel):
    policy_id: str
    policy_version: int = 1
    action_id: str


class ProviderOrder(StrictModel):
    provider: Literal["razorpay", "simulator"]
    mode: Literal["test", "simulate"]
    order_id: str
    amount: int
    currency: Literal["INR"] = "INR"
    receipt: str
    status: str


class OrderExecutionResult(StrictModel):
    policy_id: str
    policy_version: int
    action_id: str
    reservation_status: Literal["reserved"]
    replayed: bool = False
    order: ProviderOrder
