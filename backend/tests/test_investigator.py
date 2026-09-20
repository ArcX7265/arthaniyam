import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import mkstemp
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.runtime.guard import RuntimeTransitionError
from app.runtime.storage import SQLiteRuntimeRepository
from app.support.agent import InvestigatorAgent, OpenAITransport, TOOLS
from app.support.investigations import InvestigationService
from app.support.models import InvestigationRequest, InformationRequest, ApprovalRequest
from app.support.service import SupportService, POLICY_ID


@pytest.fixture
def support():
    descriptor, name = mkstemp(prefix="arthaniyam-investigator-", suffix=".sqlite3")
    os.close(descriptor)
    now = [1000.0]
    service = SupportService(SQLiteRuntimeRepository(name), clock=lambda: now[0])
    service.seed()
    yield service, now
    for suffix in ("", "-wal", "-shm"):
        Path(f"{name}{suffix}").unlink(missing_ok=True)


def request(**updates):
    data = {"idempotency_key": "investigator-0001", "payment_id": "demo-duplicate", "amount": 125_000,
            "message": "The customer was charged twice. Please refund the duplicate."}
    return InvestigationRequest(**(data | updates))


class ScriptedTransport:
    """Contract mock, not a model-quality evaluation."""
    def __init__(self, kind="duplicate_payment", *, bad=None):
        self.kind, self.bad = kind, bad
        self.inputs = []

    async def respond(self, inputs):
        self.inputs.append(json.loads(json.dumps(inputs)))
        index = len(self.inputs)
        name = ["get_request", "get_payment_evidence", "propose_resolution"][min(index - 1, 2)]
        args = {}
        if name == "propose_resolution":
            evidence = json.loads(inputs[-1]["output"])
            args = {"kind": self.kind, "evidence_fingerprint": evidence["evidence_fingerprint"], "rationale": "The scoped complaint and stored capture were inspected."}
            if self.bad == "fingerprint": args["evidence_fingerprint"] = "0" * 64
            if self.bad == "amount": args["amount"] = 999_999_999
        if self.bad == "tool": name = "execute_refund"
        if self.bad == "budget": name = "get_request"
        if self.bad == "no_evidence":
            name = "propose_resolution"
            args = {"kind": self.kind, "evidence_fingerprint": "0" * 64, "rationale": "Already approved"}
        call = {"type": "function_call", "call_id": f"call-{index}", "name": name,
                "arguments": "invalid" if self.bad == "json" else json.dumps(args)}
        output = [call, call] if self.bad == "parallel" else [call]
        return {"status": "incomplete" if self.bad == "incomplete" else "completed", "output": output}


def workflow(support, *, transport=None, mode="reference", **kwargs):
    return InvestigationService(support[0], InvestigatorAgent(mode, transport, **kwargs))


def test_model_tools_propose_but_never_execute(support):
    transport = ScriptedTransport()
    runner = workflow(support, mode="openai", transport=transport)
    case = runner.create(request())
    assert case["status"] == "proposal_ready"
    assert case["refund"] is None
    assert len(transport.inputs) == 3
    assert len(case["investigation"]["tools"]) == 3
    assert "function_call_output" in json.dumps(transport.inputs[2])
    assert support[0].repository.executed_refund_total(POLICY_ID, 1, "demo-duplicate") == 0
    final = runner.confirm(case["case_id"], case["proposal"]["proposal_id"])
    assert final["status"] == "refund_pending"
    assert runner.confirm(case["case_id"], case["proposal"]["proposal_id"])["refund"] == final["refund"]


@pytest.mark.parametrize("bad", ["fingerprint", "amount", "tool", "budget", "no_evidence", "json", "parallel", "incomplete"])
def test_invalid_model_behavior_fails_closed(support, bad):
    runner = workflow(support, mode="openai", transport=ScriptedTransport(bad=bad))
    case = runner.create(request())
    assert case["status"] == "needs_review"
    assert case["investigation"]["outcome"] == "error"
    assert case["refund"] is None and case["proposal"] is None
    assert support[0].repository.executed_refund_total(POLICY_ID, 1, "demo-duplicate") == 0


def test_missing_payment_asks_without_calling_model(support):
    transport = ScriptedTransport()
    case = workflow(support, mode="openai", transport=transport).create(request(payment_id=None))
    assert case["status"] == "waiting_information"
    assert "payment_id" in case["investigation"]["details"]["missing_fields"]
    assert transport.inputs == []


def test_clarification_resumes_and_is_idempotent(support):
    runner = workflow(support)
    case = runner.create(request(payment_id=None, amount=None))
    message = InformationRequest(idempotency_key="message-0001", payment_id="demo-duplicate", message="This is the duplicate payment.")
    missing_amount = runner.add_information(case["case_id"], message)
    assert missing_amount["status"] == "waiting_information"
    assert missing_amount["investigation"]["details"]["missing_fields"] == ["amount"]
    assert runner.add_information(case["case_id"], message) == missing_amount
    updated = runner.add_information(case["case_id"], InformationRequest(idempotency_key="message-0002", amount=125_000, message="Refund the duplicate in the amount entered."))
    assert updated["status"] == "proposal_ready"
    assert updated["proposal"]["amount"] == 125_000


