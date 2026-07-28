"""Vercel entrypoint for Aegis."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force serverless-safe defaults ASAP
if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    os.environ.setdefault("FINCH_MEMORY_BACKEND", "json")
    os.environ.setdefault("FINCH_DATA_DIR", "/tmp/finch-data")

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

# Always export an app so the runtime never dies at import
app = FastAPI(title="Aegis", version="2.0")

_finch = None
_finch_error = None


def _load_finch():
    global _finch, _finch_error
    if _finch is not None:
        return _finch
    if _finch_error is not None:
        return None
    try:
        from web import chat_server as cs  # noqa: WPS433
        _finch = cs.app
        return _finch
    except Exception:
        _finch_error = traceback.format_exc()
        print("[Finch] lazy load failed:\n", _finch_error)
        return None


@app.get("/healthz")
async def healthz():
    f = _load_finch()
    return {
        "ok": f is not None,
        "service": "aegis",
    }


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def catch_all(full_path: str, request: Request):
    """Proxy every request into the real Finch FastAPI app."""
    fapp = _load_finch()
    if fapp is None:
        body = "<h1>Aegis is temporarily unavailable</h1><p>Please try again shortly.</p>"
        if full_path.startswith("api") or "application/json" in (request.headers.get("accept") or ""):
            return JSONResponse({"error": "service_unavailable"}, status_code=503)
        return HTMLResponse(body, status_code=503)

    # Forward into Finch ASGI app
    scope = dict(request.scope)
    # Keep path as-is (includes leading / via full_path empty or filled)
    if full_path:
        scope["path"] = "/" + full_path
        scope["raw_path"] = ("/" + full_path).encode()
    else:
        scope["path"] = "/"
        scope["raw_path"] = b"/"

    async def receive():
        return await request.receive()

    status_code = 500
    response_headers = []
    body_chunks = []

    async def send(message):
        nonlocal status_code, response_headers
        if message["type"] == "http.response.start":
            status_code = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    try:
        await fapp(scope, receive, send)
    except Exception:
        tb = traceback.format_exc()
        print("[Finch] request failed:\n", tb)
        if full_path.startswith("api") or "application/json" in (request.headers.get("accept") or ""):
            return JSONResponse({"error": "internal_error"}, status_code=500)
        return HTMLResponse(
            "<h1>Something went wrong</h1><p>Please try again shortly.</p>",
            status_code=500,
        )

    headers = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
               for k, v in response_headers}
    # Drop hop-by-hop headers
    headers.pop("content-length", None)
    content = b"".join(body_chunks)
    return Response(content=content, status_code=status_code, headers=headers)
