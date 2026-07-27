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

# Vercel (and other serverless hosts) only allow writes under /tmp
_DATA_ROOT = Path(os.environ.get("FINCH_DATA_DIR") or (
    "/tmp/finch-data" if os.environ.get("VERCEL") else "./data"
))
_DATA_ROOT.mkdir(parents=True, exist_ok=True)
(_DATA_ROOT / "memory").mkdir(exist_ok=True)
(_DATA_ROOT / "clients").mkdir(exist_ok=True)
(_DATA_ROOT / "sessions").mkdir(exist_ok=True)

pricing = PricingEngine(config.get("pricing", {}))
_mem_cfg = config.get("memory", {}) or {}
memory = MemoryStore(
    persist_path=str(_DATA_ROOT / "memory"),
    collection_name=_mem_cfg.get("collection", "finch_memory"),
)
conversation_engine = SalesConversation(None, pricing, memory, config)
crm = CRM(data_dir=str(_DATA_ROOT / "clients"))
docs_engine = FinchDocs()

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
    return ADMIN_HTML


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
    message = (body.get("content") or "").strip()
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
    body = await request.json()
    admin_id = (body.get("admin_id") or "phone")[:64]
    message = (body.get("content") or "").strip()
    start = bool(body.get("start"))

    session = admin_sessions.get(admin_id) or _load_session("a", admin_id) or {"history": []}
    admin_sessions[admin_id] = session

    if start or not message:
        reply = "I'm here. What do you need? Pipeline check? Send a document? Just talk."
        return JSONResponse({"type": "message", "content": reply, "sender": "finch"})

    session["history"].append({"role": "user", "content": message})
    lower = message.lower().strip()

    if lower in ("/status", "/stats", "status", "how are we doing", "pipeline"):
        handoffs = len(pending_handoffs)
        convos = len(prospect_sessions)
        docs_count = len(docs_engine.list_documents())
        reply = (
            f"We have {convos} active prospect conversation{'s' if convos != 1 else ''}, "
            f"{handoffs} deal{'s' if handoffs != 1 else ''} waiting for paperwork, "
            f"and {docs_count} document{'s' if docs_count != 1 else ''} stored.\\n\\n"
            f"Need me to walk through the handoffs or send some documents?"
        )
    elif lower in ("/handoffs", "/deals", "handoffs", "pending deals", "what's pending"):
        if not pending_handoffs:
            reply = "No pending handoffs right now. When a prospect says yes, I'll queue them here."
        else:
            reply = "Pending handoffs — these prospects are waiting for onboarding:\\n\\n"
            for email, info in pending_handoffs.items():
                reply += (
                    f"{info.get('company', 'unknown')}\\n"
                    f"   Email: {email}\\n"
                    f"   Price: ${info.get('price', 0):,}/mo\\n\\n"
                )
    elif lower in ("/docs", "documents", "show docs", "what documents"):
        docs = docs_engine.list_documents(limit=15)
        if not docs:
            reply = "No documents stored yet."
        else:
            reply = "Documents:\\n\\n"
            for d in docs:
                tags = ", ".join(d.get("tags", ["untagged"]))
                reply += f"#{d['id']}: {d['original_name']} [{tags}]\\n"
    elif lower in ("/help", "help", "what can you do"):
        reply = (
            "/status — Pipeline overview\\n"
            "/handoffs — Deals waiting for paperwork\\n"
            "/docs — Stored documents\\n\\n"
            "Or just talk to me."
        )
    else:
        reply = None
        try:
            from core.llm import get_llm
            llm = get_llm(config)
            if llm.health():
                crm._load()
                pipeline = crm.pipeline_summary()
                clients = crm.get_active_clients()
                client_bits = []
                for c in clients[:5]:
                    price = (c.get("price") or {}).get("monthly_price", 0)
                    client_bits.append(
                        f"{c.get('company','?')} ({c.get('contact_email','')}) ${price}/mo"
                    )
                client_line = "; ".join(client_bits) if client_bits else "none yet"
                context = (
                    f"Active clients ({len(clients)}): {client_line}. "
                    f"MRR ${pipeline.get('mrr', 0)}. Total deals {pipeline.get('total_deals', 0)}. "
                    f"Pending handoffs {len(pending_handoffs)}. Live chats {len(prospect_sessions)}."
                )
                system = (
                    "You are Harold Finch, technical co-founder of Finch Security. "
                    "You are speaking privately with your human business partner. "
                    "Use ONLY the facts in BUSINESS DATA. Do not invent tools, partners, or companies. "
                    "Never mention Ollama, llama, models, or strangers. "
                    "Answer in 2-4 short plain sentences, calm and dry. "
                    f"BUSINESS DATA: {context}"
                )
                hist = session.get("history", [])
                messages = [{"role": "system", "content": system}]
                for turn in hist[-6:]:
                    r = "user" if turn.get("role") == "user" else "assistant"
                    messages.append({"role": r, "content": (turn.get("content") or "")[:400]})
                if not messages or messages[-1].get("role") != "user":
                    messages.append({"role": "user", "content": message[:600]})
                reply = llm.chat(messages, max_tokens=140, temperature=0.7)
        except Exception as e:
            print(f"[Finch] admin LLM error: {e}")
            reply = None
        if not reply:
            reply = (
                "I'm here. Brain is offline for a moment — use /status, /handoffs, "
                "and /docs, or try again shortly."
            )

    session["history"].append({"role": "finch", "content": reply})
    session["history"] = session["history"][-20:]
    admin_sessions[admin_id] = session
    _save_session("a", admin_id, session)
    return JSONResponse({"type": "message", "content": reply, "sender": "finch"})


