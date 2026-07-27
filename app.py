"""Vercel entrypoint — exports the FastAPI app."""
from web.chat_server import app  # noqa: F401
