"""Customer account and authorised-asset API."""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Cookie, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.accounts import AccountService
from core.models import AgreementAcceptance, ApprovedAsset, AssetStatus, Workspace, WorkspaceUser
from core.security import LoginThrottle
from core.tenant import require_workspace_user


CUSTOMER_COOKIE = "aegis_customer_session"


class RegistrationBody(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=200)


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetBody(TokenBody):
    password: str = Field(min_length=12, max_length=200)


class AssetBody(BaseModel):
    value: str = Field(min_length=3, max_length=300)
    authority_confirmed: bool


class AgreementBody(BaseModel):
    policy_name: str = Field(min_length=2, max_length=100)
    policy_version: str = Field(min_length=1, max_length=40)
    policy_text_hash: str = Field(min_length=64, max_length=64)


def build_customer_router(session_factory, account_mailer=None) -> APIRouter:
    router = APIRouter(prefix="/api/account", tags=["customer-account"])
    login_throttle = LoginThrottle(attempts=5, window_seconds=15 * 60)

    def error_response(error: Exception):
        code = str(error)
        allowed = {
            "invalid_email", "invalid_company_name", "password_too_short",
            "password_needs_mixed_case_and_number", "email_already_registered",
            "invalid_or_expired_token", "invalid_credentials", "authentication_required",
            "csrf_validation_failed", "authority_confirmation_required",
            "invalid_domain", "asset_limit_reached", "user_limit_reached",
            "invalid_policy_hash", "asset_not_found", "workspace_access_denied",
        }
        if code not in allowed:
            return JSONResponse({"error": "request_failed"}, status_code=500)
        status = 401 if isinstance(error, PermissionError) else 400
        if code in {"email_already_registered"}:
            status = 409
        return JSONResponse({"error": code}, status_code=status)

    def customer_context(raw_token: str | None, csrf: str | None = None, require_csrf: bool = False):
        session = session_factory()
        try:
            service = AccountService(session)
            active = service.authenticate(raw_token or "")
            if require_csrf and (not csrf or not hmac.compare_digest(csrf, active.csrf_token)):
                raise PermissionError("csrf_validation_failed")
            user = require_workspace_user(session, active.workspace_id, active.user_id)
            return session, service, active, user
        except Exception:
            session.close()
            raise

    @router.post("/register")
    def register(body: RegistrationBody):
        with session_factory() as session:
            try:
                user, token = AccountService(session).register(
                    body.company_name, body.email, body.password
                )
                session.commit()
            except Exception as error:
                session.rollback()
                return error_response(error)
        delivered = bool(
            account_mailer and account_mailer.send_verification(user.email, token)
        )
        payload = {
            "ok": True,
            "verification_required": True,
            "verification_email_sent": delivered,
        }
        if os.environ.get("AEGIS_DEV_EXPOSE_TOKENS") == "1":
            payload["verification_token"] = token
        return JSONResponse(payload, status_code=201)

    @router.post("/verify-email")
    def verify_email(body: TokenBody):
        with session_factory() as session:
            try:
                AccountService(session).verify_email(body.token)
                session.commit()
                return {"ok": True}
            except Exception as error:
                session.rollback()
                return error_response(error)

    @router.post("/login")
    def login(body: LoginBody, request: Request):
        key = f"{request.client.host if request.client else 'unknown'}:{body.email.strip().lower()}"
        if not login_throttle.allowed(key):
            return JSONResponse(
                {"error": "too_many_attempts"},
                status_code=429,
                headers={"Retry-After": "900"},
            )
        with session_factory() as session:
            try:
                issued = AccountService(session).login(body.email, body.password)
                session.commit()
            except Exception as error:
                session.rollback()
                login_throttle.failure(key)
                return error_response(error)
        login_throttle.reset(key)
        response = JSONResponse({
            "authenticated": True,
            "csrf_token": issued.csrf_token,
            "expires_at": issued.expires_at.isoformat(),
        })
        response.set_cookie(
            CUSTOMER_COOKIE,
            issued.token,
            max_age=12 * 60 * 60,
            httponly=True,
            secure=bool(os.environ.get("VERCEL") or request.url.scheme == "https"),
            samesite="strict",
            path="/",
        )
        return response

    @router.get("/session")
    def account_session(aegis_customer_session: str | None = Cookie(default=None)):
        try:
            db, _, active, user = customer_context(aegis_customer_session)
            try:
                workspace = db.get(Workspace, active.workspace_id)
                return {
                    "authenticated": True,
                    "csrf_token": active.csrf_token,
                    "user": {"id": user.id, "email": user.email, "role": user.role.value},
                    "workspace": {"id": workspace.id, "company_name": workspace.company_name},
                }
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/logout")
    def logout(
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, service, _, _ = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                service.logout(aegis_customer_session or "")
                db.commit()
            finally:
                db.close()
        except Exception as error:
            return error_response(error)
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(CUSTOMER_COOKIE, path="/", samesite="strict")
        return response

    @router.get("/assets")
    def list_assets(aegis_customer_session: str | None = Cookie(default=None)):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                assets = db.scalars(
                    select(ApprovedAsset).where(
                        ApprovedAsset.workspace_id == active.workspace_id,
                        ApprovedAsset.status == AssetStatus.APPROVED,
                    )
                ).all()
                return {"assets": [
                    {
                        "id": asset.id,
                        "value": asset.value,
                        "asset_type": asset.asset_type,
                        "authority_confirmed_at": asset.authority_confirmed_at.isoformat(),
                    }
                    for asset in assets
                ]}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/assets")
    def add_asset(
        body: AssetBody,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, service, active, user = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                asset = service.add_asset(
                    active.workspace_id, user.id, body.value, body.authority_confirmed
                )
                db.commit()
                return JSONResponse({"id": asset.id, "value": asset.value}, status_code=201)
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.delete("/assets/{asset_id}")
    def remove_asset(
        asset_id: str,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, service, active, _ = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                service.remove_asset(active.workspace_id, asset_id)
                db.commit()
                return {"ok": True}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/agreements")
    def accept_agreement(
        body: AgreementBody,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, service, active, user = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                record = service.accept_policy(
                    active.workspace_id,
                    user.id,
                    body.policy_name,
                    body.policy_version,
                    body.policy_text_hash,
                )
                db.commit()
                return JSONResponse({"id": record.id, "accepted": True}, status_code=201)
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/password-reset/request")
    def request_password_reset(body: PasswordResetRequest):
        with session_factory() as session:
            try:
                token = AccountService(session).request_password_reset(body.email)
                session.commit()
            except ValueError:
                token = None
                session.rollback()
        payload = {"ok": True}
        if token and account_mailer:
            account_mailer.send_password_reset(body.email.strip().lower(), token)
        if token and os.environ.get("AEGIS_DEV_EXPOSE_TOKENS") == "1":
            payload["reset_token"] = token
        return payload

    @router.post("/password-reset/complete")
    def complete_password_reset(body: PasswordResetBody):
        with session_factory() as session:
            try:
                AccountService(session).reset_password(body.token, body.password)
                session.commit()
                return {"ok": True}
            except Exception as error:
                session.rollback()
                return error_response(error)

    return router
