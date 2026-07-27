"""
Dynamic pricing engine — Finch autonomously determines what each 
prospect should pay based on their risk profile, company data,
and what the market will bear.

No client sees the same price. Prices are computed from:
- Company size (employees, revenue)
- Asset count (what we're actually monitoring)
- Industry risk level
- Breach history
- Budget signals from outreach
"""

import math
import json
import os

# Industry risk multipliers — based on breach frequency and regulatory pressure
INDUSTRY_RISK = {
    "healthcare": 1.4,
    "finance": 1.5,
    "banking": 1.5,
    "insurance": 1.4,
    "legal": 1.3,
    "government": 1.2,
    "defense": 1.6,
    "education": 1.1,
    "retail": 1.0,
    "ecommerce": 1.1,
    "technology": 1.0,
    "saas": 1.0,
    "manufacturing": 0.9,
    "construction": 0.8,
    "nonprofit": 0.7,
    "media": 0.8,
    "entertainment": 0.8,
    "hospitality": 0.8,
    "real_estate": 0.8,
    "agriculture": 0.7,
    "energy": 1.3,
    "telecom": 1.2,
    "transportation": 1.0,
    "unknown": 1.0,
}


class PricingEngine:
    """
    Determines optimal pricing for each prospect.
    
    Formula:
    price = base_price * company_size_mult * asset_mult * industry_mult * risk_mult
    
    With constraints:
    - Floor: $199/mo (minimum viable)
    - Ceiling: $4,999/mo (before enterprise)
    - Enterprise: custom quote above ceiling
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.base_price = self.config.get("base_monthly", 299)

    def calculate(self, prospect):
        """
        prospect: dict with any of:
            - company_name
            - industry
            - employee_count
            - estimated_revenue
            - asset_count (external-facing assets we'd monitor)
            - known_breaches (boolean)
            - recent_breach (boolean, within 12 months)
            - tech_stack (list of technologies detected)
            - budget_signals (phrases like "enterprise", "startup", etc.)
        """
        industry = prospect.get("industry", "unknown").lower()
        employees = prospect.get("employee_count", 0)
        revenue = prospect.get("estimated_revenue", 0)
        assets = prospect.get("asset_count", 1)
        known_breaches = prospect.get("known_breaches", False)
        recent_breach = prospect.get("recent_breach", False)
        budget_signals = prospect.get("budget_signals", [])

        # Company size multiplier (0.5 to 3.0)
        size_mult = self._size_multiplier(employees, revenue)

        # Asset multiplier (0.5 to 3.0)
        asset_mult = self._asset_multiplier(assets)

        # Industry risk multiplier
        industry_mult = INDUSTRY_RISK.get(industry, 1.0)

        # Breach history modifier
        breach_mult = 1.0
        if known_breaches:
            breach_mult += 0.15
        if recent_breach:
            breach_mult += 0.25  # Recent breach = higher willingness to pay

        # Budget signal modifier
        budget_mult = self._budget_signal_multiplier(budget_signals)

        # Calculate final price
        raw_price = (
            self.base_price
            * size_mult
            * asset_mult
            * industry_mult
            * breach_mult
            * budget_mult
        )

        # Apply floor and ceiling
        floor = 199
        ceiling = 4999

        price = max(floor, min(ceiling, raw_price))
        price = round(price / 9) * 9  # Round to nearest $9 (pricing psychology)

        # Generate tier
        tier = self._determine_tier(price)

        return {
            "monthly_price": price,
            "annual_price": round(price * 12 * 0.8 / 9) * 9,  # 20% annual discount
            "annual_savings": round(price * 12 * 0.2),
            "tier": tier,
            "breakdown": {
                "base": self.base_price,
                "size_multiplier": round(size_mult, 2),
                "asset_multiplier": round(asset_mult, 2),
                "industry_multiplier": round(industry_mult, 2),
                "breach_multiplier": round(breach_mult, 2),
                "budget_multiplier": round(budget_mult, 2),
            },
            "confidence": self._confidence(prospect),
        }

    def _size_multiplier(self, employees, revenue):
        """Scale price based on company size. Small = lower, large = higher."""
        if employees <= 0 and revenue <= 0:
            return 1.0

        emp_score = min(employees / 100, 3.0) if employees > 0 else 0
        rev_score = min(revenue / 10_000_000, 3.0) if revenue > 0 else 0

        score = max(emp_score, rev_score) if (emp_score or rev_score) else 1.0

        if score < 0.3:
            return 0.5  # Tiny startup / solo founder
        elif score < 0.6:
            return 0.7
        elif score < 1.0:
            return 0.85
        elif score < 1.8:
            return 1.15
        elif score < 2.5:
            return 1.5
        else:
            return 2.0  # Large enterprise

    def _asset_multiplier(self, assets):
        """More assets = more work = higher price. But sublinear scaling."""
        if assets <= 0:
            return 1.0
        return min(1.0 + math.log2(assets) * 0.3, 3.0)

    def _budget_signal_multiplier(self, signals):
        """Parse signals from outreach conversations to gauge budget."""
        if not signals:
            return 1.0

        signal_text = " ".join(signals).lower()

        high_budget = ["enterprise", "budget isn't an issue", "we have funding",
                       "series b", "series c", "fortune", "we need the best"]
        low_budget = ["startup", "bootstrapped", "tight budget", "early stage",
                      "pre-revenue", "seed", "cost-sensitive", "cheapest"]

        high_count = sum(1 for s in high_budget if s in signal_text)
        low_count = sum(1 for s in low_budget if s in signal_text)

        if high_count > low_count:
            return 1.2 + (high_count * 0.1)
        elif low_count > high_count:
            return max(0.7, 1.0 - (low_count * 0.15))
        return 1.0

    def _determine_tier(self, price):
        if price <= 299:
            return "Starter"
        elif price <= 799:
            return "Professional"
        elif price <= 1999:
            return "Business"
        else:
            return "Enterprise"

    def _confidence(self, prospect):
        """How confident is this price? Based on data completeness."""
        fields = ["industry", "employee_count", "asset_count", "estimated_revenue"]
        filled = sum(1 for f in fields if prospect.get(f))
        return min(filled / len(fields), 1.0)

    def explain_pricing(self, prospect, pricing_result, audience="client"):
        """Generate a natural-language explanation of the pricing."""
        name = prospect.get("company_name", "your organization")
        price = pricing_result["monthly_price"]
        tier = pricing_result["tier"]
        confidence = pricing_result["confidence"]

        explanations = {
            "client": (
                f"For {name}, I recommend our {tier} plan at ${price}/month. "
                f"I arrived at this number by looking at the size of your digital footprint, "
                f"your industry's risk profile, and the amount of ongoing monitoring you'd need. "
                f"It covers continuous scanning of your external assets, immediate alerts "
                f"when something changes or becomes vulnerable, and my analysis of what matters "
                f"and what doesn't."
            ),
            "executive": (
                f"{name} — {tier} tier, ${price}/month. "
                f"Priced against the cost of a single breach in your sector "
                f"(average: $4.45M). This is insurance, not overhead."
            ),
        }

        if confidence < 0.5:
            disclaimer = (
                "\n\nI should note — I'm working with limited information. "
                "This price might shift once I learn more about your infrastructure."
            )
            return explanations.get(audience, explanations["client"]) + disclaimer

        return explanations.get(audience, explanations["client"])

    def negotiate(self, prospect, pricing_result, counter_offer=None, objection=None):
        """Finch handles pricing objections autonomously."""
        price = pricing_result["monthly_price"]
        floor = int(price * 0.75)  # Max 25% discount
        annual = pricing_result["annual_price"]

        if objection:
            objection = objection.lower()

            if any(w in objection for w in ["too expensive", "can't afford", "out of budget"]):
                discounted = max(floor, round(price * 0.85 / 9) * 9)
                return {
                    "response": (
                        f"I understand. I can bring this down to ${discounted}/month "
                        f"if we commit to a scope that focuses on your most critical assets. "
                        f"Alternatively, the annual plan saves you ${pricing_result['annual_savings']}/year."
                    ),
                    "new_price": discounted,
                    "annual_option": annual,
                }

            if any(w in objection for w in ["competitor", "cheaper elsewhere"]):
                return {
                    "response": (
                        f"I'd rather you had the right protection than the cheapest one. "
                        f"Send me what they're offering and I'll tell you honestly if it's "
                        f"comparable — most aren't. If it is, I'll match it."
                    ),
                    "offer_to_match": True,
                }

            if any(w in objection for w in ["free trial", "try before"]):
                return {
                    "response": (
                        f"Fair. Let's do a 14-day monitoring period — no charge, full access. "
                        f"At the end of it, I'll show you everything I found. If it's not worth "
                        f"${price}/month, we part ways. If it is, you'll know exactly why."
                    ),
                    "trial_offer": 14,
                }

        if counter_offer:
            if counter_offer >= floor:
                return {
                    "response": f"Done. ${counter_offer}/month. I'll set it up.",
                    "accepted": True,
                    "new_price": counter_offer,
                }
            else:
                return {
                    "response": (
                        f"I can't go below ${floor}/month without cutting corners on what we monitor — "
                        f"and I won't do that. At ${floor}, I can still deliver real protection. "
                        f"Can we work with that?"
                    ),
                    "floor": floor,
                }

        return {"response": "What's your concern with the pricing? I'm happy to discuss it."}
