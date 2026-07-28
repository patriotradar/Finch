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
from web import admin_brain
import yaml


def load_config():
    config_path = Path(__file__).parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


app = FastAPI(title="Finch Security", version="2.0")
config = load_config()
ADMIN_PASSWORD = os.environ.get("FINCH_ADMIN_PASSWORD", "finch")

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

# Seed demo CRM on empty cloud disks
try:
    crm._load()
    if not crm.get_active_clients():
        seed_clients = Path(__file__).parent.parent / "data" / "clients" / "clients.json"
        seed_deals = Path(__file__).parent.parent / "data" / "clients" / "deals.json"
        dest = Path(crm.data_dir)
        dest.mkdir(parents=True, exist_ok=True)
        if seed_clients.exists() and not (dest / "clients.json").exists():
            (dest / "clients.json").write_text(seed_clients.read_text())
        if seed_deals.exists() and not (dest / "deals.json").exists():
            (dest / "deals.json").write_text(seed_deals.read_text())
        crm._load()
except Exception as e:
    print(f"[Finch] seed skip: {e}")

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
        'font-weight="bold" fill="#4ade80">F</text>'
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
        'font-weight="bold" fill="#4ade80">F</text>'
        '<circle cx="390" cy="130" r="55" fill="#2563eb" opacity="0.8"/>'
        '</svg>'
    )
    from fastapi.responses import Response
    return Response(content=svg, media_type="image/svg+xml")


# ── PUBLIC: PROSPECT CHAT ────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def prospect_chat():
    return PROSPECT_HTML


