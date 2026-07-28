"""Compatibility boundary for the retired active scanner."""


class AttackSurfaceScanner:
    """Fail closed so legacy callers cannot perform active discovery or scans.

    Production monitoring is implemented by ``asm.passive_monitor`` and requires
    a persisted, customer-approved asset.
    """

    def __init__(self, config=None):
        self.config = config or {}

    def discover_assets(self, domain):
        raise RuntimeError("active_asset_discovery_disabled")

    def scan_vulnerabilities(self, domain):
        raise RuntimeError("active_vulnerability_scanning_disabled")

    def generate_report(self, domain, assets=None, findings=None):
        raise RuntimeError("use_passive_monitoring_reporter")
