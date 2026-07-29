from messaging.account_mailer import AccountMailer


def test_account_links_are_not_created_without_public_base_url(monkeypatch):
    monkeypatch.delenv("AEGIS_PUBLIC_URL", raising=False)
    monkeypatch.delenv("FINCH_WEB_CHAT_URL", raising=False)
    monkeypatch.delenv("VERCEL_PROJECT_PRODUCTION_URL", raising=False)
    monkeypatch.delenv("VERCEL_URL", raising=False)
    assert AccountMailer().send_verification("owner@example.test", "token") is False
    assert AccountMailer().send_password_reset("owner@example.test", "token") is False


def test_verification_email_uses_one_time_account_link(monkeypatch):
    monkeypatch.setenv("AEGIS_PUBLIC_URL", "https://aegis.example")
    captured = {}
    mailer = AccountMailer()
    monkeypatch.setattr(
        mailer,
        "_send",
        lambda recipient, subject, body: captured.update(
            recipient=recipient, subject=subject, body=body
        ) or True,
    )
    assert mailer.send_verification("owner@example.test", "one-time-token")
    assert captured["recipient"] == "owner@example.test"
    assert "https://aegis.example/account?verify=one-time-token" in captured["body"]
    assert "24 hours" in captured["body"]


def test_verification_email_uses_vercel_url_fallback(monkeypatch):
    monkeypatch.delenv("AEGIS_PUBLIC_URL", raising=False)
    monkeypatch.delenv("FINCH_WEB_CHAT_URL", raising=False)
    monkeypatch.setenv("VERCEL_URL", "aegis-preview.vercel.app")
    captured = {}
    mailer = AccountMailer()
    monkeypatch.setattr(
        mailer,
        "_send",
        lambda recipient, subject, body: captured.update(body=body) or True,
    )

    assert mailer.send_verification("owner@example.test", "preview-token")
    assert "https://aegis-preview.vercel.app/account?verify=preview-token" in captured["body"]
