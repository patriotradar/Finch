from core.database import Base, build_engine, build_session_factory
from sales.pipeline import LeadPipeline


def pipeline():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    return LeadPipeline(build_session_factory(engine))


def test_pipeline_requires_eligible_company_and_public_security_signal():
    leads = pipeline()
    rejected = leads.research(
        "alpha.example",
        company="Alpha",
        source_url="https://alpha.example/news",
        signal_title="General company news",
        signal_excerpt="We opened a new office.",
        business_type="limited_company",
    )
    assert rejected["error"] == "security_interest_signal_required"

    accepted = leads.research(
        "alpha.example",
        company="Alpha Ltd",
        source_url="https://alpha.example/news/cyber-essentials",
        signal_title="Cyber Essentials renewed",
        signal_excerpt="Our cyber security programme has achieved certification.",
        business_type="limited_company",
        contact_email="security@alpha.example",
        contact_source="https://alpha.example/contact",
    )
    assert accepted["ok"]
    assert accepted["lead"]["findings"] == []
    assert accepted["lead"]["signals"][0]["source_url"].startswith("https://")
    assert leads.summary()["with_emails"] == 1


def test_pipeline_persists_stage_changes():
    leads = pipeline()
    lead = leads.research(
        "beta.example",
        company="Beta Ltd",
        source_url="https://beta.example/careers/security",
        signal_title="Hiring an information security manager",
        signal_excerpt="Information security is a priority for our growing team.",
        business_type="limited_company",
        contact_email="hello@beta.example",
        contact_source="https://beta.example/contact",
    )["lead"]
    leads.mark_drafted(lead["id"], "message-1")
    leads.mark_sent(lead["id"], "message-1")
    assert leads.get(lead["id"])["stage"] == "sent"
