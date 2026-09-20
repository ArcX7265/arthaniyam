"""Durable investigation claims and operator-confirmed, evidence-bound proposals."""
import json
from uuid import uuid4

from app.runtime.guard import RuntimeActionNotFoundError, RuntimeTransitionError
from app.support.agent import InvestigatorAgent
from app.support.models import InformationRequest, InvestigationRequest
from app.support.service import SupportService, digest


class InvestigationService:
    def __init__(self, support: SupportService, agent: InvestigatorAgent):
        self.support = support
        self.agent = agent

    def _check_payment(self, payment_id: str | None):
        if payment_id and not any(p["payment_id"] == payment_id for p in self.support.payments()):
            raise RuntimeActionNotFoundError("Select a known simulator payment; inferred payment matches are not accepted.")

    def create(self, request: InvestigationRequest) -> dict:
        service = self.support
        with service.repository.transaction() as connection:
            fingerprint = digest({"intake": "investigator", **request.model_dump()})
            prior = connection.execute("SELECT fingerprint, case_json FROM support_cases WHERE idempotency_key = ?", (request.idempotency_key,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise RuntimeTransitionError("idempotency key already used with different contents")
                return json.loads(prior["case_json"])
            self._check_payment(request.payment_id)
            case = {
                "case_id": "case_" + uuid4().hex[:16],
                "request": {"payment_id": request.payment_id, "amount": request.amount, "message": request.message, "kind": "other"},
                "status": "new", "human_owned": False, "created_at": service.clock(),
                "updated_at": service.clock(), "timeline": [], "evidence": None,
                "evidence_fingerprint": None, "refund": None, "approval": None,
                "mode": self.agent.mode + "_investigator", "intake": "investigator",
                "messages": [], "proposal": None, "investigation_run": None,
                "investigation": None,
            }
            service._event(case, "intake", "Natural-language complaint received. Investigation can propose, but cannot authorize financial actions.")
            connection.execute("INSERT INTO support_cases VALUES (?, ?, ?, ?)", (case["case_id"], request.idempotency_key, fingerprint, json.dumps(case)))
        return self.run(case["case_id"])

    def _claim(self, case_id: str) -> tuple[dict, dict] | None:
        service = self.support
        with service.repository.transaction():
            case = service.get(case_id)
            if case.get("intake") != "investigator":
                raise RuntimeTransitionError("This request uses the guided workflow.")
            if case["human_owned"] or case["refund"] or case["status"] in {"resolved", "awaiting_approval"}:
                return None
            active = case.get("investigation_run")
            if active and active["expires_at"] > service.clock():
                return None
            evidence = service._snapshot(case) if case["request"]["payment_id"] else None
            claim = {"token": uuid4().hex, "expires_at": service.clock() + self.agent.timeout + 15,
                     "input_fingerprint": digest({"request": case["request"], "messages": case["messages"]}),
                     "evidence_fingerprint": digest(evidence) if evidence else None}
            case.update(status="investigating", investigation_run=claim, proposal=None, approval=None,
                        mode=self.agent.mode + "_investigator")
            service._event(case, "investigation_started", f"Started {self.agent.mode} investigation with read-only evidence tools.")
            service._save(case)
            context = {"request": case["request"], "messages": case["messages"], "evidence": evidence,
                       "evidence_fingerprint": claim["evidence_fingerprint"]}
            return claim, context

    def run(self, case_id: str) -> dict:
        claimed = self._claim(case_id)
        if claimed is None:
            return self.support.get(case_id)
        claim, context = claimed
        # Network/model calls are outside the transaction, so takeover, updates,
        # receipt workers and unrelated cases remain available during a slow run.
        result = self.agent.run(context)
        service = self.support
        with service.repository.transaction():
            case = service.get(case_id)
            if case["human_owned"] or not case.get("investigation_run") or case["investigation_run"]["token"] != claim["token"]:
                return case  # Never apply a late result over a newer operator action.
            case["investigation_run"] = None
            fresh = service._snapshot(case) if case["request"]["payment_id"] else None
            if (claim["expires_at"] <= service.clock()
                    or (digest(fresh) if fresh else None) != claim["evidence_fingerprint"]
                    or digest({"request": case["request"], "messages": case["messages"]}) != claim["input_fingerprint"]):
                case.update(status="needs_review", proposal=None)
                service._event(case, "investigation_stale", "Evidence changed or the investigation lease expired. Discarded the proposal; investigate again.")
                return service._save(case)
            case.update(evidence=fresh, evidence_fingerprint=claim["evidence_fingerprint"],
                        investigation={"mode": self.agent.mode, "outcome": result.outcome,
                                       "details": result.details, "tools": result.trace})
            if result.outcome == "proposal":
                case["proposal"] = {**result.details, "proposal_id": uuid4().hex,
                                    "amount": case["request"]["amount"], "payment_id": case["request"]["payment_id"],
                                    "expires_at": service.clock() + 300}
                case["status"] = "proposal_ready"
                service._event(case, "proposal", "Investigation complete. Confirm the proposed complaint type, payment and amount before deterministic checks run. No refund has been submitted.")
            elif result.outcome == "question":
                case["status"] = "waiting_information"
                service._event(case, "question", result.details["question"])
            else:
                case["status"] = "needs_review"
                service._event(case, "investigation_" + result.outcome, result.details["reason"])
            return service._save(case)

    def add_information(self, case_id: str, request: InformationRequest) -> dict:
        service = self.support
        with service.repository.transaction():
            case = service.get(case_id)
            if case.get("intake") != "investigator":
                raise RuntimeTransitionError("Only investigator requests accept follow-up information.")
            prior = next((m for m in case["messages"] if m["idempotency_key"] == request.idempotency_key), None)
            if prior:
                if prior["fingerprint"] != digest(request.model_dump()):
                    raise RuntimeTransitionError("message idempotency key already used with different contents")
                return case
            if case["human_owned"] or case["refund"] or case["status"] in {"resolved", "awaiting_approval"}:
                raise RuntimeTransitionError("This request cannot be edited after takeover or financial admission/review.")
            if len(case["messages"]) >= 10:
                raise RuntimeTransitionError("Conversation limit reached; hand this request to a human.")
            self._check_payment(request.payment_id)
            case["messages"].append({**request.model_dump(), "fingerprint": digest(request.model_dump()), "at": service.clock()})
            # Omitted fields preserve the earlier operator-entered scope.
            if request.payment_id is not None:
                case["request"]["payment_id"] = request.payment_id
            if request.amount is not None:
                case["request"]["amount"] = request.amount
            case.update(status="new", investigation_run=None, proposal=None, approval=None)
            service._event(case, "information", request.message)
            service._save(case)
        return self.run(case_id)

    def confirm(self, case_id: str, proposal_id: str) -> dict:
        service = self.support
        with service.repository.transaction():
            case = service.get(case_id)
            if case.get("last_confirmed_proposal") == proposal_id:
                return case  # Safe retry after a lost response; no second refund.
            proposal = case.get("proposal")
            if case["human_owned"] or case["status"] != "proposal_ready" or not proposal or proposal["proposal_id"] != proposal_id:
                raise RuntimeTransitionError("The current proposal is not available for confirmation.")
            fresh = service._snapshot(case)
            if digest(fresh) != proposal["evidence_fingerprint"] or proposal["expires_at"] <= service.clock():
                case.update(status="needs_review", proposal=None)
                service._event(case, "proposal_invalidated", "Evidence changed or the proposal expired. Investigate again; no action was taken.")
                return service._save(case)
            case["request"]["kind"] = proposal["kind"]
            case["last_confirmed_proposal"] = proposal_id
            service._event(case, "intent_confirmed", "Operator confirmed the complaint classification and scoped request. This is not finance approval.")
            return service._investigate(case)
