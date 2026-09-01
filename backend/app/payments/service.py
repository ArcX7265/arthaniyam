from datetime import datetime, timezone
from hashlib import sha256
import hmac
from uuid import uuid4

from app.payments.gateway import PaymentGateway
from app.payments.models import (
    OrderExecutionRequest,
    OrderExecutionResult,
    PaymentConfirmationRequest,
    PaymentConfirmationResult,
)
from app.runtime.guard import RuntimeGuard, RuntimeTransitionError
from app.runtime.models import AuditEvent


class PaymentExecutionService:
    def __init__(
        self,
        guard: RuntimeGuard,
        gateway: PaymentGateway,
        key_secret: str | None = None,
        key_id: str | None = None,
    ) -> None:
        self.guard = guard
        self.gateway = gateway
        self.key_secret = key_secret
        self.key_id = key_id

    def create_order(self, request: OrderExecutionRequest) -> OrderExecutionResult:
        repository = self.guard.repository
        prior = repository.get_execution(
            request.policy_id, request.policy_version, request.action_id
        )
        if prior is not None:
            result = OrderExecutionResult.model_validate_json(prior)
            return result.model_copy(update={"replayed": True})

        entry = self.guard.get_action_entry(
            request.policy_id, request.policy_version, request.action_id
        )
        if entry.status != "reserved":
            raise RuntimeTransitionError(
                "only a policy-approved active reservation can create an order"
            )

        order = self.gateway.create_order(entry.action)
        if order.amount != entry.action.amount or order.currency != "INR":
            raise RuntimeTransitionError(
                "provider order does not match the reserved amount and currency"
            )
        result = OrderExecutionResult(
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            action_id=request.action_id,
            reservation_status="reserved",
            checkout_key_id=self.key_id if order.provider == "razorpay" else None,
            order=order,
        )
        repository.save_execution(
            request.policy_id,
            request.policy_version,
            request.action_id,
            order.order_id,
            result.model_dump_json(),
        )
        repository.append_audit(
            request.policy_id,
            request.policy_version,
            AuditEvent(
                event_id=str(uuid4()),
                action_id=request.action_id,
                event_type="order_created",
                decision=order.status,
                occurred_at=datetime.now(timezone.utc),
                reason_codes=[
                    "POLICY_RESERVATION_VERIFIED",
                    f"PROVIDER_{order.provider.upper()}",
                ],
            ),
        )
        return result

    def confirm_payment(
        self, request: PaymentConfirmationRequest
    ) -> PaymentConfirmationResult:
        repository = self.guard.repository
        prior = repository.get_confirmation(
            request.policy_id, request.policy_version, request.action_id
        )
        if prior is not None:
            return PaymentConfirmationResult.model_validate_json(prior).model_copy(
                update={"replayed": True}
            )

        execution_json = repository.get_execution(
            request.policy_id, request.policy_version, request.action_id
        )
        if execution_json is None:
            raise RuntimeTransitionError("provider order was not created for this action")
        execution = OrderExecutionResult.model_validate_json(execution_json)
        entry = self.guard.get_action_entry(
            request.policy_id, request.policy_version, request.action_id
        )
        if entry.status != "reserved":
            raise RuntimeTransitionError("payment confirmation requires an active reservation")

        if execution.order.provider == "simulator":
            if request.simulated_outcome not in {"success", "failure"}:
                raise RuntimeTransitionError(
                    "simulated_outcome is required for simulator confirmation"
                )
            payment_id = "pay_sim_" + sha256(
                execution.order.order_id.encode("utf-8")
            ).hexdigest()[:18]
            if request.simulated_outcome == "success":
                self.guard.commit(
                    request.policy_id, request.policy_version, request.action_id
                )
                result = PaymentConfirmationResult(
                    policy_id=request.policy_id,
                    policy_version=request.policy_version,
                    action_id=request.action_id,
                    order_id=execution.order.order_id,
                    payment_id=payment_id,
                    provider="simulator",
                    status="verified_and_committed",
                    signature_verified=True,
                )
                event_type, reason = "payment_verified", "SIMULATED_PAYMENT_CAPTURED"
            else:
                self.guard.release(
                    request.policy_id, request.policy_version, request.action_id
                )
                result = PaymentConfirmationResult(
                    policy_id=request.policy_id,
                    policy_version=request.policy_version,
                    action_id=request.action_id,
                    order_id=execution.order.order_id,
                    payment_id=payment_id,
                    provider="simulator",
                    status="failed_and_released",
                    signature_verified=True,
                )
                event_type, reason = "payment_failed", "SIMULATED_PAYMENT_FAILED"
            return self._store_confirmation(result, event_type, reason)

        if not all(
            [request.razorpay_payment_id, request.razorpay_order_id, request.razorpay_signature]
        ):
            raise RuntimeTransitionError("Razorpay payment ID, order ID, and signature are required")
        if request.razorpay_order_id != execution.order.order_id:
            raise RuntimeTransitionError("Checkout order does not match the stored provider order")
        if not self.key_secret:
            raise RuntimeTransitionError("Razorpay key secret is not configured")
        message = f"{execution.order.order_id}|{request.razorpay_payment_id}".encode()
        expected = hmac.new(self.key_secret.encode(), message, sha256).hexdigest()
        if not hmac.compare_digest(expected, request.razorpay_signature):
            raise RuntimeTransitionError("Razorpay payment signature is invalid")

        payment = self.gateway.fetch_payment(request.razorpay_payment_id)
        if (
            payment.order_id != execution.order.order_id
            or payment.amount != entry.action.amount
            or payment.currency != "INR"
        ):
            raise RuntimeTransitionError("Razorpay payment does not match the reservation")
        if payment.status != "captured":
            return PaymentConfirmationResult(
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                action_id=request.action_id,
                order_id=execution.order.order_id,
                payment_id=payment.payment_id,
                provider="razorpay",
                status="pending",
                signature_verified=True,
            )
        self.guard.commit(request.policy_id, request.policy_version, request.action_id)
        result = PaymentConfirmationResult(
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            action_id=request.action_id,
            order_id=execution.order.order_id,
            payment_id=payment.payment_id,
            provider="razorpay",
            status="verified_and_committed",
            signature_verified=True,
        )
        return self._store_confirmation(
            result, "payment_verified", "RAZORPAY_CAPTURE_VERIFIED"
        )

    def _store_confirmation(
        self,
        result: PaymentConfirmationResult,
        event_type: str,
        reason: str,
    ) -> PaymentConfirmationResult:
        repository = self.guard.repository
        repository.save_confirmation(
            result.policy_id,
            result.policy_version,
            result.action_id,
            result.model_dump_json(),
        )
        repository.append_audit(
            result.policy_id,
            result.policy_version,
            AuditEvent(
                event_id=str(uuid4()),
                action_id=result.action_id,
                event_type=event_type,
                decision=result.status,
                occurred_at=datetime.now(timezone.utc),
                reason_codes=[reason, "RESERVATION_STATE_FINALIZED"],
            ),
        )
        return result
