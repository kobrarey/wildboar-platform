from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.bybit.client import BybitApiError, BybitV5Client
from app.config import settings
from app.models import FundBybitAccount


class FundBybitClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class FundBybitClientContext:
    fund_id: int
    fund_bybit_account_id: int
    bybit_sub_uid: str
    bybit_subaccount_name: str | None
    api_key_label: str | None
    client: BybitV5Client

    def safe_dict(self) -> dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "fund_bybit_account_id": self.fund_bybit_account_id,
            "bybit_sub_uid": self.bybit_sub_uid,
            "bybit_subaccount_name": self.bybit_subaccount_name,
            "api_key_label": self.api_key_label,
        }


def _get_fernet() -> Fernet:
    key = str(settings.BYBIT_API_ENC_KEY or "").strip()
    if not key or key in {"CHANGE_ME", "CHANGE_ME_TO_FERNET_KEY"}:
        raise FundBybitClientError("BYBIT_API_ENC_KEY is not configured")

    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise FundBybitClientError("BYBIT_API_ENC_KEY is not a valid Fernet key") from exc


def decrypt_bybit_api_value(encrypted_value: str | None) -> str:
    if not encrypted_value:
        raise FundBybitClientError("Encrypted Bybit API value is empty")

    try:
        raw = _get_fernet().decrypt(str(encrypted_value).encode("utf-8"))
    except InvalidToken as exc:
        raise FundBybitClientError("Encrypted Bybit API value cannot be decrypted") from exc
    except Exception as exc:
        raise FundBybitClientError("Failed to decrypt Bybit API value") from exc

    value = raw.decode("utf-8").strip()
    if not value:
        raise FundBybitClientError("Decrypted Bybit API value is empty")

    return value


def get_active_fund_bybit_account(
    db: Session,
    *,
    fund_id: int,
) -> FundBybitAccount:
    account = (
        db.query(FundBybitAccount)
        .filter(FundBybitAccount.fund_id == int(fund_id))
        .filter(FundBybitAccount.is_active == True)  # noqa: E712
        .filter(FundBybitAccount.api_key_is_active == True)  # noqa: E712
        .order_by(FundBybitAccount.id.asc())
        .first()
    )

    if account is None:
        raise FundBybitClientError(
            f"No active Bybit API account for fund_id={fund_id}"
        )

    if not account.api_key_encrypted or not account.api_secret_encrypted:
        raise FundBybitClientError(
            f"Active Bybit API account has empty encrypted credentials: "
            f"fund_id={fund_id}, account_id={account.id}"
        )

    return account


def build_fund_bybit_client(
    db: Session,
    *,
    fund_id: int,
) -> FundBybitClientContext:
    """
    Build a Bybit V5 client for the fund subaccount.

    Safety:
    - Does not perform any external API call.
    - Does not use master API credentials.
    - Requires active fund_bybit_accounts row with active API key.
    - Decrypts credentials only at runtime.
    """
    account = get_active_fund_bybit_account(db, fund_id=fund_id)

    api_key = decrypt_bybit_api_value(account.api_key_encrypted)
    api_secret = decrypt_bybit_api_value(account.api_secret_encrypted)

    try:
        client = BybitV5Client(
            api_key=api_key,
            api_secret=api_secret,
        )
    except BybitApiError as exc:
        raise FundBybitClientError(
            f"Failed to build fund Bybit client: fund_id={fund_id}, "
            f"account_id={account.id}"
        ) from exc

    return FundBybitClientContext(
        fund_id=int(fund_id),
        fund_bybit_account_id=int(account.id),
        bybit_sub_uid=str(account.bybit_sub_uid),
        bybit_subaccount_name=account.bybit_subaccount_name,
        api_key_label=account.api_key_label,
        client=client,
    )