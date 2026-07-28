#!/bin/bash
# Local Aegis development setup. Production uses the documented Vercel runbook.

set -e

echo "Aegis local setup"
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data/memory data/leads data/clients data/audio

echo "Setup complete."
echo "Run migrations: .venv/bin/alembic upgrade head"
echo "Run tests:      .venv/bin/pytest -q"
echo "Start locally:  .venv/bin/uvicorn web.chat_server:app --reload"
echo "Keep secrets in environment variables; see .env.example."
