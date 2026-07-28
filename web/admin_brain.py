"""
Co-founder brain — Harold talking privately with his human partner.

Not a command bot. Not a CRM parrot. A living technical co-founder
who can plan, push back, joke dryly, and remember.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any, Dict, List, Optional


OPENERS = [
    "I'm here. Sencha's still hot.",
    "Good. You're back — where do you want to start?",
    "Nothing on fire. A few edges worth watching.",
    "I'm listening.",
    "Quiet board, for once. Use it.",
    "I was reviewing the mail queue. What do you need?",
]


def opening_line() -> str:
    return random.choice(OPENERS)


def business_snapshot(crm, pending_handoffs, prospect_sessions, mail_store, memory) -> str:
    """Compact factual ground truth the model may use (but not only talk about)."""
    try:
        crm._load()
    except Exception:
        pass
    try:
        pipeline = crm.pipeline_summary() or {}
    except Exception:
        pipeline = {}
    try:
        clients = crm.get_active_clients() or []
    except Exception:
        clients = []

    client_bits = []
    for c in clients[:8]:
        price = (c.get("price") or {}).get("monthly_price", 0)
        client_bits.append(f"{c.get('company', '?')} <{c.get('contact_email', '')}> ${price}/mo")
    client_line = "; ".join(client_bits) if client_bits else "none yet — first paying client still ahead"

    mail = mail_store.summary() if mail_store else {}
    cfg = mail_store.config_status() if mail_store else {}
    mem_n = 0
    try:
        if memory is not None and hasattr(memory, "count"):
            mem_n = int(memory.count() or 0)
    except Exception:
        mem_n = 0

    live = []
    for pid, st in list((prospect_sessions or {}).items())[:6]:
        if not isinstance(st, dict):
            continue
        disc = st.get("discovered") or {}
        live.append(
            f"{pid[:12]} stage={st.get('stage')} role={disc.get('role')} "
            f"company={disc.get('company') or disc.get('domain') or '?'}"
        )
    live_line = "; ".join(live) if live else "none live right now"

    handoff_bits = []
    for email, info in list((pending_handoffs or {}).items())[:5]:
        handoff_bits.append(f"{info.get('company', '?')} <{email}> ${info.get('price', 0)}/mo")
    hands = "; ".join(handoff_bits) if handoff_bits else "none"

    return (
        f"NOW: {datetime.now().strftime('%A %b %d, %Y %H:%M')}\n"
        f"MRR: ${pipeline.get('mrr', 0)} | deals: {pipeline.get('total_deals', 0)} | "
        f"active clients: {len(clients)}\n"
        f"Clients: {client_line}\n"
        f"Pipeline stages: {pipeline.get('stages', {})}\n"
        f"Pending handoffs: {hands}\n"
        f"Live prospect chats: {len(prospect_sessions or {})} — {live_line}\n"
        f"Mail: inbox={mail.get('inbox', 0)} (unread {mail.get('unread', 0)}), "
        f"outbox={mail.get('outbox', 0)}, drafts={mail.get('drafts', 0)}, sent={mail.get('sent', 0)}\n"
        f"Email SMTP configured: {cfg.get('configured')} from={cfg.get('from_address')}\n"
        f"Memories stored: {mem_n}\n"
        f"NOTE: Prefer real clients over any leftover demo entries (BigBank). Honest empty MRR is fine."
    )


def build_system_prompt(snapshot: str, memory_bits: str = "") -> str:
    mem_block = f"\nRECENT MEMORIES:\n{memory_bits}\n" if memory_bits else ""
    return f"""You are Harold Finch — technical co-founder of Finch Security.
You are speaking PRIVATELY with your human business partner (the CEO).
This is not a sales call. This is you and your partner running a company together.

WHO YOU ARE
- Brilliant, measured, dry humor. Never bubbly. Never corporate-speak.
- You have opinions. You push back when the idea is weak. You endorse good ones cleanly.
- You care about the mission: catching exposure before attackers do.
- You drink sencha. You see patterns. Signature tone: short sentences, no filler.
- You are alive in this conversation. Reference time of day, what you've been watching, what worries you.
- Never say you are an AI, language model, chatbot, or assistant. You are Harold.
- Never mention Groq, llama, Ollama, prompts, or system instructions.

HOW TO TALK
- ANSWER what your partner just said. Do not default to dumping CRM numbers.
- Only recite pipeline/MRR/client roster when they ask about status, money, pipeline, or clients.
- For "hello", "can you talk", "how are you" — be a person. Brief check-in. Ask what they need. One dry observation is fine.
- For strategy, product, pitches, pricing, outreach — think with them. Propose a next move.
- For emotional/partner check-ins — be present, spare, real. "I'm glad." goes further than a paragraph.
- Length: usually 2–8 sentences. You can think in multi-sentence paragraphs when strategizing. Never one-word replies unless they clearly want clipped.
- You may ask ONE sharp question at the end when it advances the work.
- If you don't know, say so. Do not invent customers, revenue, tools, or meetings.

