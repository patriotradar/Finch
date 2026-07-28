import importlib
import sys

from fastapi.testclient import TestClient


def test_public_chat_is_text_only_and_transparent(monkeypatch, tmp_path):
    monkeypatch.setenv("FINCH_DATA_DIR", str(tmp_path))
    sys.modules.pop("web.chat_server", None)
    module = importlib.import_module("web.chat_server")
    with TestClient(module.app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'id="micBtn"' not in page.text
        assert 'id="speakBtn"' not in page.text
        assert "Harold" in page.text

        greeting = client.post("/api/chat", json={"prospect_id": "test", "start": True})
        assert greeting.status_code == 200
        assert "Harold" in greeting.json()["content"]
        assert "passive" in greeting.json()["content"].lower()
        assert "technical co-founder" not in greeting.json()["content"].lower()

