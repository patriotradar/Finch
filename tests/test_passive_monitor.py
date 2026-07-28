import pytest
from sqlalchemy import func, select

from asm.passive_monitor import PassiveMonitor, PublicResult
from core.database import Base, build_engine, build_session_factory
from core.models import (
    ApprovedAsset, AssetStatus, CustomerAlert, Evidence, MonitoringRequest, Observation,
    Workspace, WorkspaceUser,
)


@pytest.fixture
def seeded():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    with factory() as session:
        workspace = Workspace(company_name="Alpha Ltd")
        session.add(workspace)
        session.flush()
        user = WorkspaceUser(
            workspace_id=workspace.id,
            email="owner@alpha.example",
            password_hash="hash",
        )
        session.add(user)
        session.flush()
        approved = ApprovedAsset(
            workspace_id=workspace.id,
            value="alpha.example",
            authority_confirmed=True,
            authority_confirmed_by=user.id,
            status=AssetStatus.APPROVED,
        )
        unapproved = ApprovedAsset(
            workspace_id=workspace.id,
            value="unapproved.example",
            authority_confirmed=False,
            status=AssetStatus.APPROVED,
        )
        session.add_all([approved, unapproved])
        session.commit()
        return factory, workspace.id, approved.id, unapproved.id


def test_collects_only_passive_metadata_and_audits_every_request(seeded):
    factory, workspace_id, asset_id, _ = seeded
    collectors = [
        lambda domain: PublicResult("DNS", "GET", f"dns://{domain}", None, {"A": ["192.0.2.1"]}),
        lambda domain: PublicResult("HTTP headers", "HEAD", f"https://{domain}/", 200, {
            "headers": {"content-security-policy": "default-src 'self'"}
        }),
    ]
    with factory() as session:
        observations = PassiveMonitor(session, collectors).monitor_asset(workspace_id, asset_id)
        session.commit()
        assert len(observations) == 2
        assert session.scalar(select(func.count()).select_from(MonitoringRequest)) == 2
        assert session.scalar(select(func.count()).select_from(Evidence)) == 2
        assert all(item.inference is None for item in session.scalars(select(Observation)))
        assert all(item.it_verification_required for item in session.scalars(select(Observation)))


def test_unapproved_asset_is_never_contacted(seeded):
    factory, workspace_id, _, unapproved_id = seeded
    contacted = []
    with factory() as session:
        monitor = PassiveMonitor(session, [lambda domain: contacted.append(domain)])
        with pytest.raises(PermissionError, match="asset_not_approved"):
            monitor.monitor_asset(workspace_id, unapproved_id)
        assert contacted == []


def test_stops_immediately_on_rate_limit(seeded):
    factory, workspace_id, asset_id, _ = seeded
    contacted = []

    def rate_limited(domain):
        contacted.append("first")
        return PublicResult(
            "HTTP headers", "HEAD", f"https://{domain}/", 429, {},
            outcome="stopped", stopped_reason="http_429",
        )

    def should_not_run(domain):
        contacted.append("second")
        return PublicResult("DNS", "GET", f"dns://{domain}", None, {})

    with factory() as session:
        observations = PassiveMonitor(session, [rate_limited, should_not_run]).monitor_asset(
            workspace_id, asset_id
        )
        session.commit()
        assert observations == []
        assert contacted == ["first"]
        audit = session.scalar(select(MonitoringRequest))
        assert audit.stopped_reason == "http_429"


def test_blocks_unsafe_method_and_off_scope_target(seeded):
    factory, workspace_id, asset_id, _ = seeded
    with factory() as session:
        with pytest.raises(ValueError, match="unsafe_method_blocked"):
            PassiveMonitor(session, [
                lambda domain: PublicResult("HTTP", "POST", f"https://{domain}/", 200, {})
            ]).monitor_asset(workspace_id, asset_id)
        with pytest.raises(ValueError, match="target_outside_approved_asset"):
            PassiveMonitor(session, [
                lambda domain: PublicResult("HTTP", "GET", "https://evil-alpha.example/", 200, {})
            ]).monitor_asset(workspace_id, asset_id)


def test_metadata_change_creates_conservative_alert(seeded):
    factory, workspace_id, asset_id, _ = seeded
    first = lambda domain: PublicResult("TLS", "GET", f"tls://{domain}:443", None, {"notAfter": "A"})
    changed = lambda domain: PublicResult("TLS", "GET", f"tls://{domain}:443", None, {"notAfter": "B"})
    with factory() as session:
        PassiveMonitor(session, [first]).monitor_asset(workspace_id, asset_id)
        session.commit()
    with factory() as session:
        PassiveMonitor(session, [changed]).monitor_asset(workspace_id, asset_id)
        session.commit()
        alert = session.scalar(select(CustomerAlert))
        assert alert.important is True
        assert "does not prove" in alert.detail.lower()
