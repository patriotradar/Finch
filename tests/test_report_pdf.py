from asm.report_pdf import build_report_pdf
from asm.report_html import render_report_html


def sample_payload():
    return {
        "period_start": "2026-07-01T00:00:00+00:00",
        "period_end": "2026-08-01T00:00:00+00:00",
        "summary": {"observations": 1, "potential_indicators": 1},
        "limitations": [
            "Passive public-information monitoring only.",
            "No authentication or exploitation was performed.",
        ],
        "items": [{
            "affected_asset": "example.co.uk",
            "status": "potential_indicator",
            "severity_estimate": "low",
            "confidence": "medium",
            "observation": "The public TLS certificate issuer and expiry date changed.",
            "inference": "The certificate may have been renewed or replaced.",
            "source": "tls://example.co.uk:443",
            "detection_date": "2026-07-28T12:00:00+00:00",
            "false_positive_guidance": "Confirm the current certificate with IT.",
            "missing_evidence": "No internal certificate inventory was accessed.",
            "evidence": [{"content_hash": "a" * 64}],
        }],
    }


def test_report_pdf_is_valid_and_contains_substantial_content():
    pdf = build_report_pdf("Example Limited", sample_payload())
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 3_000
    assert b"Example Limited" in pdf or len(pdf) > 4_000


def test_web_report_escapes_customer_controlled_content():
    payload = sample_payload()
    payload["items"][0]["observation"] = "<script>alert(1)</script>"
    html = render_report_html("Example <Limited>", payload)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Example &lt;Limited&gt;" in html
    assert "Aegis has not claimed exploitation" in html
