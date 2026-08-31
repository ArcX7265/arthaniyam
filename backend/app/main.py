from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.policy.models import PolicyDefinition
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
    RuntimeComparison,
    RuntimeEvaluationRequest,
    RuntimeStateResponse,
)


app = FastAPI(
    title="ArthaNiyam API",
    description="Verify and enforce bounded financial policies for autonomous systems.",
    version="0.1.0",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
app.mount("/assets", StaticFiles(directory=FRONTEND_ROOT), name="assets")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/policies/validate", response_model=PolicyDefinition)
def validate_policy(policy: PolicyDefinition) -> PolicyDefinition:
    """Validate a typed policy without representing schema parsing as proof."""

    return policy


@app.post("/api/v1/policies/verify", response_model=VerificationResult)
def verify_policy(request: VerificationRequest) -> VerificationResult:
    """Run a bounded search for cross-request policy violations."""

    return verify_correlated_payments(request)


@app.post("/api/v1/runtime/evaluate", response_model=RuntimeComparison)
def evaluate_runtime_action(request: RuntimeEvaluationRequest) -> RuntimeComparison:
    """Compare a request-local gateway with stateful policy enforcement."""

    try:
        return runtime_guard.evaluate(request)
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