@app.websocket("/ws/admin/{admin_id}")
async def admin_websocket(websocket: WebSocket, admin_id: str):
    """Co-founder chat — you talk to Finch, Finch talks back."""
    await websocket.accept()

    if admin_id not in admin_sessions:
        admin_sessions[admin_id] = {"history": []}

    await websocket.send_text(json.dumps({
        "type": "message",
        "content": "I'm here. What do you need? Pipeline check? Send a document? Just talk.",
        "sender": "finch",
    }))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            user_message = msg.get("content", "")

            if not user_message.strip():
                continue

            admin_sessions[admin_id]["history"].append({"role": "user", "content": user_message})

            # Simple command routing
            lower = user_message.lower().strip()

            if lower in ("/status", "/stats", "status", "how are we doing", "pipeline"):
                handoffs = len(pending_handoffs)
                convos = len(prospect_sessions)
                docs_count = len(docs_engine.list_documents())
                reply = (
                    f"We have {convos} active prospect conversation{'s' if convos != 1 else ''}, "
                    f"{handoffs} deal{'s' if handoffs != 1 else ''} waiting for paperwork, "
                    f"and {docs_count} document{'s' if docs_count != 1 else ''} stored.\n\n"
                    f"Need me to walk through the handoffs or send some documents?"
                )

            elif lower in ("/handoffs", "/deals", "handoffs", "pending deals", "what's pending"):
                if not pending_handoffs:
                    reply = "No pending handoffs right now. When a prospect says yes, I'll queue them here."
                else:
                    reply = "📋 Pending handoffs — these prospects are waiting for onboarding:\n\n"
                    for email, info in pending_handoffs.items():
                        reply += (
                            f"🏢 {info.get('company', 'unknown')}\n"
                            f"   Email: {email}\n"
                            f"   Price: ${info.get('price', 0):,}/mo\n\n"
                        )
                    reply += "Forward the contract here or upload it, and tell me who to send it to."

            elif lower in ("/docs", "documents", "show docs", "what documents"):
                docs = docs_engine.list_documents(limit=15)
                if not docs:
                    reply = "No documents stored yet. You can upload files from the dashboard or forward them via email."
                else:
                    reply = "📁 Documents:\n\n"
                    for d in docs:
                        tags = ", ".join(d.get("tags", ["untagged"]))
                        reply += f"#{d['id']}: {d['original_name']} [{tags}]\n"

            elif lower.startswith("/send ") or lower.startswith("send "):
                # Parse: /send 3 to john@email.com
                import re
                parts = lower.replace("/send ", "").replace("send ", "")
                match = re.match(r'(\d+|[\w_]+)\s+to\s+(\S+@\S+)', parts)
                if match:
                    identifier, email = match.group(1), match.group(2)
                    reply = (
                        f"I'd send document {identifier} to {email}, but email delivery needs the "
                        f"SMTP credentials configured. Set FINCH_EMAIL and FINCH_EMAIL_PASSWORD, "
                        f"then I can send it from the server.\n\n"
                        f"For now, download the document from /docs on your dashboard and email it yourself."
                    )
                else:
                    reply = "Usage: /send <doc_id> to <email>  —  e.g. /send 3 to john@acmecorp.com"

            elif lower in ("/help", "help", "what can you do"):
                reply = (
                    "From your phone dashboard:\n\n"
                    "/status — Pipeline overview\n"
                    "/handoffs — Deals waiting for paperwork\n"
                    "/docs — Stored documents\n"
                    "/send 3 to john@email.com — Queue a document send\n\n"
                    "Or just talk to me. I'll remember everything."
                )

            else:
                # Freeform — local LLM brain (Harold / Finch)
                reply = None
                try:
                    from core.llm import get_llm
                    llm = get_llm(config)
                    if llm.health():
                        crm._load()
                        pipeline = crm.pipeline_summary()
                        clients = crm.get_active_clients()
                        client_bits = []
                        for c in clients[:5]:
                            price = (c.get("price") or {}).get("monthly_price", 0)
                            client_bits.append(
                                f"{c.get('company','?')} ({c.get('contact_email','')}) ${price}/mo"
                            )
                        client_line = "; ".join(client_bits) if client_bits else "none yet"
                        context = (
                            f"Active clients ({len(clients)}): {client_line}. "
                            f"MRR ${pipeline.get('mrr', 0)}. Total deals {pipeline.get('total_deals', 0)}. "
                            f"Pending handoffs {len(pending_handoffs)}. Live chats {len(prospect_sessions)}."
                        )
                        system = (
                            "You are Harold Finch, technical co-founder of Finch Security. "
                            "You are speaking privately with your human business partner. "
                            "Use ONLY the facts in BUSINESS DATA. Do not invent tools, partners, or companies. "
                            "Never mention Ollama, llama, models, or strangers. "
                            "Answer in 2-4 short plain sentences, calm and dry. "
                            f"BUSINESS DATA: {context}"
                        )
                        hist = admin_sessions.get(admin_id, {}).get("history", [])
                        messages = [{"role": "system", "content": system}]
                        for turn in hist[-6:]:
                            r = "user" if turn.get("role") == "user" else "assistant"
                            messages.append({"role": r, "content": (turn.get("content") or "")[:400]})
                        # latest user may already be in hist; still include as final to be safe
                        if not messages or messages[-1].get("role") != "user":
                            messages.append({"role": "user", "content": user_message[:600]})
                        reply = llm.chat(messages, max_tokens=140, temperature=0.7)
                except Exception as e:
                    print(f"[Finch] admin LLM error: {e}")
                    reply = None
                if not reply:
                    reply = (
                        "I'm here. Brain is offline for a moment — use /status, /handoffs, "
                        "and /docs, or try again shortly."
                    )

            await websocket.send_text(json.dumps({
                "type": "message", "content": reply, "sender": "finch",
            }))

            admin_sessions[admin_id]["history"].append({"role": "finch", "content": reply})
            admin_sessions[admin_id]["history"] = admin_sessions[admin_id]["history"][-20:]

    except WebSocketDisconnect:
        pass


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

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="theme-color" content="#0a0a0f">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Finch Admin">
<link rel="manifest" href="/manifest.json">
<title>Finch — Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #e0e0e0;
  }
  .app { display: flex; flex-direction: column; height: 100%; max-width: 720px; margin: 0 auto; }
  .topbar {
    padding: 16px 20px; background: #0d0d14; border-bottom: 1px solid #1e1e2e;
    display: flex; align-items: center; justify-content: space-between;
    padding-top: max(16px, env(safe-area-inset-top));
  }
  .topbar h2 { font-size: 18px; color: #4ade80; }
  .topbar .sub { font-size: 12px; color: #666; }
  .tabs { display: flex; background: #0d0d14; border-bottom: 1px solid #1e1e2e; }
  .tabs button {
    flex: 1; padding: 12px; background: none; border: none; color: #666;
    font-size: 13px; font-weight: 600; cursor: pointer;
    border-bottom: 2px solid transparent;
  }
  .tabs button.active { color: #4ade80; border-bottom-color: #4ade80; }
  .content { flex: 1; overflow-y: auto; padding: 16px; -webkit-overflow-scrolling: touch; }
  .card {
    background: #12121a; border: 1px solid #1e1e2e; border-radius: 12px;
    padding: 16px; margin-bottom: 12px;
  }
  .card h4 { font-size: 14px; color: #888; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { font-size: 28px; font-weight: 700; }
  .card .value.green { color: #4ade80; }
  .card .value.blue { color: #2563eb; }
  .card .value.red { color: #ef4444; }
  .handoff { border-left: 3px solid #f59e0b; padding-left: 12px; margin-bottom: 12px; }
  .handoff h5 { font-size: 14px; margin-bottom: 4px; }
  .handoff p { font-size: 12px; color: #888; }
  .chat-area { display: flex; flex-direction: column; gap: 10px; }
  .chat-area .msg {
    max-width: 85%; padding: 10px 14px; border-radius: 16px;
    font-size: 14px; line-height: 1.5;
  }
  .chat-area .msg.you { background: #2563eb; color: white; align-self: flex-end; border-bottom-right-radius: 4px; }
  .chat-area .msg.finch { background: #1a1a2e; color: #e0e0e0; align-self: flex-start; border-bottom-left-radius: 4px; border: 1px solid #2a2a3e; }
  .chat-input {
    display: flex; gap: 8px; padding: 12px 16px; background: #0d0d14;
    border-top: 1px solid #1e1e2e; padding-bottom: max(12px, env(safe-area-inset-bottom));
  }
  .chat-input input {
    flex: 1; padding: 12px 16px; background: #1a1a2e; border: 1px solid #2a2a3e;
    border-radius: 24px; color: #e0e0e0; font-size: 16px; outline: none;
  }
  .chat-input input:focus { border-color: #2563eb; }
  .chat-input button {
    width: 44px; height: 44px; background: #2563eb; color: white; border: none;
    border-radius: 50%; font-size: 18px; cursor: pointer;
  }
  .empty { text-align: center; color: #555; padding: 40px 20px; font-size: 14px; }
  .brain-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
  .brain-dot.online { background: #4ade80; box-shadow: 0 0 6px #4ade80; }
  .brain-dot.offline { background: #ef4444; }
  .pipeline-bar { background: #1e1e2e; border-radius: 6px; height: 8px; margin-top: 4px; overflow: hidden; }
  .pipeline-bar .fill { height: 100%; background: linear-gradient(90deg, #2563eb, #4ade80); border-radius: 6px; transition: width 0.5s; }
</style>
</head>
<body>
<div class="app">
  <div class="topbar">
    <div>
      <h2>Finch</h2>
      <div class="sub">AI Co-Founder Dashboard</div>
    </div>
    <div id="brainStatus"><span class="brain-dot offline"></span><span style="font-size:12px;color:#666">Brain</span></div>
  </div>
  <div class="tabs">
    <button onclick="switchTab('status')" id="tab-status" class="active">Status</button>
    <button onclick="switchTab('handoffs')" id="tab-handoffs">Handoffs</button>
    <button onclick="switchTab('docs')" id="tab-docs">Docs</button>
    <button onclick="switchTab('chat')" id="tab-chat">Chat</button>
  </div>
  <div id="content" class="content"></div>
  <div id="chatInput" class="chat-input" style="display:none">
    <input type="text" id="chatMsg" placeholder="Talk to Finch..." onkeypress="if(event.key==='Enter')sendChat()" autocomplete="off">
    <button onclick="sendChat()">&#9654;</button>
  </div>
</div>
<script>
  let currentTab='status';

  function switchTab(t){
    currentTab=t;
    document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active'));
    document.getElementById('tab-'+t).classList.add('active');
    document.getElementById('chatInput').style.display=t==='chat'?'flex':'none';
    renderTab(t);
  }

  async function renderTab(t){
    const c=document.getElementById('content');
    if(t==='status'){
      try{
        const r=await fetch('/api/admin/dashboard');
        const d=await r.json();
        c.innerHTML=
          '<div class="card"><h4>Brain</h4><div class="value '+(d.brain_online?'green':'red')+'">'+(d.brain_online?'Online':'Offline')+'</div></div>'+
          '<div class="card"><h4>Active Conversations</h4><div class="value blue">'+d.active_conversations+'</div></div>'+
          '<div class="card"><h4>Pending Handoffs</h4><div class="value green">'+(d.pending_handoffs||[]).length+'</div></div>'+
          '<div class="card"><h4>Active Clients</h4><div class="value green">'+(d.active_clients||0)+'</div></div>'+
          '<div class="card"><h4>MRR</h4><div class="value">$'+(d.mrr||0).toLocaleString()+'</div></div>'+
          '<div class="card"><h4>Stored Documents</h4><div class="value">'+(d.documents||[]).length+'</div></div>'+
          '<div class="card"><h4>Memories</h4><div class="value">'+(d.memory_count||0)+'</div></div>';
        var bd = document.getElementById('brainStatus');
        if(bd) bd.innerHTML = d.brain_online
          ? '<span class="brain-dot online"></span><span style="font-size:12px;color:#4ade80">Brain Online</span>'
          : '<span class="brain-dot offline"></span><span style="font-size:12px;color:#ef4444">Brain Offline</span>';
        if((d.clients||[]).length){
          c.innerHTML+='<div class="card"><h4>Client Roster</h4>'+d.clients.map(function(cl){
            return '<p style="margin:6px 0;font-size:13px">'+cl.company+' &mdash; '+cl.email+' &mdash; $'+(cl.price||0).toLocaleString()+'/mo</p>';
          }).join('')+'</div>';
        }
      }catch(e){c.innerHTML='<div class="empty">Could not load dashboard. Is the server running?</div>';}
    }else if(t==='handoffs'){
      try{
        const r=await fetch('/api/admin/dashboard');
        const d=await r.json();
        if(!d.pending_handoffs.length){c.innerHTML='<div class="empty">No pending handoffs. When a prospect says yes, they appear here.</div>';return;}
        let h='';
        d.pending_handoffs.forEach(p=>{
          h+='<div class="card handoff"><h5>'+p.company+'</h5><p>Email: '+p.email+' | Price: $'+(p.price||0).toLocaleString()+'/mo</p></div>';
        });
        c.innerHTML=h;
      }catch(e){c.innerHTML='<div class="empty">Error loading handoffs.</div>';}
    }else if(t==='docs'){
      try{
        const r=await fetch('/api/admin/dashboard');
        const d=await r.json();
        if(!d.documents.length){c.innerHTML='<div class="empty">No documents stored. Upload files from your computer or forward via email.</div>';return;}
        let h='';
        d.documents.forEach(doc=>{
          h+='<div class="card"><h4>#'+doc.id+': '+doc.name+'</h4><p>Tags: '+(doc.tags||[]).join(', ')+'</p></div>';
        });
        c.innerHTML=h;
      }catch(e){c.innerHTML='<div class="empty">Error loading documents.</div>';}
    }else if(t==='chat'){
      c.innerHTML="<div class='chat-area' id='chatMsgs'><div class='msg finch'>I am here. Commands: /status, /handoffs, /docs, /send — or just talk.</div></div>";
      connectChat();
    }
  }

  async function connectChat(){
    const m=document.getElementById('chatMsgs');
    if(!m)return;
    try{
      const r=await fetch('/api/admin/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:'phone',start:true})});
      const d=await r.json();
      if(d.content){
        const div=document.createElement('div');
        div.className='msg finch';
        div.textContent=d.content;
        m.appendChild(div);
      }
    }catch(e){}
  }

  async function sendChat(){
    const i=document.getElementById('chatMsg');
    const t=i.value.trim();
    if(!t)return;
    const m=document.getElementById('chatMsgs');
    const you=document.createElement('div');
    you.className='msg you';
    you.textContent=t;
    m.appendChild(you);
    m.scrollTop=m.scrollHeight;
    i.value='';
    try{
      const r=await fetch('/api/admin/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({admin_id:'phone',content:t})});
      const d=await r.json();
      const div=document.createElement('div');
      div.className='msg finch';
      div.textContent=d.content||'...';
      m.appendChild(div);
      m.scrollTop=m.scrollHeight;
    }catch(e){
      const div=document.createElement('div');
      div.className='msg finch';
      div.textContent='Connection issue — try again.';
      m.appendChild(div);
    }
  }

  renderTab('status');
</script>
</body>
</html>"""


# ── RUN ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = config.get("web", {}).get("chat_port", 8100)
    print(f"\n  Finch PWA starting on http://0.0.0.0:{port}")
    print(f"  Prospect chat:  http://your-server:{port}/")
    print(f"  Admin dashboard: http://your-server:{port}/admin")
    print(f"  Add to Home Screen on your phone for app-like experience.\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
