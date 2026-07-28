import json

from core.paypal_checkout import PayPalCheckout


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        return json.dumps(self.payload).encode()


def checkout():
    return PayPalCheckout(
        client_id="client-id",
        client_secret="client-secret",
        public_url="https://aegis.example",
        environment="sandbox",
    )


def test_paypal_order_uses_fixed_price_and_approval_link(monkeypatch):
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/v1/oauth2/token"):
            return Response({"access_token": "access-token"})
        return Response({
            "id": "ORDER-1",
            "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow"}],
        })

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    created = checkout().create_order("workspace-1")
    assert created["id"] == "ORDER-1"
    body = json.loads(requests[1].data)
    assert body["purchase_units"][0]["amount"] == {
        "currency_code": "GBP", "value": "995.00"
    }
    assert body["purchase_units"][0]["custom_id"] == "workspace-1"


def test_paypal_capture_requires_completed_exact_gbp_amount(monkeypatch):
    def open_request(request, timeout):
        if request.full_url.endswith("/v1/oauth2/token"):
            return Response({"access_token": "access-token"})
        return Response({
            "id": "ORDER-1",
            "status": "COMPLETED",
            "purchase_units": [{
                "payments": {"captures": [{
                    "id": "CAPTURE-1",
                    "status": "COMPLETED",
                    "amount": {"currency_code": "GBP", "value": "995.00"},
                }]}
            }],
        })

    monkeypatch.setattr("urllib.request.urlopen", open_request)
    captured = checkout().capture_order("ORDER-1")
    assert captured["capture_id"] == "CAPTURE-1"
    assert captured["amount_pence"] == 99500
