"""Vercel FastAPI entrypoint — surfaces real import errors instead of a blank 500."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
    os.environ.setdefault("FINCH_MEMORY_BACKEND", "json")
    os.environ.setdefault("FINCH_DATA_DIR", "/tmp/finch-data")

_IMPORT_ERROR = None
app = None

try:
    from web.chat_server import app as _app  # noqa: E402
    app = _app
except Exception:
    _IMPORT_ERROR = traceback.format_exc()
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(title="Finch (boot error)")

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
    async def _boot_error(full_path: str = ""):
        body = (
            "<html><body style='font-family:monospace;background:#111;color:#f88;padding:24px'>"
            "<h1>Finch failed to start</h1>"
            f"<pre style='white-space:pre-wrap;color:#fcc'>{_IMPORT_ERROR}</pre>"
            "</body></html>"
        )
        if full_path.startswith("api/"):
            return JSONResponse({"error": "boot_failed", "trace": _IMPORT_ERROR}, status_code=500)
        return HTMLResponse(body, status_code=500)

# Soft signal for debugging cold start
if _IMPORT_ERROR:
    print("[Finch] BOOT FAILURE:\n", _IMPORT_ERROR)
else:
    print("[Finch] boot OK")
