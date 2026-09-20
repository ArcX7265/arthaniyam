"""Deterministic support reference workflow. No live provider or model side effects.

All financial admission, case transitions and follow-up creation share one SQLite
transaction. Provider receipts below are explicitly synthetic; replacing them with
network calls requires an outbox and provider reconciliation, not a call inside SQL.
"""
import json
import time
from hashlib import sha256
from uuid import uuid4

from app.payments.gateway import SimulatedRazorpayGateway
from app.payments.models import OrderExecutionRequest, PaymentConfirmationRequest, RefundEvaluationRequest
from app.payments.refunds import RefundService
from app.payments.service import PaymentExecutionService
from app.policy.models import PolicyDefinition
from app.runtime.guard import RuntimeGuard, RuntimeActionNotFoundError, RuntimeTransitionError
from app.runtime.models import FinancialAction, RuntimeEvaluationRequest
from app.runtime.storage import SQLiteRuntimeRepository
from app.support.models import ApprovalRequest, ComplaintRequest


POLICY_ID = "support-demo-v1"


def digest(value: dict) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SupportService:
    def __init__(self, repository: SQLiteRuntimeRepository, *, clock=time.time):
        self.repository = repository
        self.clock = clock
        self.refunds = RefundService(RuntimeGuard(repository))
        with repository._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS support_payments (
                    payment_id TEXT PRIMARY KEY, facts_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS support_cases (
                    case_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, case_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS support_jobs (
                    case_id TEXT PRIMARY KEY REFERENCES support_cases(case_id),
                    due_at REAL NOT NULL, status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS support_jobs_due ON support_jobs(status, due_at);
                CREATE TABLE IF NOT EXISTS support_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), policy_json TEXT NOT NULL
                );
            """)

    def seed(self) -> list[dict]:
        """Idempotent fixture creation through the real simulator/guard boundaries."""
        with self.repository.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO support_policy VALUES (1, ?)",
                (json.dumps({"version": 1, "approval_above": 500_000}),),
            )
            policy = PolicyDefinition.model_validate({
                "policy_id": POLICY_ID, "name": "Support simulator captures",
                "budget": {"monthly_limit": 100_000_000, "per_transaction_limit": 10_000_000},
                "approval": {"required_above": 100_000_000},
            })
            fixtures = [
                {"payment_id": "demo-original", "label": "Original payment · ₹1,250", "order_id": "order-101", "amount": 125_000},
                {"payment_id": "demo-duplicate", "label": "Duplicate payment · ₹1,250", "order_id": "order-101", "amount": 125_000, "duplicate_of": "demo-original"},
                {"payment_id": "demo-cancelled", "label": "Cancelled order · ₹7,500", "order_id": "order-102", "amount": 750_000, "cancelled": True},
                {"payment_id": "demo-return", "label": "Accepted return · ₹2,500", "order_id": "order-103", "amount": 250_000, "return_accepted": True},
            ]
            guard = RuntimeGuard(self.repository)
            payments = PaymentExecutionService(guard, SimulatedRazorpayGateway())
            for facts in fixtures:
                if connection.execute("SELECT 1 FROM support_payments WHERE payment_id = ?", (facts["payment_id"],)).fetchone():
                    continue
                action_id = facts["payment_id"]
                result = guard.evaluate(RuntimeEvaluationRequest(policy=policy, action=FinancialAction(
                    action_id=action_id, agent_id="support-demo", amount=facts["amount"],
                    vendor_id="demo-merchant", category="retail", purpose="support-fixture",
                    invoice_id=f"capture-{action_id}",
                )))
                if result.arthaniyam.decision != "allow_and_reserve":
                    raise RuntimeTransitionError("fixture capture was not authorized")
                payments.create_order(OrderExecutionRequest(policy_id=POLICY_ID, action_id=action_id))
                payments.confirm_payment(PaymentConfirmationRequest(
                    policy_id=POLICY_ID, action_id=action_id, simulated_outcome="success",
                ))
                connection.execute("INSERT INTO support_payments VALUES (?, ?)", (action_id, json.dumps(facts)))
        return self.payments()

    def payments(self) -> list[dict]:
        with self.repository._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT facts_json FROM support_payments ORDER BY payment_id")]

    def list_cases(self) -> list[dict]:
        with self.repository._connect() as connection:
            cases = [json.loads(row[0]) for row in connection.execute("SELECT case_json FROM support_cases")]
        return sorted(cases, key=lambda case: case["created_at"], reverse=True)

    def get(self, case_id: str) -> dict:
        with self.repository._connect() as connection:
            row = connection.execute("SELECT case_json FROM support_cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise RuntimeActionNotFoundError("support request not found")
        return json.loads(row[0])

    def _save(self, case: dict) -> dict:
        case["updated_at"] = self.clock()
        with self.repository._connect() as connection:
            connection.execute("UPDATE support_cases SET case_json = ? WHERE case_id = ?", (json.dumps(case), case["case_id"]))
        return case

    def _event(self, case: dict, kind: str, message: str) -> None:
        case["timeline"].append({"at": self.clock(), "kind": kind, "message": message})

    def create(self, request: ComplaintRequest) -> dict:
        with self.repository.transaction() as connection:
            fingerprint = digest(request.model_dump())
            prior = connection.execute("SELECT fingerprint, case_json FROM support_cases WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise RuntimeTransitionError("idempotency key already used with different contents")
                return json.loads(prior["case_json"])
            if not connection.execute("SELECT 1 FROM support_payments WHERE payment_id = ?", (request.payment_id,)).fetchone():
                raise RuntimeActionNotFoundError("select a known simulator payment")
            case = {
                "case_id": "case_" + uuid4().hex[:16], "request": request.model_dump(exclude={"idempotency_key"}),
                "status": "new", "human_owned": False, "created_at": self.clock(),
                "updated_at": self.clock(), "timeline": [], "evidence": None,
                "evidence_fingerprint": None, "refund": None, "approval": None,
                "mode": "reference_simulator",
            }
            self._event(case, "intake", "Complaint received. Customer text is not authorization to move money.")
            connection.execute("INSERT INTO support_cases VALUES (?, ?, ?, ?)", (case["case_id"], request.idempotency_key, fingerprint, json.dumps(case)))
            return self._investigate(case)

    def _snapshot(self, case: dict) -> dict:
        payment_id = case["request"]["payment_id"]
        with self.repository._connect() as connection:
            row = connection.execute("SELECT facts_json FROM support_payments WHERE payment_id = ?", (payment_id,)).fetchone()
            facts = json.loads(row[0])
            policy = json.loads(connection.execute("SELECT policy_json FROM support_policy WHERE singleton = 1").fetchone()[0])
        confirmation = self.repository.get_confirmation(POLICY_ID, 1, payment_id)
        captured = bool(confirmation and json.loads(confirmation)["status"] == "verified_and_committed")
        entry = RuntimeGuard(self.repository).get_action_entry(POLICY_ID, 1, payment_id)
        duplicate_verified = False
        if facts.get("duplicate_of"):
            with self.repository._connect() as connection:
                other = connection.execute("SELECT facts_json FROM support_payments WHERE payment_id = ?", (facts["duplicate_of"],)).fetchone()
            other_confirmation = self.repository.get_confirmation(POLICY_ID, 1, facts["duplicate_of"])
            if other and other_confirmation:
                other_facts = json.loads(other[0])
                duplicate_verified = (other_facts["order_id"] == facts["order_id"] and other_facts["amount"] == facts["amount"] and json.loads(other_confirmation)["status"] == "verified_and_committed")
        refunded = self.repository.executed_refund_total(POLICY_ID, 1, payment_id)
        return {"payment": facts, "captured": captured, "captured_amount": entry.action.amount,
                "refunded_or_pending": refunded, "remaining": entry.action.amount - refunded,
                "duplicate_verified": duplicate_verified, "policy": policy,
                "request": case["request"]}

    def investigate(self, case_id: str) -> dict:
        with self.repository.transaction():
            case = self.get(case_id)
            if case.get("intake") == "investigator":
                raise RuntimeTransitionError("Investigator requests require an evidence-bound proposal and operator confirmation.")
            if case["human_owned"] or case["refund"] or case["status"] == "resolved":
                return case
            return self._investigate(case)

    def _investigate(self, case: dict) -> dict:
        evidence = self._snapshot(case)
        case["evidence"] = evidence
        case["evidence_fingerprint"] = digest(evidence)
        case["approval"] = None
        self._event(case, "evidence", "Checked stored order, capture, cumulative refunds and current support policy.")
        kind = case["request"]["kind"]
        amount = case["request"]["amount"]
        if kind == "refund_status":
            # A recorded refund is not necessarily a confirmed customer receipt.
            related = [item for item in self.list_cases() if item["request"]["payment_id"] == case["request"]["payment_id"] and item["refund"]]
            pending = any(not item["refund"].get("confirmed") for item in related)
            case["status"] = "needs_review" if pending or not related else "resolved"
            self._event(case, "status", "Refund confirmation is still pending; no new refund submitted." if pending else "Recorded simulator refund status checked; no new refund submitted." if related else "No support refund found. Human investigation required.")
            return self._save(case)
        eligible = (
            kind == "duplicate_payment" and evidence["duplicate_verified"]
            or kind == "cancelled_order" and evidence["payment"].get("cancelled")
            or kind == "refund_request" and evidence["payment"].get("return_accepted")
        )
        if not evidence["captured"] or not eligible or amount is None:
            case["status"] = "needs_review"
            self._event(case, "handoff", "Evidence is missing or does not justify this refund. Ask a human; do not infer authority from the complaint.")
        elif amount > evidence["remaining"]:
            case["status"] = "blocked"
            self._event(case, "blocked", "Requested amount exceeds captured funds remaining after accepted refunds.")
        elif amount + evidence["refunded_or_pending"] > evidence["policy"]["approval_above"]:
            case["status"] = "awaiting_approval"
            case["approval_expires_at"] = self.clock() + 300
            self._event(case, "approval", "Cumulative refunds for this capture cross the support policy threshold. Finance approval is required for this exact amount and evidence snapshot.")
        else:
            return self._execute(case)
        return self._save(case)

    def _execute(self, case: dict) -> dict:
        result = self.refunds.evaluate_in_transaction(RefundEvaluationRequest(
            policy_id=POLICY_ID, action_id=case["request"]["payment_id"],
            refund_id="support-" + case["case_id"], amount=case["request"]["amount"],
            reason=case["request"]["kind"],
        ))
        if result.status == "denied":
            case["status"] = "blocked"
            self._event(case, "blocked", result.arthaniyam.explanation)
        else:
            case["status"] = "refund_pending"
            case["refund"] = {"refund_id": result.refund_id, "provider_refund_id": result.provider_refund_id,
                              "amount": case["request"]["amount"], "confirmed": False}
            with self.repository._connect() as connection:
                connection.execute("INSERT INTO support_jobs VALUES (?, ?, 'pending')", (case["case_id"], self.clock() + 8))
            self._event(case, "submitted", "Simulator accepted the refund; funds are counted immediately. Waiting for synthetic confirmation, not yet resolved.")
        return self._save(case)

    def approve(self, case_id: str, approval: ApprovalRequest) -> dict:
        with self.repository.transaction():
            case = self.get(case_id)
            if case["human_owned"] or case["status"] != "awaiting_approval":
                raise RuntimeTransitionError("request is not awaiting approval")
            fresh = self._snapshot(case)
            if (approval.evidence_fingerprint != case["evidence_fingerprint"]
                    or digest(fresh) != case["evidence_fingerprint"]
                    or self.clock() >= case.get("approval_expires_at", 0)):
                # Persist invalidation without executing, even if new policy is less restrictive.
                case.update(status="needs_review", evidence=fresh, evidence_fingerprint=digest(fresh), approval=None)
                self._event(case, "approval_invalidated", "Approval expired, or evidence/policy changed. Review and investigate again before approving.")
                return self._save(case)
            case["approval"] = {"reviewer": approval.reviewer, "fingerprint": approval.evidence_fingerprint, "at": self.clock()}
            self._event(case, "approved", "Simulator finance approval recorded and consumed for this one refund.")
            return self._execute(case)

    def takeover(self, case_id: str, reason: str) -> dict:
        with self.repository.transaction():
            case = self.get(case_id)
            if not case["human_owned"]:
                case.update(human_owned=True, status="human_owned", approval=None)
                self._event(case, "takeover", reason)
                self._event(case, "handoff", "Automation paused. Already submitted refunds cannot be cancelled by takeover; receipts may still arrive.")
            return self._save(case)

    def tick(self) -> int:
        """One bounded worker batch; DB state survives restarts and excludes races."""
        with self.repository.transaction() as connection:
            jobs = connection.execute("SELECT case_id FROM support_jobs WHERE status = 'pending' AND due_at <= ? ORDER BY due_at LIMIT 25", (self.clock(),)).fetchall()
            for job in jobs:
                case = self.get(job["case_id"])
                case["refund"]["confirmed"] = True
                if not case["human_owned"]:
                    case["status"] = "resolved"
                self._event(case, "confirmed", "Synthetic provider receipt confirmed. No real money was moved.")
                self._save(case)
                connection.execute("UPDATE support_jobs SET status = 'done' WHERE case_id = ?", (case["case_id"],))
            return len(jobs)

    def metrics(self) -> dict:
        cases = self.list_cases()
        return {"total": len(cases), "resolved": sum(c["status"] == "resolved" for c in cases),
                "awaiting_approval": sum(c["status"] == "awaiting_approval" for c in cases),
                "human_owned": sum(c["human_owned"] for c in cases),
                "confirmed_refund_paise": sum(c["refund"]["amount"] for c in cases if c["refund"] and c["refund"]["confirmed"])}