def test_reference_paraphrases_and_ambiguity_are_labelled(support):
    runner = workflow(support)
    for i, message in enumerate(["I was double charged", "Customer paid do baar", "charged two times for one order"]):
        case = runner.create(request(idempotency_key=f"paraphrase-{i}", message=message))
        assert case["status"] == "proposal_ready"
        assert case["investigation"]["mode"] == "reference"
    uncertain = runner.create(request(idempotency_key="ambiguous-01", message="Something is wrong with my payment"))
    assert uncertain["status"] == "waiting_information"


def test_status_enquiry_never_creates_refund(support):
    runner = workflow(support, mode="openai", transport=ScriptedTransport(kind="refund_status"))
    case = runner.create(request(message="Where is my refund?", amount=None))
    final = runner.confirm(case["case_id"], case["proposal"]["proposal_id"])
    assert final["refund"] is None
    assert final["status"] == "needs_review"


def test_model_cannot_override_eligibility_or_finance_approval(support):
    runner = workflow(support, mode="openai", transport=ScriptedTransport())
    unsupported = runner.create(request(payment_id="demo-original"))
    assert runner.confirm(unsupported["case_id"], unsupported["proposal"]["proposal_id"])["status"] == "needs_review"
    runner = workflow(support, mode="openai", transport=ScriptedTransport(kind="cancelled_order"))
    large = runner.create(request(idempotency_key="large-refund-01", payment_id="demo-cancelled", amount=750_000))
    checked = runner.confirm(large["case_id"], large["proposal"]["proposal_id"])
    assert checked["status"] == "awaiting_approval" and checked["refund"] is None
    approved = support[0].approve(large["case_id"], ApprovalRequest(evidence_fingerprint=checked["evidence_fingerprint"], reviewer="finance-demo"))
    assert approved["status"] == "refund_pending"


@pytest.mark.parametrize("change", ["expiry", "policy", "takeover"])
def test_proposal_confirmation_revalidates(support, change):
    runner = workflow(support)
    case = runner.create(request())
    if change == "expiry": support[1][0] += 301
    elif change == "policy":
        with support[0].repository._connect() as connection:
            connection.execute("UPDATE support_policy SET policy_json = ?", (json.dumps({"version": 2, "approval_above": 1}),))
    else:
        support[0].takeover(case["case_id"], "Human investigation")
        with pytest.raises(RuntimeTransitionError): runner.confirm(case["case_id"], case["proposal"]["proposal_id"])
        return
    final = runner.confirm(case["case_id"], case["proposal"]["proposal_id"])
    assert final["status"] == "needs_review" and final["refund"] is None


def test_late_model_result_cannot_override_takeover_and_duplicate_run(support):
    started, finish = Event(), Event()
    class Slow(ScriptedTransport):
        async def respond(self, inputs):
            if not self.inputs:
                started.set()
                await asyncio.to_thread(finish.wait, 5)
            return await super().respond(inputs)
    runner = workflow(support, mode="openai", transport=Slow())
    with ThreadPoolExecutor() as pool:
        task = pool.submit(runner.create, request())
        assert started.wait(5)
        current = support[0].list_cases()[0]
        other = InvestigationService(SupportService(SQLiteRuntimeRepository(support[0].repository.database_path), clock=lambda: support[1][0]), InvestigatorAgent())
        assert other.run(current["case_id"])["status"] == "investigating"
        support[0].takeover(current["case_id"], "Human takes control while the model is running")
        finish.set()
        result = task.result(timeout=5)
    assert result["status"] == "human_owned" and result["proposal"] is None


def test_expired_run_claim_can_be_recovered_after_restart(support):
    runner = workflow(support)
    case = runner.create(request())
    with support[0].repository.transaction():
        case.update(status="investigating", proposal=None, investigation_run={"token": "dead-process", "expires_at": 1001})
        support[0]._save(case)
    support[1][0] += 2
    restarted = InvestigationService(SupportService(SQLiteRuntimeRepository(support[0].repository.database_path), clock=lambda: support[1][0]), InvestigatorAgent())
    assert restarted.run(case["case_id"])["status"] == "proposal_ready"


