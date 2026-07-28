"""Vercel FastAPI entrypoint."""
import os
import sys
from pathlib import Path

# Ensure project root is on path (Vercel packaging edge cases)
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Force light memory on Vercel before any imports that touch storage
if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
    os.environ.setdefault("FINCH_MEMORY_BACKEND", "json")
    os.environ.setdefault("FINCH_DATA_DIR", "/tmp/finch-data")

from web.chat_server import app  # noqa: E402,F401
