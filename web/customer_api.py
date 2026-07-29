"""Customer account and authorised-asset API."""

from __future__ import annotations

import hmac
import os
from io import BytesIO

from fastapi import APIRouter, Cookie, Header, Request
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from core.accounts import AccountService
from core.billing import BillingService
from core.models import (
    AgreementAcceptance, ApprovedAsset, AssetStatus, CustomerAlert, Observation,
    Invoice, Report, Role, Subscription, Workspace, WorkspaceUser, utcnow,
)
from core.paypal_checkout import PayPalCheckout
from core.security import LoginThrottle
from core.tenant import require_workspace_user
from asm.report_pdf import build_report_pdf
from asm.report_html import render_report_html


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

class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)

class AcceptInviteBody(TokenBody):
    password: str = Field(min_length=12, max_length=200)

class PayPalCaptureBody(BaseModel):
    order_id: str = Field(min_length=3, max_length=100)


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
            "alert_not_found", "report_not_found",
            "user_not_found", "cannot_disable_self", "owner_access_required",
            "workspace_already_cancelled",
            "checkout_not_configured", "checkout_provider_unavailable",
            "licence_already_active",
            "subscription_not_found", "payment_not_completed",
            "unexpected_payment_amount", "invalid_order_id",
        }
        if code not in allowed:
            return JSONResponse({"error": "request_failed"}, status_code=500)
        status = 401 if isinstance(error, PermissionError) else 400
        if code in {"email_already_registered", "licence_already_active"}:
            status = 409
        if code in {"checkout_not_configured", "checkout_provider_unavailable"}:
            status = 503
        if code in {
            "asset_not_found", "alert_not_found", "report_not_found",
            "user_not_found", "subscription_not_found",
        }:
            status = 404
        return JSONResponse({"error": code}, status_code=status)

    def customer_context(raw_token: str | None, csrf: str | None = None, require_csrf: bool = False):
        if not raw_token:
            raise PermissionError("authentication_required")
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

    @router.post("/verification/resend")
    def resend_verification(body: PasswordResetRequest):
        token = None
        recipient = body.email.strip().lower()
        with session_factory() as session:
            try:
                token = AccountService(session).request_verification(recipient)
                session.commit()
            except Exception:
                session.rollback()
        delivered = bool(
            token and account_mailer and account_mailer.send_verification(recipient, token)
        )
        return {
            "ok": True,
            "message": "If an unverified account exists, a new link has been requested.",
            "verification_email_sent": delivered,
        }

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
                    "workspace": {
                        "id": workspace.id,
                        "company_name": workspace.company_name,
                        "status": workspace.status,
                    },
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

    @router.get("/users")
    def list_users(aegis_customer_session: str | None = Cookie(default=None)):
        try:
            db, _, active, actor = customer_context(aegis_customer_session)
            try:
                if actor.role not in {Role.OWNER, Role.ADMIN}:
                    raise PermissionError("owner_access_required")
                users = db.scalars(select(WorkspaceUser).where(
                    WorkspaceUser.workspace_id == active.workspace_id
                ).order_by(WorkspaceUser.created_at)).all()
                return {"users": [{
                    "id": item.id,
                    "email": item.email,
                    "role": item.role.value,
                    "verified": item.email_verified_at is not None,
                    "disabled": item.disabled_at is not None,
                } for item in users]}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/users/invite")
    def invite_user(
        body: InviteBody,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, service, active, actor = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                if actor.role not in {Role.OWNER, Role.ADMIN}:
                    raise PermissionError("owner_access_required")
                invited, token = service.invite_user(active.workspace_id, body.email)
                workspace = db.get(Workspace, active.workspace_id)
                db.commit()
                delivered = bool(account_mailer and account_mailer.send_invitation(
                    invited.email, token, workspace.company_name
                ))
                payload = {"ok": True, "invitation_email_sent": delivered}
                if os.environ.get("AEGIS_DEV_EXPOSE_TOKENS") == "1":
                    payload["invitation_token"] = token
                return JSONResponse(payload, status_code=201)
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/users/accept-invite")
    def accept_invite(body: AcceptInviteBody):
        with session_factory() as session:
            try:
                AccountService(session).accept_invite(body.token, body.password)
                session.commit()
                return {"ok": True}
            except Exception as error:
                session.rollback()
                return error_response(error)

    @router.delete("/users/{user_id}")
    def disable_user(
        user_id: str,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, service, active, actor = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                if actor.role not in {Role.OWNER, Role.ADMIN}:
                    raise PermissionError("owner_access_required")
                service.disable_user(active.workspace_id, actor.id, user_id)
                db.commit()
                return {"ok": True}
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

    @router.get("/alerts")
    def list_alerts(aegis_customer_session: str | None = Cookie(default=None)):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                alerts = db.scalars(
                    select(CustomerAlert).where(
                        CustomerAlert.workspace_id == active.workspace_id
                    ).order_by(CustomerAlert.created_at.desc()).limit(100)
                ).all()
                return {"alerts": [
                    {
                        "id": alert.id,
                        "title": alert.title,
                        "detail": alert.detail,
                        "important": alert.important,
                        "created_at": alert.created_at.isoformat(),
                        "acknowledged": alert.acknowledged_at is not None,
                    }
                    for alert in alerts
                ]}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/alerts/{alert_id}/acknowledge")
    def acknowledge_alert(
        alert_id: str,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, _, active, _ = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                alert = db.scalar(select(CustomerAlert).where(
                    CustomerAlert.id == alert_id,
                    CustomerAlert.workspace_id == active.workspace_id,
                ))
                if alert is None:
                    raise LookupError("alert_not_found")
                from core.models import utcnow
                alert.acknowledged_at = utcnow()
                db.commit()
                return {"ok": True}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.get("/reports")
    def list_reports(aegis_customer_session: str | None = Cookie(default=None)):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                reports = db.scalars(
                    select(Report).where(
                        Report.workspace_id == active.workspace_id,
                        Report.status == "ready",
                    ).order_by(Report.period_end.desc()).limit(36)
                ).all()
                return {"reports": [
                    {
                        "id": report.id,
                        "period_start": report.period_start.isoformat(),
                        "period_end": report.period_end.isoformat(),
                        "status": report.status,
                        "summary": (report.web_payload or {}).get("summary", {}),
                    }
                    for report in reports
                ]}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.get("/reports/{report_id}")
    def get_report(
        report_id: str,
        aegis_customer_session: str | None = Cookie(default=None),
    ):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                report = db.scalar(select(Report).where(
                    Report.id == report_id,
                    Report.workspace_id == active.workspace_id,
                    Report.status == "ready",
                ))
                if report is None:
                    raise LookupError("report_not_found")
                return {"id": report.id, "report": report.web_payload}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.get("/reports/{report_id}/pdf")
    def download_report_pdf(
        report_id: str,
        aegis_customer_session: str | None = Cookie(default=None),
    ):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                report = db.scalar(select(Report).where(
                    Report.id == report_id,
                    Report.workspace_id == active.workspace_id,
                    Report.status == "ready",
                ))
                if report is None:
                    raise LookupError("report_not_found")
                workspace = db.get(Workspace, active.workspace_id)
                pdf = build_report_pdf(workspace.company_name, report.web_payload)
            finally:
                db.close()
        except Exception as error:
            return error_response(error)
        return StreamingResponse(
            BytesIO(pdf),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="aegis-report-{report_id}.pdf"'},
        )

    @router.get("/reports/{report_id}/view", response_class=HTMLResponse)
    def view_report(
        report_id: str,
        aegis_customer_session: str | None = Cookie(default=None),
    ):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                report = db.scalar(select(Report).where(
                    Report.id == report_id,
                    Report.workspace_id == active.workspace_id,
                    Report.status == "ready",
                ))
                if report is None:
                    raise LookupError("report_not_found")
                workspace = db.get(Workspace, active.workspace_id)
                html = render_report_html(workspace.company_name, report.web_payload)
            finally:
                db.close()
        except Exception as error:
            return error_response(error)
        return HTMLResponse(html)

    @router.get("/export")
    def export_workspace_data(aegis_customer_session: str | None = Cookie(default=None)):
        """Export customer-controlled records without credentials or internal secrets."""
        try:
            db, _, active, user = customer_context(aegis_customer_session)
            try:
                if user.role not in {Role.OWNER, Role.ADMIN}:
                    raise PermissionError("owner_access_required")
                workspace = db.get(Workspace, active.workspace_id)
                users = db.scalars(select(WorkspaceUser).where(
                    WorkspaceUser.workspace_id == active.workspace_id
                )).all()
                assets = db.scalars(select(ApprovedAsset).where(
                    ApprovedAsset.workspace_id == active.workspace_id
                )).all()
                agreements = db.scalars(select(AgreementAcceptance).where(
                    AgreementAcceptance.workspace_id == active.workspace_id
                )).all()
                observations = db.scalars(select(Observation).where(
                    Observation.workspace_id == active.workspace_id
                )).all()
                reports = db.scalars(select(Report).where(
                    Report.workspace_id == active.workspace_id
                )).all()
                payload = {
                    "exported_at": utcnow().isoformat(),
                    "workspace": {
                        "id": workspace.id, "company_name": workspace.company_name,
                        "status": workspace.status, "created_at": workspace.created_at.isoformat(),
                    },
                    "users": [{
                        "id": item.id, "email": item.email, "role": item.role.value,
                        "verified_at": item.email_verified_at.isoformat() if item.email_verified_at else None,
                        "disabled_at": item.disabled_at.isoformat() if item.disabled_at else None,
                    } for item in users],
                    "assets": [{
                        "id": item.id, "value": item.value, "status": item.status.value,
                        "authority_confirmed_at": item.authority_confirmed_at.isoformat()
                        if item.authority_confirmed_at else None,
                        "removed_at": item.removed_at.isoformat() if item.removed_at else None,
                    } for item in assets],
                    "agreements": [{
                        "policy_name": item.policy_name, "policy_version": item.policy_version,
                        "policy_text_hash": item.policy_text_hash,
                        "accepted_at": item.accepted_at.isoformat(),
                    } for item in agreements],
                    "observations": [{
                        "id": item.id, "asset_id": item.asset_id, "status": item.status.value,
                        "title": item.title, "observation": item.observation,
                        "inference": item.inference, "severity": item.severity,
                        "confidence": item.confidence, "source": item.source,
                        "detected_at": item.detected_at.isoformat(),
                    } for item in observations],
                    "reports": [{
                        "id": item.id, "period_start": item.period_start.isoformat(),
                        "period_end": item.period_end.isoformat(), "status": item.status,
                        "report": item.web_payload,
                    } for item in reports],
                }
            finally:
                db.close()
        except Exception as error:
            return error_response(error)
        return JSONResponse(
            payload,
            headers={"Content-Disposition": 'attachment; filename="aegis-workspace-export.json"'},
        )

    @router.post("/cancel")
    def cancel_workspace(
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, _, active, user = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                if user.role != Role.OWNER:
                    raise PermissionError("owner_access_required")
                workspace = db.get(Workspace, active.workspace_id)
                if workspace.cancelled_at is not None:
                    raise ValueError("workspace_already_cancelled")
                workspace.status = "cancelled"
                workspace.cancelled_at = utcnow()
                db.query(ApprovedAsset).filter(
                    ApprovedAsset.workspace_id == active.workspace_id,
                    ApprovedAsset.status == AssetStatus.APPROVED,
                ).update({"status": AssetStatus.REMOVED, "removed_at": utcnow()})
                from core.models import CustomerSession
                db.query(CustomerSession).filter(
                    CustomerSession.workspace_id == active.workspace_id,
                    CustomerSession.revoked_at.is_(None),
                ).update({"revoked_at": utcnow()})
                db.commit()
            finally:
                db.close()
        except Exception as error:
            return error_response(error)
        response = JSONResponse({"cancelled": True})
        response.delete_cookie(CUSTOMER_COOKIE, path="/", samesite="strict")
        return response

    @router.get("/billing")
    def billing_status(aegis_customer_session: str | None = Cookie(default=None)):
        try:
            db, _, active, _ = customer_context(aegis_customer_session)
            try:
                subscription = db.scalar(select(Subscription).where(
                    Subscription.workspace_id == active.workspace_id
                ))
                invoices = db.scalars(select(Invoice).where(
                    Invoice.workspace_id == active.workspace_id
                ).order_by(Invoice.issued_at.desc()).limit(20)).all()
                return {
                    "checkout_configured": PayPalCheckout().configured,
                    "checkout_provider": "paypal",
                    "subscription": None if subscription is None else {
                        "status": subscription.status,
                        "amount_pence": subscription.amount_pence,
                        "currency": subscription.currency,
                        "starts_at": subscription.starts_at.isoformat()
                        if subscription.starts_at else None,
                        "renews_at": subscription.renews_at.isoformat()
                        if subscription.renews_at else None,
                    },
                    "invoices": [{
                        "id": invoice.id,
                        "status": invoice.status,
                        "amount_pence": invoice.amount_pence,
                        "currency": invoice.currency,
                        "receipt_url": invoice.receipt_url,
                        "issued_at": invoice.issued_at.isoformat(),
                    } for invoice in invoices],
                }
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/billing/checkout")
    def start_checkout(
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, _, active, user = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                if user.role != Role.OWNER:
                    raise PermissionError("owner_access_required")
                existing = db.scalar(select(Subscription).where(
                    Subscription.workspace_id == active.workspace_id
                ))
                if existing and existing.status == "active":
                    raise ValueError("licence_already_active")
                provider = PayPalCheckout()
                created = provider.create_order(active.workspace_id)
                BillingService(db).create_pending(
                    active.workspace_id, "paypal", created["id"]
                )
                db.commit()
                return {"checkout_url": created["url"]}
            finally:
                db.close()
        except Exception as error:
            return error_response(error)

    @router.post("/billing/paypal/capture")
    def capture_paypal_order(
        body: PayPalCaptureBody,
        aegis_customer_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ):
        try:
            db, _, active, user = customer_context(
                aegis_customer_session, x_csrf_token, require_csrf=True
            )
            try:
                if user.role != Role.OWNER:
                    raise PermissionError("owner_access_required")
                subscription = db.scalar(select(Subscription).where(
                    Subscription.workspace_id == active.workspace_id,
                    Subscription.provider == "paypal",
                    Subscription.provider_reference == body.order_id,
                ))
                if subscription is None:
                    raise LookupError("subscription_not_found")
                captured = PayPalCheckout().capture_order(body.order_id)
                BillingService(db).record_payment(
                    "paypal",
                    f"paypal-capture-{captured['capture_id']}",
                    body.order_id,
                    captured["capture_id"],
                    captured["amount_pence"],
                    captured["currency"],
                    payload=captured["raw"],
                )
                db.commit()
                return {"captured": True, "licence_status": "active"}
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
