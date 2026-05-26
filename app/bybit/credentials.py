from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.config import settings
from app.models import Fund, FundBybitAccount


class BybitCredentialsError(RuntimeError):
    pass


@dataclass(frozen=True)
class FundBybitCredentialsResult:
    fund_code: str
    fund_id: int
    bybit_sub_uid: str
    api_key_label: str | None
    api_permissions: str | None
    api_ip_whitelist: str | None
    api_key_is_active: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_fernet() -> Fernet:
    key = (settings.BYBIT_API_ENC_KEY or "").strip()
    if not key:
        raise BybitCredentialsError(
            "BYBIT_API_ENC_KEY is required to encrypt/decrypt per-fund Bybit API credentials"
        )

    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise BybitCredentialsError(
            "Invalid BYBIT_API_ENC_KEY. Generate it once with Fernet.generate_key()."
        ) from exc


def encrypt_bybit_api_value(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise BybitCredentialsError("Cannot encrypt empty Bybit API credential value")

    fernet = _get_fernet()
    return fernet.encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_bybit_api_value(value: str) -> str:
    encrypted = (value or "").strip()
    if not encrypted:
        raise BybitCredentialsError("Cannot decrypt empty Bybit API credential value")

    fernet = _get_fernet()

    try:
        return fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise BybitCredentialsError(
            "Failed to decrypt Bybit API credential value. Check BYBIT_API_ENC_KEY."
        ) from exc


def _get_fund_by_code(db: Session, *, fund_code: str) -> Fund:
    code = (fund_code or "").strip().lower()
    if not code:
        raise BybitCredentialsError("fund_code is required")

    fund = db.query(Fund).filter(Fund.code == code).first()
    if fund is None:
        raise BybitCredentialsError(f"Fund not found: {code}")

    return fund


def _get_active_fund_bybit_account(
    db: Session,
    *,
    fund_id: int,
    coin: str = "USDT",
    chain_type: str = "BSC",
) -> FundBybitAccount:
    account = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == fund_id,
            FundBybitAccount.coin == coin,
            FundBybitAccount.chain_type == chain_type,
            FundBybitAccount.is_active == True,
        )
        .first()
    )

    if account is None:
        raise BybitCredentialsError(
            f"Active fund_bybit_accounts row not found for fund_id={fund_id}, "
            f"coin={coin}, chain_type={chain_type}"
        )

    return account


def set_fund_bybit_api_credentials(
    db: Session,
    *,
    fund_code: str,
    api_key: str,
    api_secret: str,
    api_key_label: str | None = None,
    api_permissions: str | None = None,
    api_ip_whitelist: str | None = None,
    coin: str = "USDT",
    chain_type: str = "BSC",
) -> FundBybitCredentialsResult:
    """
    Store encrypted Bybit subaccount API credentials for one fund.

    Does not commit.
    Caller controls transaction boundary.

    Plain API key/secret must not be logged or returned.
    """
    fund = _get_fund_by_code(db, fund_code=fund_code)

    account = _get_active_fund_bybit_account(
        db,
        fund_id=fund.id,
        coin=coin,
        chain_type=chain_type,
    )

    account.api_key_encrypted = encrypt_bybit_api_value(api_key)
    account.api_secret_encrypted = encrypt_bybit_api_value(api_secret)
    account.api_permissions = api_permissions
    account.api_ip_whitelist = api_ip_whitelist
    account.api_key_label = api_key_label
    account.api_key_added_at = utcnow()
    account.api_key_is_active = True

    db.add(account)
    db.flush()

    return FundBybitCredentialsResult(
        fund_code=fund.code,
        fund_id=fund.id,
        bybit_sub_uid=account.bybit_sub_uid,
        api_key_label=account.api_key_label,
        api_permissions=account.api_permissions,
        api_ip_whitelist=account.api_ip_whitelist,
        api_key_is_active=account.api_key_is_active,
    )


def get_active_fund_bybit_client(
    db: Session,
    *,
    fund_id: int,
    coin: str = "USDT",
    chain_type: str = "BSC",
) -> BybitV5Client:
    """
    Build BybitV5Client from encrypted per-fund subaccount API credentials.

    Used for:
    - FUND -> UNIFIED internal transfer;
    - future trading allocation;
    - future Earn operations.

    Not used for master-level query-sub-member-record.
    """
    account = _get_active_fund_bybit_account(
        db,
        fund_id=fund_id,
        coin=coin,
        chain_type=chain_type,
    )

    if not account.api_key_is_active:
        raise BybitCredentialsError(
            f"Fund Bybit API credentials are inactive for fund_id={fund_id}"
        )

    if not account.api_key_encrypted or not account.api_secret_encrypted:
        fund = db.query(Fund).filter(Fund.id == fund_id).first()
        fund_code = fund.code if fund is not None else str(fund_id)

        raise BybitCredentialsError(
            f"Fund Bybit API credentials missing for fund_code={fund_code}"
        )

    api_key = decrypt_bybit_api_value(account.api_key_encrypted)
    api_secret = decrypt_bybit_api_value(account.api_secret_encrypted)

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=settings.BYBIT_MASTER_RECV_WINDOW_MS,
    )