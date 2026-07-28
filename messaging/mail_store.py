"""
Persistent mail queue for Finch admin dashboard.

On Vercel, long-running IMAP is impossible, so mail lives as JSON under
FINCH_DATA_DIR. Outbound can still ship via SMTP when FINCH_EMAIL + password
are set; otherwise messages stay in `queued` / `draft` until credentials exist.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MailStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "mail.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {"messages": [], "leads": []}
        self._load()
        if not self._data["messages"]:
            self._seed()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                self._data.setdefault("messages", [])
                self._data.setdefault("leads", [])
            except Exception:
                self._data = {"messages": [], "leads": []}

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            print(f"[MailStore] save failed: {e}")

    def _seed(self) -> None:
        """Demo content so the Emails tab never looks empty on first open."""
        samples = [
            {
                "id": "seed-in-1",
                "folder": "inbox",
                "direction": "inbound",
                "status": "unread",
                "from": "jordan@northline.io",
                "to": "kane@aegis.security",
                "subject": "Re: Security finding for Northline",
                "body": (
                    "Kane — interesting. Who is this, and how'd you find that subdomain? "
                    "I'm the CTO. Happy to hear more if this isn't a cold spray."
                ),
                "created_at": _now(),
                "thread": "northline",
                "company": "Northline",
            },
            {
                "id": "seed-out-1",
                "folder": "sent",
                "direction": "outbound",
                "status": "sent",
                "from": "kane@aegis.security",
                "to": "jordan@northline.io",
                "subject": "Security finding for Northline",
                "body": (
                    "Hi Jordan,\n\nI'm Kane, technical co-founder at Aegis. "
                    "We monitor external infrastructure — Northline came up in our scans.\n\n"
                    "I found an exposed staging host on a stale DNS record. It's the kind of "
                    "door attackers scan for first.\n\nHappy to walk you through it in plain "
                    "English. No pitch — just the picture.\n\nBest,\nHarold Finch"
                ),
                "created_at": _now(),
                "thread": "northline",
                "company": "Northline",
            },
            {
                "id": "seed-draft-1",
                "folder": "drafts",
                "direction": "outbound",
                "status": "draft",
                "from": "kane@aegis.security",
                "to": "ceo@acmecorp.com",
                "subject": "Security finding for Acme Corp",
                "body": (
                    "Hi,\n\nI'm Kane at Aegis. A quick external look at "
                    "acmecorp.com surfaced a few items I'd want to know about if it were mine.\n\n"
                    "Five-minute walkthrough, free, no commitment. Reply or chat here:\n"
                    "{chat}\n\nHarold"
                ),
                "created_at": _now(),
                "thread": "acme",
                "company": "Acme Corp",
            },
            {
                "id": "seed-q-1",
                "folder": "outbox",
                "direction": "outbound",
                "status": "queued",
                "from": "kane@aegis.security",
                "to": "ciso@brightpath.health",
                "subject": "Quick note on Brightpath's external surface",
                "body": (
                    "Hi,\n\nKane here. Healthcare attack surfaces change quietly — "
                    "patient portals, vendor SaaS, forgotten FHIR endpoints.\n\n"
                    "I can show you a plain-English map of what's exposed from the outside.\n\n"
                    "Kane"
                ),
                "created_at": _now(),
                "thread": "brightpath",
                "company": "Brightpath Health",
            },
        ]
        chat = os.environ.get("FINCH_WEB_CHAT_URL", "https://finch-ocxl.vercel.app")
        for m in samples:
            m["body"] = m["body"].replace("{chat}", chat)
            m["example"] = True
        self._data["messages"] = samples
        self._data["leads"] = [
            {"company": "Northline", "email": "jordan@northline.io", "stage": "replied"},
            {"company": "Acme Corp", "email": "ceo@acmecorp.com", "stage": "draft"},
            {"company": "Brightpath Health", "email": "ciso@brightpath.health", "stage": "queued"},
        ]
        self._save()

    def smtp_ready(self) -> bool:
        return bool(os.environ.get("FINCH_EMAIL") and os.environ.get("FINCH_EMAIL_PASSWORD"))

    def config_status(self) -> Dict[str, Any]:
        addr = os.environ.get("FINCH_EMAIL") or ""
        return {
            "configured": self.smtp_ready(),
            "from_address": addr or None,
            "smtp_host": os.environ.get("FINCH_SMTP_HOST", "smtp.gmail.com"),
            "note": (
                "SMTP live — queued mail can send."
                if self.smtp_ready()
                else "Set FINCH_EMAIL + FINCH_EMAIL_PASSWORD on Vercel to send for real. "
                     "Until then, drafts and queue still work as a full local outbox."
            ),
        }

    def summary(self) -> Dict[str, int]:
        folders = {"inbox": 0, "outbox": 0, "drafts": 0, "sent": 0}
        unread = 0
        for m in self._data["messages"]:
            f = m.get("folder", "inbox")
            if f in folders:
                folders[f] += 1
            if m.get("folder") == "inbox" and m.get("status") == "unread":
                unread += 1
        return {**folders, "unread": unread, "total": len(self._data["messages"])}

    def list_messages(self, folder: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        msgs = list(self._data["messages"])
        if folder and folder != "all":
            msgs = [m for m in msgs if m.get("folder") == folder]
        msgs.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return msgs[:limit]

    def get(self, msg_id: str) -> Optional[Dict[str, Any]]:
        for m in self._data["messages"]:
            if m.get("id") == msg_id:
                return m
        return None

    def mark_read(self, msg_id: str) -> bool:
        m = self.get(msg_id)
        if not m:
            return False
        if m.get("status") == "unread":
            m["status"] = "read"
            self._save()
        return True

    def compose(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        company: str = "",
        as_draft: bool = True,
        from_addr: Optional[str] = None,
    ) -> Dict[str, Any]:
        msg = {
            "id": f"m-{uuid.uuid4().hex[:10]}",
            "folder": "drafts" if as_draft else "outbox",
            "direction": "outbound",
            "status": "draft" if as_draft else "queued",
            "from": from_addr or os.environ.get("FINCH_EMAIL") or "kane@aegis.security",
            "to": (to or "").strip(),
            "subject": (subject or "").strip() or "(no subject)",
            "body": body or "",
            "created_at": _now(),
            "thread": company.lower().replace(" ", "-") if company else "general",
            "company": company or "",
        }
        self._data["messages"].insert(0, msg)
        self._save()
        return msg

    def update_draft(self, msg_id: str, **fields) -> Optional[Dict[str, Any]]:
        m = self.get(msg_id)
        if not m or m.get("folder") not in ("drafts", "outbox"):
            return None
        for k in ("to", "subject", "body", "company"):
            if k in fields and fields[k] is not None:
                m[k] = fields[k]
        self._save()
        return m

    def queue_send(self, msg_id: str) -> Dict[str, Any]:
        m = self.get(msg_id)
        if not m:
            return {"ok": False, "error": "not_found"}
        m["folder"] = "outbox"
        m["status"] = "queued"
        m["queued_at"] = _now()
        self._save()
        # Try real send if SMTP ready
        if self.smtp_ready():
            return self.send_now(msg_id)
        return {"ok": True, "status": "queued", "message": m, "sent": False}

    def send_now(self, msg_id: str) -> Dict[str, Any]:
        m = self.get(msg_id)
        if not m:
            return {"ok": False, "error": "not_found"}
        if not self.smtp_ready():
            m["folder"] = "outbox"
            m["status"] = "queued"
            self._save()
            return {
                "ok": True,
                "sent": False,
                "status": "queued",
                "error": "smtp_not_configured",
                "message": m,
            }
        from_addr = os.environ.get("FINCH_EMAIL")
        passwd = os.environ.get("FINCH_EMAIL_PASSWORD")
        host = os.environ.get("FINCH_SMTP_HOST", "smtp.gmail.com")
        port = int(os.environ.get("FINCH_SMTP_PORT", "587"))
        try:
            msg = MIMEMultipart()
            msg["From"] = f"Kane <{from_addr}>"
            msg["To"] = m["to"]
            msg["Subject"] = m["subject"]
            msg.attach(MIMEText(m.get("body") or "", "plain"))
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(from_addr, passwd)
                s.sendmail(from_addr, m["to"], msg.as_string())
            m["folder"] = "sent"
            m["status"] = "sent"
            m["sent_at"] = _now()
            m["from"] = from_addr
            self._save()
            return {"ok": True, "sent": True, "status": "sent", "message": m}
        except Exception as e:
            m["status"] = "failed"
            m["error"] = str(e)[:200]
            m["folder"] = "outbox"
            self._save()
            return {"ok": False, "sent": False, "error": str(e), "message": m}

    def receive(
        self,
        from_addr: str,
        subject: str,
        body: str,
        *,
        company: str = "",
        to: Optional[str] = None,
    ) -> Dict[str, Any]:
        msg = {
            "id": f"in-{uuid.uuid4().hex[:10]}",
            "folder": "inbox",
            "direction": "inbound",
            "status": "unread",
            "from": from_addr,
            "to": to or os.environ.get("FINCH_EMAIL") or "kane@aegis.security",
            "subject": subject,
            "body": body,
            "created_at": _now(),
            "thread": company.lower().replace(" ", "-") if company else "inbound",
            "company": company or "",
        }
        self._data["messages"].insert(0, msg)
        self._save()
        return msg

    def delete(self, msg_id: str) -> bool:
        before = len(self._data["messages"])
        self._data["messages"] = [m for m in self._data["messages"] if m.get("id") != msg_id]
        if len(self._data["messages"]) < before:
            self._save()
            return True
        return False

    def craft_outreach(self, company: str, email: str, finding: str = "") -> Dict[str, str]:
        chat = os.environ.get("FINCH_WEB_CHAT_URL", "https://finch-ocxl.vercel.app")
        company = company or "your organization"
        if finding:
            body = (
                f"Hi,\n\nI'm Kane, technical co-founder at Aegis. "
                f"We monitor external infrastructure — and {company} came up.\n\n"
                f"I found something I'd want to know about if it were mine:\n\n"
                f"{finding}\n\n"
                f"Happy to walk you through the full picture in plain English. "
                f"No pitch, five minutes.\n\n"
                f"Or chat live: {chat}\n\n"
                f"Best,\nHarold Finch\nTechnical Co-Founder, Aegis"
            )
            subject = f"Security finding for {company}"
        else:
            body = (
                f"Hi,\n\nI'm Kane at Aegis. I help teams see what "
                f"their internet perimeter actually looks like from the outside.\n\n"
                f"Most organizations have 3–5× more exposed assets than they track. "
                f"I'd like to show you {company}'s picture — free, plain English, no commitment.\n\n"
                f"Chat: {chat}\n\n"
                f"Kane"
            )
            subject = f"A quick look at {company}'s external surface"
        return {"subject": subject, "body": body, "to": email, "company": company}
