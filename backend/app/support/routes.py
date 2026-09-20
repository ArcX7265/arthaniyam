from fastapi import APIRouter, Depends, HTTPException, Request

from app.runtime.guard import RuntimeActionNotFoundError, RuntimeTransitionError, runtime_guard
from app.settings import settings
from app.support.models import (ApprovalRequest, ComplaintRequest, TakeoverRequest,
                                InvestigationRequest, InformationRequest, ConfirmProposalRequest)
from app.support.service import SupportService
from app.support.agent import configured_agent
from app.support.investigations import InvestigationService


service = SupportService(runtime_guard.repository)


def investigator() -> InvestigationService:
    return InvestigationService(service, configured_agent(settings))


def simulator_only(request: Request) -> None:
    if settings.razorpay_mode != "simulate":
        raise HTTPException(403, "support demo is disabled outside simulator mode")
    # Demo controls are NOT authentication. Bind the server to loopback only.
    if request.method != "GET":
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "cross-origin demo mutations are disabled")


router = APIRouter(prefix="/api/v1/support", tags=["Support simulator"], dependencies=[Depends(simulator_only)])


@router.get("/investigator/capabilities")
def investigator_capabilities():
    return {"mode": settings.support_investigator_mode,
            "configured": settings.support_investigator_mode != "openai" or bool(settings.openai_api_key and settings.openai_api_key != "replace_me"),
            "model": settings.ollama_model if settings.support_investigator_mode == "ollama" else settings.openai_model if settings.support_investigator_mode == "openai" else None,
            "max_tool_calls": 6, "timeout_seconds": 180 if settings.support_investigator_mode == "ollama" else 45,
            "requires_intent_confirmation": True,
            "external_data": "Ollama uses the local server at 127.0.0.1:11434; it must be running with the selected model installed. OpenAI mode sends complaint and selected synthetic evidence to OpenAI. Reference mode uses offline keywords."}


@router.post("/investigations", status_code=201)
def create_investigation(request: InvestigationRequest):
    return call(investigator().create, request)


@router.post("/requests/{case_id}/messages")
def add_information(case_id: str, request: InformationRequest):
    return call(investigator().add_information, case_id, request)


@router.post("/requests/{case_id}/confirm-proposal")
def confirm_proposal(case_id: str, request: ConfirmProposalRequest):
    return call(investigator().confirm, case_id, request.proposal_id)


def call(method, *args):
    try:
        return method(*args)
    except RuntimeActionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/demo/seed")
def seed():
    return call(service.seed)


@router.get("/payments")
def payments():
    return service.payments()


@router.get("/metrics")
def metrics():
    return service.metrics()


@router.get("/requests")
def requests():
    return service.list_cases()


@router.post("/requests", status_code=201)
def create(request: ComplaintRequest):
    return call(service.create, request)


@router.get("/requests/{case_id}")
def get(case_id: str):
    return call(service.get, case_id)


@router.post("/requests/{case_id}/investigate")
def investigate(case_id: str):
    case = call(service.get, case_id)
    if case.get("intake") == "investigator":
        return call(investigator().run, case_id)
    return call(service.investigate, case_id)


@router.post("/requests/{case_id}/approve")
def approve(case_id: str, request: ApprovalRequest):
    return call(service.approve, case_id, request)


@router.post("/requests/{case_id}/takeover")
def takeover(case_id: str, request: TakeoverRequest):
    return call(service.takeover, case_id, request.reason)
