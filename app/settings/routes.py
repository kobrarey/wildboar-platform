"""Settings routes: security, emails, password change."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import or_, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import settings
from app.db import get_db
from app.web import templates
from app.i18n import t
from app.models import User, SessionModel, UserTotpRecoveryCode
from app.emails import send_email, render_email_template
from app.codes import create_code, verify_code
from app.security import hash_password, validate_password

from app.auth import (
    get_current_user,
    get_lang_from_request,
    normalize_email,
    is_valid_email,
    ensure_email_available_for_use,
    get_slot_email,
)
from app.auth.deps import COOKIE_NAME
from app.auth.code_action_cooldown import enforce_code_action_cooldown

from app.totp import (
    ISSUER_NAME,
    TotpConfigError,
    TotpSecretError,
    build_totp_qr_svg,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    verify_totp_code,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _cooldown_key(action: str, *parts) -> str:
    safe_parts = [str(p or "").strip().lower() for p in parts]
    return ":".join([action.strip().lower(), *safe_parts])


class PasswordChangeRequest(BaseModel):
    new_password: str
    slot: int  # 1 или 2


class PasswordChangeConfirm(BaseModel):
    new_password: str
    code: str


class EmailSendCodePayload(BaseModel):
    slot: int
    email: str


class EmailConfirmPayload(BaseModel):
    slot: int
    code: str


class EmailDeletePayload(BaseModel):
    slot: int


class TotpConfirmPayload(BaseModel):
    code: str


class TotpDisablePayload(BaseModel):
    code: str


@router.get("/security")
def security_settings_page(request: Request, user: User = Depends(get_current_user)):
    lang = get_lang_from_request(request)
    emails = [
        {"slot": 1, "email": user.email, "verified": user.is_email_verified},
        {"slot": 2, "email": user.backup_email, "verified": user.is_backup_email_verified},
    ]
    return templates.TemplateResponse(
        "security_settings.html",
        {
            "request": request,
            "lang": lang,
            "user": user,
            "emails": emails,
            "account_type": user.account_type,
            "totp_enabled": bool(getattr(user, "totp_enabled", False)),
            "totp_confirmed_at": getattr(user, "totp_confirmed_at", None),
        },
    )


@router.post("/security/emails/send-code")
def emails_send_code(
    payload: EmailSendCodePayload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    email_norm = normalize_email(payload.email)
    if not is_valid_email(email_norm):
        return JSONResponse({"status": "error", "message": t(lang, "invalid_email_format")}, status_code=400)

    other_email = user.backup_email if slot == 1 else user.email
    if other_email and email_norm == other_email:
        return JSONResponse({"status": "error", "message": t(lang, "email_already_used")}, status_code=400)

    if not ensure_email_available_for_use(db, email_norm, user.id):
        return JSONResponse({"status": "error", "message": t(lang, "email_already_used")}, status_code=400)

    try:
        enforce_code_action_cooldown(
            _cooldown_key("email_slot_send", user.id, slot, email_norm)
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    if slot == 1:
        user.email = email_norm
        user.is_email_verified = False
    else:
        user.backup_email = email_norm
        user.is_backup_email_verified = False

    db.add(user)
    db.commit()

    try:
        code = create_code(user.id, "registration", db=db)
    except ValueError as e:
        if str(e) == "code_cooldown":
            row = db.execute(
                text(
                    """
                    SELECT code
                    FROM public.security_codes
                    WHERE user_id = :user_id
                      AND purpose = :purpose
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user.id, "purpose": "registration"},
            ).mappings().first()
            if not row:
                return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)
            code = row["code"]
        else:
            return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    ttl = settings.SECURITY_CODE_TTL_MINUTES
    html = render_email_template(
        "emails/email_verify_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — email confirmation" if lang == "en" else "WildBoar — подтверждение почты"

    try:
        send_email(email_norm, subject, html)
    except Exception:
        return JSONResponse({"status": "error", "message": t(lang, "send_email_failed")}, status_code=500)

    return {"status": "ok"}


@router.post("/security/emails/confirm")
def emails_confirm(
    payload: EmailConfirmPayload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    current_email = get_slot_email(user, slot)
    if not current_email:
        return JSONResponse({"status": "error", "message": t(lang, "email_slot_empty")}, status_code=400)

    code = (payload.code or "").strip()

    try:
        verify_code(user.id, "registration", code, db=db)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    if slot == 1:
        user.is_email_verified = True
    else:
        user.is_backup_email_verified = True

    db.add(user)
    db.commit()

    return {"status": "ok"}


@router.post("/security/emails/delete")
def emails_delete(
    payload: EmailDeletePayload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    non_empty = 0
    if user.email:
        non_empty += 1
    if user.backup_email:
        non_empty += 1

    if non_empty <= 1:
        return JSONResponse({"status": "error", "message": t(lang, "cannot_delete_last_email")}, status_code=400)

    if slot == 1:
        user.email = user.backup_email
        user.is_email_verified = user.is_backup_email_verified
        user.backup_email = None
        user.is_backup_email_verified = False
    else:
        user.backup_email = None
        user.is_backup_email_verified = False

    db.add(user)
    db.commit()

    return {"status": "ok"}


@router.post("/security/totp/setup/start")
def totp_setup_start(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    if bool(getattr(user, "totp_enabled", False)):
        return JSONResponse(
            {"status": "error", "message": t(lang, "totp_already_enabled")},
            status_code=400,
        )

    try:
        secret = generate_totp_secret()
        encrypted_secret = encrypt_totp_secret(secret)
        qr_svg = build_totp_qr_svg(secret, user.email)
    except TotpConfigError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=500,
        )
    except Exception:
        return JSONResponse(
            {"status": "error", "message": t(lang, "totp_setup_required")},
            status_code=500,
        )

    user.totp_secret_encrypted = encrypted_secret
    user.totp_enabled = False
    user.totp_confirmed_at = None
    user.totp_last_used_step = None

    db.add(user)
    db.commit()

    return {
        "status": "ok",
        "qr_svg": qr_svg,
        "manual_key": secret,
        "issuer": ISSUER_NAME,
        "account": user.email,
    }


@router.post("/security/totp/setup/confirm")
def totp_setup_confirm(
    payload: TotpConfirmPayload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    if bool(getattr(user, "totp_enabled", False)):
        return JSONResponse(
            {"status": "error", "message": t(lang, "totp_already_enabled")},
            status_code=400,
        )

    try:
        enforce_code_action_cooldown(
            _cooldown_key("totp_confirm", user.id),
            seconds=5,
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    try:
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
    except TotpConfigError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=500,
        )
    except TotpSecretError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    result = verify_totp_code(
        secret=secret,
        code=payload.code,
        last_used_step=user.totp_last_used_step,
    )
    if not result.ok:
        return JSONResponse(
            {"status": "error", "message": t(lang, result.error_key or "totp_invalid_code")},
            status_code=400,
        )

    recovery_codes = generate_recovery_codes()

    db.query(UserTotpRecoveryCode).filter(
        UserTotpRecoveryCode.user_id == user.id,
    ).delete(synchronize_session=False)

    for code in recovery_codes:
        db.add(
            UserTotpRecoveryCode(
                user_id=user.id,
                code_hash=hash_recovery_code(code),
                is_used=False,
            )
        )

    user.totp_enabled = True
    user.totp_confirmed_at = datetime.now(timezone.utc)
    user.totp_last_used_step = result.step

    db.add(user)
    db.commit()

    return {
        "status": "ok",
        "recovery_codes": recovery_codes,
    }


@router.post("/security/totp/disable")
def totp_disable(
    payload: TotpDisablePayload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    if not bool(getattr(user, "totp_enabled", False)):
        return {"status": "ok"}

    try:
        enforce_code_action_cooldown(
            _cooldown_key("totp_disable", user.id),
            seconds=5,
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    try:
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
    except TotpConfigError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=500,
        )
    except TotpSecretError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    result = verify_totp_code(
        secret=secret,
        code=payload.code,
        last_used_step=user.totp_last_used_step,
    )
    if not result.ok:
        return JSONResponse(
            {"status": "error", "message": t(lang, result.error_key or "totp_invalid_code")},
            status_code=400,
        )

    user.totp_enabled = False
    user.totp_secret_encrypted = None
    user.totp_confirmed_at = None
    user.totp_last_used_step = None

    db.query(UserTotpRecoveryCode).filter(
        UserTotpRecoveryCode.user_id == user.id,
        UserTotpRecoveryCode.is_used == False,
    ).delete(synchronize_session=False)

    db.add(user)
    db.commit()

    return {"status": "ok"}


@router.post("/security/send-code")
def send_password_change_code(
    payload: PasswordChangeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    new_pwd = (payload.new_password or "").strip()
    err = validate_password(new_pwd, lang)
    if err:
        return JSONResponse({"status": "error", "message": err}, status_code=400)

    slot = int(payload.slot or 0)
    if slot not in (1, 2):
        return JSONResponse({"status": "error", "message": t(lang, "unsupported_slot")}, status_code=400)

    if slot == 1:
        to_email = user.email
        is_verified = bool(user.is_email_verified)
    else:
        to_email = user.backup_email
        is_verified = bool(user.is_backup_email_verified)

    if not to_email:
        return JSONResponse({"status": "error", "message": t(lang, "email_slot_empty")}, status_code=400)
    if not is_verified:
        return JSONResponse({"status": "error", "message": t(lang, "email_not_verified")}, status_code=400)

    try:
        enforce_code_action_cooldown(
            _cooldown_key("password_change_initial", user.id, slot)
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    try:
        code = create_code(user.id, "password_change", db=db)
    except ValueError as e:
        if str(e) == "code_cooldown":
            row = db.execute(
                text(
                    """
                    SELECT code
                    FROM public.security_codes
                    WHERE user_id = :user_id
                      AND purpose = :purpose
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user.id, "purpose": "password_change"},
            ).mappings().first()
            if not row:
                return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)
            code = row["code"]
        else:
            return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    ttl = settings.SECURITY_CODE_TTL_MINUTES
    html = render_email_template(
        "emails/password_change_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )

    subject = "WildBoar — password change confirmation" if lang == "en" else "WildBoar — подтверждение смены пароля"

    try:
        send_email(to_email, subject, html)
    except Exception:
        return JSONResponse({"status": "error", "message": t(lang, "send_email_failed")}, status_code=500)

    return {"status": "ok"}


@router.post("/security/change-password")
def change_password_confirm(
    payload: PasswordChangeConfirm,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    new_pwd = (payload.new_password or "").strip()
    err = validate_password(new_pwd, lang)
    if err:
        return JSONResponse({"status": "error", "message": err}, status_code=400)

    code = (payload.code or "").strip()
    try:
        verify_code(user.id, "password_change", code, db=db)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    user.password_hash = hash_password(new_pwd)

    current_sid = request.cookies.get(COOKIE_NAME)
    q = db.query(SessionModel).filter(SessionModel.user_id == user.id)
    if current_sid:
        q = q.filter(SessionModel.id != current_sid)
    q.delete(synchronize_session=False)

    db.commit()
    return {"status": "ok"}