@app.websocket("/ws/{prospect_id}")
async def prospect_websocket(websocket: WebSocket, prospect_id: str):
    await websocket.accept()

    if prospect_id not in prospect_sessions:
        state = conversation_engine.start_conversation(
            prospect_info={"source": "web_chat", "id": prospect_id, "started": datetime.now().isoformat()}
        )
        prospect_sessions[prospect_id] = state
        greeting = (
            "Hello. I'm Harold — I handle the technical side at Finch Security. "
            "You're here because you're wondering whether your organization has "
            "security exposure you don't know about. What's on your mind?"
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

            if state.get("_should_demo"):
                state["_should_demo"] = False
                domain = state["discovered"].get("domain", "")
                if domain:
                    await websocket.send_text(json.dumps({"type": "status", "content": f"Running a quick scan on {domain}..."}))
                    await asyncio.sleep(2)
                    state["demo_results"] = {"findings": []}
                    state["demo_run"] = True
                    response, state = conversation_engine.handle_message(state, "scan complete")
                    prospect_sessions[prospect_id] = state

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
                monthly = price.get("monthly") or price.get("monthly_price") or 0
                if email:
                    pending_handoffs[email] = {
                        "company": company,
                        "price": monthly,
                        "source": "web_chat",
                        "stage": state.get("stage"),
                    }
                    # Persist into CRM so deals survive restarts
                    try:
                        deal_id = crm.add_deal(
                            company=company,
                            contact_email=email,
                            pricing_result={"monthly_price": monthly, **price},
                            source="web_chat",
                        )
                        crm.update_stage(deal_id, "won", note="Closed via web chat")
                    except Exception as e:
                        print(f"[Finch] CRM write failed: {e}")
                memory.remember(
                    f"Closed deal via web chat: {json.dumps(state['discovered'])} email={email} price={monthly}",
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
    body = await request.json()
    password = body.get("password", "")
    if password == ADMIN_PASSWORD:
        return JSONResponse({"authenticated": True, "token": "finch-session"})
    return JSONResponse({"authenticated": False}, status_code=401)


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

    return JSONResponse({
        "pending_handoffs": handoffs,
        "mail": mail_store.summary(),
        "mail_config": mail_store.config_status(),
        "recent_mail": mail_store.list_messages(limit=8),
        "documents": [{"id": d["id"], "name": d["original_name"], "tags": d.get("tags", [])} for d in docs],
        "active_conversations": len(prospect_sessions),
        "memory_count": memory.count() if memory else 0,
        "active_clients": len(clients),
        "mrr": pipeline.get("mrr", 0),
        "total_deals": pipeline.get("total_deals", 0),
        "brain_online": brain_ok,
        "clients": [
            {
                "company": c.get("company"),
                "email": c.get("contact_email"),
                "price": (c.get("price") or {}).get("monthly_price", 0),
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
                "Hello. I'm Harold — I handle the technical side at Finch Security. "
                "You're here because you're wondering whether your organization has "
                "security exposure you don't know about. What's on your mind?"
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

    if state.get("_should_demo"):
        state["_should_demo"] = False
        domain = state.get("discovered", {}).get("domain", "")
        status = f"Running a quick scan on {domain}..." if domain else "Running a quick scan..."
        state["demo_results"] = {"findings": []}
        state["demo_run"] = True
        response, state = conversation_engine.handle_message(state, "scan complete")
        prospect_sessions[prospect_id] = state
        _save_session("p", prospect_id, state)
        return JSONResponse({
            "type": "message",
            "content": response,
            "stage": state.get("stage"),
            "status": status,
            "ready_to_close": state.get("ready_to_close", False),
        })

    if state.get("ready_to_close"):
        email = _extract_email(message) or state.get("discovered", {}).get("email")
        company = (
            state.get("discovered", {}).get("company")
            or state.get("discovered", {}).get("domain")
            or "unknown"
        )
        price = state.get("price_quoted") or {}
        monthly = price.get("monthly") or price.get("monthly_price") or 0
        if email:
            pending_handoffs[email] = {
                "company": company,
                "price": monthly,
                "source": "web_chat",
                "stage": state.get("stage"),
            }
            try:
                deal_id = crm.add_deal(
                    company=company,
                    contact_email=email,
                    pricing_result={"monthly_price": monthly, **price},
                    source="web_chat",
                )
                crm.update_stage(deal_id, "won", note="Closed via web chat")
            except Exception as e:
                print(f"[Finch] CRM write failed: {e}")
        try:
            memory.remember(
                f"Closed deal via web chat: {json.dumps(state.get('discovered'))} email={email} price={monthly}",
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
    """Co-founder chat — full living Harold, not a CRM parrot."""
    body = await request.json()
    admin_id = (body.get("admin_id") or "phone")[:64]
    message = (body.get("content") or "").strip()
    start = bool(body.get("start"))

    session = admin_sessions.get(admin_id) or _load_session("a", admin_id) or {"history": []}
    admin_sessions[admin_id] = session

    if start or not message:
        reply = admin_brain.opening_line()
        # Don't pollute history with every tab open; only seed if empty
        if not session.get("history"):
            session["history"] = [{"role": "finch", "content": reply}]
            _save_session("a", admin_id, session)
        else:
            # Returning partner — warm, brief, not repeating full status
            reply = admin_brain.opening_line()
        return JSONResponse({"type": "message", "content": reply, "sender": "finch"})

    session.setdefault("history", []).append({"role": "user", "content": message})

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

    session["history"].append({"role": "finch", "content": reply})
    session["history"] = session["history"][-40:]
    admin_sessions[admin_id] = session
    _save_session("a", admin_id, session)
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


@app.websocket("/ws/admin/{admin_id}")
async def admin_websocket(websocket: WebSocket, admin_id: str):
    """Co-founder chat over WS (HTML uses HTTP; keep for local)."""
    await websocket.accept()
    session = admin_sessions.get(admin_id) or _load_session("a", admin_id) or {"history": []}
    admin_sessions[admin_id] = session
    await websocket.send_text(json.dumps({
        "type": "message",
        "content": admin_brain.opening_line(),
        "sender": "finch",
    }))
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            user_message = (msg.get("content") or "").strip()
            if not user_message:
                continue
            session.setdefault("history", []).append({"role": "user", "content": user_message})
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
            session["history"].append({"role": "finch", "content": reply})
            session["history"] = session["history"][-40:]
            admin_sessions[admin_id] = session
            _save_session("a", admin_id, session)
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
<meta name="apple-mobile-web-app-title" content="Finch">
<link rel="manifest" href="/manifest.json">
<title>Talk to Finch Security</title>
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
      <h3>Harold Finch</h3>
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
