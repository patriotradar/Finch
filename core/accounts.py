"""Customer account and authorised-scope service."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import (
    AccountToken, AgreementAcceptance, ApprovedAsset, AssetStatus,
    CustomerSession, Role, Workspace, WorkspaceUser, utcnow,
)
from core.security import hash_password, verify_password


MAX_USERS = 5
MAX_ASSETS = 25
TOKEN_LIFETIME = timedelta(hours=24)
SESSION_LIFETIME = timedelta(hours=12)
DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 320 or "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError("invalid_email")
    return value


def normalize_domain(value: str) -> str:
    domain = (value or "").strip().lower().removeprefix("https://").removeprefix("http://")
    domain = domain.split("/", 1)[0].removeprefix("www.").rstrip(".")
    if not DOMAIN_RE.fullmatch(domain):
        raise ValueError("invalid_domain")
    return domain


def validate_password(password: str) -> None:
    if len(password or "") < 12:
        raise ValueError("password_too_short")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise ValueError("password_needs_mixed_case_and_number")


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    user_id: str
    workspace_id: str
    expires_at: object


class AccountService:
    def __init__(self, session: Session):
        self.session = session

    def register(self, company_name: str, email: str, password: str) -> tuple[WorkspaceUser, str]:
        company = (company_name or "").strip()
        if len(company) < 2 or len(company) > 200:
            raise ValueError("invalid_company_name")
        email = normalize_email(email)
        validate_password(password)
        if self.session.scalar(select(WorkspaceUser.id).where(WorkspaceUser.email == email)):
            raise ValueError("email_already_registered")
        workspace = Workspace(company_name=company, status="pending")
        self.session.add(workspace)
        self.session.flush()
        user = WorkspaceUser(
            workspace_id=workspace.id,
            email=email,
            password_hash=hash_password(password),
            role=Role.OWNER,
        )
        self.session.add(user)
        self.session.flush()
        raw_token = self._issue_account_token(user.id, "verify_email")
        return user, raw_token

    def verify_email(self, raw_token: str) -> WorkspaceUser:
        token = self._consume_account_token(raw_token, "verify_email")
        user = self.session.get(WorkspaceUser, token.user_id)
        if user is None:
            raise ValueError("invalid_token")
        user.email_verified_at = utcnow()
        workspace = self.session.get(Workspace, user.workspace_id)
        if workspace:
            workspace.status = "trial_pending_payment"
        return user

    def request_verification(self, email: str) -> str | None:
        email = normalize_email(email)
        user = self.session.scalar(select(WorkspaceUser).where(WorkspaceUser.email == email))
        if user is None or user.disabled_at is not None or user.email_verified_at is not None:
            return None
        return self._issue_account_token(user.id, "verify_email")

    def request_password_reset(self, email: str) -> str | None:
        email = normalize_email(email)
        user = self.session.scalar(select(WorkspaceUser).where(WorkspaceUser.email == email))
        if user is None or user.disabled_at is not None:
            return None
        return self._issue_account_token(user.id, "password_reset")

    def reset_password(self, raw_token: str, new_password: str) -> None:
        validate_password(new_password)
        token = self._consume_account_token(raw_token, "password_reset")
        user = self.session.get(WorkspaceUser, token.user_id)
        if user is None:
            raise ValueError("invalid_token")
        user.password_hash = hash_password(new_password)
        self.session.query(CustomerSession).filter(
            CustomerSession.user_id == user.id,
            CustomerSession.revoked_at.is_(None),
        ).update({"revoked_at": utcnow()})

    def login(self, email: str, password: str) -> IssuedSession:
        email = normalize_email(email)
        user = self.session.scalar(select(WorkspaceUser).where(WorkspaceUser.email == email))
        if (
            user is None
            or user.disabled_at is not None
            or user.email_verified_at is None
            or not verify_password(password, user.password_hash)
        ):
            raise PermissionError("invalid_credentials")
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expires = utcnow() + SESSION_LIFETIME
        self.session.add(CustomerSession(
            user_id=user.id,
            workspace_id=user.workspace_id,
            token_hash=token_hash(raw),
            csrf_token=csrf,
            expires_at=expires,
        ))
        return IssuedSession(raw, csrf, user.id, user.workspace_id, expires)

    def authenticate(self, raw_token: str) -> CustomerSession:
        session = self.session.scalar(
            select(CustomerSession).where(
                CustomerSession.token_hash == token_hash(raw_token or ""),
                CustomerSession.revoked_at.is_(None),
                CustomerSession.expires_at > utcnow(),
            )
        )
        if session is None:
            raise PermissionError("authentication_required")
        return session

    def logout(self, raw_token: str) -> None:
        active = self.authenticate(raw_token)
        active.revoked_at = utcnow()

    def invite_user(
        self, workspace_id: str, email: str, temporary_password: str | None = None
    ) -> tuple[WorkspaceUser, str]:
        count = self.session.scalar(
            select(func.count()).select_from(WorkspaceUser).where(
                WorkspaceUser.workspace_id == workspace_id,
                WorkspaceUser.disabled_at.is_(None),
            )
        )
        if int(count or 0) >= MAX_USERS:
            raise ValueError("user_limit_reached")
        email = normalize_email(email)
        if self.session.scalar(select(WorkspaceUser.id).where(WorkspaceUser.email == email)):
            raise ValueError("email_already_registered")
        temporary_password = temporary_password or secrets.token_urlsafe(32)
        user = WorkspaceUser(
            workspace_id=workspace_id,
            email=email,
            password_hash=hash_password(temporary_password),
            role=Role.MEMBER,
        )
        self.session.add(user)
        self.session.flush()
        return user, self._issue_account_token(user.id, "accept_invite")

    def accept_invite(self, raw_token: str, password: str) -> WorkspaceUser:
        validate_password(password)
        token = self._consume_account_token(raw_token, "accept_invite")
        user = self.session.get(WorkspaceUser, token.user_id)
        if user is None or user.disabled_at is not None:
            raise ValueError("invalid_or_expired_token")
        user.password_hash = hash_password(password)
        user.email_verified_at = utcnow()
        return user

    def disable_user(self, workspace_id: str, actor_user_id: str, user_id: str) -> None:
        if actor_user_id == user_id:
            raise ValueError("cannot_disable_self")
        user = self.session.scalar(select(WorkspaceUser).where(
            WorkspaceUser.id == user_id,
            WorkspaceUser.workspace_id == workspace_id,
            WorkspaceUser.disabled_at.is_(None),
        ))
        if user is None or user.role == Role.OWNER:
            raise LookupError("user_not_found")
        user.disabled_at = utcnow()
        self.session.query(CustomerSession).filter(
            CustomerSession.user_id == user.id,
            CustomerSession.revoked_at.is_(None),
        ).update({"revoked_at": utcnow()})

    def accept_policy(
        self, workspace_id: str, user_id: str, policy_name: str,
        policy_version: str, policy_text_hash: str,
    ) -> AgreementAcceptance:
        if not re.fullmatch(r"[a-f0-9]{64}", policy_text_hash or ""):
            raise ValueError("invalid_policy_hash")
        record = AgreementAcceptance(
            workspace_id=workspace_id,
            user_id=user_id,
            policy_name=policy_name,
            policy_version=policy_version,
            policy_text_hash=policy_text_hash,
        )
        self.session.add(record)
        return record

    def add_asset(self, workspace_id: str, user_id: str, value: str, authority_confirmed: bool) -> ApprovedAsset:
        if authority_confirmed is not True:
            raise ValueError("authority_confirmation_required")
        count = self.session.scalar(
            select(func.count()).select_from(ApprovedAsset).where(
                ApprovedAsset.workspace_id == workspace_id,
                ApprovedAsset.status == AssetStatus.APPROVED,
            )
        )
        if int(count or 0) >= MAX_ASSETS:
            raise ValueError("asset_limit_reached")
        asset = ApprovedAsset(
            workspace_id=workspace_id,
            value=normalize_domain(value),
            authority_confirmed=True,
            authority_confirmed_by=user_id,
            authority_confirmed_at=utcnow(),
            status=AssetStatus.APPROVED,
        )
        self.session.add(asset)
        return asset

    def remove_asset(self, workspace_id: str, asset_id: str) -> None:
        asset = self.session.scalar(
            select(ApprovedAsset).where(
                ApprovedAsset.id == asset_id,
                ApprovedAsset.workspace_id == workspace_id,
                ApprovedAsset.status == AssetStatus.APPROVED,
            )
        )
        if asset is None:
            raise LookupError("asset_not_found")
        asset.status = AssetStatus.REMOVED
        asset.removed_at = utcnow()

    def _issue_account_token(self, user_id: str, purpose: str) -> str:
        raw = secrets.token_urlsafe(32)
        self.session.add(AccountToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash(raw),
            expires_at=utcnow() + TOKEN_LIFETIME,
        ))
        return raw

    def _consume_account_token(self, raw_token: str, purpose: str) -> AccountToken:
        record = self.session.scalar(
            select(AccountToken).where(
                AccountToken.token_hash == token_hash(raw_token or ""),
                AccountToken.purpose == purpose,
                AccountToken.consumed_at.is_(None),
                AccountToken.expires_at > utcnow(),
            )
        )
        if record is None:
            raise ValueError("invalid_or_expired_token")
        record.consumed_at = utcnow()
        return record
