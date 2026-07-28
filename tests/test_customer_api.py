from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import Base, build_engine, build_session_factory
from web.customer_api import build_customer_router


def client(monkeypatch):
    monkeypatch.setenv("AEGIS_DEV_EXPOSE_TOKENS", "1")
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(build_customer_router(build_session_factory(engine)))
    return TestClient(app)


def verified_login(client):
    registration = client.post("/api/account/register", json={
        "company_name": "Alpha Ltd",
        "email": "owner@alpha.example",
        "password": "SecureAccount42",
    })
    assert registration.status_code == 201
    verification = registration.json()["verification_token"]
    assert client.post("/api/account/verify-email", json={"token": verification}).status_code == 200
    login = client.post("/api/account/login", json={
        "email": "owner@alpha.example",
        "password": "SecureAccount42",
    })
    assert login.status_code == 200
    return login.json()["csrf_token"]


def test_customer_asset_journey_requires_csrf_and_authority(monkeypatch):
    with client(monkeypatch) as api:
        csrf = verified_login(api)
        assert api.post("/api/account/assets", json={
            "value": "alpha.example",
            "authority_confirmed": True,
        }).status_code == 401

        added = api.post(
            "/api/account/assets",
            json={"value": "https://alpha.example/path", "authority_confirmed": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert added.status_code == 201
        asset_id = added.json()["id"]
        assert api.get("/api/account/assets").json()["assets"][0]["value"] == "alpha.example"
        assert api.delete(
            f"/api/account/assets/{asset_id}",
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200
        assert api.get("/api/account/assets").json()["assets"] == []


def test_customer_session_and_logout(monkeypatch):
    with client(monkeypatch) as api:
        csrf = verified_login(api)
        current = api.get("/api/account/session")
        assert current.status_code == 200
        assert current.json()["workspace"]["company_name"] == "Alpha Ltd"
        assert api.post("/api/account/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert api.get("/api/account/session").status_code == 401


def test_owner_invites_user_and_member_accepts(monkeypatch):
    with client(monkeypatch) as api:
        csrf = verified_login(api)
        invited = api.post(
            "/api/account/users/invite",
            json={"email": "member@alpha.example"},
            headers={"X-CSRF-Token": csrf},
        )
        assert invited.status_code == 201
        token = invited.json()["invitation_token"]
        assert api.post("/api/account/users/accept-invite", json={
            "token": token,
            "password": "MemberPassword42",
        }).status_code == 200
        users = api.get("/api/account/users").json()["users"]
        assert len(users) == 2
        assert any(user["email"] == "member@alpha.example" and user["verified"] for user in users)
        assert api.post("/api/account/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        member_login = api.post("/api/account/login", json={
            "email": "member@alpha.example",
            "password": "MemberPassword42",
        })
        assert member_login.status_code == 200
        assert api.get("/api/account/users").status_code == 401
        assert api.post(
            "/api/account/cancel",
            headers={"X-CSRF-Token": member_login.json()["csrf_token"]},
        ).status_code == 401


def test_workspace_export_is_tenant_scoped_and_cancellation_revokes_session(monkeypatch):
    with client(monkeypatch) as api:
        csrf = verified_login(api)
        api.post(
            "/api/account/assets",
            json={"value": "alpha.example", "authority_confirmed": True},
            headers={"X-CSRF-Token": csrf},
        )
        exported = api.get("/api/account/export")
        assert exported.status_code == 200
        assert exported.json()["workspace"]["company_name"] == "Alpha Ltd"
        assert exported.json()["assets"][0]["value"] == "alpha.example"
        assert "password_hash" not in exported.text

        cancelled = api.post("/api/account/cancel", headers={"X-CSRF-Token": csrf})
        assert cancelled.status_code == 200
        assert api.get("/api/account/session").status_code == 401


def test_owner_can_start_fixed_price_checkout(monkeypatch):
    monkeypatch.setattr(
        "web.customer_api.StripeCheckout.create_session",
        lambda self, workspace_id, email: {
            "id": "cs_test_workspace",
            "url": "https://checkout.stripe.com/c/pay/test",
        },
    )
    with client(monkeypatch) as api:
        csrf = verified_login(api)
        checkout = api.post(
            "/api/account/billing/checkout",
            headers={"X-CSRF-Token": csrf},
        )
        assert checkout.status_code == 200
        assert checkout.json()["checkout_url"].startswith("https://checkout.stripe.com/")
        billing = api.get("/api/account/billing").json()
        assert billing["subscription"]["status"] == "pending"
        assert billing["subscription"]["amount_pence"] == 99500
