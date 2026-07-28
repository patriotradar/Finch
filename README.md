# Aegis

Aegis is customer-controlled, passive public-exposure monitoring software operated
by a UK sole trader. Harold is its assistant.

The founding licence is **£995 for 12 months**, covering up to five authorised
users and 25 customer-approved assets. Checkout uses PayPal.

## Safety boundary

Aegis uses public certificate-transparency, DNS, TLS, HTTP-header and published
advisory information. It does not authenticate, submit forms, brute-force paths,
guess accounts, send exploit payloads or access protected data. Observations and
potential indicators require qualified IT verification.

## Local development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn web.chat_server:app --reload
```

Run tests with:

```bash
.venv/bin/pytest -q
```

Production configuration and release gates are documented in
`docs/PRODUCTION_RUNBOOK.md` and `docs/LAUNCH_READINESS.md`. Production must use
PostgreSQL; SQLite is for local development and automated tests only.

Existing `FINCH_*` environment names remain supported internally for compatibility.
No production deployment is authorised by repository changes.
