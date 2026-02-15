import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal

ALLOWED_PURPOSES = {"registration", "reset", "login_2fa", "password_change"}

CODE_LENGTH = settings.SECURITY_CODE_LENGTH
CODE_TTL_MINUTES = settings.SECURITY_CODE_TTL_MINUTES
MAX_ATTEMPTS = settings.SECURITY_CODE_MAX_ATTEMPTS
RESEND_COOLDOWN_SECONDS = settings.SECURITY_CODE_RESEND_COOLDOWN_SECONDS


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _gen_code(length: int) -> str:
    # numeric 6-digit by default
    return "".join(secrets.choice("0123456789") for _ in range(length))


def create_code(user_id: int, purpose: str, db: Optional[Session] = None) -> str:
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError("Invalid purpose")
    if not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("Invalid user_id")

    now = utcnow()
    code = _gen_code(CODE_LENGTH)
    expires_at = now + timedelta(minutes=CODE_TTL_MINUTES)

    close_after = False
    if db is None:
        db = SessionLocal()
        close_after = True

    try:
        # rate-limit: not more often than once per RESEND_COOLDOWN_SECONDS
        # rate-limit только для части сценариев (регистрация и смена пароля).
        # Для login_2fa и reset позволяем запрашивать код без ожидания.
        if purpose in {"registration", "password_change"}:
            last = db.execute(
                text(
                    """
                    SELECT created_at
                    FROM public.security_codes
                    WHERE user_id = :user_id
                      AND purpose = :purpose
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user_id, "purpose": purpose},
            ).mappings().first()

            if last is not None:
                last_created = last["created_at"]
                if (now - last_created).total_seconds() < RESEND_COOLDOWN_SECONDS:
                    raise ValueError("code_cooldown")

        # deactivate all previous unused codes for this user/purpose
        db.execute(
            text(
                """
                UPDATE public.security_codes
                SET is_used = TRUE
                WHERE user_id = :user_id
                  AND purpose = :purpose
                  AND is_used = FALSE
                """
            ),
            {"user_id": user_id, "purpose": purpose},
        )

        db.execute(
            text(
                """
                INSERT INTO public.security_codes (user_id, purpose, code, expires_at)
                VALUES (:user_id, :purpose, :code, :expires_at)
                """
            ),
            {"user_id": user_id, "purpose": purpose, "code": code, "expires_at": expires_at},
        )
        db.commit()
        return code
    finally:
        if close_after:
            db.close()


def verify_code(user_id: int, purpose: str, code: str, db: Optional[Session] = None) -> bool:
    """
    Returns True on success; otherwise raises ValueError with a human-readable reason.
    Increments attempts on the latest active code for this (user_id, purpose).
    """
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError("Invalid purpose")
    
    # быстрая проверка формата кода
    if not code or not code.isdigit() or len(code) != CODE_LENGTH:
        raise ValueError("invalid_code")

    close_after = False
    if db is None:
        db = SessionLocal()
        close_after = True

    try:
        # 1) try exact match (best case)
        row = db.execute(
            text(
                """
                SELECT id, code, expires_at, is_used, attempts
                FROM public.security_codes
                WHERE user_id = :user_id
                  AND purpose = :purpose
                  AND code = :code
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"user_id": user_id, "purpose": purpose, "code": code},
        ).mappings().first()

        # 2) if not found, take latest active code (to increment attempts)
        if row is None:
            active = db.execute(
                text(
                    """
                    SELECT id, code, expires_at, is_used, attempts
                    FROM public.security_codes
                    WHERE user_id = :user_id
                      AND purpose = :purpose
                      AND is_used = FALSE
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user_id": user_id, "purpose": purpose},
            ).mappings().first()

            if active is not None:
                db.execute(
                    text("UPDATE public.security_codes SET attempts = attempts + 1 WHERE id = :id"),
                    {"id": active["id"]},
                )
                db.commit()

            raise ValueError("invalid_code")

        # 3) validation
        if row["is_used"]:
            raise ValueError("code_used")

        if row["expires_at"] < utcnow():
            raise ValueError("code_expired")

        if row["attempts"] >= MAX_ATTEMPTS:
            # optionally burn it
            db.execute(
                text("UPDATE public.security_codes SET is_used = TRUE WHERE id = :id"),
                {"id": row["id"]},
            )
            db.commit()
            raise ValueError("too_many_attempts")

        # 4) success: attempts++ and is_used=true
        db.execute(
            text(
                """
                UPDATE public.security_codes
                SET attempts = attempts + 1, is_used = TRUE
                WHERE id = :id
                """
            ),
            {"id": row["id"]},
        )
        db.commit()
        return True
    finally:
        if close_after:
            db.close()


def get_active_code(user_id: int, purpose: str, db: Session) -> Optional[str]:
    if purpose not in ALLOWED_PURPOSES:
        raise ValueError("Invalid purpose")

    row = db.execute(
        text(
            """
            SELECT code
            FROM public.security_codes
            WHERE user_id = :user_id
              AND purpose = :purpose
              AND is_used = FALSE
              AND expires_at > :now
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"user_id": user_id, "purpose": purpose, "now": utcnow()},
    ).mappings().first()

    return row["code"] if row else None
