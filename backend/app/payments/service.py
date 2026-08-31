from datetime import datetime, timezone
from uuid import uuid4

from app.payments.gateway import PaymentGateway
from app.payments.models import OrderExecutionRequest, OrderExecutionResult
from app.runtime.guard import RuntimeGuard, RuntimeTransitionError
from app.runtime.models import AuditEvent


class PaymentExecutionService:
    def __init__(self, guard: RuntimeGuard, gateway: PaymentGateway) -> None:
        self.guard = guard
        self.gateway = gateway

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
            order=order,
        )
        repository.save_execution(
            request.policy_id,
            request.policy_version,
            request.action_id,
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
