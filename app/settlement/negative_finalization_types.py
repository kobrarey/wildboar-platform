from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


class NegativeFinalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativeFinalizationResult:
    ok: bool
    finalization_batch_id: int | None
    settlement_batch_id: int
    payout_batch_id: int | None
    fund_id: int | None
    fund_code: str | None
    status_before: str | None
    status_after: str | None
    settlement_status_before: str | None
    settlement_status_after: str | None
    buy_order_count: int | None = None
    redeem_order_count: int | None = None
    success_order_count: int | None = None
    shares_outstanding_before: str | None = None
    shares_outstanding_after: str | None = None
    total_buy_usdt: str | None = None
    total_buy_shares: str | None = None
    total_redeem_shares: str | None = None
    planned_net_shares_change: str | None = None
    actual_net_shares_change: str | None = None
    total_net_user_payout_usdt: str | None = None
    total_partial_month_fee_usdt: str | None = None
    accounting_finalized_at: str | None = None
    pricing_unlocked_at: str | None = None
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

    if isinstance(value, (list, tuple, set)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}