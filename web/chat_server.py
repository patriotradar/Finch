"""
Finch Web App — installable PWA for your phone's home screen.

Two interfaces:
  /       — Public prospect chat (Finch sells for you)
  /admin  — Your phone dashboard (pipeline, handoffs, docs, co-founder chat)

Works as a Progressive Web App: open on your phone, tap "Add to Home Screen,"
and it becomes an app icon. No app store. No Telegram. Just Finch.

Prospects never see the admin panel. You never see the public chat.
"""

import json
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:
    print("FastAPI not installed. Install with: pip install fastapi uvicorn")
    raise

from sales.conversation import SalesConversation
from sales.crm import CRM
from pricing.engine import PricingEngine
from memory.vector_store import MemoryStore
from messaging.telegram_docs import FinchDocs
from messaging.mail_store import MailStore
from sales.pipeline import LeadPipeline
from web import admin_brain
from core.security import LoginThrottle, OwnerAuth, SESSION_COOKIE
from core.database import build_session_factory
from web.customer_api import build_customer_router
from core.controls import ControlService
from core.owner_assistant import OwnerConversationService, build_daily_briefing
from core.paypal_checkout import PayPalCheckout
from sales.outreach_policy import OptOutTokens, OutreachPolicy
from messaging.account_mailer import AccountMailer
import yaml


def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


app = FastAPI(title="Aegis", version="2.0")
db_session_factory = build_session_factory()
app.include_router(build_customer_router(db_session_factory, AccountMailer()))
config = load_config()
owner_auth = OwnerAuth.from_environment()
login_throttle = LoginThrottle()


@app.middleware("http")
async def protect_owner_api(request: Request, call_next):
    """Require an expiring owner session and CSRF proof for private APIs."""
    path = request.url.path
    public_owner_paths = {"/api/admin/login", "/api/admin/session"}
    if path.startswith("/api/admin/") and path not in public_owner_paths:
        session = owner_auth.validate(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        request.state.owner_session = session
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            import hmac
            supplied = request.headers.get("x-csrf-token", "")
            if not supplied or not hmac.compare_digest(supplied, session.csrf_token):
                return JSONResponse({"error": "csrf_validation_failed"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), geolocation=(), payment=(self), microphone=(self)",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "object-src 'none'; form-action 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self' wss:; frame-src https://www.paypal.com "
        "https://www.sandbox.paypal.com",
    )
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.url.path.startswith(("/api/admin/", "/api/account/")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

# Serverless hosts (Vercel/Lambda) only allow writes under /tmp
_IS_SERVERLESS = bool(
    os.environ.get("VERCEL")
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    or os.environ.get("VERCEL_ENV")
)
if _IS_SERVERLESS:
    os.environ.setdefault("FINCH_MEMORY_BACKEND", "json")

_DATA_ROOT = Path(os.environ.get("FINCH_DATA_DIR") or (
    "/tmp/finch-data" if _IS_SERVERLESS else "./data"
))
try:
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for _sub in ("memory", "clients", "sessions", "documents"):
        (_DATA_ROOT / _sub).mkdir(exist_ok=True)
except Exception as e:
    print(f"[Finch] data root setup failed: {e}")
    _DATA_ROOT = Path("/tmp/finch-data")
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for _sub in ("memory", "clients", "sessions", "documents"):
            (_DATA_ROOT / _sub).mkdir(exist_ok=True)

pricing = PricingEngine(config.get("pricing", {}))
_mem_cfg = config.get("memory", {}) or {}
memory = MemoryStore(
    persist_path=str(_DATA_ROOT / "memory"),
    collection_name=_mem_cfg.get("collection", "finch_memory"),
)
conversation_engine = SalesConversation(None, pricing, memory, config)
crm = CRM(data_dir=str(_DATA_ROOT / "clients"))
docs_engine = FinchDocs(docs_dir=str(_DATA_ROOT / "documents"))
mail_store = MailStore(str(_DATA_ROOT))
lead_pipeline = LeadPipeline(db_session_factory)

# Start with honest empty records. Real clients arrive through verified purchases.
try:
    Path(crm.data_dir).mkdir(parents=True, exist_ok=True)
    crm._load()
except Exception as e:
    print(f"[Finch] crm init: {e}")

prospect_sessions = {}
admin_sessions = {}
pending_handoffs = {}

def _session_path(kind: str, sid: str) -> Path:
    safe = "".join(c for c in sid if c.isalnum() or c in "-_")[:64] or "x"
    return _DATA_ROOT / "sessions" / f"{kind}_{safe}.json"

def _load_session(kind: str, sid: str):
    path = _session_path(kind, sid)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _save_session(kind: str, sid: str, state):
    path = _session_path(kind, sid)
    try:
        path.write_text(json.dumps(state, default=str), encoding="utf-8")
    except Exception as e:
        print(f"[Finch] session save failed: {e}")


def _load_admin_session(admin_id: str):
    cached = admin_sessions.get(admin_id)
    if cached is not None:
        return cached
    try:
        with db_session_factory() as db:
            _, messages = OwnerConversationService(db).load(admin_id)
            db.commit()
        state = {"history": messages}
    except Exception as error:
        print(f"[Aegis] owner history database read failed: {error}")
        state = _load_session("a", admin_id) or {"history": []}
    admin_sessions[admin_id] = state
    return state


def _save_admin_session(admin_id: str, state: dict):
    history = list(state.get("history") or [])[-80:]
    state["history"] = history
    try:
        with db_session_factory() as db:
            record, _ = OwnerConversationService(db).load(admin_id)
            OwnerConversationService(db).save(record, history)
            db.commit()
    except Exception as error:
        print(f"[Aegis] owner history database write failed: {error}")
        _save_session("a", admin_id, state)


def _owner_opening(admin_id: str, state: dict) -> str:
    try:
        with db_session_factory() as db:
            service = OwnerConversationService(db)
            record, persisted = service.load(admin_id)
            reply = service.daily_opening(record, persisted)
            db.commit()
        state["history"] = persisted
        return reply
    except Exception as error:
        print(f"[Aegis] owner briefing database read failed: {error}")
        return admin_brain.opening_line()


# ── STATIC FILES ─────────────────────────────────────────────────────

@app.get("/manifest.json")
async def webmanifest():
    return FileResponse(Path(__file__).parent / "manifest.json", media_type="application/manifest+json")

@app.get("/sw.js")
async def service_worker():
    return FileResponse(Path(__file__).parent / "sw.js", media_type="application/javascript")

@app.get("/static/icon-192.png")
async def icon_192():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="192" height="192" viewBox="0 0 192 192">'
        '<rect width="192" height="192" rx="32" fill="#0a0a0f"/>'
        '<text x="96" y="110" text-anchor="middle" font-family="serif" font-size="96" '
        'font-weight="bold" fill="#4ade80">A</text>'
        '<circle cx="150" cy="50" r="20" fill="#2563eb" opacity="0.8"/>'
        '</svg>'
    )
    from fastapi.responses import Response
    return Response(content=svg, media_type="image/svg+xml")

@app.get("/static/icon-512.png")
async def icon_512():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="80" fill="#0a0a0f"/>'
        '<text x="256" y="290" text-anchor="middle" font-family="serif" font-size="256" '
        'font-weight="bold" fill="#4ade80">A</text>'
        '<circle cx="390" cy="130" r="55" fill="#2563eb" opacity="0.8"/>'
        '</svg>'
    )
    from fastapi.responses import Response
    return Response(content=svg, media_type="image/svg+xml")


