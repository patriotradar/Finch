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

    def _smtp_cfg_path(self) -> Path:
        return self.path.parent / "smtp_config.json"

    def smtp_credentials(self) -> Dict[str, str]:
        """Read SMTP credentials only from protected runtime environment variables."""
        email = (os.environ.get("FINCH_EMAIL") or "").strip()
        password = (os.environ.get("FINCH_EMAIL_PASSWORD") or "").strip()
        host = (os.environ.get("FINCH_SMTP_HOST") or "smtp.gmail.com").strip()
        port = str(os.environ.get("FINCH_SMTP_PORT") or "587").strip()
        source = "env" if email and password else "none"
        return {
            "email": email,
            "password": password,
            "smtp_host": host,
            "smtp_port": port,
            "source": source,
        }

    def smtp_ready(self) -> bool:
        c = self.smtp_credentials()
        return bool(c["email"] and c["password"])

    def config_status(self) -> Dict[str, Any]:
        c = self.smtp_credentials()
        return {
            "configured": self.smtp_ready(),
            "from_address": c["email"] or None,
            "smtp_host": c["smtp_host"],
            "source": c["source"],
            "note": (
                f"SMTP live via {c['source']} — queued mail can send from {c['email']}."
                if self.smtp_ready()
                else "Set FINCH_EMAIL and FINCH_EMAIL_PASSWORD in the protected deployment "
                     "environment. Drafts and queue still work without SMTP."
            ),
        }

    def save_smtp_config(
        self,
        email: str,
        password: str,
        *,
        smtp_host: str = "smtp.gmail.com",
        smtp_port: str = "587",
        test: bool = True,
    ) -> Dict[str, Any]:
        email = (email or "").strip()
        password = (password or "").replace(" ", "").strip()
        smtp_host = (smtp_host or "smtp.gmail.com").strip()
        smtp_port = str(smtp_port or "587").strip()
        if not email or not password:
            return {"ok": False, "error": "email_and_password_required"}
        if test:
            try:
                ctx = ssl.create_default_context()
                with smtplib.SMTP(smtp_host, int(smtp_port), timeout=20) as s:
                    s.ehlo()
                    s.starttls(context=ctx)
                    s.ehlo()
                    s.login(email, password)
            except Exception as e:
                return {"ok": False, "error": f"login_failed: {e}"}
        return {
            "ok": False,
            "error": "environment_configuration_required",
            "message": (
                "Credentials were tested but not stored. Set FINCH_EMAIL, "
                "FINCH_EMAIL_PASSWORD, FINCH_SMTP_HOST and FINCH_SMTP_PORT "
                "in the protected deployment environment."
            ),
        }

    def clear_smtp_config(self) -> Dict[str, Any]:
        path = self._smtp_cfg_path()
        if path.exists():
            path.unlink()
        return {"ok": True, **self.config_status()}

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
            "from": from_addr or self.smtp_credentials()["email"] or "kane@aegis.security",
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
        creds = self.smtp_credentials()
        from_addr = creds["email"]
        passwd = creds["password"]
        host = creds["smtp_host"]
        port = int(creds["smtp_port"] or "587")
        try:
            msg = MIMEMultipart()
            msg["From"] = f"Harold from Aegis <{from_addr}>"
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
            "to": to or self.smtp_credentials()["email"] or "hello@aegis.security",
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
        chat = os.environ.get("AEGIS_PUBLIC_URL") or os.environ.get("FINCH_WEB_CHAT_URL") or ""
        company = company or "your organization"
        if finding:
            body = (
                f"Hi,\n\nI'm Harold from Aegis. I found this public company update from {company}:\n\n"
                f"{finding}\n\n"
                "I am not suggesting that Aegis found a vulnerability. The update simply "
                "indicates that public security visibility may be relevant to your organisation.\n\n"
                f"A seven-minute overview is available here: {chat}\n\n"
                f"Best,\nHarold\nAegis"
            )
            subject = f"Public-information monitoring for {company}"
        else:
            body = (
                f"Hi,\n\nI'm Harold from Aegis. Aegis provides passive public-information "
                f"monitoring for assets a customer owns or is authorised to manage.\n\n"
                f"If that is relevant to {company}, the short overview is available here:\n\n"
                f"Chat: {chat}\n\n"
                f"Best,\nHarold\nAegis"
            )
            subject = f"Passive public-information monitoring for {company}"
        try:
            from sales.outreach_policy import OptOutTokens
            token = OptOutTokens().issue(email)
            body += f"\n\nOpt out: {chat.rstrip('/')}/unsubscribe?token={token}"
        except Exception:
            body += "\n\nOpt-out link will be added when outreach delivery is configured."
        return {"subject": subject, "body": body, "to": email, "company": company}
