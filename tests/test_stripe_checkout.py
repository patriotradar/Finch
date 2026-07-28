import hashlib
import hmac
import json
import time
import urllib.parse

from core.stripe_checkout import StripeCheckout


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        return json.dumps({
            "id": "cs_test_123",
            "url": "https://checkout.stripe.com/c/pay/test",
        }).encode()


def test_checkout_uses_fixed_price_and_workspace_reference(monkeypatch):
    captured = {}

    def open_request(request, timeout):
        captured["body"] = request.data.decode()
        captured["authorization"] = request.headers["Authorization"]
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    checkout = StripeCheckout(
        secret_key="sk_test_example",
        webhook_secret="whsec_" + "x" * 32,
        public_url="https://aegis.example",
    )
    created = checkout.create_session("workspace-1", "owner@example.test")
    assert created["id"] == "cs_test_123"
    form = urllib.parse.parse_qs(captured["body"])
    assert form["line_items[0][price_data][unit_amount]"] == ["99500"]
    assert form["client_reference_id"] == ["workspace-1"]
    assert captured["authorization"].startswith("Bearer sk_test_")


def test_webhook_signature_is_verified(monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(time, "time", lambda: now)
    secret = "whsec_" + "z" * 32
    raw = b'{"id":"evt_1","type":"checkout.session.completed"}'
    digest = hmac.new(
        secret.encode(), str(now).encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    checkout = StripeCheckout(
        secret_key="sk_test_example",
        webhook_secret=secret,
        public_url="https://aegis.example",
    )
    assert checkout.verify_event(raw, f"t={now},v1={digest}")["id"] == "evt_1"