# ── PUBLIC: PROSPECT CHAT ────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing_page():
    return HTMLResponse((Path(__file__).parent / "landing.html").read_text(encoding="utf-8"))


@app.get("/chat", response_class=HTMLResponse)
async def prospect_chat():
    html_path = Path(__file__).parent / "chat.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse(PROSPECT_HTML)


@app.get("/account", response_class=HTMLResponse)
async def customer_account_page():
    return HTMLResponse((Path(__file__).parent / "account.html").read_text(encoding="utf-8"))


@app.get("/brand/aegis-mark.svg")
async def aegis_mark():
    return FileResponse(Path(__file__).parent / "aegis-mark.svg", media_type="image/svg+xml")


@app.get("/brand/aegis-wordmark.svg")
async def aegis_wordmark():
    return FileResponse(Path(__file__).parent / "aegis-wordmark.svg", media_type="image/svg+xml")


@app.get("/brand/{asset_name}")
async def aegis_brand_asset(asset_name: str):
    allowed = {
        "aegis-wordmark-light.svg": "image/svg+xml",
        "aegis-wordmark-dark.svg": "image/svg+xml",
        "aegis-icon-64.png": "image/png",
        "aegis-icon-192.png": "image/png",
        "aegis-icon-512.png": "image/png",
        "aegis-email-signature.png": "image/png",
    }
    media_type = allowed.get(asset_name)
    if media_type is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return FileResponse(Path(__file__).parent / asset_name, media_type=media_type)


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(Path(__file__).parent / "favicon.ico", media_type="image/x-icon")


@app.get("/legal/privacy", response_class=HTMLResponse)
async def privacy_notice():
    return HTMLResponse((Path(__file__).parent / "privacy.html").read_text(encoding="utf-8"))


@app.get("/legal/terms", response_class=HTMLResponse)
async def subscription_terms():
    return HTMLResponse((Path(__file__).parent / "terms.html").read_text(encoding="utf-8"))


