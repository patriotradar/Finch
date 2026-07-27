"""
Attack Surface Scanner — Finch's eyes on the outside world.
Continuously discovers and monitors external assets.
"""

import subprocess
import json
import os
import hashlib
from datetime import datetime


class AttackSurfaceScanner:
    def __init__(self, config=None):
        self.config = config or {}
        self.data_dir = "./data/clients/"
        os.makedirs(self.data_dir, exist_ok=True)

    def discover_assets(self, domain):
        """Discover subdomains and external assets for a domain."""
        assets = {"domain": domain, "subdomains": [], "live_hosts": [], "timestamp": datetime.now().isoformat()}

        # Subfinder
        try:
            r = subprocess.run(["subfinder", "-d", domain, "-silent"], capture_output=True, text=True, timeout=120)
            assets["subdomains"] = [s.strip() for s in r.stdout.strip().split("\n") if s.strip()]
        except Exception:
            pass

        # Combine domain + subdomains for httpx
        targets = [domain] + assets["subdomains"]
        if targets:
            targets_file = f"/tmp/finch_targets_{hashlib.md5(domain.encode()).hexdigest()[:8]}.txt"
            with open(targets_file, "w") as f:
                f.write("\n".join(targets))

            try:
                r = subprocess.run(
                    ["httpx", "-l", targets_file, "-silent", "-status-code", "-title", "-tech-detect", "-json"],
                    capture_output=True, text=True, timeout=180
                )
                for line in r.stdout.strip().split("\n"):
                    if line.strip():
                        try:
                            assets["live_hosts"].append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass
            finally:
                os.remove(targets_file) if os.path.exists(targets_file) else None

        return assets

    def scan_vulnerabilities(self, domain):
        """Run nuclei on discovered assets for a domain."""
        findings = []
        try:
            r = subprocess.run(
                ["nuclei", "-u", f"https://{domain}", "-severity", "critical,high,medium",
                 "-silent", "-json", "-timeout", "5"],
                capture_output=True, text=True, timeout=180
            )
            for line in r.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        findings.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        return findings

    def generate_report(self, domain, assets=None, findings=None):
        """Generate a plain-language report for a client."""
        if assets is None:
            assets = self.discover_assets(domain)
        if findings is None:
            findings = self.scan_vulnerabilities(domain)

        live_count = len(assets.get("live_hosts", []))
        vuln_count = len(findings)
        critical_count = sum(1 for f in findings if f.get("info", {}).get("severity", "").lower() == "critical")
        high_count = sum(1 for f in findings if f.get("info", {}).get("severity", "").lower() == "high")

        report = {
            "domain": domain,
            "scan_date": datetime.now().isoformat(),
            "summary": {
                "live_assets": live_count,
                "total_vulnerabilities": vuln_count,
                "critical": critical_count,
                "high": high_count,
            },
            "assets": {
                "subdomains": assets.get("subdomains", []),
                "live_hosts": assets.get("live_hosts", []),
            },
            "findings": findings,
        }

        return report
