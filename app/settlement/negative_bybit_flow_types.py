from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")


class NegativeBybitFlowError(RuntimeError):
    pass


@dataclass(frozen=True)
class UniversalTransferMock:
    status: str
    reconcile_status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WithdrawalMock:
    status: str
    reconcile_status: str
    withdrawal_id: str | None
    tx_hash: str | None
    fee_type: int
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SettlementWalletReceiptMock:
    status: str
    received_amount_matches: bool
    received_amount_usdt: Decimal | None
    tx_hash: str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WhitelistMock:
    internal_db_whitelist_passed: bool
    bybit_address_whitelist_mock_passed: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativeBybitFlowMock:
    mock_id: str
    mock_only: bool
    master_uid: str
    fund_sub_uid: str
    universal_transfer: UniversalTransferMock
    withdrawal: WithdrawalMock
    settlement_wallet_receipt: SettlementWalletReceiptMock
    whitelist: WhitelistMock
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativeBybitFlowResult:
    ok: bool
    flow_id: int | None
    settlement_batch_id: int
    sale_batch_id: int | None
    fund_id: int | None
    fund_code: str | None
    status_before: str | None
    status_after: str | None
    settlement_status_before: str | None
    settlement_status_after: str | None
    universal_transfer_id: str | None
    withdrawal_request_id: str | None
    settlement_wallet_address: str | None
    idempotent: bool = False
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(asdict(self))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}