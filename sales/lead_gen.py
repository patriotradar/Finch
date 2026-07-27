"""
Lead generation engine — Finch autonomously finds companies
with vulnerable attack surfaces and enriches them for outreach.
"""

import json
import os
import subprocess
import re
import requests
from datetime import datetime


class LeadGenerator:
    """Finds companies that need attack surface monitoring."""

    def __init__(self, data_dir="./data/leads/"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.leads_file = os.path.join(data_dir, "leads.json")
        self._load_leads()

    def _load_leads(self):
        if os.path.exists(self.leads_file):
            with open(self.leads_file) as f:
                self.leads = json.load(f)
        else:
            self.leads = {}

    def _save_leads(self):
        with open(self.leads_file, "w") as f:
            json.dump(self.leads, f, indent=2, default=str)

    def discover_from_shodan(self, query, limit=20):
        """
        Discover companies with exposed services via Shodan.
        Requires SHODAN_API_KEY environment variable.
        """
        api_key = os.environ.get("SHODAN_API_KEY")
        if not api_key:
            print("[Finch] SHODAN_API_KEY not set. Skipping Shodan discovery.")
            return []

        leads = []
        try:
            resp = requests.get(
                f"https://api.shodan.io/shodan/host/search",
                params={"key": api_key, "query": query, "limit": limit}
            )
            for match in resp.json().get("matches", []):
                org = match.get("org", match.get("isp", "Unknown"))
                hostnames = match.get("hostnames", [])
                domain = hostnames[0] if hostnames else match.get("ip_str", "")

                lead = self._build_lead(
                    company=org,
                    domain=domain,
                    source="shodan",
                    raw_data=match,
                )
                leads.append(lead)
                self._add_lead(lead)
        except Exception as e:
            print(f"[Finch] Shodan error: {e}")

        self._save_leads()
        return leads

    def discover_from_builtwith(self, domain):
        """Enrich a lead with technology stack data from BuiltWith."""
        api_key = os.environ.get("BUILTWITH_API_KEY")
        if not api_key:
            return {}

        try:
            resp = requests.get(
                f"https://api.builtwith.com/free1/api.json",
                params={"KEY": api_key, "LOOKUP": domain}
            )
            return resp.json()
        except Exception:
            return {}

    def scan_for_vulnerabilities(self, domain):
        """
        Quick scan a domain to find obvious issues.
        This is the "proof" Finch sends in cold emails.
        """
        findings = []
        try:
            # Quick httpx probe
            result = subprocess.run(
                ["httpx", "-u", f"https://{domain}", "-silent", "-status-code", "-tech-detect"],
                capture_output=True, text=True, timeout=30
            )
            if result.stdout.strip():
                findings.append({
                    "tool": "httpx",
                    "result": result.stdout.strip(),
                })
        except Exception:
            pass

        try:
            # Quick nuclei scan for criticals only
            result = subprocess.run(
                ["nuclei", "-u", f"https://{domain}", "-severity", "critical,high",
                 "-silent", "-timeout", "5", "-retries", "0"],
                capture_output=True, text=True, timeout=60
            )
            if result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    findings.append({
                        "tool": "nuclei",
                        "finding": line.strip(),
                    })
        except Exception:
            pass

        return findings

    def _build_lead(self, company, domain, source, raw_data):
        return {
            "id": domain.lower().replace(".", "_"),
            "company": company,
            "domain": domain,
            "source": source,
            "discovered_at": datetime.now().isoformat(),
            "stage": "discovered",
            "enrichment": {},
            "vulnerabilities": [],
            "outreach": [],
            "raw_data": str(raw_data)[:500],
        }

    def _add_lead(self, lead):
        lead_id = lead["id"]
        if lead_id not in self.leads:
            self.leads[lead_id] = lead
            print(f"[Finch] New lead: {lead['company']} ({lead['domain']})")

    def enrich_all(self):
        """Enrich all undiscovered leads with tech stack and vuln data."""
        for lead_id, lead in self.leads.items():
            if lead["stage"] == "discovered":
                print(f"[Finch] Enriching: {lead['company']}...")

                # Scan for vulnerabilities
                vulns = self.scan_for_vulnerabilities(lead["domain"])
                if vulns:
                    lead["vulnerabilities"] = vulns
                    lead["stage"] = "enriched"

        self._save_leads()

    def get_ready_for_outreach(self):
        """Return leads that are enriched and ready for outreach."""
        return [
            lead for lead in self.leads.values()
            if lead["stage"] == "enriched" or lead["stage"] == "outreached"
        ]

    def get_stats(self):
        """Lead pipeline statistics for Finch's reports."""
        stages = {}
        for lead in self.leads.values():
            stage = lead.get("stage", "unknown")
            stages[stage] = stages.get(stage, 0) + 1

        return {
            "total": len(self.leads),
            "stages": stages,
            "with_vulnerabilities": sum(
                1 for l in self.leads.values() if l.get("vulnerabilities")
            ),
        }