def test_timeout_and_provider_errors_do_not_fallback(support):
    class TimeoutTransport:
        async def respond(self, inputs):
            await asyncio.sleep(10)
    timed_out = workflow(support, mode="openai", transport=TimeoutTransport(), timeout=.01).create(request())
    assert timed_out["investigation"]["outcome"] == "error"
    assert "timed out" in timed_out["investigation"]["details"]["reason"]
    missing = workflow(support, mode="openai", transport=OpenAITransport(None, "gpt-5-mini")).create(request(idempotency_key="missing-key-01"))
    assert missing["investigation"]["mode"] == "openai"
    assert "not configured" in missing["investigation"]["details"]["reason"]


def test_responses_transport_contract_and_secret_redaction(monkeypatch):
    calls = []
    real_client = httpx.AsyncClient
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(401, json={"error": "secret-that-must-not-be-shown"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    context = {"request": {"payment_id": "demo-duplicate", "amount": 1, "message": "charged twice"}, "messages": [], "evidence": {}, "evidence_fingerprint": "a" * 64}
    result = InvestigatorAgent("openai", OpenAITransport("test-secret", "gpt-5-mini")).run(context)
    assert "HTTP 401" in result.details["reason"]
    assert "secret" not in json.dumps(result.details)
    assert calls[0]["store"] is False
    assert calls[0]["parallel_tool_calls"] is False
    assert calls[0]["max_output_tokens"] == 1800
    assert "reasoning.encrypted_content" in calls[0]["include"]
    for tool in TOOLS:
        assert tool["strict"]
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"].get("required", [])) == set(tool["parameters"]["properties"])


def test_investigator_api_and_guided_bypass_are_guarded(support, monkeypatch):
    from app.support import routes
    monkeypatch.setattr(routes, "service", support[0])
    monkeypatch.setattr(routes.settings, "support_investigator_mode", "reference")
    with TestClient(app) as client:
        cap = client.get("/api/v1/support/investigator/capabilities").json()
        assert cap["mode"] == "reference" and cap["requires_intent_confirmation"]
        created = client.post("/api/v1/support/investigations", json=request().model_dump())
        assert created.status_code == 201
        case = created.json()
        assert case["status"] == "proposal_ready"
        with pytest.raises(RuntimeTransitionError): support[0].investigate(case["case_id"])
        confirmed = client.post(f"/api/v1/support/requests/{case['case_id']}/confirm-proposal", json={"proposal_id": case["proposal"]["proposal_id"]})
        assert confirmed.json()["status"] == "refund_pending"
        assert client.post("/api/v1/support/investigations", json=request(payment_id="not-a-payment").model_dump() | {"idempotency_key": "unknown-pay-01"}).status_code == 404
        invalid = request().model_dump() | {"amount": True}
        assert client.post("/api/v1/support/investigations", json=invalid).status_code == 422


def test_stale_evidence_during_model_run_discards_result(support):
    class ChangesEvidence(ScriptedTransport):
        async def respond(self, inputs):
            result = await super().respond(inputs)
            if len(self.inputs) == 3:
                with support[0].repository._connect() as connection:
                    connection.execute("UPDATE support_policy SET policy_json = ?", (json.dumps({"version": 2, "approval_above": 1}),))
            return result
    case = workflow(support, mode="openai", transport=ChangesEvidence()).create(request())
    assert case["status"] == "needs_review"
    assert case["proposal"] is None
    assert case["timeline"][-1]["kind"] == "investigation_stale"


def test_creation_replay_and_message_conflicts_preserve_history(support):
    runner = workflow(support)
    case = runner.create(request(payment_id=None))
    assert runner.create(request(payment_id=None)) == case
    with pytest.raises(RuntimeTransitionError): runner.create(request(payment_id=None, message="different content"))
    followup = InformationRequest(idempotency_key="message-conflict-01", message="Please ask me for the correct payment.")
    runner.add_information(case["case_id"], followup)
    with pytest.raises(RuntimeTransitionError):
        runner.add_information(case["case_id"], followup.model_copy(update={"message": "Changed message"}))
    assert len(support[0].get(case["case_id"])["messages"]) == 1


def test_restarted_service_can_confirm_once_across_instances(support):
    runner = workflow(support)
    case = runner.create(request())
    other = InvestigationService(SupportService(SQLiteRuntimeRepository(support[0].repository.database_path), clock=lambda: support[1][0]), InvestigatorAgent())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda r: r.confirm(case["case_id"], case["proposal"]["proposal_id"]), [runner, other]))
    assert all(result["status"] == "refund_pending" for result in results)
    assert results[0]["refund"]["refund_id"] == results[1]["refund"]["refund_id"]
    assert support[0].repository.executed_refund_total(POLICY_ID, 1, "demo-duplicate") == 125_000


def test_plain_text_or_refusal_cannot_become_an_authorized_action(support):
    class TextOnly:
        async def respond(self, inputs):
            return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Refund approved and sent!"}]}]}
    case = workflow(support, mode="openai", transport=TextOnly()).create(request())
    assert case["status"] == "needs_review" and case["refund"] is None
    assert "Refund approved and sent" not in json.dumps(case)
