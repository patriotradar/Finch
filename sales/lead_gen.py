"""Public-signal-only lead discovery for ethical Aegis outreach."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse


SECURITY_TERMS = {
    "cyber security", "cybersecurity", "information security", "data security",
    "security certification", "iso 27001", "cyber essentials", "security hiring",
    "security engineer", "security manager", "data protection",
}
ELIGIBLE_COMPANY_TYPES = {"limited_company", "plc", "corporate_body"}


class LeadGenerator:
    """Store sourced prospects based on public company communications.

    This component does not scan company systems, use Shodan, run vulnerability
    templates, authenticate, or infer that a business has a vulnerability.
    """

    def __init__(self, data_dir: str = "./data/leads/"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.leads_file = os.path.join(data_dir, "leads.json")
        self._load_leads()

    def _load_leads(self) -> None:
        if os.path.exists(self.leads_file):
            with open(self.leads_file, encoding="utf-8") as handle:
                self.leads = json.load(handle)
        else:
            self.leads = {}

    def _save_leads(self) -> None:
        with open(self.leads_file, "w", encoding="utf-8") as handle:
            json.dump(self.leads, handle, indent=2, default=str)

    @staticmethod
    def qualifies_public_signal(title: str, excerpt: str) -> bool:
        text = f"{title} {excerpt}".lower()
        return any(term in text for term in SECURITY_TERMS)

    def ingest_public_signal(
        self,
        *,
        company: str,
        domain: str,
        source_url: str,
        title: str,
        excerpt: str,
        business_type: str,
        published_at: str | None = None,
    ) -> dict | None:
        """Ingest a sourced public result; return None when it is unsuitable."""
        host = urlparse(source_url).hostname
        domain = (domain or "").strip().lower().removeprefix("www.")
        if (
            not company.strip()
            or not domain
            or not host
            or business_type not in ELIGIBLE_COMPANY_TYPES
            or not self.qualifies_public_signal(title, excerpt)
        ):
            return None
        lead_id = hashlib.sha256(f"{company.lower()}|{domain}".encode()).hexdigest()[:20]
        lead = self.leads.get(lead_id) or {
            "id": lead_id,
            "company": company.strip(),
            "domain": domain,
            "business_type": business_type,
            "stage": "sourced",
            "signals": [],
            "outreach": [],
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
        if not any(item.get("source_url") == source_url for item in lead["signals"]):
            lead["signals"].append({
                "source_url": source_url,
                "title": title[:240],
                "excerpt": excerpt[:500],
                "published_at": published_at,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            })
        self.leads[lead_id] = lead
        self._save_leads()
        return lead

    def get_ready_for_outreach(self) -> list[dict]:
        return [
            lead for lead in self.leads.values()
            if lead.get("stage") in {"sourced", "qualified"}
            and lead.get("signals")
        ]

    def get_stats(self) -> dict:
        stages: dict[str, int] = {}
        for lead in self.leads.values():
            stage = lead.get("stage", "unknown")
            stages[stage] = stages.get(stage, 0) + 1
        return {
            "total": len(self.leads),
            "stages": stages,
            "with_public_signals": sum(1 for lead in self.leads.values() if lead.get("signals")),
        }

    # Explicit fail-closed methods retained for older callers.
    def discover_from_shodan(self, *args, **kwargs):
        raise RuntimeError("prohibited_discovery_method")

    def scan_for_vulnerabilities(self, *args, **kwargs):
        raise RuntimeError("prohibited_scanning_method")

    def enrich_all(self):
        return []
