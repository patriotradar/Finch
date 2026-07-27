"""Classic Vercel Python fallback entrypoint."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from web.chat_server import app  # noqa: F401
