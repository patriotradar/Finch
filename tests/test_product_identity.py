from pathlib import Path

from messaging.mail_store import MailStore
from sales.outreach import OutreachEngine


ROOT = Path(__file__).resolve().parents[1]


def test_public_product_files_have_no_kane_or_voice_clone_identity():
    paths = [
        ROOT / "web" / "admin.html",
        ROOT / "web" / "chat.html",
        ROOT / "web" / "landing.html",
        ROOT / "web" / "account.html",
        ROOT / "sales" / "conversation.py",
        ROOT / "core" / "persona.py",
        ROOT / "config.yaml",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    assert "kane" not in combined
    assert "michael_emerson" not in combined
    assert "voice clone" not in combined


def test_repository_has_no_celebrity_voice_or_active_prospect_scanning():
    files = [
        ROOT / "voice" / "tts.py",
        ROOT / "sales" / "lead_gen.py",
        ROOT / "asm" / "scanner.py",
        ROOT / "asm" / "monitor.py",
        ROOT / "config.yaml",
        ROOT / "setup.sh",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    assert "michael emerson" not in combined
    assert "voice cloning" not in combined
    assert '["nuclei"' not in combined
    assert '["subfinder"' not in combined
    assert '["httpx"' not in combined


def test_landing_page_has_required_factual_sales_content():
    page = (ROOT / "web" / "landing.html").read_text(encoding="utf-8")
    required = [
        "£995", "12 months", "up to 5 users", "up to 25 approved assets",
        "sole trader", "not evidence of compromise", "Customer sign in",
        "/legal/privacy", "/legal/terms", "/legal/acceptable-use",
    ]
    for text in required:
        assert text in page
    forbidden = ["trusted by", "testimonial", "guaranteed protection", "penetration test included"]
    assert all(text not in page.lower() for text in forbidden)


def test_service_worker_does_not_cache_private_interfaces():
    worker = (ROOT / "web" / "sw.js").read_text(encoding="utf-8")
    install_cache = worker.split('self.addEventListener("fetch"', 1)[0]
    assert '"/admin"' not in install_cache
    assert '"/account"' not in install_cache
    assert 'startsWith("/api/")' in worker


def test_outreach_is_signed_by_harold_without_fake_finding(tmp_path):
    store = MailStore(str(tmp_path))
    draft = store.craft_outreach("Example Ltd", "contact@example.test")
    assert "Harold" in draft["body"]
    assert "technical co-founder" not in draft["body"].lower()
    assert "came up in our scans" not in draft["body"].lower()

    generic = OutreachEngine().craft_initial_email({"company": "Example Ltd"})
    assert "Harold" in generic["body"]
    assert "technical co-founder" not in generic["body"].lower()
    assert "came up in our scans" not in generic["body"].lower()
