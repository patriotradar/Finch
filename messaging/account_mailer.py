"""Transactional customer account email using existing FINCH_* SMTP settings."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote


class AccountMailer:
    def _send(self, recipient: str, subject: str, body: str) -> bool:
        sender = (os.environ.get("FINCH_EMAIL") or "").strip()
        password = (os.environ.get("FINCH_EMAIL_PASSWORD") or "").strip()
        host = (os.environ.get("FINCH_SMTP_HOST") or "smtp.gmail.com").strip()
        port = int(os.environ.get("FINCH_SMTP_PORT") or "587")
        if not sender or not password:
            return False
        message = EmailMessage()
        message["From"] = f"Harold from Aegis <{sender}>"
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(sender, password)
                smtp.send_message(message)
            return True
        except Exception:
            return False

    @staticmethod
    def _base_url() -> str:
        return (os.environ.get("AEGIS_PUBLIC_URL") or os.environ.get("FINCH_WEB_CHAT_URL") or "").rstrip("/")

    def send_verification(self, recipient: str, token: str) -> bool:
        base = self._base_url()
        if not base:
            return False
        link = f"{base}/account?verify={quote(token)}"
        return self._send(
            recipient,
            "Verify your Aegis account",
            "Verify your Aegis customer account using this one-time link:\n\n"
            f"{link}\n\nThe link expires after 24 hours.\n\nHarold\nAegis",
        )

    def send_password_reset(self, recipient: str, token: str) -> bool:
        base = self._base_url()
        if not base:
            return False
        link = f"{base}/account?reset={quote(token)}"
        return self._send(
            recipient,
            "Reset your Aegis password",
            "Reset your Aegis password using this one-time link:\n\n"
            f"{link}\n\nThe link expires after 24 hours. If you did not request this, "
            "you can ignore the message.\n\nHarold\nAegis",
        )

    def send_invitation(self, recipient: str, token: str, company_name: str) -> bool:
        base = self._base_url()
        if not base:
            return False
        link = f"{base}/account?invite={quote(token)}"
        return self._send(
            recipient,
            f"You have been invited to {company_name} on Aegis",
            f"You have been invited to the {company_name} Aegis workspace.\n\n"
            "Choose your password using this one-time link:\n\n"
            f"{link}\n\nThe link expires after 24 hours. If you were not expecting "
            "this invitation, you can ignore it.\n\nHarold\nAegis",
        )
