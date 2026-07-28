from sqlalchemy import select

from asm.advisories import AdvisoryService
from core.database import Base, build_engine, build_session_factory
from core.models import (
    ApprovedAsset, AssetSnapshot, AssetStatus, MonitoringRequest, Workspace,
    WorkspaceUser,
)


def test_advisory_name_match_never_claims_affected_version():
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
        asset = ApprovedAsset(
            workspace_id=workspace.id,
            value="alpha.example",
            authority_confirmed=True,
            authority_confirmed_by=user.id,
            status=AssetStatus.APPROVED,
        )
        session.add(asset)
        session.flush()
        session.add(AssetSnapshot(
            workspace_id=workspace.id,
            asset_id=asset.id,
            source_type="HTTP headers",
            fingerprint="a" * 64,
            payload={"headers": {"server": "ExampleServer"}},
        ))
        session.flush()

        def feed(_):
            return 200, {"vulnerabilities": [{
                "cveID": "CVE-2099-0001",
                "vendorProject": "Example",
                "product": "ExampleServer",
            }]}

        matches = AdvisoryService(session, feed).match_public_advisories(
            workspace.id, asset.id
        )
        session.commit()
        assert len(matches) == 1
        assert matches[0].confidence == "low"
        assert "does not establish" in matches[0].inference
        assert matches[0].missing_evidence
        assert session.scalar(select(MonitoringRequest)).outcome == "completed"


def test_no_disclosed_technology_means_no_external_request():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    called = []
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
        asset = ApprovedAsset(
            workspace_id=workspace.id,
            value="alpha.example",
            authority_confirmed=True,
            authority_confirmed_by=user.id,
            status=AssetStatus.APPROVED,
        )
        session.add(asset)
        session.flush()
        matches = AdvisoryService(
            session, lambda url: called.append(url)
        ).match_public_advisories(workspace.id, asset.id)
        assert matches == []
        assert called == []
