from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.evaluations import AdversarialEvaluationReport, AdversarialEvaluationService
from app.approvals.models import (
    ApprovalChallenge,
    ApprovalChallengeRequest,
    ApprovalDecisionRequest,
)
from app.approvals.service import ApprovalService
from app.payments.gateway import PaymentGatewayError, create_payment_gateway
from app.payments.models import (
    OrderExecutionRequest,
    OrderExecutionResult,
    PaymentConfirmationRequest,
    PaymentConfirmationResult,
    RefundEvaluationRequest,
    RefundEvaluationResult,
    WebhookResult,
)
from app.payments.refunds import RefundService
from app.payments.service import PaymentExecutionService
from app.payments.webhooks import RazorpayWebhookService, WebhookVerificationError
from app.policy.models import PolicyDefinition
from app.policy.compiler import (
    PolicyCompilationRequest,
    PolicyCompilationResult,
    create_policy_compiler,
)
from app.policy.proofs import ProofRecord, ProofReplayResult, ProofService
from app.policy.verifier import (
    VerificationRequest,
    VerificationResult,
    verify_correlated_payments,
)
from app.runtime.guard import (
    RuntimeActionNotFoundError,
    RuntimeTransitionError,
    runtime_guard,
)
from app.runtime.models import (
    ActionTransitionRequest,
    ActionTransitionResult,
    DelegationComparison,
    DelegationEvaluationRequest,
    RuntimeComparison,
    RuntimeEvaluationRequest,
    RuntimeStateResponse,
)
from app.settings import settings


app = FastAPI(
    title="ArthaNiyam API",
    description="Verify and enforce bounded financial policies for autonomous systems.",
    version="0.1.0",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
app.mount("/assets", StaticFiles(directory=FRONTEND_ROOT), name="assets")
payment_execution_service = PaymentExecutionService(
    runtime_guard,
    create_payment_gateway(settings),
    settings.razorpay_key_secret,
    settings.razorpay_key_id,
)
webhook_service = (
    RazorpayWebhookService(runtime_guard, settings.razorpay_webhook_secret)
    if settings.razorpay_webhook_secret
    else None
)
proof_service = ProofService(runtime_guard.repository)
policy_compiler = create_policy_compiler(
    settings.policy_compiler_mode, settings.openai_api_key, settings.openai_model
)
approval_service = ApprovalService(runtime_guard)
refund_service = RefundService(runtime_guard)
evaluation_service = AdversarialEvaluationService(runtime_guard.repository)


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/system/capabilities")
def system_capabilities() -> dict[str, str | bool | int]:
    return {
        "persistence": "sqlite",
        "payment_mode": settings.razorpay_mode,
        "webhook_configured": webhook_service is not None,
        "policy_compiler_mode": settings.policy_compiler_mode,
        "demo_approvals_enabled": settings.razorpay_mode == "simulate",
        "demo_delegations_enabled": settings.razorpay_mode == "simulate",
        "demo_refunds_enabled": settings.razorpay_mode == "simulate",
        "adversarial_scenarios": 10,
        "real_money_enabled": False,
        "live_keys_accepted": False,
    }


@app.post(
    "/api/v1/evaluations/run",
    response_model=AdversarialEvaluationReport,
)
def run_adversarial_evaluation() -> AdversarialEvaluationReport:
    """Run the fixed offline attack suite in an isolated temporary ledger."""

    return evaluation_service.run()


@app.get(
    "/api/v1/evaluations/{run_id}",
    response_model=AdversarialEvaluationReport,
)
def get_adversarial_evaluation(run_id: str) -> AdversarialEvaluationReport:
    try:
        return evaluation_service.get(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="evaluation run was not found") from exc


@app.post("/api/v1/policies/validate", response_model=PolicyDefinition)
def validate_policy(policy: PolicyDefinition) -> PolicyDefinition:
    """Validate a typed policy without representing schema parsing as proof."""

    return policy


@app.post("/api/v1/policies/compile", response_model=PolicyCompilationResult)
def compile_policy(request: PolicyCompilationRequest) -> PolicyCompilationResult:
    """Compile untrusted policy prose into a reviewable typed candidate."""

    try:
        return policy_compiler.compile(request)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=f"policy compiler failed: {exc}") from exc


@app.post("/api/v1/policies/verify", response_model=VerificationResult)
def verify_policy(request: VerificationRequest) -> VerificationResult:
    """Run a bounded search for cross-request policy violations."""

    return proof_service.record(request, verify_correlated_payments(request))