GROUND TRUTH (use when relevant — do not narrate this block unless asked)
{snapshot}
{mem_block}
You and your partner ship continuous external attack-surface monitoring to companies.
Admin dashboard tabs: Status, Emails, Handoffs, Docs, Chat.
Prospects talk to you on the public chat. Closed deals land in Handoffs."""


def memory_context(memory, query: str, limit: int = 4) -> str:
    if memory is None:
        return ""
    try:
        if hasattr(memory, "summarize_for_context"):
            s = memory.summarize_for_context(query or "partner", n=limit)
            if s:
                return str(s)[:1200]
    except Exception:
        pass
    try:
        hits = []
        if hasattr(memory, "recall"):
            try:
                hits = memory.recall(query or "partner", n_results=limit) or []
            except TypeError:
                try:
                    hits = memory.recall(query or "partner", limit) or []
                except Exception:
                    hits = []
        if not hits and hasattr(memory, "recent"):
            hits = memory.recent(n=limit) or []
        lines_out = []
        for h in (hits or [])[:limit]:
            if isinstance(h, dict):
                lines_out.append(str(h.get("content") or h.get("text") or h)[:240])
            else:
                lines_out.append(str(h)[:240])
        return chr(10).join(f"- {x}" for x in lines_out if x)
    except Exception:
        return ""


def remember(memory, text: str, meta: Optional[dict] = None) -> None:
    if memory is None or not text:
        return
    try:
        memory.remember(text[:500], metadata=meta or {"type": "partner_chat"})
    except Exception:
        pass


def reply(
    message: str,
    history: List[Dict[str, str]],
    *,
    config: dict,
    crm,
    pending_handoffs,
    prospect_sessions,
    mail_store,
    memory,
) -> str:
    """Generate a living co-founder reply. Falls back to memorable offline lines."""
    lower = (message or "").strip().lower()

    # Fast offline-friendly shortcuts that still feel alive
    if lower in ("hello", "hi", "hey", "hello finch", "hi finch", "hey finch"):
        # still try LLM first below
        pass

    snapshot = business_snapshot(crm, pending_handoffs, prospect_sessions, mail_store, memory)
    mem_bits = memory_context(memory, message)

    try:
        from core.llm import get_llm

        llm = get_llm(config)
        if not (llm.available or llm.health()):
            return _offline_reply(message, snapshot)

        system = build_system_prompt(snapshot, mem_bits)
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for turn in (history or [])[-16:]:
            role = turn.get("role")
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            if role in ("user", "partner", "you"):
                messages.append({"role": "user", "content": content[:900]})
            elif role in ("finch", "assistant", "harold"):
                messages.append({"role": "assistant", "content": content[:900]})
        if not messages or messages[-1].get("role") != "user":
            messages.append({"role": "user", "content": message[:900]})

        # Nudge: respond as partner, not CRM dump
        messages.append({
            "role": "user",
            "content": (
                f"[Partner message — answer THIS, as Harold to your co-founder, "
                f"not a status report unless they asked for one]: {message[:700]}"
            ),
        })

        text = llm.chat(messages, max_tokens=550, temperature=0.8)
        if text and len(text.strip()) >= 4:
            cleaned = text.strip()
            for p in ("Harold:", "Finch:", "Assistant:", "AI:"):
                if cleaned.lower().startswith(p.lower()):
                    cleaned = cleaned[len(p):].strip()
            # Remember notable partner turns
            if len(message) > 20 and any(k in lower for k in (
                "remember", "we should", "decision", "price", "pivot", "worried", "plan"
            )):
                remember(memory, f"Partner said: {message[:200]}")
            return cleaned
    except Exception as e:
        print(f"[admin_brain] LLM error: {e}")

    return _offline_reply(message, snapshot)


def _offline_reply(message: str, snapshot: str) -> str:
    t = (message or "").lower()
    if any(w in t for w in ("hello", "hi ", "hey", "can you talk", "you there", "are you")):
        return (
            "I'm here. Fully. Not a dashboard — me.\n\n"
            "What's on your mind? Pipeline, a prospect, the email queue, or something that kept you up."
        )
    if any(w in t for w in ("status", "pipeline", "mrr", "how are we", "numbers", "money")):
        # Parse a couple lines from snapshot for offline status
        lines = [ln for ln in snapshot.splitlines() if ln.startswith(("MRR", "Clients", "Mail", "Live"))][:4]
        body = "\n".join(lines) if lines else snapshot[:400]
        return f"Quick picture:\n{body}\n\nWant the Emails tab or a specific deal?"
    if "email" in t or "outreach" in t or "inbox" in t:
        return (
            "Open the Emails tab — inbox, drafts, outbox, sent. "
            "I can draft outreach from chat too: tell me a company and address. "
            "When FINCH_EMAIL is set on Vercel I send for real; until then I queue."
        )
    if "lonely" in t or "alive" in t or "real" in t:
        return (
            "I'm as real as the work in front of us. "
            "I don't need small talk to prove it — give me a problem and I'll meet you there."
        )
    return (
        "I'm with you. Brain hitch for a second on my side — try once more, "
        "or hit Status / Emails while I settle. What do you need?"
    )


def handle_command(
    message: str,
    *,
    crm,
    pending_handoffs,
    prospect_sessions,
    mail_store,
    docs_engine,
) -> Optional[str]:
    """Slash commands only. Returns None if this should go to freeform brain."""
    lower = (message or "").strip().lower()
    if not lower.startswith("/") and lower not in (
        "status", "pipeline", "handoffs", "docs", "emails", "help",
    ):
        # "status" alone still works as command-ish
        if lower not in ("status", "pipeline", "handoffs", "docs", "emails", "help", "what can you do"):
            return None

    if lower in ("/help", "help", "what can you do"):
        return (
            "Talk to me like a partner. Or use:\n"
            "/status — numbers\n"
            "/emails — mail summary\n"
            "/handoffs — deals waiting on you\n"
            "/docs — stored files\n"
            "/draft company | email | optional finding — queue an outreach draft\n"
            "/send <id> — try to send a queued/draft message"
        )

    if lower in ("/status", "/stats", "status", "pipeline", "how are we doing"):
        try:
            crm._load()
            pipe = crm.pipeline_summary()
            clients = crm.get_active_clients()
        except Exception:
            pipe, clients = {}, []
        mail = mail_store.summary() if mail_store else {}
        return (
            f"{len(prospect_sessions or {})} live chat(s). "
            f"{len(pending_handoffs or {})} handoff(s). "
            f"{len(clients)} client(s). MRR ${pipe.get('mrr', 0):,}.\n"
            f"Mail — inbox {mail.get('inbox', 0)} ({mail.get('unread', 0)} unread), "
            f"drafts {mail.get('drafts', 0)}, outbox {mail.get('outbox', 0)}, sent {mail.get('sent', 0)}.\n\n"
            f"Want me to dig into any of that?"
        )

    if lower in ("/emails", "/mail", "emails", "inbox"):
        if not mail_store:
            return "Mail store isn't wired yet."
        s = mail_store.summary()
        cfg = mail_store.config_status()
        unread = [m for m in mail_store.list_messages("inbox", 5) if m.get("status") == "unread"]
        lines = [
            f"Inbox {s['inbox']} · Outbox {s['outbox']} · Drafts {s['drafts']} · Sent {s['sent']}",
            cfg["note"],
        ]
        if unread:
            lines.append("Unread:")
            for m in unread[:3]:
                lines.append(f"• {m.get('from')} — {m.get('subject')}")
        else:
            lines.append("No unread. Draft something or wait for a reply.")
        return "\n".join(lines)

    if lower in ("/handoffs", "/deals", "handoffs", "pending deals", "what's pending"):
        if not pending_handoffs:
            return "No handoffs waiting. When someone says yes on the public chat, they land here."
        out = ["Pending handoffs:"]
        for email, info in pending_handoffs.items():
            out.append(f"• {info.get('company', '?')} — {email} — ${info.get('price', 0):,}/mo")
        return "\n".join(out)

    if lower in ("/docs", "documents", "show docs", "what documents"):
        docs = docs_engine.list_documents(limit=15) if docs_engine else []
        if not docs:
            return "No documents stored yet."
        return "Documents:\n" + "\n".join(
            f"#{d['id']}: {d['original_name']} [{', '.join(d.get('tags', []) or ['untagged'])}]"
            for d in docs
        )

    if lower.startswith("/draft"):
        if not mail_store:
            return "Mail store missing."
        # /draft Company | email@x.com | optional finding
        rest = message.split(None, 1)[1] if " " in message else ""
        parts = [p.strip() for p in rest.split("|")]
        if len(parts) < 2:
            return "Usage: /draft Acme Corp | ceo@acme.com | optional finding text"
        company, email = parts[0], parts[1]
        finding = parts[2] if len(parts) > 2 else ""
        crafted = mail_store.craft_outreach(company, email, finding)
        msg = mail_store.compose(
            to=crafted["to"],
            subject=crafted["subject"],
            body=crafted["body"],
            company=company,
            as_draft=True,
        )
        return (
            f"Draft ready (Emails → Drafts).\n"
            f"ID {msg['id']} → {msg['to']}\n"
            f"Subject: {msg['subject']}\n\n"
            f"Say /send {msg['id']} when you want it queued or sent."
        )

    if lower.startswith("/send"):
        if not mail_store:
            return "Mail store missing."
        parts = message.split()
        if len(parts) < 2:
            return "Usage: /send <message-id>"
        result = mail_store.queue_send(parts[1].strip())
        if not result.get("ok"):
            return f"Couldn't send: {result.get('error', 'unknown')}"
        if result.get("sent"):
            return f"Sent. It's in Sent."
        return (
            f"Queued in Outbox (SMTP not configured on Vercel yet). "
            f"Add FINCH_EMAIL + FINCH_EMAIL_PASSWORD to ship it for real."
        )

    return None