@app.get("/legal/acceptable-use", response_class=HTMLResponse)
async def acceptable_use():
    return HTMLResponse((Path(__file__).parent / "acceptable-use.html").read_text(encoding="utf-8"))


@app.websocket("/ws/{prospect_id}")
async def prospect_websocket(websocket: WebSocket, prospect_id: str):
    await websocket.accept()

    if prospect_id not in prospect_sessions:
        state = conversation_engine.start_conversation(
            prospect_info={"source": "web_chat", "id": prospect_id, "started": datetime.now().isoformat()}
        )
        prospect_sessions[prospect_id] = state
        greeting = (
            "Hello. I'm Harold, the Aegis customer guide. I can explain how our "
            "passive, public-information monitoring works and help you decide whether "
            "it fits your organisation. What would you like to know?"
        )
        await websocket.send_text(json.dumps({"type": "message", "content": greeting, "stage": state["stage"]}))
    else:
        state = prospect_sessions[prospect_id]
        await websocket.send_text(json.dumps({"type": "message", "content": "Welcome back. Where were we?", "stage": state["stage"]}))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            prospect_message = msg.get("content", "")
            if not prospect_message.strip():
                continue

            if prospect_message.lower() in ("/reset", "/restart"):
                state = conversation_engine.start_conversation(prospect_info={"source": "web_chat", "id": prospect_id})
                prospect_sessions[prospect_id] = state
                await websocket.send_text(json.dumps({"type": "message", "content": "Starting fresh. What can I help you with?", "stage": "greeting"}))
                continue

            response, state = conversation_engine.handle_message(state, prospect_message)
            prospect_sessions[prospect_id] = state

            # Chain transitions: keep processing while stage advances
            # objections→pricing→closing→handoff all on one prospect message
            prev_stage = None
            for _ in range(5):
                prev_stage = state.get("stage")
                if prev_stage in ("closing", "handoff") and not state.get("ready_to_close"):
                    response, state = conversation_engine.handle_message(state, prospect_message)
                    prospect_sessions[prospect_id] = state
                elif prev_stage == "pricing":
                    response, state = conversation_engine.handle_message(state, prospect_message)
                    prospect_sessions[prospect_id] = state
                else:
                    break
                if state.get("stage") == prev_stage:
                    break

            await websocket.send_text(json.dumps({
                "type": "message", "content": response, "stage": state["stage"],
                "ready_to_close": state.get("ready_to_close", False),
            }))

            if state.get("ready_to_close"):
                email = _extract_email(prospect_message) or state["discovered"].get("email")
                company = (
                    state["discovered"].get("company")
                    or state["discovered"].get("domain")
                    or "unknown"
                )
                price = state.get("price_quoted") or {}
                annual = price.get("annual") or price.get("annual_price") or 995
                if email:
                    pending_handoffs[email] = {
                        "company": company,
                        "price": annual,
                        "source": "web_chat",
                        "stage": state.get("stage"),
                    }
                    # Persist into CRM so deals survive restarts
                    try:
                        deal_id = crm.add_deal(
                            company=company,
                            contact_email=email,
                            pricing_result={"annual_price": annual, **price},
                            source="web_chat",
                        )
                        crm.update_stage(deal_id, "won", note="Closed via web chat")
                    except Exception as e:
                        print(f"[Finch] CRM write failed: {e}")
                memory.remember(
                    f"Closed deal via web chat: {json.dumps(state['discovered'])} email={email} annual_price={annual}",
                    metadata={"type": "closed_deal", "prospect_id": prospect_id}
                )

    except WebSocketDisconnect:
        pass


# ── ADMIN: YOUR PHONE DASHBOARD ──────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    html_path = Path(__file__).parent / "admin.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse(ADMIN_HTML)


@app.post("/api/admin/login")
async def admin_login(request: Request):
    client_key = request.client.host if request.client else "unknown"
    if not login_throttle.allowed(client_key):
        return JSONResponse(
            {"authenticated": False, "error": "too_many_attempts"},
            status_code=429,
            headers={"Retry-After": "900"},
        )
    if not owner_auth.configured:
        return JSONResponse(
            {"authenticated": False, "error": "owner_login_not_configured"},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    password = str(body.get("password", ""))
    if not owner_auth.check_password(password):
        login_throttle.failure(client_key)
        return JSONResponse({"authenticated": False, "error": "invalid_credentials"}, status_code=401)

    login_throttle.reset(client_key)
    token, session = owner_auth.issue()
    response = JSONResponse({
        "authenticated": True,
        "csrf_token": session.csrf_token,
        "expires_at": session.expires_at,
    })
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=owner_auth.lifetime_seconds,
        httponly=True,
        secure=bool(os.environ.get("VERCEL") or request.url.scheme == "https"),
        samesite="strict",
        path="/",
    )
    return response


