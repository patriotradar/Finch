import pytest

from core.billing import ANNUAL_PRICE_PENCE, BillingService
from core.database import Base, build_engine, build_session_factory
from core.models import Invoice, Workspace


def setup():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def test_payment_activates_exact_annual_licence_and_is_idempotent():
    factory = setup()
    with factory() as session:
        workspace = Workspace(company_name="Alpha Ltd")
        session.add(workspace)
        session.flush()
        billing = BillingService(session)
        subscription = billing.create_pending(workspace.id, "provider", "sub-1")
        session.flush()
        billing.record_payment(
            "provider", "event-1", "sub-1", "invoice-1",
            ANNUAL_PRICE_PENCE, "GBP", "https://provider.example/receipt",
        )
        session.commit()
        assert subscription.status == "active"
        assert subscription.renews_at is not None
        assert session.get(Workspace, workspace.id).status == "active"
        billing.record_payment(
            "provider", "event-1", "sub-1", "invoice-duplicate",
            ANNUAL_PRICE_PENCE, "GBP",
        )
        session.commit()
        assert session.query(Invoice).count() == 1


def test_wrong_amount_cannot_activate_licence():
    factory = setup()
    with factory() as session:
        workspace = Workspace(company_name="Alpha Ltd")
        session.add(workspace)
        session.flush()
        billing = BillingService(session)
        billing.create_pending(workspace.id, "provider", "sub-1")
        session.flush()
        with pytest.raises(ValueError, match="unexpected_payment_amount"):
            billing.record_payment("provider", "event", "sub-1", "invoice", 1, "GBP")


def test_sales_goal_is_a_target_not_a_guarantee():
    factory = setup()
    with factory() as session:
        goal = BillingService(session).sales_goal()
        assert goal["goal_sales"] == 4
        assert goal["goal_revenue_pence"] == ANNUAL_PRICE_PENCE * 4
        assert goal["guaranteed"] is False

