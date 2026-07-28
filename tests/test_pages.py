import importlib
import sys

from fastapi.testclient import TestClient


def test_public_customer_and_legal_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("FINCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'accounts.db'}")
    sys.modules.pop("web.chat_server", None)
    module = importlib.import_module("web.chat_server")
    with TestClient(module.app) as client:
        landing = client.get("/")
        assert landing.status_code == 200
        assert "Know what the public internet shows" in landing.text
        assert "£995" in landing.text
        assert client.get("/chat").status_code == 200
        assert client.get("/account").status_code == 200
        assert client.get("/legal/privacy").status_code == 200
        assert client.get("/legal/terms").status_code == 200
        assert client.get("/legal/acceptable-use").status_code == 200
        assert client.get("/brand/aegis-mark.svg").status_code == 200
