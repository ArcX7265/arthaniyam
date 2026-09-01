from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from app.approvals.binding import approval_binding
from app.policy.models import PolicyDefinition
from app.runtime.models import (
    ActionTransitionResult, AuditEvent, DecisionDetail, DelegationComparison,
    DelegationEvaluationRequest, DelegationGrant, FinancialAction, RuntimeComparison,
    RuntimeEvaluationRequest, RuntimeStateResponse, StateSnapshot,
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
                original = FinancialAction.model_validate_json(prior.fingerprint)
                approval_resume = (
                    prior.response.arthaniyam.decision == "require_approval"
                    and self._same_action_scope(original, action)
                    and set(original.approval_ids).issubset(action.approval_ids)
                )
                if not approval_resume:
                    return self._conflict_response(policy, action)

            naive = self._naive_decision(policy, action)
            arthaniyam, correlated_amount = self._arthaniyam_decision(policy, action)
            if arthaniyam.decision == "allow_and_reserve":
                self.repository.upsert_entry(
                    policy.policy_id, policy.version, action, "reserved",
                    datetime.now(timezone.utc),
                )
                if (
                    action.approval_ids
                    and "REQUIRED_APPROVAL_PRESENT" in arthaniyam.reason_codes
                ):
                    self.repository.consume_approval_grants(action.approval_ids)
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

    def evaluate_delegation(
        self, request: DelegationEvaluationRequest
    ) -> DelegationComparison:
        with self._lock:
            policy, grant = request.policy, request.grant
            try:
                self.repository.register_policy(policy)
            except ValueError as exc:
                raise RuntimeTransitionError(str(exc)) from exc
            fingerprint = grant.model_dump_json()
            prior = self.repository.get_delegation(
                policy.policy_id, policy.version, grant.grant_id
            )
            if prior is not None:
                if prior.fingerprint == fingerprint:
                    event = self._event(
                        grant.grant_id,
                        "delegation_evaluation",
                        prior.response.arthaniyam.decision,
                        ["IDEMPOTENT_REPLAY"],
                    )
                    self.repository.append_audit(policy.policy_id, policy.version, event)
                    return prior.response.model_copy(
                        update={"replayed": True, "audit_event": event}
                    )
                return self._delegation_result(
                    policy,
                    grant,
                    self._deny(
                        "IDEMPOTENCY_KEY_CONFLICT",
                        "The grant ID was already used with different contents.",
                    ),
                    0,
                )

            now = datetime.now(timezone.utc)
            parent_authority = self._agent_authority_limit(
                policy, grant.parent_agent_id, now
            )
            naive = (
                DecisionDetail(
                    decision="allow",
                    reason_codes=["LOCAL_GRANT_LIMIT_PASSED"],
                    explanation="The grant fits the parent's total authority in isolation.",
                )
                if grant.authority_limit <= parent_authority and grant.expires_at > now
                else self._deny(
                    "LOCAL_GRANT_INVALID",
                    "The individual grant exceeds authority or is already expired.",
                )
            )
            active = self._active_delegations(policy, now)
            delegated_before = sum(
                item.grant.authority_limit
                for item in active
                if item.grant.parent_agent_id == grant.parent_agent_id
            )
            delegated_total = delegated_before + grant.authority_limit
            reason: DecisionDetail | None = None
            if not policy.delegation.enabled:
                reason = self._deny("DELEGATION_DISABLED", "This policy does not allow delegation.")
            elif grant.expires_at <= now:
                reason = self._deny("GRANT_EXPIRED", "A grant must expire in the future.")
            elif any(item.grant.child_agent_id == grant.child_agent_id for item in active):
                reason = self._deny(
                    "CHILD_ALREADY_DELEGATED",
                    "A child agent can have only one active authority parent.",
                )
            elif self._would_create_cycle(active, grant):
                reason = self._deny(
                    "DELEGATION_CYCLE",
                    "The proposed authority edge would create a delegation cycle.",
                )
            elif self._agent_depth(active, grant.parent_agent_id) + 1 > policy.delegation.maximum_depth:
                reason = self._deny(
                    "DELEGATION_DEPTH_EXCEEDED",
                    "The proposed child exceeds the maximum delegation depth.",
                )
            elif policy.delegation.conserve_authority and delegated_total > parent_authority:
                reason = self._deny(
                    "DELEGATED_AUTHORITY_MULTIPLICATION",
                    "Sibling grants would exceed the parent's conserved authority.",
                )
            arthaniyam = reason or DecisionDetail(
                decision="allow",
                reason_codes=["AUTHORITY_CONSERVED", "DELEGATION_RECORDED"],
                explanation="The grant was recorded without multiplying parent authority.",
            )
            result = DelegationComparison(
                grant_id=grant.grant_id,
                naive_gateway=naive,
                arthaniyam=arthaniyam,
                parent_authority=parent_authority,
                delegated_total=delegated_total,
                remaining_authority=max(
                    0,
                    parent_authority
                    - (
                        delegated_total
                        if arthaniyam.decision == "allow"
                        else delegated_before
                    ),
                ),
                audit_event=self._event(
                    grant.grant_id,
                    "delegation_evaluation",
                    arthaniyam.decision,
                    arthaniyam.reason_codes,
                ),
            )
            self.repository.append_audit(
                policy.policy_id, policy.version, result.audit_event
            )
            if arthaniyam.decision == "allow":
                self.repository.save_delegation(
                    policy.policy_id,
                    policy.version,
                    grant,
                    fingerprint,
                    result,
                    now,
                )
            return result

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
        authority_limit = self._agent_spend_limit(policy, action.agent_id)
        agent_spend = sum(
            entry.action.amount
            for entry in active
            if entry.action.agent_id == action.agent_id
        )
        if agent_spend + action.amount > authority_limit:
            return self._deny(
                "DELEGATED_AUTHORITY_EXCEEDED",
                "This agent's spend plus reservations exceeds its conserved authority.",
            ), action.amount

        cutoff = datetime.now(timezone.utc) - timedelta(hours=policy.correlation.window_hours)
        correlated = [entry for entry in active if entry.created_at >= cutoff and self._correlation_key(policy, entry.action) == self._correlation_key(policy, action)]
        correlated_amount = action.amount + sum(entry.action.amount for entry in correlated)
        needs_approval = correlated_amount > policy.approval.required_above
        binding = approval_binding(policy.policy_id, policy.version, action)
        valid_approvals = self.repository.valid_approval_count(
            action.approval_ids, binding, datetime.now(timezone.utc)
        )
        has_approval = valid_approvals >= policy.approval.approver_count
        if needs_approval and not has_approval:
            reason = (
                "APPROVAL_INVALID_OR_EXPIRED"
                if action.approval_ids
                else "CORRELATED_APPROVAL_THRESHOLD_EXCEEDED"
            )
            return DecisionDetail(
                decision="require_approval",
                reason_codes=[reason],
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
    def _same_action_scope(first: FinancialAction, second: FinancialAction) -> bool:
        return first.model_dump(exclude={"approval_ids"}) == second.model_dump(
            exclude={"approval_ids"}
        )

    def _active_delegations(self, policy: PolicyDefinition, now: datetime):
        return [
            item
            for item in self.repository.list_delegations(policy.policy_id, policy.version)
            if item.grant.expires_at > now
        ]

    def _agent_authority_limit(
        self, policy: PolicyDefinition, agent_id: str, now: datetime
    ) -> int:
        incoming = [
            item.grant.authority_limit
            for item in self._active_delegations(policy, now)
            if item.grant.child_agent_id == agent_id
        ]
        return incoming[0] if incoming else policy.budget.monthly_limit

    def _agent_spend_limit(self, policy: PolicyDefinition, agent_id: str) -> int:
        now = datetime.now(timezone.utc)
        active = self._active_delegations(policy, now)
        base = self._agent_authority_limit(policy, agent_id, now)
        delegated = sum(
            item.grant.authority_limit
            for item in active
            if item.grant.parent_agent_id == agent_id
        )
        return max(0, base - delegated)

    @staticmethod
    def _would_create_cycle(active, proposed: DelegationGrant) -> bool:
        adjacency: dict[str, set[str]] = {}
        for item in active:
            adjacency.setdefault(item.grant.parent_agent_id, set()).add(
                item.grant.child_agent_id
            )
        adjacency.setdefault(proposed.parent_agent_id, set()).add(proposed.child_agent_id)
        pending = [proposed.child_agent_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == proposed.parent_agent_id:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(adjacency.get(current, set()))
        return False

    @staticmethod
    def _agent_depth(active, agent_id: str) -> int:
        parents = {
            item.grant.child_agent_id: item.grant.parent_agent_id for item in active
        }
        depth = 0
        current = agent_id
        seen: set[str] = set()
        while current in parents and current not in seen:
            seen.add(current)
            current = parents[current]
            depth += 1
        return depth

    def _delegation_result(
        self,
        policy: PolicyDefinition,
        grant: DelegationGrant,
        decision: DecisionDetail,
        delegated_total: int,
    ) -> DelegationComparison:
        parent_authority = self._agent_authority_limit(
            policy, grant.parent_agent_id, datetime.now(timezone.utc)
        )
        event = self._event(
            grant.grant_id,
            "delegation_evaluation",
            decision.decision,
            decision.reason_codes,
        )
        self.repository.append_audit(policy.policy_id, policy.version, event)
        return DelegationComparison(
            grant_id=grant.grant_id,
            naive_gateway=DecisionDetail(
                decision="allow",
                reason_codes=["LOCAL_GRANT_LIMIT_PASSED"],
                explanation="A request-local gateway does not remember the earlier grant ID.",
            ),
            arthaniyam=decision,
            parent_authority=parent_authority,
            delegated_total=delegated_total,
            remaining_authority=max(0, parent_authority - delegated_total),
            audit_event=event,
        )

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
