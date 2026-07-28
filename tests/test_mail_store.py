from messaging.mail_store import MailStore


def test_new_mail_store_contains_no_demo_businesses(tmp_path):
    store = MailStore(str(tmp_path))
    assert store.list_messages() == []
    assert store.summary() == {
        "inbox": 0,
        "outbox": 0,
        "drafts": 0,
        "sent": 0,
        "unread": 0,
        "total": 0,
    }

