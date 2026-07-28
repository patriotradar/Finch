import asyncio

import app as entrypoint


def test_health_does_not_expose_internal_trace(monkeypatch):
    monkeypatch.setattr(entrypoint, "_load_finch", lambda: None)
    monkeypatch.setattr(entrypoint, "_finch_error", "SECRET TRACE")

    body = asyncio.run(entrypoint.healthz())

    assert body == {"ok": False, "service": "aegis"}
    assert "SECRET TRACE" not in repr(body)
