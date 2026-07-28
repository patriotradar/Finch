import importlib
import sys

from fastapi.testclient import TestClient


def load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("FINCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FINCH_ADMIN_PASSWORD", "owner-test-password")
    monkeypatch.setenv("FINCH_SESSION_SECRET", "test-secret-that-is-long-and-independent")
    sys.modules.pop("web.chat_server", None)
    module = importlib.import_module("web.chat_server")
    return module.app


def test_private_api_requires_login_and_csrf(monkeypatch, tmp_path):
    with TestClient(load_app(monkeypatch, tmp_path)) as client:
        assert client.get("/api/admin/dashboard").status_code == 401
        assert client.post("/api/admin/chat", json={"start": True}).status_code == 401

        failed = client.post("/api/admin/login", json={"password": "wrong"})
        assert failed.status_code == 401

        login = client.post("/api/admin/login", json={"password": "owner-test-password"})
        assert login.status_code == 200
        assert "finch-session" not in login.text
        csrf = login.json()["csrf_token"]

        assert client.get("/api/admin/dashboard").status_code == 200
        assert client.post("/api/admin/chat", json={"start": True}).status_code == 403
        assert client.post(
            "/api/admin/chat",
            json={"start": True},
            headers={"X-CSRF-Token": csrf},
        ).status_code == 200

        logout = client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf})
        assert logout.status_code == 200
        assert client.get("/api/admin/dashboard").status_code == 401


def test_owner_login_has_no_default_password(monkeypatch, tmp_path):
    monkeypatch.setenv("FINCH_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FINCH_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("FINCH_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("FINCH_SESSION_SECRET", raising=False)
    sys.modules.pop("web.chat_server", None)
    module = importlib.import_module("web.chat_server")
    with TestClient(module.app) as client:
        response = client.post("/api/admin/login", json={"password": "finch"})
        assert response.status_code == 503
        assert response.json()["error"] == "owner_login_not_configured"