@app.get("/api/admin/session")
async def admin_session(request: Request):
    session = owner_auth.validate(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return JSONResponse({"authenticated": False}, status_code=401)
    return JSONResponse({
        "authenticated": True,
        "csrf_token": session.csrf_token,
        "expires_at": session.expires_at,
    })


@app.post("/api/admin/logout")
async def admin_logout():
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return response


@app.get("/api/admin/controls")
async def admin_controls():
    with db_session_factory() as session:
        return {"harold_paused": ControlService(session).is_paused()}


@app.post("/api/admin/controls/pause")
async def admin_pause_harold(request: Request):
    body = await request.json()
    paused = body.get("paused")
    if not isinstance(paused, bool):
        return JSONResponse({"error": "paused_boolean_required"}, status_code=400)
    with db_session_factory() as session:
        ControlService(session).set_paused(
            paused, changed_by="owner", reason=str(body.get("reason") or "")[:200]
        )
        session.commit()
    return {"harold_paused": paused}


@app.get("/api/admin/briefing")
async def admin_briefing():
    with db_session_factory() as session:
        return {"briefing": build_daily_briefing(session), "generated_at": datetime.now().isoformat()}


@app.get("/unsubscribe")
async def unsubscribe(token: str = ""):
    try:
        email = OptOutTokens().validate(token)
    except Exception:
        return HTMLResponse(
            "<h1>Invalid opt-out link</h1><p>No preferences were changed.</p>",
            status_code=400,
        )
    with db_session_factory() as session:
        OutreachPolicy(session).suppress(email, "opt_out", "one_click")
        session.commit()
    return HTMLResponse(
        "<h1>You have been opted out</h1>"
        "<p>Aegis will not send further outreach to this address.</p>"
    )




@app.get("/api/admin/dashboard")
async def admin_dashboard():
    """Return pipeline, pending handoffs, docs, and recent activity."""
    crm._load()
    handoffs = []
    for email, info in pending_handoffs.items():
        handoffs.append({"email": email, **info})

    docs = docs_engine.list_documents(limit=10)

    pipeline = crm.pipeline_summary()
    clients = crm.get_active_clients()
    brain_ok = False
    try:
        from core.llm import get_llm
        brain_ok = bool(get_llm(config).health())
    except Exception:
        brain_ok = False

    annual_revenue = sum(
        int((c.get("price") or {}).get("annual_price") or (c.get("price") or {}).get("annual") or 0)
        for c in clients
    )
    paypal = PayPalCheckout()
    paypal_environment = (os.environ.get("PAYPAL_ENVIRONMENT") or "sandbox").lower()

    return JSONResponse({
        "pending_handoffs": handoffs,
        "mail": mail_store.summary(),
        "mail_config": mail_store.config_status(),
        "recent_mail": mail_store.list_messages(limit=8),
        "documents": [{"id": d["id"], "name": d["original_name"], "tags": d.get("tags", [])} for d in docs],
        "active_conversations": len(prospect_sessions),
        "memory_count": memory.count() if memory else 0,
        "active_clients": len(clients),
        "annual_revenue": annual_revenue,
        "paypal": {
            "configured": paypal.configured,
            "environment": paypal_environment if paypal_environment in {"sandbox", "live"} else "invalid",
            "client_id_present": bool(os.environ.get("PAYPAL_CLIENT_ID")),
            "client_secret_present": bool(os.environ.get("PAYPAL_CLIENT_SECRET")),
            "annual_price_gbp": 995,
        },
        "total_deals": pipeline.get("total_deals", 0),
        "brain_online": brain_ok,
        "leads": lead_pipeline.summary(),
        "recent_leads": lead_pipeline.list_leads(limit=5),
        "clients": [
            {
                "company": c.get("company"),
                "email": c.get("contact_email"),
                "price": (
                    (c.get("price") or {}).get("annual_price")
                    or (c.get("price") or {}).get("annual")
                    or 0
                ),
                "status": c.get("status"),
            }
            for c in clients
        ],
    })



# ── HTTP CHAT (Vercel-friendly; no long-lived sockets required) ──────

@app.post("/api/chat")
async def api_prospect_chat(request: Request):
    body = await request.json()
    prospect_id = (body.get("prospect_id") or "anon")[:64]
    message = (body.get("content") or body.get("message") or "").strip()
    start = bool(body.get("start"))

    state = prospect_sessions.get(prospect_id) or _load_session("p", prospect_id)
    if state is None or start or message.lower() in ("/reset", "/restart"):
        state = conversation_engine.start_conversation(
            prospect_info={"source": "web_chat", "id": prospect_id, "started": datetime.now().isoformat()}
        )
        prospect_sessions[prospect_id] = state
        _save_session("p", prospect_id, state)
        if start or not message or message.lower() in ("/reset", "/restart"):
            greeting = (
                "Hello. I'm Harold, the Aegis customer guide. I can explain our "
                "passive, public-information monitoring and help with onboarding. "
                "What would you like to know?"
            )
            if message.lower() in ("/reset", "/restart"):
                greeting = "Starting fresh. What can I help you with?"
            return JSONResponse({"type": "message", "content": greeting, "stage": state.get("stage", "greeting")})

    if not message:
        return JSONResponse({"type": "message", "content": "Welcome back. Where were we?", "stage": state.get("stage", "")})

    response, state = conversation_engine.handle_message(state, message)
    prospect_sessions[prospect_id] = state

    prev_stage = None
    for _ in range(5):
        prev_stage = state.get("stage")
        if prev_stage in ("closing", "handoff") and not state.get("ready_to_close"):
            response, state = conversation_engine.handle_message(state, message)
            prospect_sessions[prospect_id] = state
        elif prev_stage == "pricing":
            response, state = conversation_engine.handle_message(state, message)
            prospect_sessions[prospect_id] = state
        else:
            break
        if state.get("stage") == prev_stage:
            break

    if state.get("ready_to_close"):
        email = _extract_email(message) or state.get("discovered", {}).get("email")
        company = (
            state.get("discovered", {}).get("company")
            or state.get("discovered", {}).get("domain")
            or "unknown"
        )
        price = state.get("price_quoted") or {}
        annual = price.get("annual") or price.get("annual_price") or 995
        if email:
            pending_handoffs[email] = {
                "company": company,
                "price": annual,
                "source": "web_chat",
                "stage": state.get("stage"),
            }
            try:
                deal_id = crm.add_deal(
                    company=company,
                    contact_email=email,
                    pricing_result={"annual_price": annual, **price},
                    source="web_chat",
                )
                crm.update_stage(deal_id, "won", note="Closed via web chat")
            except Exception as e:
                print(f"[Finch] CRM write failed: {e}")
        try:
            memory.remember(
                f"Closed deal via web chat: {json.dumps(state.get('discovered'))} email={email} annual_price={annual}",
                metadata={"type": "closed_deal", "prospect_id": prospect_id},
            )
        except Exception:
            pass

    _save_session("p", prospect_id, state)
    return JSONResponse({
        "type": "message",
        "content": response,
        "stage": state.get("stage"),
        "ready_to_close": state.get("ready_to_close", False),
    })


@app.post("/api/admin/chat")
async def api_admin_chat(request: Request):
    """Private owner conversation with Harold."""
    body = await request.json()
    admin_id = (body.get("admin_id") or "phone")[:64]
    message = (body.get("content") or "").strip()
    start = bool(body.get("start"))

    session = _load_admin_session(admin_id)
    admin_sessions[admin_id] = session

    if start or not message:
        reply = _owner_opening(admin_id, session)
        admin_sessions[admin_id] = session
        return JSONResponse({"type": "message", "content": reply, "sender": "finch"})

    session.setdefault("history", []).append({
        "role": "user", "content": message, "created_at": datetime.now().isoformat()
    })

    # Commands first
    cmd = admin_brain.handle_command(
        message,
        crm=crm,
        pending_handoffs=pending_handoffs,
        prospect_sessions=prospect_sessions,
        mail_store=mail_store,
        docs_engine=docs_engine,
    )
    if cmd is not None:
        reply = cmd
    else:
        reply = admin_brain.reply(
            message,
            session.get("history") or [],
            config=config,
            crm=crm,
            pending_handoffs=pending_handoffs,
            prospect_sessions=prospect_sessions,
            mail_store=mail_store,
            memory=memory,
        )

    session["history"].append({
        "role": "finch", "content": reply, "created_at": datetime.now().isoformat()
    })
    session["history"] = session["history"][-80:]
    admin_sessions[admin_id] = session
    _save_admin_session(admin_id, session)
    return JSONResponse({"type": "message", "content": reply, "sender": "finch"})


@app.get("/api/admin/emails")
async def admin_list_emails(folder: str = "all"):
    return JSONResponse({
        "config": mail_store.config_status(),
        "summary": mail_store.summary(),
        "messages": mail_store.list_messages(folder=folder if folder != "all" else None, limit=80),
    })


@app.get("/api/admin/emails/{msg_id}")
async def admin_get_email(msg_id: str):
    m = mail_store.get(msg_id)
    if not m:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if m.get("folder") == "inbox":
        mail_store.mark_read(msg_id)
        m = mail_store.get(msg_id)
    return JSONResponse({"message": m})


@app.post("/api/admin/emails")
async def admin_compose_email(request: Request):
    body = await request.json()
    action = (body.get("action") or "draft").lower()
    if action == "craft":
        crafted = mail_store.craft_outreach(
            body.get("company") or "",
            body.get("to") or "",
            body.get("finding") or "",
        )
        msg = mail_store.compose(
            to=crafted["to"],
            subject=crafted.get("subject") or body.get("subject") or "",
            body=crafted.get("body") or body.get("body") or "",
            company=body.get("company") or "",
            as_draft=True,
        )
        return JSONResponse({"ok": True, "message": msg})
    msg = mail_store.compose(
        to=body.get("to") or "",
        subject=body.get("subject") or "",
        body=body.get("body") or "",
        company=body.get("company") or "",
        as_draft=action != "queue",
    )
    if action == "queue":
        result = mail_store.queue_send(msg["id"])
        return JSONResponse(result)
    return JSONResponse({"ok": True, "message": msg})


@app.post("/api/admin/emails/{msg_id}/send")
async def admin_send_email(msg_id: str):
    return JSONResponse(mail_store.queue_send(msg_id))


@app.delete("/api/admin/emails/{msg_id}")
async def admin_delete_email(msg_id: str):
    ok = mail_store.delete(msg_id)
    return JSONResponse({"ok": ok})


@app.post("/api/admin/mail/config")
async def admin_mail_config(request: Request):
    """Test credentials without persisting secrets; deployment secrets use env vars."""
    body = await request.json()
    action = (body.get("action") or "save").lower()
    if action == "clear":
        return JSONResponse(mail_store.clear_smtp_config())
    result = mail_store.save_smtp_config(
        email=body.get("email") or "",
        password=body.get("password") or body.get("app_password") or "",
        smtp_host=body.get("smtp_host") or "smtp.gmail.com",
        smtp_port=str(body.get("smtp_port") or "587"),
        test=body.get("test", True) is not False,
    )
    return JSONResponse(result)


@app.post("/api/admin/mail/test")
async def admin_mail_test():
    """Send a short test message to the configured From address."""
    cfg = mail_store.config_status()
    if not cfg.get("configured"):
        return JSONResponse({"ok": False, "error": "smtp_not_configured"}, status_code=400)
    to = cfg.get("from_address")
    draft = mail_store.compose(
        to=to,
        subject="Aegis SMTP test — Harold is online",
        body=(
            "Hi,\n\nThis is a test from your Aegis command HQ. "
            "If you're reading this, outbound email is working.\n\n"
            "— Harold from Aegis"
        ),
        company="Aegis",
        as_draft=True,
    )
    result = mail_store.queue_send(draft["id"])
    return JSONResponse(result)




@app.get("/api/admin/leads")
async def admin_list_leads():
    return JSONResponse({
        "summary": lead_pipeline.summary(),
        "leads": lead_pipeline.list_leads(limit=50),
        "hunter": bool(__import__("os").environ.get("HUNTER_API_KEY") or __import__("os").environ.get("HUNTER_KEY")),
        "smtp": mail_store.config_status(),
    })


@app.post("/api/admin/leads/research")
async def admin_research_lead(request: Request):
    body = await request.json()
    domain = (body.get("domain") or body.get("company") or "").strip()
    company = (body.get("company_name") or body.get("name") or "").strip()
    result = lead_pipeline.research(
        domain,
        company=company,
        source_url=(body.get("source_url") or "").strip(),
        signal_title=(body.get("signal_title") or "").strip(),
        signal_excerpt=(body.get("signal_excerpt") or "").strip(),
        business_type=(body.get("business_type") or "").strip(),
        contact_email=(body.get("contact_email") or "").strip(),
        contact_source=(body.get("contact_source") or "").strip(),
    )
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.get("/api/admin/leads/{lead_id}")
async def admin_get_lead(lead_id: str):
    lead = lead_pipeline.get(lead_id)
    if not lead:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"lead": lead})