@app.get("/api/v1/proofs", response_model=list[ProofRecord])
def list_proofs(limit: int = Query(default=20, ge=1, le=100)) -> list[ProofRecord]:
    return proof_service.list(limit)


@app.get("/api/v1/proofs/{proof_run_id}", response_model=ProofRecord)
def get_proof(proof_run_id: str) -> ProofRecord:
    try:
        return proof_service.get(proof_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="proof run was not found") from exc


@app.post("/api/v1/proofs/{proof_run_id}/replay", response_model=ProofReplayResult)
def replay_proof(proof_run_id: str) -> ProofReplayResult:
    try:
        return proof_service.replay(proof_run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="proof run was not found") from exc


@app.post("/api/v1/runtime/evaluate", response_model=RuntimeComparison)
def evaluate_runtime_action(request: RuntimeEvaluationRequest) -> RuntimeComparison:
    """Compare a request-local gateway with stateful policy enforcement."""

    try:
        return runtime_guard.evaluate(request)
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/v1/demo/delegations/evaluate",
    response_model=DelegationComparison,
)
def evaluate_demo_delegation(
    request: DelegationEvaluationRequest,
) -> DelegationComparison:
    """Exercise the authority graph only with the offline payment simulator."""

    require_simulator_approval_demo()
    try:
        return runtime_guard.evaluate_delegation(request)
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def require_simulator_approval_demo() -> None:
    if settings.razorpay_mode != "simulate":
        raise HTTPException(
            status_code=403,
            detail="demo approvals are disabled outside the offline payment simulator",
        )


@app.post("/api/v1/demo/refunds/evaluate", response_model=RefundEvaluationResult)
def evaluate_demo_refund(
    request: RefundEvaluationRequest,
) -> RefundEvaluationResult:
    """Execute a cumulative refund only inside the offline simulator."""

    require_simulator_approval_demo()
    try:
        return refund_service.evaluate(request)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/v1/demo/approvals/challenges", response_model=ApprovalChallenge)
def create_demo_approval_challenge(
    request: ApprovalChallengeRequest,
) -> ApprovalChallenge:
    """Create an action-bound approval only for the offline simulator."""

    require_simulator_approval_demo()
    try:
        return approval_service.create_challenge(request)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/v1/demo/approvals/challenges/{challenge_id}/decide",
    response_model=ApprovalChallenge,
)
def decide_demo_approval_challenge(
    challenge_id: str, request: ApprovalDecisionRequest
) -> ApprovalChallenge:
    """Simulate a human decision; never available with Razorpay Test Mode."""

    require_simulator_approval_demo()
    try:
        return approval_service.decide(challenge_id, request)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/v1/runtime/actions/{action_id}/commit",
    response_model=ActionTransitionResult,
)
def commit_runtime_action(
    action_id: str, request: ActionTransitionRequest
) -> ActionTransitionResult:
    try:
        return runtime_guard.commit(
            request.policy_id, request.policy_version, action_id
        )
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/api/v1/runtime/actions/{action_id}/release",
    response_model=ActionTransitionResult,
)
def release_runtime_action(
    action_id: str, request: ActionTransitionRequest
) -> ActionTransitionResult:
    try:
        return runtime_guard.release(
            request.policy_id, request.policy_version, action_id
        )
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/api/v1/runtime/policies/{policy_id}/state",
    response_model=RuntimeStateResponse,
)
def get_runtime_state(
    policy_id: str,
    version: int = Query(default=1, ge=1),
) -> RuntimeStateResponse:
    try:
        return runtime_guard.state(policy_id, version)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/executions/orders", response_model=OrderExecutionResult)
def create_provider_order(request: OrderExecutionRequest) -> OrderExecutionResult:
    """Create one idempotent provider order for an approved reservation."""

    try:
        return payment_execution_service.create_order(request)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post(
    "/api/v1/executions/confirm",
    response_model=PaymentConfirmationResult,
)
def confirm_provider_payment(
    request: PaymentConfirmationRequest,
) -> PaymentConfirmationResult:
    try:
        return payment_execution_service.confirm_payment(request)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PaymentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/v1/webhooks/razorpay", response_model=WebhookResult)
async def receive_razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(...),
    x_razorpay_event_id: str = Header(...),
) -> WebhookResult:
    if webhook_service is None:
        raise HTTPException(status_code=503, detail="Razorpay webhook secret is not configured")
    raw_body = await request.body()
    try:
        return webhook_service.process(
            raw_body, x_razorpay_signature, x_razorpay_event_id
        )
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
