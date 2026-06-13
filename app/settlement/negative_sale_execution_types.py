from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")


ONE = Decimal("1")


HUNDRED = Decimal("100")


class NegativeSaleExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SymbolExecutionMock:
    symbol: str
    category: str | None
    last_price: Decimal
    best_bid: Decimal
    best_ask: Decimal
    available_liquidity_usdt: Decimal
    available_liquidity_qty: Decimal
    native_strategy_supported: bool
    mock_fill_ratio: Decimal
    fee_usdt: Decimal
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EarnExecutionMock:
    initial_redeemable_usdt: Decimal
    initial_redeem_fill_usdt: Decimal
    additional_redeemable_usdt: Decimal
    additional_redeem_fill_usdt: Decimal


@dataclass(frozen=True)
class ExtraSaleExecutionMock:
    enabled: bool
    preferred_symbol: str | None
    last_price: Decimal
    best_bid: Decimal
    best_ask: Decimal
    available_liquidity_usdt: Decimal
    available_liquidity_qty: Decimal
    mock_fill_ratio: Decimal
    fee_usdt: Decimal
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NegativeSaleExecutionMock:
    mock_id: str
    mock_only: bool
    sell_corridor_pct: Decimal
    fill_acceptance_pct: Decimal
    slices: int
    max_active_strategy_orders: int
    extra_largest_asset_buffer_pct: Decimal
    usdt_earn: EarnExecutionMock
    symbols: dict[str, SymbolExecutionMock]
    extra_sale: ExtraSaleExecutionMock
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LegExecutionComputation:
    leg_id: int
    leg_index: int
    deterministic_key: str
    symbol: str | None
    category: str | None
    planned_cash_usdt: Decimal
    actual_execution_mode: str
    execution_round: str
    status: str
    transition_status: str
    last_price: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    corridor_pct: Decimal | None
    available_liquidity_usdt: Decimal
    available_liquidity_qty: Decimal
    filled_qty: Decimal | None
    filled_usdt: Decimal
    avg_fill_price: Decimal | None
    fill_ratio: Decimal
    unfilled_usdt: Decimal
    fee_usdt: Decimal
    cash_delta_usdt: Decimal
    planned_suborders: int | None
    executed_suborders: int | None
    suborders_json: dict[str, Any] | None
    mock_execution_json: dict[str, Any]
    execution_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(asdict(self))


@dataclass(frozen=True)
class NegativeSaleExecutionResult:
    ok: bool
    sale_batch_id: int
    settlement_batch_id: int
    fund_id: int
    fund_code: str
    status_before: str
    status_after: str
    settlement_status_before: str
    settlement_status_after: str
    final_available_usdt: Decimal | None
    final_shortage_usdt: Decimal | None
    final_surplus_usdt: Decimal | None
    executed_leg_count: int
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


def _max_zero(value: Decimal) -> Decimal:
    return value if value > ZERO else ZERO

