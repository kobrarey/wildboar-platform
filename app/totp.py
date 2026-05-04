from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass

import pyotp
import segno
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.security import hash_password, verify_password


ISSUER_NAME = "WildBoar"
TOTP_INTERVAL_SECONDS = 30
TOTP_VALID_WINDOW = 1
RECOVERY_CODES_COUNT = 10
RECOVERY_CODE_GROUPS = 3
RECOVERY_CODE_GROUP_LEN = 4
RECOVERY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class TotpConfigError(Exception):
    pass


class TotpSecretError(Exception):
    pass


@dataclass(frozen=True)
class TotpVerificationResult:
    ok: bool
    step: int | None = None
    error_key: str | None = None


def _get_fernet() -> Fernet:
    key = (settings.TOTP_ENC_KEY or "").strip()
    if not key:
        raise TotpConfigError("totp_not_configured")

    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise TotpConfigError("totp_not_configured") from exc


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_totp_secret(secret: str) -> str:
    if not secret:
        raise TotpSecretError("totp_setup_required")

    f = _get_fernet()
    return f.encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_totp_secret(encrypted_secret: str | None) -> str:
    if not encrypted_secret:
        raise TotpSecretError("totp_setup_required")

    f = _get_fernet()
    try:
        return f.decrypt(encrypted_secret.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise TotpSecretError("totp_setup_required") from exc


def build_totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL_SECONDS).provisioning_uri(
        name=email,
        issuer_name=ISSUER_NAME,
    )


def build_totp_qr_svg(secret: str, email: str) -> str:
    uri = build_totp_uri(secret, email)
    return segno.make(uri).svg_inline(scale=4)


def current_totp_step(for_time: int | None = None) -> int:
    ts = int(for_time if for_time is not None else time.time())
    return ts // TOTP_INTERVAL_SECONDS


def verify_totp_code(
    secret: str,
    code: str,
    last_used_step: int | None = None,
) -> TotpVerificationResult:
    code_norm = (code or "").strip().replace(" ", "")
    if not code_norm.isdigit() or len(code_norm) != 6:
        return TotpVerificationResult(ok=False, error_key="totp_invalid_code")

    now_ts = int(time.time())
    step = current_totp_step(now_ts)

    if last_used_step is not None and step <= int(last_used_step):
        return TotpVerificationResult(ok=False, step=step, error_key="totp_code_reused")

    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL_SECONDS)
    ok = bool(totp.verify(code_norm, for_time=now_ts, valid_window=TOTP_VALID_WINDOW))
    if not ok:
        return TotpVerificationResult(ok=False, step=step, error_key="totp_invalid_code")

    return TotpVerificationResult(ok=True, step=step, error_key=None)


def generate_recovery_code() -> str:
    parts = []
    for _ in range(RECOVERY_CODE_GROUPS):
        part = "".join(secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(RECOVERY_CODE_GROUP_LEN))
        parts.append(part)
    return "-".join(parts)


def generate_recovery_codes(count: int = RECOVERY_CODES_COUNT) -> list[str]:
    return [generate_recovery_code() for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    return hash_password(normalize_recovery_code(code))


def verify_recovery_code(code: str, code_hash: str) -> bool:
    return verify_password(normalize_recovery_code(code), code_hash)


def normalize_recovery_code(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "")