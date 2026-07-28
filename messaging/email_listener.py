"""
Email listener — Finch monitors your business inbox for replies to cold emails
and responds as Kane. No Telegram needed.

Uses IMAP IDLE to watch for new messages in real time. When a prospect replies,
Finch picks up the conversation thread and handles it through the full
SalesConversation flow (greeting → qualification → demo → objections → pricing → close).

Phone workflow:
  - You send cold emails from Finch's address
  - Prospect replies "Who is this?" or "Tell me more"
  - Finch replies autonomously, you get notified on your dashboard
  - When they say yes, the handoff appears at /admin on your phone
"""

import email
import imaplib
import smtplib
import ssl
import time
import re
import os
import sys
import json
from datetime import datetime
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parseaddr

sys.path.insert(0, str(Path(__file__).parent.parent))

from sales.conversation import SalesConversation
from memory.vector_store import MemoryStore


class EmailListener:
    """Watches for replies to cold emails and lets Finch respond autonomously."""

    def __init__(self, config=None, daemon=None):
        self.config = config or {}
        self.daemon = daemon

        # IMAP for reading replies
        self.imap_host = os.environ.get("FINCH_IMAP_HOST", "imap.gmail.com")
        self.imap_port = int(os.environ.get("FINCH_IMAP_PORT", "993"))
        self.email_addr = os.environ.get("FINCH_EMAIL")
        self.email_pass = os.environ.get("FINCH_EMAIL_PASSWORD")

        # SMTP for sending replies
        self.smtp_host = os.environ.get("FINCH_SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("FINCH_SMTP_PORT", "587"))

        self.memory = MemoryStore(self.config.get("memory", {}))
        self.conversation_engine = SalesConversation(
            persona_engine=None, pricing_engine=None, memory=self.memory, config=self.config
        )

        # Active email threads we're tracking
        # {thread_subject_key: {"state": conversation_state, "prospect_email": ..., "prospect_name": ...}}
        self.active_threads = {}
        self.processed_ids = set()

    def start(self):
        """Begin watching the inbox. Runs in a background thread."""
        if not self.email_addr or not self.email_pass:
            print("[Finch Email] FINCH_EMAIL or FINCH_EMAIL_PASSWORD not set. Email listener disabled.")
            return

        import threading
        thread = threading.Thread(target=self._watch_loop, daemon=True)
        thread.start()
        print(f"[Finch Email] Watching {self.email_addr} for prospect replies...")

    def _watch_loop(self):
        """Main IMAP IDLE loop."""
        while True:
            try:
                context = ssl.create_default_context()
                with imaplib.IMAP4_SSL(self.imap_host, self.imap_port, ssl_context=context) as imap:
                    imap.login(self.email_addr, self.email_pass)
                    imap.select("INBOX")

                    # Check for new messages
                    self._check_inbox(imap)

                    # Use IDLE for real-time notifications
                    try:
                        imap.idle()
                        while True:
                            # Wait for new mail notification
                            responses = imap.idle_check(timeout=60)
                            if responses:
                                imap.idle_done()
                                self._check_inbox(imap)
                                imap.idle()
                    except imaplib.IMAP4.abort:
                        # Connection dropped, reconnect
                        pass

            except Exception as e:
                print(f"[Finch Email] Error: {e}. Reconnecting in 30s...")
                time.sleep(30)

    def _check_inbox(self, imap):
        """Check for new unread replies from prospects."""
        status, messages = imap.search(None, '(UNSEEN)')
        if status != "OK":
            return

        for msg_id in messages[0].split():
            if msg_id in self.processed_ids:
                continue

            try:
                status, msg_data = imap.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                parsed = email.message_from_bytes(raw_email)

                # Skip anything that isn't a direct reply (notifications, bounces, etc.)
                if not self._is_prospect_reply(parsed):
                    self.processed_ids.add(msg_id)
                    continue

                prospect_addr = parseaddr(parsed["From"])[1]
                subject = self._normalize_subject(parsed["Subject"] or "No Subject")
                body = self._extract_body(parsed)

                if not prospect_addr or not body:
                    self.processed_ids.add(msg_id)
                    continue

                print(f"[Finch Email] Reply from {prospect_addr}: {subject[:60]}")

                response = self._handle_reply(subject, prospect_addr, body)

                # Send response back
                self._send_reply(prospect_addr, subject, response)

                self.processed_ids.add(msg_id)

            except Exception as e:
                print(f"[Finch Email] Error processing message: {e}")
                self.processed_ids.add(msg_id)

    def _is_prospect_reply(self, parsed):
        """Filter out auto-replies, bounces, and notifications."""
        subject = (parsed["Subject"] or "").lower()
        from_addr = (parsed["From"] or "").lower()

        skip_phrases = [
            "auto-reply", "automatic reply", "out of office", "vacation",
            "undelivered", "delivery status", "mail delivery", "returned mail",
            "postmaster", "mailer-daemon", "noreply", "no-reply", "donotreply",
            "notification", "alert", "reminder", "newsletter", "digest",
        ]
        for phrase in skip_phrases:
            if phrase in subject or phrase in from_addr:
                return False

        # Must be from someone other than us
        if self.email_addr and self.email_addr.lower() in from_addr:
            return False

        return True

    def _normalize_subject(self, subject):
        """Strip Re:/Fwd: prefixes for thread matching."""
        return re.sub(r'^(Re:\s*)+', '', subject, flags=re.IGNORECASE).strip()

    def _extract_body(self, parsed):
        """Extract plain text body from an email message."""
        if parsed.is_multipart():
            for part in parsed.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(errors="replace").strip()
        else:
            payload = parsed.get_payload(decode=True)
            if payload:
                return payload.decode(errors="replace").strip()
        return ""

    def _handle_reply(self, subject, prospect_addr, body):
        """Route the reply through the SalesConversation engine and return Finch's response."""

        # Use subject as thread key to maintain conversation state
        thread_key = subject.lower().strip()

        if thread_key not in self.active_threads:
            # New thread — start conversation
            state = self.conversation_engine.start_conversation(
                prospect_info={"source": "email_reply", "email": prospect_addr, "subject": subject}
            )
            self.active_threads[thread_key] = {
                "state": state,
                "prospect_email": prospect_addr,
                "started": datetime.now().isoformat(),
            }
        else:
            state = self.active_threads[thread_key]["state"]

        # Process the message
        response, state = self.conversation_engine.handle_message(state, body)
        self.active_threads[thread_key]["state"] = state

        # Log significant events
        if state.get("ready_to_close"):
            self.memory.remember(
                f"PROSPECT CLOSED VIA EMAIL: {prospect_addr}. "
                f"Discovered: {state['discovered']}",
                metadata={"type": "deal_closed", "source": "email", "prospect_email": prospect_addr}
            )

        return response

    def _send_reply(self, to_addr, subject, body):
        """Send Finch's reply via SMTP."""
        if not self.email_addr or not self.email_pass:
            print("[Finch Email] Cannot send — credentials not set.")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = f"Kane <{self.email_addr}>"
            msg["To"] = to_addr
            msg["Subject"] = f"Re: {subject}"
            msg.attach(MIMEText(body, "plain"))

            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as s:
                s.starttls(context=ctx)
                s.login(self.email_addr, self.email_pass)
                s.sendmail(self.email_addr, to_addr, msg.as_string())

            print(f"[Finch Email] Replied to {to_addr}")
            return True
        except Exception as e:
            print(f"[Finch Email] Failed to send reply: {e}")
            return False

    def get_status(self):
        """Return status for the dashboard."""
        return {
            "active_threads": len(self.active_threads),
            "processed_replies": len(self.processed_ids),
            "prospects_in_pipeline": sum(
                1 for t in self.active_threads.values()
                if t["state"].get("ready_to_close")
            ),
        }


# ── TEST ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    listener = EmailListener()
    print("EmailListener initialized.")
    print(f"  IMAP: {listener.imap_host}:{listener.imap_port}")
    print(f"  Email: {listener.email_addr or 'NOT SET'}")
    print(f"  Active threads: {listener.get_status()}")

    if not listener.email_addr:
        print("\nTo enable email replies, set:")
        print("  export FINCH_EMAIL='your-business@gmail.com'")
        print("  export FINCH_EMAIL_PASSWORD='your-app-password'")
        print("  (Use Gmail App Passwords — Settings → Security → 2FA → App Passwords)")
