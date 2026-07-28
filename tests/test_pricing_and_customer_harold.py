from pricing.engine import PricingEngine
from sales.conversation import SalesConversation


def test_pricing_is_fixed_and_not_based_on_risk_or_budget():
    engine = PricingEngine()
    ordinary = engine.calculate({"company_name": "Example Ltd"})
    pressured = engine.calculate({
        "company_name": "Example Ltd",
        "recent_breach": True,
        "budget_signals": ["enterprise", "budget isn't an issue"],
    })
    assert ordinary["annual_price"] == 995
    assert pressured["annual_price"] == 995
    assert ordinary["currency"] == "GBP"
    assert ordinary["allowances"] == {
        "authorised_users": 5,
        "approved_assets": 25,
        "term_months": 12,
    }


def test_harold_discloses_software_identity():
    conversation = SalesConversation(pricing_engine=PricingEngine())
    state = conversation.start_conversation()
    reply, _ = conversation.handle_message(state, "Are you a human or an AI?")
    assert "software" in reply.lower()
    assert "not a human" in reply.lower()


def test_public_chat_does_not_claim_to_scan_supplied_domain():
    conversation = SalesConversation(pricing_engine=PricingEngine())
    state = conversation.start_conversation()
    _, state = conversation.handle_message(state, "I own example.com")
    reply, state = conversation.handle_message(state, "Please check it")
    assert "not inspected" in reply.lower() or "will not" in reply.lower()
    assert not state.get("_should_demo")


def test_harold_quotes_only_approved_annual_price():
    conversation = SalesConversation(pricing_engine=PricingEngine())
    state = conversation.start_conversation()
    state["stage"] = "pricing"
    reply, state = conversation.handle_message(state, "We have a very large budget. How much?")
    assert "£995" in reply
    # Direct questions are answered without forcing the funnel to advance.
    assert state["stage"] == "pricing"
