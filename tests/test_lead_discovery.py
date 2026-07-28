import pytest

from sales.lead_gen import LeadGenerator


def test_public_security_signal_is_sourced_and_deduplicated(tmp_path):
    generator = LeadGenerator(str(tmp_path))
    payload = dict(
        company="Alpha Ltd",
        domain="alpha.example",
        source_url="https://alpha.example/news/security-certification",
        title="Alpha renews Cyber Essentials certification",
        excerpt="The company continues its cyber security programme.",
        business_type="limited_company",
    )
    assert generator.ingest_public_signal(**payload)
    assert generator.ingest_public_signal(**payload)
    ready = generator.get_ready_for_outreach()
    assert len(ready) == 1
    assert len(ready[0]["signals"]) == 1
    assert ready[0]["signals"][0]["source_url"] == payload["source_url"]


def test_unsuitable_or_unsourced_leads_are_rejected(tmp_path):
    generator = LeadGenerator(str(tmp_path))
    assert generator.ingest_public_signal(
        company="Person",
        domain="person.example",
        source_url="https://person.example/post",
        title="A normal update",
        excerpt="Nothing about information security.",
        business_type="sole_trader",
    ) is None
    with pytest.raises(RuntimeError, match="prohibited"):
        generator.scan_for_vulnerabilities("person.example")
