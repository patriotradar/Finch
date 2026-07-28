import hashlib

import pytest
from sqlalchemy import select

from core.accounts import AccountService
from core.database import Base, build_engine, build_session_factory
from core.models import ApprovedAsset, AssetStatus, CustomerSession, WorkspaceUser


PASSWORD = "SecureAccount42"


@pytest.fixture
def factory():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    return build_session_factory(engine)


def registered(factory):
    with factory() as session:
        service = AccountService(session)
        user, verification = service.register("Alpha Ltd", "OWNER@Example.test", PASSWORD)
        session.commit()
        return user.id, user.workspace_id, verification


def test_registration_verification_login_and_logout(factory):
    user_id, workspace_id, verification = registered(factory)
    with factory() as session:
        service = AccountService(session)
        user = service.verify_email(verification)
        assert user.id == user_id
        session.commit()

    with factory() as session:
        service = AccountService(session)
        issued = service.login("owner@example.test", PASSWORD)
        session.commit()
        assert issued.workspace_id == workspace_id
        active = service.authenticate(issued.token)
        assert active.csrf_token == issued.csrf_token
        service.logout(issued.token)
        session.commit()
        with pytest.raises(PermissionError):
            service.authenticate(issued.token)


def test_unverified_account_cannot_login(factory):
    registered(factory)
    with factory() as session:
        with pytest.raises(PermissionError):
            AccountService(session).login("owner@example.test", PASSWORD)


def test_password_reset_revokes_existing_sessions(factory):
    _, _, verification = registered(factory)
    with factory() as session:
        service = AccountService(session)
        service.verify_email(verification)
        session.commit()
        issued = service.login("owner@example.test", PASSWORD)
        session.commit()
        reset = service.request_password_reset("owner@example.test")
        service.reset_password(reset, "ReplacementPassword84")
        session.commit()
        with pytest.raises(PermissionError):
            service.authenticate(issued.token)
        assert service.login("owner@example.test", "ReplacementPassword84")


def test_authority_confirmation_and_asset_removal(factory):
    user_id, workspace_id, _ = registered(factory)
    with factory() as session:
        service = AccountService(session)
        with pytest.raises(ValueError, match="authority_confirmation_required"):
            service.add_asset(workspace_id, user_id, "alpha.example", False)
        asset = service.add_asset(workspace_id, user_id, "https://www.alpha.example/path", True)
        session.commit()
        assert asset.value == "alpha.example"
        assert asset.authority_confirmed_at is not None
        service.remove_asset(workspace_id, asset.id)
        session.commit()
        assert session.get(ApprovedAsset, asset.id).status == AssetStatus.REMOVED


def test_five_user_limit_is_enforced(factory):
    _, workspace_id, _ = registered(factory)
    with factory() as session:
        service = AccountService(session)
        for number in range(2, 6):
            service.invite_user(
                workspace_id,
                f"user{number}@example.test",
                f"TemporaryPassword{number}",
            )
            session.flush()
        with pytest.raises(ValueError, match="user_limit_reached"):
            service.invite_user(workspace_id, "sixth@example.test", "TemporaryPassword6")


def test_agreement_records_exact_policy_hash(factory):
    user_id, workspace_id, _ = registered(factory)
    policy_hash = hashlib.sha256(b"public-information-policy-v1").hexdigest()
    with factory() as session:
        service = AccountService(session)
        record = service.accept_policy(
            workspace_id,
            user_id,
            "public-information-policy",
            "1.0",
            policy_hash,
        )
        session.commit()
        assert record.policy_text_hash == policy_hash

