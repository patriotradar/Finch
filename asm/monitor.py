"""
Continuous monitoring — Finch watches client assets and alerts on changes.
"""

import json
import os
import hashlib
from datetime import datetime
from asm.scanner import AttackSurfaceScanner


class ContinuousMonitor:
    def __init__(self, config=None):
        self.config = config or {}
        self.scanner = AttackSurfaceScanner(config)
        self.snapshots_dir = "./data/clients/snapshots/"
        os.makedirs(self.snapshots_dir, exist_ok=True)

    def take_snapshot(self, domain):
        """Capture current state of a domain's attack surface."""
        assets = self.scanner.discover_assets(domain)
        findings = self.scanner.scan_vulnerabilities(domain)
        snapshot = {
            "domain": domain,
            "timestamp": datetime.now().isoformat(),
            "assets": assets,
            "findings": findings,
            "checksum": hashlib.md5(
                json.dumps([assets.get("live_hosts", []), findings], sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
        # Save snapshot
        snap_file = os.path.join(self.snapshots_dir, f"{domain}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(snap_file, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
        return snapshot

    def compare_snapshots(self, domain):
        """Compare latest two snapshots and report changes."""
        snaps = sorted(
            [f for f in os.listdir(self.snapshots_dir) if f.startswith(domain)],
            reverse=True,
        )
        if len(snaps) < 2:
            return {"new_findings": [], "resolved_findings": [], "new_assets": [], "message": "Not enough snapshots for comparison."}

        with open(os.path.join(self.snapshots_dir, snaps[0])) as f:
            current = json.load(f)
        with open(os.path.join(self.snapshots_dir, snaps[1])) as f:
            previous = json.load(f)

        changes = {"new_findings": [], "resolved_findings": [], "new_assets": [], "removed_assets": []}

        # Compare findings by name
        prev_names = {f.get("info", {}).get("name", ""): f for f in previous.get("findings", [])}
        curr_names = {f.get("info", {}).get("name", ""): f for f in current.get("findings", [])}

        for name, finding in curr_names.items():
            if name not in prev_names:
                changes["new_findings"].append(finding)

        for name in prev_names:
            if name not in curr_names:
                changes["resolved_findings"].append(prev_names[name])

        # Compare live hosts
        prev_hosts = {h.get("url", ""): h for h in previous.get("assets", {}).get("live_hosts", [])}
        curr_hosts = {h.get("url", ""): h for h in current.get("assets", {}).get("live_hosts", [])}

        for url in curr_hosts:
            if url not in prev_hosts:
                changes["new_assets"].append(url)

        for url in prev_hosts:
            if url not in curr_hosts:
                changes["removed_assets"].append(url)

        if any(changes.values()):
            changes["message"] = (
                f"{len(changes['new_findings'])} new findings, "
                f"{len(changes['resolved_findings'])} resolved, "
                f"{len(changes['new_assets'])} new assets, "
                f"{len(changes['removed_assets'])} removed."
            )
        else:
            changes["message"] = "No changes detected."

        return changes
