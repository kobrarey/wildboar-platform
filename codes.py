import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

load_dotenv()

ALLOWED_PURPOSES = {"registration", "reset", "login_2fa"}

CODE_LENGTH = int(os.getenv("SECURITY_CODE_LENGTH", "6"))
CODE_TTL_MINUTES = int(os.getenv("SECURITY_CODE_TTL_MINUTES", "15"))
MAX_ATTEMPTS = int(os.getenv("SECURITY_CODE_MAX_ATTEMPTS", "5"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required for codes.py")

_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
_SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


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

    code = _gen_code(CODE_LENGTH)
    expires_at = utcnow() + timedelta(minutes=CODE_TTL_MINUTES)

    close_after = False
    if db is None:
        db = _SessionLocal()
        close_after = True

    try:
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

    close_after = False
    if db is None:
        db = _SessionLocal()
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

            raise ValueError("Invalid code")

        # 3) validation
        if row["is_used"]:
            raise ValueError("Code already used")

        if row["expires_at"] < utcnow():
            raise ValueError("Code expired")

        if row["attempts"] >= MAX_ATTEMPTS:
            # optionally burn it
            db.execute(
                text("UPDATE public.security_codes SET is_used = TRUE WHERE id = :id"),
                {"id": row["id"]},
            )
            db.commit()
            raise ValueError("Too many attempts")

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
