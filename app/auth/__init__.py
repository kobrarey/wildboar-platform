"""Auth, session, and user/email helpers."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.config import settings
from app.db import get_db
from app.models import User

from app.auth.deps import (
    NotAuthenticated,
    utcnow,
    create_session,
    get_current_user as _get_current_user,
    COOKIE_NAME,
    SESSION_TTL_DAYS,
)
from app.i18n import get_lang_from_request, SUPPORTED_LANGS, DEFAULT_LANG, LANG_COOKIE_NAME

if TYPE_CHECKING:
    pass

_RESEND_LAST_AT: dict[tuple[int, str], datetime] = {}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency wrapper for deps.get_current_user."""
    return _get_current_user(request, db)


# Re-export for backward compatibility
__all__ = [
    "get_lang_from_request",
    "SUPPORTED_LANGS",
    "DEFAULT_LANG",
    "LANG_COOKIE_NAME",
]


def _enforce_resend_cooldown(user_id: int, purpose: str) -> None:
    cooldown = settings.SECURITY_CODE_RESEND_COOLDOWN_SECONDS
    now = utcnow()
    key = (user_id, purpose)
    last = _RESEND_LAST_AT.get(key)
    if last and (now - last).total_seconds() < cooldown:
        raise ValueError("code_cooldown")
    _RESEND_LAST_AT[key] = now


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def get_user_by_any_email(db: Session, email: str) -> User | None:
    email = normalize_email(email)
    return (
        db.query(User)
        .filter(
            or_(
                User.email == email,
                User.backup_email == email,
            )
        )
        .first()
    )


def email_taken_by_other_user(db: Session, email_norm: str, current_user_id: int) -> bool:
    q = (
        db.query(User.id)
        .filter(
            User.id != current_user_id,
            or_(
                User.email == email_norm,
                User.backup_email == email_norm,
            ),
        )
        .first()
    )
    return q is not None


def ensure_email_available_for_use(db: Session, email_norm: str, current_user_id: int) -> bool:
    """
    True  -> email можно использовать
    False -> email занят реальным аккаунтом (есть хотя бы одна подтвержденная почта)
    """
    other = (
        db.query(User)
        .filter(
            User.id != current_user_id,
            or_(User.email == email_norm, User.backup_email == email_norm),
        )
        .first()
    )
    if not other:
        return True

    if (not other.is_email_verified) and (not other.is_backup_email_verified):
        db.delete(other)
        db.commit()
        return True

    return False


def is_slot_email_verified(user: User, slot: int) -> bool:
    if slot == 1:
        return bool(user.is_email_verified)
    return bool(user.is_backup_email_verified)


def get_slot_email(user: User, slot: int) -> str | None:
    return user.email if slot == 1 else user.backup_email


def _is_entered_email_verified(user: User, entered_email_norm: str) -> bool:
    if entered_email_norm == user.email:
        return bool(user.is_email_verified)
    if user.backup_email and entered_email_norm == user.backup_email:
        return bool(user.is_backup_email_verified)
    return False


def is_entered_email_verified(user: User, entered_email_norm: str) -> bool:
    """Public wrapper for _is_entered_email_verified."""
    return _is_entered_email_verified(user, entered_email_norm)


def send_login_2fa_code(db: Session, user: User, raw_email: str, lang: str) -> tuple[bool, str]:
    from app.codes import create_code
    from app.emails import send_email, render_email_template
    from app.i18n import t

    to_email = normalize_email(raw_email)

    if to_email != user.email and to_email != (user.backup_email or ""):
        return False, ("Invalid email" if lang == "en" else "Неверная почта")

    if not _is_entered_email_verified(user, to_email):
        return False, ("This email is not verified" if lang == "en" else "Эта почта не подтверждена")

    try:
        code = create_code(user.id, "login_2fa", db=db)
    except ValueError as e:
        return False, t(lang, str(e))

    ttl = settings.SECURITY_CODE_TTL_MINUTES
    html = render_email_template(
        "emails/login_2fa_code.html",
        {"code": code, "ttl_minutes": ttl, "title": "Wild Boar", "lang": lang, "user": user},
    )
    subject = "WildBoar — login confirmation code" if lang == "en" else "WildBoar — код подтверждения входа"

    try:
        send_email(to_email, subject, html)
    except Exception:
        return False, ("Failed to send email" if lang == "en" else "Не удалось отправить письмо")

    return True, ""


