"""Compatibility wrapper for durable passive monitoring."""

from asm.passive_monitor import PassiveMonitor


class ContinuousMonitor:
    """Run monitoring only through the approved-asset database boundary."""

    def __init__(self, session, config=None, collectors=None):
        self.session = session
        self.config = config or {}
        self.monitor = PassiveMonitor(session, collectors=collectors)

    def take_snapshot(self, workspace_id: str, asset_id: str):
        return self.monitor.monitor_asset(workspace_id, asset_id)

    def compare_snapshots(self, *args, **kwargs):
        raise RuntimeError("changes_are_recorded_in_asset_snapshots_and_customer_alerts")
