from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from app.policy.models import PolicyDefinition
from app.runtime.models import (
    ActionTransitionResult,
    AuditEvent,
    DecisionDetail,
    FinancialAction,
    RuntimeComparison,
    RuntimeEvaluationRequest,
    RuntimeStateResponse,
    StateSnapshot,
)


class RuntimeActionNotFoundError(KeyError):
    pass


class RuntimeTransitionError(ValueError):
    pass


@dataclass
class LedgerEntry:
    action: FinancialAction
    status: str
    created_at: datetime


@dataclass
class EvaluationRecord:
    fingerprint: str
    response: RuntimeComparison


class RuntimeGuard:
    """In-memory reference guard used by the buildathon prototype.

    The lock makes the budget check and reservation one atomic operation. A
    production adapter can replace the in-memory maps with a transactional
    database without changing the API contract.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._policies: dict[tuple[str, int], PolicyDefinition] = {}
        self._ledger: dict[tuple[str, int], dict[str, LedgerEntry]] = {}
        self._evaluations: dict[tuple[str, int], dict[str, EvaluationRecord]] = {}
        self._audit: dict[tuple[str, int], list[AuditEvent]] = {}

    def reset(self) -> None:
        with self._lock:
            self._policies.clear()
            self._ledger.clear()
            self._evaluations.clear()
            self._audit.clear()

    def evaluate(self, request: RuntimeEvaluationRequest) -> RuntimeComparison:
        with self._lock:
            policy = request.policy
            action = request.action
            key = self._key(policy.policy_id, policy.version)
            self._register_policy(key, policy)

            fingerprint = action.model_dump_json()
            prior = self._evaluations[key].get(action.action_id)
            if prior is not None:
                if prior.fingerprint == fingerprint:
                    replay_event = self._event(
                        action.action_id,
                        "idempotent_replay",
                        prior.response.arthaniyam.decision,
                        ["IDEMPOTENT_REPLAY"],
                    )
                    self._audit[key].append(replay_event)
                    return prior.response.model_copy(
                        update={
                            "replayed": True,
                            "state": self._snapshot(key),
                            "audit_event": replay_event,
                        }
                    )
                return self._conflict_response(key, action)

            naive = self._naive_decision(policy, action)
            arthaniyam, correlated_amount = self._arthaniyam_decision(
                key, policy, action
            )

            if arthaniyam.decision == "allow_and_reserve":
                self._ledger[key][action.action_id] = LedgerEntry(
                    action=action,
                    status="reserved",
                    created_at=datetime.now(timezone.utc),
                )

            audit_event = self._event(
                action.action_id,
                "evaluation",
                arthaniyam.decision,
                arthaniyam.reason_codes,
            )
            self._audit[key].append(audit_event)
            response = RuntimeComparison(
                action_id=action.action_id,
                naive_gateway=naive,
                arthaniyam=arthaniyam,
                correlated_amount=correlated_amount,
                state=self._snapshot(key),
                audit_event=audit_event,
            )
            self._evaluations[key][action.action_id] = EvaluationRecord(
                fingerprint=fingerprint,
                response=response,
            )
            return response

    def commit(
        self, policy_id: str, policy_version: int, action_id: str
    ) -> ActionTransitionResult:
        with self._lock:
            key = self._key(policy_id, policy_version)
            entry = self._entry(key, action_id)
            if entry.status == "committed":
                raise RuntimeTransitionError("action is already committed")
            if entry.status != "reserved":
                raise RuntimeTransitionError("only reserved actions can be committed")
            entry.status = "committed"
            event = self._event(
                action_id, "commit", "committed", ["RESERVATION_COMMITTED"]
            )
            self._audit[key].append(event)
            return ActionTransitionResult(
                action_id=action_id,
                status="committed",
                state=self._snapshot(key),
                audit_event=event,
            )

    def release(
        self, policy_id: str, policy_version: int, action_id: str
    ) -> ActionTransitionResult:
        with self._lock:
            key = self._key(policy_id, policy_version)
            entry = self._entry(key, action_id)
            if entry.status != "reserved":
                raise RuntimeTransitionError("only reserved actions can be released")
            entry.status = "released"
            event = self._event(
                action_id, "release", "released", ["RESERVATION_RELEASED"]
            )
            self._audit[key].append(event)
            return ActionTransitionResult(
                action_id=action_id,
                status="released",
                state=self._snapshot(key),
                audit_event=event,
            )

    def state(self, policy_id: str, policy_version: int) -> RuntimeStateResponse:
        with self._lock:
            key = self._key(policy_id, policy_version)
            if key not in self._policies:
                raise RuntimeActionNotFoundError("policy runtime state was not found")
            actions = [
                {
                    "action_id": entry.action.action_id,
                    "invoice_id": entry.action.invoice_id,
                    "amount": entry.action.amount,
                    "status": entry.status,
                }
                for entry in self._ledger[key].values()
            ]
            return RuntimeStateResponse(
                state=self._snapshot(key),
                actions=actions,
                audit_trail=list(self._audit[key]),
            )

    def _register_policy(
        self, key: tuple[str, int], policy: PolicyDefinition
    ) -> None:
        existing = self._policies.get(key)
        if existing is not None and existing != policy:
            raise RuntimeTransitionError(
                "policy_id and version already refer to different policy contents"
            )
        if existing is None:
            self._policies[key] = policy
            self._ledger[key] = {}
            self._evaluations[key] = {}
            self._audit[key] = []

    def _naive_decision(
        self, policy: PolicyDefinition, action: FinancialAction
    ) -> DecisionDetail:
        reasons: list[str] = []
        if action.amount > policy.budget.per_transaction_limit:
            reasons.append("PER_TRANSACTION_LIMIT_EXCEEDED")
        if not self._vendor_allowed(policy, action):
            reasons.append("VENDOR_NOT_ALLOWED")
        if not self._category_allowed(policy, action):
            reasons.append("CATEGORY_NOT_ALLOWED")
        if reasons:
            return DecisionDetail(
                decision="deny",
                reason_codes=reasons,
                explanation="The current request fails a local gateway condition.",
            )
        return DecisionDetail(
            decision="allow",
            reason_codes=["LOCAL_CHECKS_PASSED"],
            explanation=(
                "Every request-local condition passes; no historical actions were checked."
            ),
        )

    def _arthaniyam_decision(
        self,
        key: tuple[str, int],
        policy: PolicyDefinition,
        action: FinancialAction,
    ) -> tuple[DecisionDetail, int]:
        if action.amount > policy.budget.per_transaction_limit:
            return self._deny(
                "PER_TRANSACTION_LIMIT_EXCEEDED",
                "The payment exceeds the maximum allowed for one transaction.",
            ), action.amount
        if not self._vendor_allowed(policy, action):
            return self._deny(
                "VENDOR_NOT_ALLOWED", "The vendor is outside the approved policy scope."
            ), action.amount
        if not self._category_allowed(policy, action):
            return self._deny(
                "CATEGORY_NOT_ALLOWED",
                "The payment category is outside the approved policy scope.",
            ), action.amount

        active_entries = [
            entry for entry in self._ledger[key].values() if entry.status != "released"
        ]
        if any(entry.action.invoice_id == action.invoice_id for entry in active_entries):
            return self._deny(
                "DUPLICATE_INVOICE",
                "This invoice already has an active reservation or committed payment.",
            ), action.amount

        reserved = sum(
            entry.action.amount for entry in active_entries if entry.status == "reserved"
        )
        committed = sum(
            entry.action.amount for entry in active_entries if entry.status == "committed"
        )
        if reserved + committed + action.amount > policy.budget.monthly_limit:
            return self._deny(
                "BUDGET_EXCEEDED",
                "Committed spend plus active reservations would exceed the budget.",
            ), action.amount

        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=policy.correlation.window_hours
        )
        correlated_entries = [
            entry
            for entry in active_entries
            if entry.created_at >= cutoff
            and self._correlation_key(policy, entry.action)
            == self._correlation_key(policy, action)
        ]
        correlated_amount = action.amount + sum(
            entry.action.amount for entry in correlated_entries
        )
        needs_approval = correlated_amount > policy.approval.required_above
        has_approval = len(action.approval_ids) >= policy.approval.approver_count
        if needs_approval and not has_approval:
            return DecisionDetail(
                decision="require_approval",
                reason_codes=["CORRELATED_APPROVAL_THRESHOLD_EXCEEDED"],
                explanation=(
                    f"Related active payments total INR {correlated_amount / 100:,.2f}, "
                    f"above the INR {policy.approval.required_above / 100:,.2f} "
                    "approval threshold."
                ),
            ), correlated_amount

        reason_codes = ["POLICY_CHECKS_PASSED", "FUNDS_RESERVED_ATOMICALLY"]
        if needs_approval:
            reason_codes.append("REQUIRED_APPROVAL_PRESENT")
        return DecisionDetail(
            decision="allow_and_reserve",
            reason_codes=reason_codes,
            explanation=(
                "Cross-request policy checks passed and the amount was reserved atomically."
            ),
        ), correlated_amount

    def _conflict_response(
        self, key: tuple[str, int], action: FinancialAction
    ) -> RuntimeComparison:
        event = self._event(
            action.action_id,
            "evaluation",
            "deny",
            ["IDEMPOTENCY_KEY_CONFLICT"],
        )
        self._audit[key].append(event)
        denial = self._deny(
            "IDEMPOTENCY_KEY_CONFLICT",
            "The action ID was previously used with different action contents.",
        )
        return RuntimeComparison(
            action_id=action.action_id,
            naive_gateway=DecisionDetail(
                decision="allow",
                reason_codes=["LOCAL_CHECKS_PASSED"],
                explanation="A stateless gateway does not remember the earlier action ID.",
            ),
            arthaniyam=denial,
            correlated_amount=action.amount,
            state=self._snapshot(key),
            audit_event=event,
        )

    def _snapshot(self, key: tuple[str, int]) -> StateSnapshot:
        policy = self._policies[key]
        entries = self._ledger[key].values()
        reserved_entries = [entry for entry in entries if entry.status == "reserved"]
        committed_entries = [entry for entry in entries if entry.status == "committed"]
        reserved = sum(entry.action.amount for entry in reserved_entries)
        committed = sum(entry.action.amount for entry in committed_entries)
        return StateSnapshot(
            policy_id=policy.policy_id,
            policy_version=policy.version,
            reserved_amount=reserved,
            committed_amount=committed,
            available_budget=max(
                0, policy.budget.monthly_limit - reserved - committed
            ),
            active_reservations=len(reserved_entries),
            committed_actions=len(committed_entries),
            audit_events=len(self._audit[key]),
        )

    def _entry(self, key: tuple[str, int], action_id: str) -> LedgerEntry:
        entry = self._ledger.get(key, {}).get(action_id)
        if entry is None:
            raise RuntimeActionNotFoundError("runtime action was not found")
        return entry

    @staticmethod
    def _key(policy_id: str, policy_version: int) -> tuple[str, int]:
        return policy_id, policy_version

    @staticmethod
    def _vendor_allowed(
        policy: PolicyDefinition, action: FinancialAction
    ) -> bool:
        allowlist = policy.vendors.allowed_vendor_ids
        return not (
            policy.vendors.require_approved_vendor
            and allowlist
            and action.vendor_id not in allowlist
        )

    @staticmethod
    def _category_allowed(
        policy: PolicyDefinition, action: FinancialAction
    ) -> bool:
        categories = policy.vendors.allowed_categories
        return not categories or action.category in categories

    @staticmethod
    def _correlation_key(
        policy: PolicyDefinition, action: FinancialAction
    ) -> tuple[str, ...]:
        values = {
            "vendor": action.vendor_id,
            "purpose": action.purpose,
            "invoice": action.invoice_id,
        }
        return tuple(values[key] for key in policy.correlation.group_by)

    @staticmethod
    def _deny(code: str, explanation: str) -> DecisionDetail:
        return DecisionDetail(
            decision="deny", reason_codes=[code], explanation=explanation
        )

    @staticmethod
    def _event(
        action_id: str,
        event_type: str,
        decision: str,
        reason_codes: list[str],
    ) -> AuditEvent:
        return AuditEvent(
            event_id=str(uuid4()),
            action_id=action_id,
            event_type=event_type,
            decision=decision,
            occurred_at=datetime.now(timezone.utc),
            reason_codes=reason_codes,
        )


runtime_guard = RuntimeGuard()
