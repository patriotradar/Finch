"""Relational data model for Aegis customer, monitoring and business records."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


def uuid4() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class AssetStatus(str, enum.Enum):
    APPROVED = "approved"
    REMOVED = "removed"


class ObservationStatus(str, enum.Enum):
    OBSERVATION = "observation"
    POTENTIAL_INDICATOR = "potential_indicator"
    DISMISSED = "dismissed"


class OwnerAccount(Base):
    __tablename__ = "owner_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_type: Mapped[str] = mapped_column(String(80), default="limited_company")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceUser(Base):
    __tablename__ = "workspace_users"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.MEMBER)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountToken(Base):
    __tablename__ = "account_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(ForeignKey("workspace_users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomerSession(Base):
    __tablename__ = "customer_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    user_id: Mapped[str] = mapped_column(ForeignKey("workspace_users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovedAsset(Base):
    __tablename__ = "approved_assets"
    __table_args__ = (UniqueConstraint("workspace_id", "value"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(253), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(32), default="domain")
    authority_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    authority_confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("workspace_users.id"))
    authority_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.APPROVED)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgreementAcceptance(Base):
    __tablename__ = "agreement_acceptances"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("workspace_users.id"))
    policy_name: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(40))
    policy_text_hash: Mapped[str] = mapped_column(String(64))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("approved_assets.id", ondelete="CASCADE"), index=True)
    status: Mapped[ObservationStatus] = mapped_column(Enum(ObservationStatus))
    title: Mapped[str] = mapped_column(String(240))
    observation: Mapped[str] = mapped_column(Text)
    inference: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(24), default="unknown")
    confidence: Mapped[str] = mapped_column(String(16), default="low")
    source: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    false_positive_guidance: Mapped[str | None] = mapped_column(Text)
    missing_evidence: Mapped[str | None] = mapped_column(Text)
    it_verification_required: Mapped[bool] = mapped_column(Boolean, default=True)


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="draft")
    web_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    pdf_storage_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationRecord(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    participant_ref: Mapped[str] = mapped_column(String(200))
    messages: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_reference: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    amount_pence: Mapped[int] = mapped_column(Integer, default=99500)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    renews_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutreachActivity(Base):
    __tablename__ = "outreach_activity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    company_name: Mapped[str] = mapped_column(String(200))
    contact_email: Mapped[str] = mapped_column(String(320), index=True)
    contact_source: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class SuppressionRecord(Base):
    __tablename__ = "suppression_records"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    reason: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OwnerActivity(Base):
    __tablename__ = "owner_activity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    action: Mapped[str] = mapped_column(String(120), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemControl(Base):
    __tablename__ = "system_controls"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_by: Mapped[str | None] = mapped_column(String(100))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
