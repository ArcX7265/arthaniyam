from datetime import datetime, timezone
from hashlib import sha256
from threading import RLock
from uuid import uuid4

from app.payments.models import (
    PaymentConfirmationResult,
    RefundEvaluationRequest,
    RefundEvaluationResult,
)
from app.runtime.guard import RuntimeActionNotFoundError, RuntimeGuard, RuntimeTransitionError
from app.runtime.models import AuditEvent, DecisionDetail


class RefundService:
    """Conserve captured funds across cumulative simulator refunds."""

    def __init__(self, guard: RuntimeGuard) -> None:
        self.guard = guard
        self._lock = RLock()

    def evaluate(self, request: RefundEvaluationRequest) -> RefundEvaluationResult:
        with self._lock:
            repository = self.guard.repository
            fingerprint = request.model_dump_json()
            prior = repository.get_refund(
                request.policy_id,
                request.policy_version,
                request.refund_id,
            )
            if prior is not None:
                prior_fingerprint, result_json = prior
                if prior_fingerprint != fingerprint:
                    raise RuntimeTransitionError(
                        "refund_id was already used with different contents"
                    )
                return RefundEvaluationResult.model_validate_json(result_json).model_copy(
                    update={"replayed": True}
                )

            confirmation_json = repository.get_confirmation(
                request.policy_id,
                request.policy_version,
                request.action_id,
            )
            if confirmation_json is None:
                raise RuntimeActionNotFoundError("captured payment confirmation was not found")
            confirmation = PaymentConfirmationResult.model_validate_json(confirmation_json)
            if confirmation.status != "verified_and_committed":
                raise RuntimeTransitionError("only a captured payment can be refunded")
            entry = self.guard.get_action_entry(
                request.policy_id,
                request.policy_version,
                request.action_id,
            )
            captured = entry.action.amount
            refunded_before = repository.executed_refund_total(
                request.policy_id,
                request.policy_version,
                request.action_id,
            )
            naive = (
                DecisionDetail(
                    decision="allow",
                    reason_codes=["INDIVIDUAL_REFUND_FITS_CAPTURE"],
                    explanation="This refund alone does not exceed the captured payment.",
                )
                if request.amount <= captured
                else DecisionDetail(
                    decision="deny",
                    reason_codes=["INDIVIDUAL_REFUND_EXCEEDS_CAPTURE"],
                    explanation="This refund alone exceeds the captured payment.",
                )
            )
            attempted_total = refunded_before + request.amount
            if attempted_total > captured:
                arthaniyam = DecisionDetail(
                    decision="deny",
                    reason_codes=["CUMULATIVE_REFUND_EXCEEDS_CAPTURE"],
                    explanation="Prior successful refunds plus this request exceed captured funds.",
                )
            else:
                arthaniyam = DecisionDetail(
                    decision="allow",
                    reason_codes=["CAPTURED_FUNDS_CONSERVED", "REFUND_RECORDED"],
                    explanation="The cumulative refund remains within captured funds.",
                )

            executed = arthaniyam.decision == "allow"
            refunded_after = attempted_total if executed else refunded_before
            provider_refund_id = (
                "rfnd_sim_"
                + sha256(
                    f"{confirmation.payment_id}|{request.refund_id}".encode("utf-8")
                ).hexdigest()[:18]
                if executed
                else None
            )
            result = RefundEvaluationResult(
                policy_id=request.policy_id,
                policy_version=request.policy_version,
                action_id=request.action_id,
                refund_id=request.refund_id,
                provider_refund_id=provider_refund_id,
                status="executed" if executed else "denied",
                naive_gateway=naive,
                arthaniyam=arthaniyam,
                captured_amount=captured,
                refunded_before=refunded_before,
                refunded_after=refunded_after,
                remaining_refundable=captured - refunded_after,
            )
            repository.save_refund(
                request.policy_id,
                request.policy_version,
                request.action_id,
                request.refund_id,
                request.amount,
                result.status,
                fingerprint,
                result.model_dump_json(),
            )
            repository.append_audit(
                request.policy_id,
                request.policy_version,
                AuditEvent(
                    event_id=str(uuid4()),
                    action_id=request.action_id,
                    event_type="refund_executed" if executed else "refund_denied",
                    decision=result.status,
                    occurred_at=datetime.now(timezone.utc),
                    reason_codes=arthaniyam.reason_codes,
                ),
            )
            return result
