"""Compliance and deliverability gates for Aegis outreach."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.controls import ControlService
from core.models import OutreachActivity, SuppressionRecord


DAILY_LIMIT = 20
MAX_FOLLOWUPS = 3
HARD_BOUNCE_STOP_RATE = 0.03
ELIGIBLE_TYPES = {"limited_company", "plc", "corporate_body"}


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


class OutreachPolicy:
    def __init__(self, session: Session):
        self.session = session

    def can_contact(
        self,
        company_name: str,
        email: str,
        contact_source: str,
        business_type: str,
        *,
        consent: bool = False,
        followup: bool = False,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        now = now or datetime.now(timezone.utc)
        email = normalize_email(email)
        if ControlService(self.session).is_paused():
            return False, "harold_paused"
        if not email or not contact_source.strip():
            return False, "contact_or_source_missing"
        if business_type not in ELIGIBLE_TYPES and not consent:
            return False, "business_type_not_eligible"
        if self.session.get(SuppressionRecord, email):
            return False, "suppressed"
        activities = self.session.scalars(
            select(OutreachActivity).where(OutreachActivity.contact_email == email)
        ).all()
        if any(item.replied_at is not None or item.status == "replied" for item in activities):
            return False, "reply_received"
        if any(item.status == "hard_bounce" for item in activities):
            return False, "hard_bounce"
        if followup:
            followups = sum(1 for item in activities if item.message_type.startswith("followup_"))
            if followups >= MAX_FOLLOWUPS:
                return False, "followup_limit_reached"
        elif any(item.message_type == "initial" for item in activities):
            return False, "duplicate_contact"
        day_start = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)
        sent_today = self.session.scalar(
            select(func.count()).select_from(OutreachActivity).where(
                OutreachActivity.sent_at >= day_start,
                OutreachActivity.status.in_(("sent", "delivered", "replied", "hard_bounce")),
            )
        ) or 0
        if int(sent_today) >= DAILY_LIMIT:
            return False, "daily_limit_reached"
        return True, "allowed"

    def record_sent(
        self, company_name: str, email: str, contact_source: str,
        message_type: str, payload: dict | None = None,
    ) -> OutreachActivity:
        if message_type != "initial" and not message_type.startswith("followup_"):
            raise ValueError("invalid_message_type")
        item = OutreachActivity(
            company_name=company_name,
            contact_email=normalize_email(email),
            contact_source=contact_source,
            message_type=message_type,
            status="sent",
            sent_at=datetime.now(timezone.utc),
            payload=payload or {},
        )
        self.session.add(item)
        return item

    def record_reply(self, email: str) -> None:
        activities = self.session.scalars(
            select(OutreachActivity).where(
                OutreachActivity.contact_email == normalize_email(email)
            )
        ).all()
        now = datetime.now(timezone.utc)
        for item in activities:
            item.status = "replied"
            item.replied_at = now

    def record_hard_bounce(self, activity_id: str) -> bool:
        item = self.session.get(OutreachActivity, activity_id)
        if item is None:
            raise LookupError("outreach_activity_not_found")
        item.status = "hard_bounce"
        sent = self.session.scalar(
            select(func.count()).select_from(OutreachActivity).where(
                OutreachActivity.status.in_(("sent", "delivered", "replied", "hard_bounce"))
            )
        ) or 0
        bounces = self.session.scalar(
            select(func.count()).select_from(OutreachActivity).where(
                OutreachActivity.status == "hard_bounce"
            )
        ) or 0
        should_pause = bool(sent and (int(bounces) / int(sent)) > HARD_BOUNCE_STOP_RATE)
        if should_pause:
            ControlService(self.session).set_paused(
                True, changed_by="deliverability_guard", reason="hard_bounce_rate_exceeded"
            )
        return should_pause

    def suppress(self, email: str, reason: str, source: str) -> SuppressionRecord:
        email = normalize_email(email)
        existing = self.session.get(SuppressionRecord, email)
        if existing:
            return existing
        record = SuppressionRecord(email=email, reason=reason, source=source)
        self.session.add(record)
        return record


class OptOutTokens:
    def __init__(self, secret: str | None = None):
        self.secret = (secret or os.environ.get("FINCH_OPTOUT_SECRET") or "").encode()

    def issue(self, email: str) -> str:
        if len(self.secret) < 24:
            raise RuntimeError("optout_secret_not_configured")
        payload = base64.urlsafe_b64encode(
            json.dumps({"email": normalize_email(email)}, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def validate(self, token: str) -> str:
        if len(self.secret) < 24:
            raise RuntimeError("optout_secret_not_configured")
        try:
            payload, signature = token.split(".", 1)
            expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            return normalize_email(json.loads(raw)["email"])
        except Exception as error:
            raise ValueError("invalid_optout_token") from error
