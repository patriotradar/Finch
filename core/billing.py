"""Provider-neutral annual licence state for Aegis."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Invoice, PaymentEvent, Subscription, Workspace, utcnow


ANNUAL_PRICE_PENCE = 99_500
GOAL_SALES = 4
GOAL_REVENUE_PENCE = ANNUAL_PRICE_PENCE * GOAL_SALES


class BillingService:
    def __init__(self, session: Session):
        self.session = session

    def create_pending(self, workspace_id: str, provider: str, reference: str) -> Subscription:
        existing = self.session.scalar(
            select(Subscription).where(Subscription.workspace_id == workspace_id)
        )
        if existing and existing.status == "active":
            return existing
        if existing:
            existing.provider = provider
            existing.provider_reference = reference
            existing.status = "pending"
            return existing
        subscription = Subscription(
            workspace_id=workspace_id,
            provider=provider,
            provider_reference=reference,
            status="pending",
            currency="GBP",
            amount_pence=ANNUAL_PRICE_PENCE,
        )
        self.session.add(subscription)
        return subscription

    def record_payment(
        self,
        provider: str,
        event_id: str,
        subscription_reference: str,
        invoice_reference: str,
        amount_pence: int,
        currency: str,
        receipt_url: str | None = None,
        payload: dict | None = None,
    ) -> Subscription:
        if self.session.scalar(
            select(PaymentEvent.id).where(PaymentEvent.provider_event_id == event_id)
        ):
            return self._by_reference(subscription_reference)
        if amount_pence != ANNUAL_PRICE_PENCE or currency.upper() != "GBP":
            raise ValueError("unexpected_payment_amount")
        subscription = self._by_reference(subscription_reference)
        now = utcnow()
        subscription.status = "active"
        subscription.starts_at = subscription.starts_at or now
        subscription.renews_at = now + timedelta(days=365)
        workspace = self.session.get(Workspace, subscription.workspace_id)
        if workspace:
            workspace.status = "active"
        self.session.add(PaymentEvent(
            provider=provider,
            provider_event_id=event_id,
            event_type="payment_confirmed",
            payload=payload or {},
        ))
        self.session.add(Invoice(
            workspace_id=subscription.workspace_id,
            subscription_id=subscription.id,
            provider_reference=invoice_reference,
            amount_pence=amount_pence,
            currency="GBP",
            status="paid",
            receipt_url=receipt_url,
            paid_at=now,
        ))
        return subscription

    def payment_failed(self, event_id: str, subscription_reference: str, payload=None) -> None:
        if self.session.scalar(
            select(PaymentEvent.id).where(PaymentEvent.provider_event_id == event_id)
        ):
            return
        subscription = self._by_reference(subscription_reference)
        subscription.status = "past_due"
        self.session.add(PaymentEvent(
            provider=subscription.provider,
            provider_event_id=event_id,
            event_type="payment_failed",
            payload=payload or {},
        ))

    def cancel(self, workspace_id: str) -> Subscription:
        subscription = self.session.scalar(
            select(Subscription).where(Subscription.workspace_id == workspace_id)
        )
        if subscription is None:
            raise LookupError("subscription_not_found")
        subscription.status = "cancel_at_period_end"
        subscription.cancelled_at = utcnow()
        return subscription

    def sales_goal(self) -> dict:
        sales = self.session.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.status == "paid")
        ) or 0
        revenue = self.session.scalar(
            select(func.coalesce(func.sum(Invoice.amount_pence), 0)).where(Invoice.status == "paid")
        ) or 0
        return {
            "sales": int(sales),
            "goal_sales": GOAL_SALES,
            "revenue_pence": int(revenue),
            "goal_revenue_pence": GOAL_REVENUE_PENCE,
            "guaranteed": False,
        }

    def _by_reference(self, reference: str) -> Subscription:
        subscription = self.session.scalar(
            select(Subscription).where(Subscription.provider_reference == reference)
        )
        if subscription is None:
            raise LookupError("subscription_not_found")
        return subscription
