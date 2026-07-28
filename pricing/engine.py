"""Approved Aegis licence pricing.

Pricing is intentionally deterministic. Harold may explain the approved offer,
but cannot infer willingness to pay, invent discounts, or negotiate a different
price from sensitive company or security information.
"""

from __future__ import annotations

from typing import Any


class PricingEngine:
    """Return the configured annual licence without autonomous negotiation."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.founding_annual = int(self.config.get("founding_annual_gbp", 995))
        self.standard_annual = int(self.config.get("standard_annual_gbp", 3000))
        self.founding_slots = int(self.config.get("founding_slots", 8))

    def calculate(self, prospect: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the approved founding offer.

        Availability is confirmed by the billing/licence system rather than
        guessed here. The standard price is included for transparent display.
        """
        annual = self.founding_annual
        return {
            "currency": "GBP",
            "billing_period": "annual",
            "annual_price": annual,
            "monthly_price": round(annual / 12, 2),  # compatibility/display only
            "tier": "Founding licence",
            "founding_slots": self.founding_slots,
            "standard_annual_price": self.standard_annual,
            "allowances": {
                "authorised_users": 5,
                "approved_assets": 25,
                "term_months": 12,
            },
            "confidence": 1.0,
        }

    def calculate_price(self, company=None, scan_data=None) -> dict[str, Any]:
        """Compatibility wrapper for the existing conversation flow."""
        result = self.calculate({"company_name": company} if company else {})
        return {
            **result,
            "annual": result["annual_price"],
            "monthly": result["monthly_price"],
        }

    def explain_pricing(self, prospect, pricing_result=None, audience="client") -> str:
        result = pricing_result or self.calculate(prospect)
        return (
            f"The Aegis founding licence is £{result['annual_price']:,} for 12 months. "
            "It includes up to five authorised users and 25 customer-approved assets. "
            "There are no hidden monthly charges, and monitoring begins only after "
            "the customer confirms its authority for each asset."
        )

    def negotiate(self, prospect, pricing_result=None, counter_offer=None, objection=None):
        """Decline autonomous discounts and repeat the approved commercial terms."""
        result = pricing_result or self.calculate(prospect)
        return {
            "response": self.explain_pricing(prospect, result),
            "new_price": result["annual_price"],
            "currency": "GBP",
            "discount_applied": False,
            "requires_owner_approval_for_change": True,
        }
