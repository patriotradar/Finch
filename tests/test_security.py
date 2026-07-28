from core.security import LoginThrottle, OwnerAuth, hash_password, verify_password


def test_password_hash_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert encoded != "correct horse battery staple"
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)


def test_signed_session_expires_and_rejects_tampering():
    auth = OwnerAuth(
        legacy_password="private-password",
        session_secret="a-long-independent-session-secret",
        lifetime_seconds=60,
    )
    token, session = auth.issue(now=100)
    assert auth.validate(token, now=159) == session
    assert auth.validate(token, now=160) is None
    assert auth.validate(token + "tampered", now=120) is None


def test_login_throttle_blocks_after_failure_limit():
    throttle = LoginThrottle(attempts=2, window_seconds=60)
    assert throttle.allowed("client", now=100)
    throttle.failure("client", now=100)
    throttle.failure("client", now=101)
    assert not throttle.allowed("client", now=102)
    assert throttle.allowed("client", now=161)

