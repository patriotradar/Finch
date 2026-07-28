from core.database import Base, build_engine, build_session_factory
from core.models import (
    ConversationRecord, CustomerAlert, MonitoringRequest, OutreachActivity,
    PaymentEvent, Subscription, SystemControl, Workspace, utcnow,
)
from core.owner_assistant import OwnerConversationService, build_daily_briefing


def factory():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def test_owner_conversation_is_durable_and_daily_opening_is_not_repeated():
    sessions = factory()
    with sessions() as db:
        service = OwnerConversationService(db)
        record, history = service.load("phone")
        first = service.daily_opening(record, history)
        db.commit()
        assert "verified Aegis picture" in first

    with sessions() as db:
        service = OwnerConversationService(db)
        record, history = service.load("phone")
        assert any(item.get("kind") == "daily_briefing" for item in history)
        assert service.daily_opening(record, history) == "I'm here. What needs your attention?"
        history.append({"role": "user", "content": "Keep this decision."})
        service.save(record, history)
        db.commit()

    with sessions() as db:
        stored = db.query(ConversationRecord).one()
        assert stored.messages[-1]["content"] == "Keep this decision."


def test_briefing_uses_persisted_figures_and_surfaces_exceptions():
    sessions = factory()
    with sessions() as db:
        workspace = Workspace(company_name="Alpha Ltd", status="active")
        db.add(workspace)
        db.flush()
        db.add(Subscription(
            workspace_id=workspace.id,
            provider="test",
            provider_reference="licence-1",
            status="active",
            amount_pence=99500,
        ))
        db.add(OutreachActivity(
            company_name="Prospect Ltd",
            contact_email="hello@prospect.example",
            contact_source="public company website",
            message_type="initial",
            status="sent",
            sent_at=utcnow(),
        ))
        db.add(CustomerAlert(
            workspace_id=workspace.id,
            asset_id="asset-1",
            alert_type="change",
            title="Public metadata changed",
            detail="Verification required",
            important=True,
        ))
        db.add(MonitoringRequest(
            workspace_id=workspace.id,
            asset_id="asset-1",
            source_type="http_headers",
            method="HEAD",
            target="https://alpha.example/",
            outcome="stopped",
            stopped_reason="rate_limited",
        ))
        db.add(PaymentEvent(
            provider="test",
            provider_event_id="event-1",
            event_type="payment_failed",
            payload={},
        ))
        db.add(SystemControl(key="harold_paused", enabled=True))
        db.commit()

        briefing = build_daily_briefing(db)
        assert "Licences: 1 active across 1 customer workspace(s)." in briefing
        assert "£995.00" in briefing
        assert "Outreach today: 1 sent" in briefing
        assert "Harold is globally paused." in briefing
        assert "important customer alert" in briefing
        assert "monitoring request" in briefing
        assert "payment failure" in briefing