@app.post("/api/admin/leads/{lead_id}/select")
async def admin_select_lead_email(lead_id: str, request: Request):
    body = await request.json()
    email = (body.get("email") or "").strip()
    lead = lead_pipeline.select_email(lead_id, email)
    if not lead:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"ok": True, "lead": lead})


@app.post("/api/admin/leads/{lead_id}/draft")
async def admin_draft_lead(lead_id: str, request: Request):
    lead = lead_pipeline.get(lead_id)
    if not lead:
        return JSONResponse({"error": "not_found"}, status_code=404)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    to = (body.get("email") or lead.get("selected_email") or "").strip()
    if not to and lead.get("emails"):
        to = lead["emails"][0]["email"]
    if not to:
        return JSONResponse({"ok": False, "error": "No email selected"}, status_code=400)
    if body.get("email"):
        lead_pipeline.select_email(lead_id, to)
        lead = lead_pipeline.get(lead_id) or lead
    hook = body.get("finding") or lead_pipeline.best_hook(lead)
    crafted = mail_store.craft_outreach(lead.get("company") or "", to, hook)
    msg = mail_store.compose(
        to=to,
        subject=crafted.get("subject") or f"Security note for {lead.get('company')}",
        body=crafted.get("body") or "",
        company=lead.get("company") or "",
        as_draft=True,
    )
    lead_pipeline.mark_drafted(lead_id, msg.get("id") or "")
    return JSONResponse({"ok": True, "message": msg, "lead": lead_pipeline.get(lead_id)})


