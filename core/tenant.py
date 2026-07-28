"""Fail-closed helpers for workspace-scoped database access."""

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from core.models import ApprovedAsset, AssetStatus, WorkspaceUser


def tenant_select(model, workspace_id: str) -> Select:
    if not workspace_id:
        raise ValueError("workspace_id_required")
    if not hasattr(model, "workspace_id"):
        raise TypeError("model_is_not_tenant_scoped")
    return select(model).where(model.workspace_id == workspace_id)


def require_workspace_user(session: Session, workspace_id: str, user_id: str) -> WorkspaceUser:
    user = session.scalar(
        select(WorkspaceUser).where(
            WorkspaceUser.id == user_id,
            WorkspaceUser.workspace_id == workspace_id,
            WorkspaceUser.disabled_at.is_(None),
        )
    )
    if user is None:
        raise PermissionError("workspace_access_denied")
    return user


def require_monitorable_asset(session: Session, workspace_id: str, asset_id: str) -> ApprovedAsset:
    asset = session.scalar(
        select(ApprovedAsset).where(
            ApprovedAsset.id == asset_id,
            ApprovedAsset.workspace_id == workspace_id,
            ApprovedAsset.authority_confirmed.is_(True),
            ApprovedAsset.status == AssetStatus.APPROVED,
            ApprovedAsset.removed_at.is_(None),
        )
    )
    if asset is None:
        raise PermissionError("asset_not_approved")
    return asset
