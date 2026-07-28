from datetime import datetime, timedelta, timezone

from core.database import Base, build_engine, build_session_factory
from core.models import (
    ApprovedAsset, AssetStatus, Evidence, Observation, ObservationStatus,
    Workspace, WorkspaceUser,
)
from asm.reporting import ReportService


def test_monthly_report_separates_evidence_observation_and_inference():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = build_session_factory(engine)
    now = datetime.now(timezone.utc)
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
        observation = Observation(
            workspace_id=workspace.id,
            asset_id=asset.id,
            status=ObservationStatus.POTENTIAL_INDICATOR,
            title="Header changed",
            observation="The public response header changed.",
            inference="The configuration may have changed.",
            severity="low",
            confidence="medium",
            source="https://alpha.example/",
            detected_at=now,
            false_positive_guidance="Confirm with the deployment owner.",
            missing_evidence="No internal configuration was inspected.",
            it_verification_required=True,
        )
        session.add(observation)
        session.flush()
        session.add(Evidence(
            workspace_id=workspace.id,
            observation_id=observation.id,
            source_url="https://alpha.example/",
            content_hash="a" * 64,
            metadata_json={"header": "value"},
        ))
        session.flush()
        report = ReportService(session).generate_monthly(
            workspace.id, now - timedelta(days=1), now + timedelta(days=1)
        )
        session.commit()
        item = report.web_payload["items"][0]
        assert item["observation"] == "The public response header changed."
        assert item["inference"] == "The configuration may have changed."
        assert item["exploitation_claimed"] is False
        assert item["it_verification_required"] is True
        assert item["evidence"][0]["content_hash"] == "a" * 64