@app.post("/api/admin/leads/{lead_id}/send")
async def admin_send_lead(lead_id: str, request: Request):
    lead = lead_pipeline.get(lead_id)
    if not lead:
        return JSONResponse({"error": "not_found"}, status_code=404)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    to = (body.get("email") or lead.get("selected_email") or "").strip()
    if not to and lead.get("emails"):
        to = lead["emails"][0]["email"]
    try:
        OptOutTokens().issue(to)
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "optout_secret_not_configured"},
            status_code=503,
        )
    with db_session_factory() as db:
        allowed, reason = OutreachPolicy(db).can_contact(
            lead.get("company") or "",
            to,
            lead.get("contact_source") or "",
            lead.get("business_type") or "",
            followup=False,
        )
        if not allowed:
            return JSONResponse({"ok": False, "error": reason}, status_code=409)
    # Prefer existing draft
    mid = body.get("message_id") or lead.get("draft_message_id")
    if not mid:
        # draft first
        if not to:
            return JSONResponse({"ok": False, "error": "No email"}, status_code=400)
        hook = lead_pipeline.best_hook(lead)
        crafted = mail_store.craft_outreach(lead.get("company") or "", to, hook)
        msg = mail_store.compose(
            to=to,
            subject=crafted.get("subject") or "",
            body=crafted.get("body") or "",
            company=lead.get("company") or "",
            as_draft=True,
        )
        mid = msg["id"]
        lead_pipeline.mark_drafted(lead_id, mid)
    result = mail_store.queue_send(mid)
    if result.get("sent"):
        lead_pipeline.mark_sent(lead_id, mid)
        with db_session_factory() as db:
            OutreachPolicy(db).record_sent(
                lead.get("company") or "",
                to,
                lead.get("contact_source") or "",
                "initial",
                {"lead_id": lead_id, "message_id": mid},
            )
            db.commit()
    else:
        lead_pipeline.mark_drafted(lead_id, mid)
    if result.get("ok"):
        result["lead"] = lead_pipeline.get(lead_id)
    return JSONResponse(result)


