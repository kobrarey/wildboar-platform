from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


class NegativePayoutFlowError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativePayoutGasMock:
    settlement_wallet_bnb_before: Decimal
    required_bnb: Decimal
    ok_gas_wallet_bnb_available: Decimal
    topup_amount_bnb: Decimal
    topup_tx_hash: str | None
    topup_status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativePayoutsMock:
    default_confirmations: int
    tx_hash_prefix: str
    all_confirmed: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativePayoutBalanceRefreshMock:
    settlement_wallet_usdt_before: Decimal | None
    settlement_wallet_usdt_after: Decimal | None
    user_wallet_balances: dict[str, Any] | str | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativePayoutMock:
    mock_id: str
    mock_only: bool
    coin: str
    chain: str
    gas: NegativePayoutGasMock
    payouts: NegativePayoutsMock
    balance_refresh: NegativePayoutBalanceRefreshMock
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativePayoutResult:
    ok: bool
    payout_batch_id: int | None
    settlement_batch_id: int
    bybit_flow_id: int | None
    fund_id: int | None
    fund_code: str | None
    status_before: str | None
    status_after: str | None
    settlement_status_before: str | None
    settlement_status_after: str | None
    payout_leg_count: int | None = None
    confirmed_payout_leg_count: int | None = None
    expected_total_payout_usdt: str | None = None
    confirmed_total_payout_usdt: str | None = None
    idempotent: bool = False
    paused_operator_action_required: bool = False
    operator_action_id: int | None = None
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