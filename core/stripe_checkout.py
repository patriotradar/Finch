"""Minimal hosted Stripe Checkout adapter with verified webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

from core.billing import ANNUAL_PRICE_PENCE


STRIPE_API = "https://api.stripe.com/v1"


class StripeCheckout:
    def __init__(
        self,
        secret_key: str | None = None,
        webhook_secret: str | None = None,
        public_url: str | None = None,
    ):
        self.secret_key = secret_key or os.environ.get("STRIPE_SECRET_KEY") or ""
        self.webhook_secret = webhook_secret or os.environ.get("STRIPE_WEBHOOK_SECRET") or ""
        self.public_url = (
            public_url
            or os.environ.get("AEGIS_PUBLIC_URL")
            or os.environ.get("FINCH_WEB_CHAT_URL")
            or ""
        ).rstrip("/")

    @property
    def configured(self) -> bool:
        return (
            self.secret_key.startswith("sk_")
            and len(self.webhook_secret) >= 24
            and self.public_url.startswith("https://")
        )

    def create_session(self, workspace_id: str, customer_email: str) -> dict:
        if not self.configured:
            raise RuntimeError("checkout_not_configured")
        form = {
            "mode": "payment",
            "client_reference_id": workspace_id,
            "customer_email": customer_email,
            "success_url": f"{self.public_url}/account?payment=success",
            "cancel_url": f"{self.public_url}/account?payment=cancelled",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "gbp",
            "line_items[0][price_data][unit_amount]": str(ANNUAL_PRICE_PENCE),
            "line_items[0][price_data][product_data][name]": "Aegis annual founding licence",
            "line_items[0][price_data][product_data][description]":
                "12 months, up to five authorised users and 25 approved assets",
            "metadata[workspace_id]": workspace_id,
        }
        request = urllib.request.Request(
            f"{STRIPE_API}/checkout/sessions",
            data=urllib.parse.urlencode(form).encode(),
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": f"aegis-checkout-{workspace_id}-{int(time.time() // 300)}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read(200_000))
        except Exception as error:
            raise RuntimeError("checkout_provider_unavailable") from error
        if not payload.get("id") or not str(payload.get("url") or "").startswith("https://"):
            raise RuntimeError("invalid_checkout_response")
        return payload

    def verify_event(self, raw_body: bytes, signature_header: str, tolerance: int = 300) -> dict:
        if len(self.webhook_secret) < 24:
            raise RuntimeError("webhook_not_configured")
        parts: dict[str, list[str]] = {}
        for item in (signature_header or "").split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                parts.setdefault(key, []).append(value)
        try:
            timestamp = int(parts["t"][0])
            signatures = parts["v1"]
        except Exception as error:
            raise ValueError("invalid_webhook_signature") from error
        if abs(int(time.time()) - timestamp) > tolerance:
            raise ValueError("expired_webhook_signature")
        signed = str(timestamp).encode() + b"." + raw_body
        expected = hmac.new(
            self.webhook_secret.encode(), signed, hashlib.sha256
        ).hexdigest()
        if not any(hmac.compare_digest(expected, value) for value in signatures):
            raise ValueError("invalid_webhook_signature")
        try:
            return json.loads(raw_body)
        except Exception as error:
            raise ValueError("invalid_webhook_payload") from error