@app.delete("/api/admin/leads/{lead_id}")
async def admin_delete_lead(lead_id: str):
    ok = lead_pipeline.delete(lead_id)
    return JSONResponse({"ok": ok})


@app.websocket("/ws/admin/{admin_id}")
async def admin_websocket(websocket: WebSocket, admin_id: str):
    """Co-founder chat over WS (HTML uses HTTP; keep for local)."""
    if owner_auth.validate(websocket.cookies.get(SESSION_COOKIE)) is None:
        await websocket.close(code=4401, reason="Authentication required")
        return
    await websocket.accept()
    session = _load_admin_session(admin_id)
    admin_sessions[admin_id] = session
    opening = _owner_opening(admin_id, session)
    await websocket.send_text(json.dumps({
        "type": "message",
        "content": opening,
        "sender": "finch",
    }))
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            user_message = (msg.get("content") or "").strip()
            if not user_message:
                continue
            session.setdefault("history", []).append({
                "role": "user", "content": user_message,
                "created_at": datetime.now().isoformat(),
            })
            cmd = admin_brain.handle_command(
                user_message,
                crm=crm,
                pending_handoffs=pending_handoffs,
                prospect_sessions=prospect_sessions,
                mail_store=mail_store,
                docs_engine=docs_engine,
            )
            if cmd is not None:
                reply = cmd
            else:
                reply = admin_brain.reply(
                    user_message,
                    session.get("history") or [],
                    config=config,
                    crm=crm,
                    pending_handoffs=pending_handoffs,
                    prospect_sessions=prospect_sessions,
                    mail_store=mail_store,
                    memory=memory,
                )
            session["history"].append({
                "role": "finch", "content": reply,
                "created_at": datetime.now().isoformat(),
            })
            session["history"] = session["history"][-80:]
            admin_sessions[admin_id] = session
            _save_admin_session(admin_id, session)
            await websocket.send_text(json.dumps({
                "type": "message", "content": reply, "sender": "finch",
            }))
    except WebSocketDisconnect:
        pass


# ── HELPERS

# ── HELPERS ──────────────────────────────────────────────────────────

def _extract_email(text):
    import re
    match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    return match.group(0) if match else None


# ── PWA HTML — PROSPECT CHAT ─────────────────────────────────────────

