from __future__ import annotations

from hashlib import sha256
from typing import Protocol

import httpx

from app.payments.models import ProviderOrder, ProviderPayment
from app.runtime.models import FinancialAction
from app.settings import Settings


class PaymentGatewayError(RuntimeError):
    pass


class PaymentGateway(Protocol):
    def create_order(self, action: FinancialAction) -> ProviderOrder: ...
    def fetch_payment(self, payment_id: str) -> ProviderPayment: ...


def receipt_for(action_id: str) -> str:
    digest = sha256(action_id.encode("utf-8")).hexdigest()[:28]
    return f"an_{digest}"


class SimulatedRazorpayGateway:
    """Deterministic offline gateway with the same order boundary."""

    def create_order(self, action: FinancialAction) -> ProviderOrder:
        digest = sha256(action.model_dump_json().encode("utf-8")).hexdigest()[:18]
        return ProviderOrder(
            provider="simulator",
            mode="simulate",
            order_id=f"order_sim_{digest}",
            amount=action.amount,
            currency="INR",
            receipt=receipt_for(action.action_id),
            status="created",
        )

    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        raise PaymentGatewayError("simulated payments are confirmed locally")


class RazorpayTestGateway:
    API_URL = "https://api.razorpay.com/v1/orders"

    def __init__(self, key_id: str, key_secret: str) -> None:
        if not key_id.startswith("rzp_test_"):
            raise PaymentGatewayError(
                "Only Razorpay Test Mode keys beginning with rzp_test_ are allowed"
            )
        if not key_secret:
            raise PaymentGatewayError("Razorpay Test Mode key secret is required")
        self.key_id = key_id
        self.key_secret = key_secret

    def create_order(self, action: FinancialAction) -> ProviderOrder:
        payload = {
            "amount": action.amount,
            "currency": "INR",
            "receipt": receipt_for(action.action_id),
            "notes": {
                "arthaniyam_action_id": action.action_id[:256],
                "invoice_id": action.invoice_id[:256],
                "agent_id": action.agent_id[:256],
            },
        }
        try:
            response = httpx.post(
                self.API_URL,
                auth=(self.key_id, self.key_secret),
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PaymentGatewayError(
                f"Razorpay Test Mode order creation failed: {exc}"
            ) from exc
        body = response.json()
        return ProviderOrder(
            provider="razorpay",
            mode="test",
            order_id=body["id"],
            amount=body["amount"],
            currency=body["currency"],
            receipt=body.get("receipt") or payload["receipt"],
            status=body["status"],
        )

    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        try:
            response = httpx.get(
                f"https://api.razorpay.com/v1/payments/{payment_id}",
                auth=(self.key_id, self.key_secret),
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PaymentGatewayError(
                f"Razorpay Test Mode payment lookup failed: {exc}"
            ) from exc
        body = response.json()
        return ProviderPayment(
            payment_id=body["id"],
            order_id=body["order_id"],
            amount=body["amount"],
            currency=body["currency"],
            status=body["status"],
        )


def create_payment_gateway(settings: Settings) -> PaymentGateway:
    if settings.razorpay_mode == "simulate":
        return SimulatedRazorpayGateway()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise PaymentGatewayError(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required in test mode"
        )
    return RazorpayTestGateway(
        settings.razorpay_key_id, settings.razorpay_key_secret
    )
