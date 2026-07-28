from sqlalchemy import select

from core.database import Base, build_engine, build_session_factory
from core.models import (
    ApprovedAsset, AssetStatus, Observation, ObservationStatus, Workspace,
    WorkspaceUser,
)
from core.tenant import require_monitorable_asset, require_workspace_user, tenant_select


def database():
    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, build_session_factory(engine)


def seed(factory):
    with factory() as session:
        alpha = Workspace(company_name="Alpha Ltd")
        beta = Workspace(company_name="Beta Ltd")
        session.add_all([alpha, beta])
        session.flush()
        alpha_user = WorkspaceUser(
            workspace_id=alpha.id,
            email="alpha@example.test",
            password_hash="hash",
        )
        beta_user = WorkspaceUser(
            workspace_id=beta.id,
            email="beta@example.test",
            password_hash="hash",
        )
        session.add_all([alpha_user, beta_user])
        session.flush()
        alpha_asset = ApprovedAsset(
            workspace_id=alpha.id,
            value="alpha.example",
            authority_confirmed=True,
            authority_confirmed_by=alpha_user.id,
            status=AssetStatus.APPROVED,
        )
        beta_asset = ApprovedAsset(
            workspace_id=beta.id,
            value="beta.example",
            authority_confirmed=True,
            authority_confirmed_by=beta_user.id,
            status=AssetStatus.APPROVED,
        )
        session.add_all([alpha_asset, beta_asset])
        session.flush()
        session.add_all([
            Observation(
                workspace_id=alpha.id,
                asset_id=alpha_asset.id,
                status=ObservationStatus.OBSERVATION,
                title="Alpha observation",
                observation="Public DNS record",
                source="DNS",
            ),
            Observation(
                workspace_id=beta.id,
                asset_id=beta_asset.id,
                status=ObservationStatus.OBSERVATION,
                title="Beta observation",
                observation="Public TLS metadata",
                source="TLS",
            ),
        ])
        session.commit()
        return alpha.id, beta.id, alpha_user.id, beta_user.id, alpha_asset.id


def test_tenant_query_never_returns_other_workspace_records():
    _, factory = database()
    alpha_id, _, _, _, _ = seed(factory)
    with factory() as session:
        observations = list(session.scalars(tenant_select(Observation, alpha_id)))
        assert [item.title for item in observations] == ["Alpha observation"]


def test_user_cannot_cross_workspace_boundary():
    _, factory = database()
    alpha_id, beta_id, alpha_user_id, _, _ = seed(factory)
    with factory() as session:
        require_workspace_user(session, alpha_id, alpha_user_id)
        try:
            require_workspace_user(session, beta_id, alpha_user_id)
        except PermissionError as error:
            assert str(error) == "workspace_access_denied"
        else:
            raise AssertionError("cross-tenant access was not denied")


def test_removed_asset_stops_being_monitorable():
    _, factory = database()
    alpha_id, _, _, _, asset_id = seed(factory)
    with factory() as session:
        asset = require_monitorable_asset(session, alpha_id, asset_id)
        asset.status = AssetStatus.REMOVED
        session.commit()
    with factory() as session:
        try:
            require_monitorable_asset(session, alpha_id, asset_id)
        except PermissionError as error:
            assert str(error) == "asset_not_approved"
        else:
            raise AssertionError("removed asset remained monitorable")


def test_unscoped_model_is_rejected():
    try:
        tenant_select(Workspace, "workspace")
    except TypeError as error:
        assert str(error) == "model_is_not_tenant_scoped"
    else:
        raise AssertionError("unscoped model was accepted")

