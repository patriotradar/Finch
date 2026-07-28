from datetime import datetime, timezone

from core.controls import ControlService
from core.database import Base, build_engine, build_session_factory
from core.models import OutreachActivity
from sales.outreach_policy import OptOutTokens, OutreachPolicy
from sales.outreach import OutreachEngine


def setup():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def test_eligibility_suppression_reply_and_duplicate_gates():
    factory = setup()
    with factory() as session:
        policy = OutreachPolicy(session)
        args = ("Alpha Ltd", "cto@alpha.example", "company website", "limited_company")
        assert policy.can_contact(*args) == (True, "allowed")
        policy.record_sent(*args[:3], message_type="initial")
        session.flush()
        assert policy.can_contact(*args) == (False, "duplicate_contact")
        policy.record_reply(args[1])
        assert policy.can_contact(*args, followup=True) == (False, "reply_received")
        policy.suppress("other@alpha.example", "opt_out", "one_click")
        assert policy.can_contact(
            "Alpha Ltd", "other@alpha.example", "company website", "limited_company"
        ) == (False, "suppressed")


def test_sole_trader_requires_consent():
    factory = setup()
    with factory() as session:
        policy = OutreachPolicy(session)
        assert policy.can_contact(
            "Example", "owner@example.test", "website", "sole_trader"
        ) == (False, "business_type_not_eligible")
        assert policy.can_contact(
            "Example", "owner@example.test", "consent form", "sole_trader", consent=True
        ) == (True, "allowed")


def test_daily_limit_and_followup_limit():
    factory = setup()
    with factory() as session:
        policy = OutreachPolicy(session)
        now = datetime.now(timezone.utc)
        for number in range(20):
            session.add(OutreachActivity(
                company_name=f"Company {number}",
                contact_email=f"person{number}@example.test",
                contact_source="public directory",
                message_type="initial",
                status="sent",
                sent_at=now,
            ))
        session.flush()
        assert policy.can_contact(
            "Next Ltd", "next@example.test", "website", "limited_company", now=now
        ) == (False, "daily_limit_reached")


def test_bounce_rate_pauses_all_harold_activity():
    factory = setup()
    with factory() as session:
        policy = OutreachPolicy(session)
        activity = policy.record_sent(
            "Alpha Ltd", "cto@alpha.example", "website", "initial"
        )
        session.flush()
        assert policy.record_hard_bounce(activity.id) is True
        assert ControlService(session).is_paused() is True
        assert policy.can_contact(
            "Beta Ltd", "cto@beta.example", "website", "limited_company"
        ) == (False, "harold_paused")


def test_one_click_optout_token_is_signed_and_permanent():
    factory = setup()
    tokens = OptOutTokens("a-secure-optout-secret-value")
    token = tokens.issue("Person@Example.test")
    assert tokens.validate(token) == "person@example.test"
    with factory() as session:
        OutreachPolicy(session).suppress(tokens.validate(token), "opt_out", "one_click")
        session.commit()
        assert OutreachPolicy(session).can_contact(
            "Example Ltd", "person@example.test", "website", "limited_company"
        ) == (False, "suppressed")


def test_legacy_autonomous_sender_fails_closed_without_policy():
    actions = OutreachEngine().process_outreach_queue([{
        "company": "Alpha Ltd",
        "contact_email": "cto@alpha.example",
    }])
    assert actions == ["Blocked: outreach policy is not configured"]
    assert OutreachEngine().send_email(
        "cto@alpha.example", {"subject": "Test", "body": "Test"}
    ) is False
