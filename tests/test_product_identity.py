from pathlib import Path

from messaging.mail_store import MailStore
from sales.outreach import OutreachEngine


ROOT = Path(__file__).resolve().parents[1]


def test_public_product_files_have_no_kane_or_voice_clone_identity():
    paths = [
        ROOT / "web" / "admin.html",
        ROOT / "web" / "chat.html",
        ROOT / "sales" / "conversation.py",
        ROOT / "core" / "persona.py",
        ROOT / "config.yaml",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    assert "kane" not in combined
    assert "michael_emerson" not in combined
    assert "voice clone" not in combined


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

