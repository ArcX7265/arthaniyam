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


class ProviderPayment(StrictModel):
    payment_id: str
    order_id: str
    amount: int
    currency: Literal["INR"] = "INR"
    status: str


class OrderExecutionResult(StrictModel):
    policy_id: str
    policy_version: int
    action_id: str
    reservation_status: Literal["reserved"]
    replayed: bool = False
    checkout_key_id: str | None = None
    order: ProviderOrder


class PaymentConfirmationRequest(StrictModel):
    policy_id: str
    policy_version: int = 1
    action_id: str
    razorpay_payment_id: str | None = None
    razorpay_order_id: str | None = None
    razorpay_signature: str | None = None
    simulated_outcome: Literal["success", "failure"] | None = None


class PaymentConfirmationResult(StrictModel):
    policy_id: str
    policy_version: int
    action_id: str
    order_id: str
    payment_id: str
    provider: Literal["razorpay", "simulator"]
    status: Literal["verified_and_committed", "failed_and_released", "pending"]
    signature_verified: bool
    replayed: bool = False


class WebhookResult(StrictModel):
    event_id: str
    event_type: str
    status: Literal["processed", "duplicate", "ignored"]
    action_id: str | None = None