PROSPECT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0a0a0f">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Aegis">
<link rel="manifest" href="/manifest.json">
<title>Talk to Aegis</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #e0e0e0;
  }
  .chat-container {
    display: flex; flex-direction: column; height: 100%; max-width: 720px;
    margin: 0 auto; background: #12121a;
  }
  .chat-header {
    padding: 16px 20px; background: #0d0d14; border-bottom: 1px solid #1e1e2e;
    display: flex; align-items: center; gap: 12px;
    padding-top: max(16px, env(safe-area-inset-top));
  }
  .chat-header .avatar {
    width: 40px; height: 40px; border-radius: 50%; background: #2563eb;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; font-weight: bold; color: white; flex-shrink: 0;
  }
  .chat-header .info h3 { font-size: 16px; color: #f0f0f0; }
  .chat-header .info span { font-size: 12px; color: #888; }
  .dot { width: 8px; height: 8px; background: #4ade80; border-radius: 50%;
         display: inline-block; margin-right: 4px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .messages {
    flex: 1; overflow-y: auto; padding: 16px;
    display: flex; flex-direction: column; gap: 12px;
    -webkit-overflow-scrolling: touch;
  }
  .message {
    max-width: 85%; padding: 10px 14px; border-radius: 16px;
    font-size: 15px; line-height: 1.5;
    animation: slideIn .25s ease;
  }
  @keyframes slideIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
  .message.finch { background: #1a1a2e; color: #e0e0e0; align-self: flex-start; border: 1px solid #2a2a3e; border-bottom-left-radius: 4px; }
  .message.prospect { background: #2563eb; color: white; align-self: flex-end; border-bottom-right-radius: 4px; }
  .message.status { background: transparent; color: #666; font-size: 12px; align-self: center; font-style: italic; }
  .input-area {
    padding: 12px 16px; border-top: 1px solid #1e1e2e; display: flex; gap: 8px;
    background: #0d0d14; padding-bottom: max(12px, env(safe-area-inset-bottom));
  }
  .input-area input {
    flex: 1; padding: 12px 16px; background: #1a1a2e; border: 1px solid #2a2a3e;
    border-radius: 24px; color: #e0e0e0; font-size: 16px; outline: none;
  }
  .input-area input:focus { border-color: #2563eb; }
  .input-area button {
    width: 44px; height: 44px; background: #2563eb; color: white; border: none;
    border-radius: 50%; font-size: 18px; cursor: pointer; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
  }
  .stage-bar {
    text-align: center; font-size: 11px; color: #444; padding: 6px;
    background: #0d0d14; border-top: 1px solid #111;
  }
</style>
</head>
<body>
<div class="chat-container">
  <div class="chat-header">
    <div class="avatar">F</div>
    <div class="info">
      <h3>Harold</h3>
      <span><span class="dot"></span>Online now</span>
    </div>
  </div>
  <div class="messages" id="messages"></div>
  <div class="stage-bar" id="stage">connecting...</div>
  <div class="input-area">
    <input type="text" id="input" placeholder="Message..." autofocus
           onkeypress="if(event.key==='Enter')send()" autocomplete="off">
    <button onclick="send()" id="btn">▶</button>
  </div>
</div>
<script>
  const pid=localStorage.getItem('finch_pid')||('p_'+Math.random().toString(36).slice(2,8));
  localStorage.setItem('finch_pid',pid);
  const m=()=>document.getElementById('messages');
  function add(cls,text){const d=document.createElement('div');d.className='message '+cls;d.textContent=text;m().appendChild(d);m().scrollTop=m().scrollHeight;}
  async function chat(payload){
    document.getElementById('stage').textContent='thinking...';
    try{
      const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const d=await r.json();
      if(d.status)add('status',d.status);
      add('finch',d.content||'...');
      document.getElementById('stage').textContent=d.ready_to_close?'Deal ready':(d.stage||'ready');
    }catch(e){
      add('status','Connection issue — try again.');
      document.getElementById('stage').textContent='error';
    }
  }
  async function send(){
    const i=document.getElementById('input');
    const t=i.value.trim();
    if(!t)return;
    add('prospect',t);i.value='';
    await chat({prospect_id:pid,content:t});
  }
  chat({prospect_id:pid,start:true});
</script>
</body>
</html>"""


# ── PWA HTML — ADMIN DASHBOARD ───────────────────────────────────────

ADMIN_HTML = "<html><body style='background:#111;color:#eee;font-family:sans-serif;padding:24px'>Admin UI missing. Redeploy.</body></html>"





# ── RUN ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = config.get("web", {}).get("chat_port", 8100)
    print(f"\n  Finch PWA starting on http://0.0.0.0:{port}")
    print(f"  Prospect chat:  http://your-server:{port}/")
    print(f"  Admin dashboard: http://your-server:{port}/admin")
    print(f"  Add to Home Screen on your phone for app-like experience.\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
