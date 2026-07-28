"""Durable, sourced and public-signal-only prospect pipeline."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from core.accounts import normalize_domain
from core.models import LeadCandidate, utcnow
from sales.lead_gen import ELIGIBLE_COMPANY_TYPES, LeadGenerator


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")


class LeadPipeline:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _dict(lead: LeadCandidate) -> dict:
        emails = []
        if lead.contact_email:
            emails.append({
                "email": lead.contact_email,
                "source": lead.contact_source,
                "confidence": 1.0 if lead.email_validated else 0.7,
                "kind": "sourced",
            })
        return {
            "id": lead.id,
            "domain": lead.domain,
            "company": lead.company_name,
            "business_type": lead.business_type,
            "contact_source": lead.contact_source or "",
            "stage": lead.stage,
            "emails": emails,
            "selected_email": lead.contact_email or "",
            "hook": (
                f"Your public update, “{lead.signal_title}”, indicates that "
                "external security visibility is relevant to your organisation."
            ),
            "signals": [{
                "source_url": lead.signal_url,
                "title": lead.signal_title,
                "excerpt": lead.signal_excerpt,
            }],
            "findings": [],
            "draft_message_id": lead.draft_message_id,
            "sent_message_id": lead.sent_message_id,
            "created_at": lead.created_at.isoformat(),
            "updated_at": lead.updated_at.isoformat(),
        }

    def list_leads(self, limit: int = 50) -> list[dict]:
        try:
            with self.session_factory() as db:
                leads = db.scalars(
                    select(LeadCandidate).order_by(LeadCandidate.updated_at.desc()).limit(limit)
                ).all()
                return [self._dict(lead) for lead in leads]
        except SQLAlchemyError:
            return []

    def get(self, lead_id: str) -> dict | None:
        with self.session_factory() as db:
            lead = db.get(LeadCandidate, lead_id)
            return self._dict(lead) if lead else None

    def delete(self, lead_id: str) -> bool:
        with self.session_factory() as db:
            lead = db.get(LeadCandidate, lead_id)
            if lead is None:
                return False
            db.delete(lead)
            db.commit()
            return True

    def summary(self) -> dict:
        try:
            with self.session_factory() as db:
                total = int(db.scalar(select(func.count()).select_from(LeadCandidate)) or 0)
                ready = int(db.scalar(
                    select(func.count()).select_from(LeadCandidate).where(
                        LeadCandidate.stage == "ready"
                    )
                ) or 0)
                sent = int(db.scalar(
                    select(func.count()).select_from(LeadCandidate).where(
                        LeadCandidate.stage == "sent"
                    )
                ) or 0)
                with_emails = int(db.scalar(
                    select(func.count()).select_from(LeadCandidate).where(
                        LeadCandidate.contact_email.is_not(None)
                    )
                ) or 0)
                return {
                    "total": total,
                    "researched": total,
                    "with_emails": with_emails,
                    "ready": ready,
                    "sent": sent,
                }
        except SQLAlchemyError:
            return {"total": 0, "researched": 0, "with_emails": 0, "ready": 0, "sent": 0}

    def research(
        self,
        domain_or_url: str,
        company: str = "",
        *,
        source_url: str = "",
        signal_title: str = "",
        signal_excerpt: str = "",
        business_type: str = "",
        contact_email: str = "",
        contact_source: str = "",
    ) -> dict:
        try:
            domain = normalize_domain(domain_or_url)
        except ValueError:
            return {"ok": False, "error": "invalid_domain"}
        if business_type not in ELIGIBLE_COMPANY_TYPES:
            return {"ok": False, "error": "ineligible_business_type"}
        if not urlparse(source_url).hostname:
            return {"ok": False, "error": "public_signal_source_required"}
        if not LeadGenerator.qualifies_public_signal(signal_title, signal_excerpt):
            return {"ok": False, "error": "security_interest_signal_required"}
        email = contact_email.strip().lower()
        if email and (not EMAIL_RE.fullmatch(email) or not contact_source.strip()):
            return {"ok": False, "error": "sourced_contact_required"}
        with self.session_factory() as db:
            lead = db.scalar(select(LeadCandidate).where(LeadCandidate.domain == domain))
            if lead is None:
                lead = LeadCandidate(
                    company_name=company.strip() or domain,
                    domain=domain,
                    business_type=business_type,
                    signal_url=source_url,
                    signal_title=signal_title[:240],
                    signal_excerpt=signal_excerpt[:1000],
                )
                db.add(lead)
            else:
                lead.company_name = company.strip() or lead.company_name
                lead.business_type = business_type
                lead.signal_url = source_url
                lead.signal_title = signal_title[:240]
                lead.signal_excerpt = signal_excerpt[:1000]
            lead.contact_email = email or None
            lead.contact_source = contact_source.strip() or None
            lead.stage = "ready" if email else "sourced"
            lead.updated_at = utcnow()
            db.commit()
            db.refresh(lead)
            return {"ok": True, "lead": self._dict(lead)}

    def select_email(self, lead_id: str, email: str) -> dict | None:
        email = email.strip().lower()
        if not EMAIL_RE.fullmatch(email):
            return None
        with self.session_factory() as db:
            lead = db.get(LeadCandidate, lead_id)
            if lead is None or not lead.contact_source:
                return None
            lead.contact_email = email
            lead.stage = "ready"
            lead.updated_at = utcnow()
            db.commit()
            db.refresh(lead)
            return self._dict(lead)

    def mark_drafted(self, lead_id: str, message_id: str = "") -> dict | None:
        return self._mark(lead_id, "drafted", "draft_message_id", message_id)

    def mark_sent(self, lead_id: str, message_id: str = "") -> dict | None:
        return self._mark(lead_id, "sent", "sent_message_id", message_id)

    def _mark(self, lead_id: str, stage: str, field: str, message_id: str) -> dict | None:
        with self.session_factory() as db:
            lead = db.get(LeadCandidate, lead_id)
            if lead is None:
                return None
            lead.stage = stage
            setattr(lead, field, message_id)
            lead.updated_at = utcnow()
            db.commit()
            db.refresh(lead)
            return self._dict(lead)

    @staticmethod
    def best_hook(lead: dict) -> str:
        return (lead.get("hook") or "").strip()
