"""Vercel FastAPI entrypoint — zero-config detection looks for `app` here."""
from web.chat_server import app  # noqa: F401

__all__ = ["app"]
