from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from uuid import uuid4

from app.payments.models import (
    OrderExecutionResult,
    PaymentConfirmationResult,
    WebhookResult,
)
from app.runtime.guard import RuntimeGuard, RuntimeTransitionError
from app.runtime.models import AuditEvent


class WebhookVerificationError(ValueError):
    pass


class RazorpayWebhookService:
    def __init__(self, guard: RuntimeGuard, webhook_secret: str) -> None:
        self.guard = guard
        self.webhook_secret = webhook_secret

    def process(
        self, raw_body: bytes, signature: str, event_id: str
    ) -> WebhookResult:
        expected = hmac.new(
            self.webhook_secret.encode("utf-8"), raw_body, sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookVerificationError("Razorpay webhook signature is invalid")

        repository = self.guard.repository
        if repository.has_webhook(event_id):
            return WebhookResult(
                event_id=event_id, event_type="duplicate", status="duplicate"
            )
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise WebhookVerificationError("Webhook body is not valid JSON") from exc
        event_type = body.get("event", "unknown")
        payload = body.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payload.get("order_id")
        payment_id = payload.get("id")
        matched = repository.find_execution_by_order(order_id) if order_id else None

        action_id: str | None = None
        if matched and event_type in {"payment.captured", "payment.failed"}:
            policy_id, version, action_id, execution_json = matched
            execution = OrderExecutionResult.model_validate_json(execution_json)
            entry = self.guard.get_action_entry(policy_id, version, action_id)
            prior = repository.get_confirmation(policy_id, version, action_id)
            if prior is None and entry.status == "reserved":
                if event_type == "payment.captured":
                    if (
                        payload.get("amount") != entry.action.amount
                        or payload.get("currency") != "INR"
                        or execution.order.order_id != order_id
                    ):
                        raise RuntimeTransitionError(
                            "Webhook payment does not match the reserved action"
                        )
                    self.guard.commit(policy_id, version, action_id)
                    confirmation = PaymentConfirmationResult(
                        policy_id=policy_id,
                        policy_version=version,
                        action_id=action_id,
                        order_id=order_id,
                        payment_id=payment_id,
                        provider="razorpay",
                        status="verified_and_committed",
                        signature_verified=True,
                    )
                else:
                    self.guard.release(policy_id, version, action_id)
                    confirmation = PaymentConfirmationResult(
                        policy_id=policy_id,
                        policy_version=version,
                        action_id=action_id,
                        order_id=order_id,
                        payment_id=payment_id,
                        provider="razorpay",
                        status="failed_and_released",
                        signature_verified=True,
                    )
                repository.save_confirmation(
                    policy_id, version, action_id, confirmation.model_dump_json()
                )
            repository.append_audit(
                policy_id,
                version,
                AuditEvent(
                    event_id=str(uuid4()),
                    action_id=action_id,
                    event_type="webhook_received",
                    decision=event_type,
                    occurred_at=datetime.now(timezone.utc),
                    reason_codes=["WEBHOOK_SIGNATURE_VERIFIED", "EVENT_ID_RECORDED"],
                ),
            )

        repository.save_webhook(
            event_id,
            event_type,
            sha256(raw_body).hexdigest(),
            datetime.now(timezone.utc),
        )
        return WebhookResult(
            event_id=event_id,
            event_type=event_type,
            status="processed" if matched else "ignored",
            action_id=action_id,
        )
