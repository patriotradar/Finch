import json

from messaging.mail_store import MailStore


def test_plaintext_smtp_file_is_never_loaded(monkeypatch, tmp_path):
    (tmp_path / "smtp_config.json").write_text(
        json.dumps({"email": "old@example.test", "password": "plaintext-secret"}),
        encoding="utf-8",
    )
    monkeypatch.delenv("FINCH_EMAIL", raising=False)
    monkeypatch.delenv("FINCH_EMAIL_PASSWORD", raising=False)
    store = MailStore(str(tmp_path))
    credentials = store.smtp_credentials()
    assert credentials["email"] == ""
    assert credentials["password"] == ""
    assert credentials["source"] == "none"


def test_smtp_environment_names_remain_compatible(monkeypatch, tmp_path):
    monkeypatch.setenv("FINCH_EMAIL", "owner@example.test")
    monkeypatch.setenv("FINCH_EMAIL_PASSWORD", "app-password")
    monkeypatch.setenv("FINCH_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("FINCH_SMTP_PORT", "2525")
    credentials = MailStore(str(tmp_path)).smtp_credentials()
    assert credentials == {
        "email": "owner@example.test",
        "password": "app-password",
        "smtp_host": "smtp.example.test",
        "smtp_port": "2525",
        "source": "env",
    }

