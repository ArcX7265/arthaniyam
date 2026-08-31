from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from app.policy.models import PolicyDefinition
from app.runtime.models import (
    ActionTransitionResult, AuditEvent, DecisionDetail, FinancialAction,
    RuntimeComparison, RuntimeEvaluationRequest, RuntimeStateResponse, StateSnapshot,
)
from app.runtime.storage import SQLiteRuntimeRepository, StoredLedgerEntry


class RuntimeActionNotFoundError(KeyError):
    pass


class RuntimeTransitionError(ValueError):
    pass


class RuntimeGuard:
    """Stateful financial guard backed by a durable SQLite ledger."""

    def __init__(self, repository: SQLiteRuntimeRepository | None = None) -> None:
        self.repository = repository or SQLiteRuntimeRepository()
        self._lock = RLock()

    def reset(self) -> None:
        with self._lock:
            self.repository.reset()

    def evaluate(self, request: RuntimeEvaluationRequest) -> RuntimeComparison:
        with self._lock:
            policy, action = request.policy, request.action
            try:
                self.repository.register_policy(policy)
            except ValueError as exc:
                raise RuntimeTransitionError(str(exc)) from exc

            fingerprint = action.model_dump_json()
            prior = self.repository.get_evaluation(
                policy.policy_id, policy.version, action.action_id
            )
            if prior is not None:
                if prior.fingerprint == fingerprint:
                    event = self._event(
                        action.action_id, "idempotent_replay",
                        prior.response.arthaniyam.decision, ["IDEMPOTENT_REPLAY"],
                    )
                    self.repository.append_audit(policy.policy_id, policy.version, event)
                    return prior.response.model_copy(update={
                        "replayed": True,
                        "state": self._snapshot(policy),
                        "audit_event": event,
                    })
                return self._conflict_response(policy, action)

            naive = self._naive_decision(policy, action)
            arthaniyam, correlated_amount = self._arthaniyam_decision(policy, action)
            if arthaniyam.decision == "allow_and_reserve":
                self.repository.upsert_entry(
                    policy.policy_id, policy.version, action, "reserved",
                    datetime.now(timezone.utc),
                )
            event = self._event(
                action.action_id, "evaluation", arthaniyam.decision,
                arthaniyam.reason_codes,
            )
            self.repository.append_audit(policy.policy_id, policy.version, event)
            response = RuntimeComparison(
                action_id=action.action_id,
                naive_gateway=naive,
                arthaniyam=arthaniyam,
                correlated_amount=correlated_amount,
                state=self._snapshot(policy),
                audit_event=event,
            )
            self.repository.save_evaluation(
                policy.policy_id, policy.version, action.action_id,
                fingerprint, response,
            )
            return response

    def commit(self, policy_id: str, policy_version: int, action_id: str) -> ActionTransitionResult:
        with self._lock:
            policy = self._policy(policy_id, policy_version)
            entry = self.get_action_entry(policy_id, policy_version, action_id)
            if entry.status == "committed":
                raise RuntimeTransitionError("action is already committed")
            if entry.status != "reserved":
                raise RuntimeTransitionError("only reserved actions can be committed")
            self.repository.upsert_entry(
                policy_id, policy_version, entry.action, "committed", entry.created_at
            )
            event = self._event(
                action_id, "commit", "committed", ["RESERVATION_COMMITTED"]
            )
            self.repository.append_audit(policy_id, policy_version, event)
            return ActionTransitionResult(
                action_id=action_id, status="committed",
                state=self._snapshot(policy), audit_event=event,
            )

    def release(self, policy_id: str, policy_version: int, action_id: str) -> ActionTransitionResult:
        with self._lock:
            policy = self._policy(policy_id, policy_version)
            entry = self.get_action_entry(policy_id, policy_version, action_id)
            if entry.status != "reserved":
                raise RuntimeTransitionError("only reserved actions can be released")
            self.repository.upsert_entry(
                policy_id, policy_version, entry.action, "released", entry.created_at
            )
            event = self._event(
                action_id, "release", "released", ["RESERVATION_RELEASED"]
            )
            self.repository.append_audit(policy_id, policy_version, event)
            return ActionTransitionResult(
                action_id=action_id, status="released",
                state=self._snapshot(policy), audit_event=event,
            )

    def state(self, policy_id: str, policy_version: int) -> RuntimeStateResponse:
        with self._lock:
            policy = self._policy(policy_id, policy_version)
            entries = self.repository.list_entries(policy_id, policy_version)
            return RuntimeStateResponse(
                state=self._snapshot(policy),
                actions=[{
                    "action_id": entry.action.action_id,
                    "invoice_id": entry.action.invoice_id,
                    "amount": entry.action.amount,
                    "status": entry.status,
                } for entry in entries],
                audit_trail=self.repository.list_audit(policy_id, policy_version),
            )

    def get_action_entry(
        self, policy_id: str, policy_version: int, action_id: str
    ) -> StoredLedgerEntry:
        entry = self.repository.get_entry(policy_id, policy_version, action_id)
        if entry is None:
            raise RuntimeActionNotFoundError("runtime action was not found")
        return entry

    def _policy(self, policy_id: str, version: int) -> PolicyDefinition:
        policy = self.repository.get_policy(policy_id, version)
        if policy is None:
            raise RuntimeActionNotFoundError("policy runtime state was not found")
        return policy

    def _naive_decision(self, policy: PolicyDefinition, action: FinancialAction) -> DecisionDetail:
        reasons: list[str] = []
        if action.amount > policy.budget.per_transaction_limit:
            reasons.append("PER_TRANSACTION_LIMIT_EXCEEDED")
        if not self._vendor_allowed(policy, action):
            reasons.append("VENDOR_NOT_ALLOWED")
        if not self._category_allowed(policy, action):
            reasons.append("CATEGORY_NOT_ALLOWED")
        if reasons:
            return DecisionDetail(
                decision="deny", reason_codes=reasons,
                explanation="The current request fails a local gateway condition.",
            )
        return DecisionDetail(
            decision="allow", reason_codes=["LOCAL_CHECKS_PASSED"],
            explanation="Every request-local condition passes; no historical actions were checked.",
        )

    def _arthaniyam_decision(
        self, policy: PolicyDefinition, action: FinancialAction
    ) -> tuple[DecisionDetail, int]:
        if action.amount > policy.budget.per_transaction_limit:
            return self._deny("PER_TRANSACTION_LIMIT_EXCEEDED", "The payment exceeds the maximum allowed for one transaction."), action.amount
        if not self._vendor_allowed(policy, action):
            return self._deny("VENDOR_NOT_ALLOWED", "The vendor is outside the approved policy scope."), action.amount
        if not self._category_allowed(policy, action):
            return self._deny("CATEGORY_NOT_ALLOWED", "The payment category is outside the approved policy scope."), action.amount

        entries = self.repository.list_entries(policy.policy_id, policy.version)
        active = [entry for entry in entries if entry.status != "released"]
        if any(entry.action.invoice_id == action.invoice_id for entry in active):
            return self._deny("DUPLICATE_INVOICE", "This invoice already has an active reservation or committed payment."), action.amount
        if sum(entry.action.amount for entry in active) + action.amount > policy.budget.monthly_limit:
            return self._deny("BUDGET_EXCEEDED", "Committed spend plus active reservations would exceed the budget."), action.amount

        cutoff = datetime.now(timezone.utc) - timedelta(hours=policy.correlation.window_hours)
        correlated = [entry for entry in active if entry.created_at >= cutoff and self._correlation_key(policy, entry.action) == self._correlation_key(policy, action)]
        correlated_amount = action.amount + sum(entry.action.amount for entry in correlated)
        needs_approval = correlated_amount > policy.approval.required_above
        has_approval = len(action.approval_ids) >= policy.approval.approver_count
        if needs_approval and not has_approval:
            return DecisionDetail(
                decision="require_approval",
                reason_codes=["CORRELATED_APPROVAL_THRESHOLD_EXCEEDED"],
                explanation=(
                    f"Related active payments total INR {correlated_amount / 100:,.2f}, "
                    f"above the INR {policy.approval.required_above / 100:,.2f} approval threshold."
                ),
            ), correlated_amount
        reasons = ["POLICY_CHECKS_PASSED", "FUNDS_RESERVED_ATOMICALLY"]
        if needs_approval:
            reasons.append("REQUIRED_APPROVAL_PRESENT")
        return DecisionDetail(
            decision="allow_and_reserve", reason_codes=reasons,
            explanation="Cross-request policy checks passed and the amount was reserved atomically.",
        ), correlated_amount

    def _conflict_response(self, policy: PolicyDefinition, action: FinancialAction) -> RuntimeComparison:
        event = self._event(action.action_id, "evaluation", "deny", ["IDEMPOTENCY_KEY_CONFLICT"])
        self.repository.append_audit(policy.policy_id, policy.version, event)
        return RuntimeComparison(
            action_id=action.action_id,
            naive_gateway=DecisionDetail(
                decision="allow", reason_codes=["LOCAL_CHECKS_PASSED"],
                explanation="A stateless gateway does not remember the earlier action ID.",
            ),
            arthaniyam=self._deny("IDEMPOTENCY_KEY_CONFLICT", "The action ID was previously used with different action contents."),
            correlated_amount=action.amount,
            state=self._snapshot(policy), audit_event=event,
        )

    def _snapshot(self, policy: PolicyDefinition) -> StateSnapshot:
        entries = self.repository.list_entries(policy.policy_id, policy.version)
        reserved = [entry for entry in entries if entry.status == "reserved"]
        committed = [entry for entry in entries if entry.status == "committed"]
        reserved_amount = sum(entry.action.amount for entry in reserved)
        committed_amount = sum(entry.action.amount for entry in committed)
        return StateSnapshot(
            policy_id=policy.policy_id, policy_version=policy.version,
            reserved_amount=reserved_amount, committed_amount=committed_amount,
            available_budget=max(0, policy.budget.monthly_limit - reserved_amount - committed_amount),
            active_reservations=len(reserved), committed_actions=len(committed),
            audit_events=len(self.repository.list_audit(policy.policy_id, policy.version)),
        )

    @staticmethod
    def _vendor_allowed(policy: PolicyDefinition, action: FinancialAction) -> bool:
        allowlist = policy.vendors.allowed_vendor_ids
        return not (policy.vendors.require_approved_vendor and allowlist and action.vendor_id not in allowlist)

    @staticmethod
    def _category_allowed(policy: PolicyDefinition, action: FinancialAction) -> bool:
        categories = policy.vendors.allowed_categories
        return not categories or action.category in categories

    @staticmethod
    def _correlation_key(policy: PolicyDefinition, action: FinancialAction) -> tuple[str, ...]:
        values = {"vendor": action.vendor_id, "purpose": action.purpose, "invoice": action.invoice_id}
        return tuple(values[key] for key in policy.correlation.group_by)

    @staticmethod
    def _deny(code: str, explanation: str) -> DecisionDetail:
        return DecisionDetail(decision="deny", reason_codes=[code], explanation=explanation)

    @staticmethod
    def _event(action_id: str, event_type: str, decision: str, reason_codes: list[str]) -> AuditEvent:
        return AuditEvent(
            event_id=str(uuid4()), action_id=action_id, event_type=event_type,
            decision=decision, occurred_at=datetime.now(timezone.utc),
            reason_codes=reason_codes,
        )


runtime_guard = RuntimeGuard()
