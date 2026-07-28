"""Durable private Harold history and factual owner briefings."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    ConversationRecord, CustomerAlert, MonitoringRequest, OutreachActivity,
    PaymentEvent, Subscription, SystemControl, Workspace, utcnow,
)


OWNER_CHANNEL = "owner_private"
MAX_HISTORY = 80


class OwnerConversationService:
    def __init__(self, session: Session):
        self.session = session

    def load(self, participant_ref: str) -> tuple[ConversationRecord, list[dict]]:
        ref = (participant_ref or "owner")[:200]
        record = self.session.scalar(select(ConversationRecord).where(
            ConversationRecord.channel == OWNER_CHANNEL,
            ConversationRecord.participant_ref == ref,
        ))
        if record is None:
            record = ConversationRecord(
                channel=OWNER_CHANNEL,
                participant_ref=ref,
                messages=[],
            )
            self.session.add(record)
            self.session.flush()
        return record, list(record.messages or [])

    def save(self, record: ConversationRecord, messages: list[dict]) -> None:
        record.messages = list(messages or [])[-MAX_HISTORY:]
        record.updated_at = utcnow()

    def daily_opening(self, record: ConversationRecord, messages: list[dict]) -> str:
        today = utcnow().date().isoformat()
        already_sent = any(
            item.get("kind") == "daily_briefing" and item.get("date") == today
            for item in messages
        )
        if already_sent:
            return "I'm here. What needs your attention?"
        briefing = build_daily_briefing(self.session)
        messages.append({
            "role": "finch",
            "content": briefing,
            "kind": "daily_briefing",
            "date": today,
            "created_at": utcnow().isoformat(),
        })
        self.save(record, messages)
        return briefing


def build_daily_briefing(session: Session) -> str:
    """Build a conservative briefing from persisted records only."""
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    active_workspaces = int(session.scalar(
        select(func.count()).select_from(Workspace).where(
            Workspace.cancelled_at.is_(None),
            Workspace.status.not_in(("pending", "cancelled")),
        )
    ) or 0)
    active_licences = int(session.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == "active"
        )
    ) or 0)
    annual_revenue_pence = int(session.scalar(
        select(func.coalesce(func.sum(Subscription.amount_pence), 0)).where(
            Subscription.status == "active"
        )
    ) or 0)
    sent_today = int(session.scalar(
        select(func.count()).select_from(OutreachActivity).where(
            OutreachActivity.sent_at >= day_start
        )
    ) or 0)
    replies_today = int(session.scalar(
        select(func.count()).select_from(OutreachActivity).where(
            OutreachActivity.replied_at >= day_start
        )
    ) or 0)
    important_alerts = int(session.scalar(
        select(func.count()).select_from(CustomerAlert).where(
            CustomerAlert.important.is_(True),
            CustomerAlert.acknowledged_at.is_(None),
        )
    ) or 0)
    stopped_requests = int(session.scalar(
        select(func.count()).select_from(MonitoringRequest).where(
            MonitoringRequest.requested_at >= now - timedelta(hours=24),
            MonitoringRequest.stopped_reason.is_not(None),
        )
    ) or 0)
    failed_payments = int(session.scalar(
        select(func.count()).select_from(PaymentEvent).where(
            PaymentEvent.processed_at >= now - timedelta(hours=24),
            PaymentEvent.event_type.in_(("payment_failed", "invoice.payment_failed")),
        )
    ) or 0)
    pause = session.get(SystemControl, "harold_paused")
    paused = bool(pause and pause.enabled)

    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    lines = [
        f"{greeting}. Here is the verified Aegis picture.",
        f"Licences: {active_licences} active across {active_workspaces} customer workspace(s).",
        f"Recorded annual licence revenue: £{annual_revenue_pence / 100:,.2f}.",
        f"Outreach today: {sent_today} sent, {replies_today} replies.",
    ]
    exceptions = []
    if paused:
        exceptions.append("Harold is globally paused.")
    if important_alerts:
        exceptions.append(f"{important_alerts} important customer alert(s) await acknowledgement.")
    if stopped_requests:
        exceptions.append(f"{stopped_requests} monitoring request(s) stopped safely in the last 24 hours.")
    if failed_payments:
        exceptions.append(f"{failed_payments} payment failure event(s) were recorded in the last 24 hours.")
    if exceptions:
        lines.append("Needs attention: " + " ".join(exceptions))
    else:
        lines.append("No recorded exception currently needs your attention.")
    lines.append("These figures come from stored records; zero means no qualifying record exists.")
    return "\n".join(lines)
