"""PayPal Orders integration for the fixed Aegis annual licence."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid

from core.billing import ANNUAL_PRICE_PENCE


class PayPalCheckout:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        public_url: str | None = None,
        environment: str | None = None,
    ):
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID") or ""
        self.client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET") or ""
        explicit_url = (
            public_url
            or os.environ.get("AEGIS_PUBLIC_URL")
            or os.environ.get("FINCH_WEB_CHAT_URL")
            or ""
        )
        vercel_url = (
            os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
            or os.environ.get("VERCEL_URL")
            or ""
        )
        self.public_url = (
            explicit_url.rstrip("/")
            if explicit_url
            else f"https://{vercel_url.rstrip('/')}" if vercel_url else ""
        )
        selected = (environment or os.environ.get("PAYPAL_ENVIRONMENT") or "sandbox").lower()
        self.api_base = (
            "https://api-m.paypal.com"
            if selected == "live"
            else "https://api-m.sandbox.paypal.com"
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and self.public_url.startswith("https://")
        )

    def _access_token(self) -> str:
        if not self.configured:
            raise RuntimeError("checkout_not_configured")
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        request = urllib.request.Request(
            f"{self.api_base}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        payload = self._open_json(request)
        if not payload.get("access_token"):
            raise RuntimeError("checkout_provider_unavailable")
        return str(payload["access_token"])

    def create_order(self, workspace_id: str) -> dict:
        token = self._access_token()
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": workspace_id,
                "custom_id": workspace_id,
                "description": "Aegis annual founding licence",
                "amount": {"currency_code": "GBP", "value": f"{ANNUAL_PRICE_PENCE / 100:.2f}"},
            }],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "Aegis",
                        "user_action": "PAY_NOW",
                        "return_url": f"{self.public_url}/account?paypal=return",
                        "cancel_url": f"{self.public_url}/account?paypal=cancelled",
                    }
                }
            },
        }
        request = urllib.request.Request(
            f"{self.api_base}/v2/checkout/orders",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": f"aegis-{workspace_id}-{uuid.uuid4()}",
                "Prefer": "return=representation",
            },
            method="POST",
        )
        payload = self._open_json(request)
        approve = next(
            (item.get("href") for item in payload.get("links", []) if item.get("rel") == "payer-action"),
            None,
        ) or next(
            (item.get("href") for item in payload.get("links", []) if item.get("rel") == "approve"),
            None,
        )
        if not payload.get("id") or not str(approve or "").startswith("https://"):
            raise RuntimeError("invalid_checkout_response")
        return {"id": payload["id"], "url": approve}

    def capture_order(self, order_id: str) -> dict:
        if not order_id or len(order_id) > 100:
            raise ValueError("invalid_order_id")
        token = self._access_token()
        request = urllib.request.Request(
            f"{self.api_base}/v2/checkout/orders/{order_id}/capture",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": f"aegis-capture-{order_id}",
                "Prefer": "return=representation",
            },
            method="POST",
        )
        payload = self._open_json(request)
        try:
            capture = payload["purchase_units"][0]["payments"]["captures"][0]
            amount = capture["amount"]
        except Exception as error:
            raise RuntimeError("invalid_capture_response") from error
        if payload.get("status") != "COMPLETED" or capture.get("status") != "COMPLETED":
            raise RuntimeError("payment_not_completed")
        if amount.get("currency_code") != "GBP" or amount.get("value") != "995.00":
            raise RuntimeError("unexpected_payment_amount")
        return {
            "order_id": payload["id"],
            "capture_id": capture["id"],
            "amount_pence": ANNUAL_PRICE_PENCE,
            "currency": "GBP",
            "raw": {
                "order_id": payload["id"],
                "capture_id": capture["id"],
                "status": capture["status"],
            },
        }

    @staticmethod
    def _open_json(request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read(500_000))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError("checkout_provider_unavailable") from error
