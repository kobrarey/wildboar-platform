from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.allocation.idempotency import make_mock_strategy_id
from app.allocation.instrument_info import round_price_to_tick


ZERO = Decimal("0")
ONE = Decimal("1")

_DELIVERY_INVERSE_RE = re.compile(r"USD[FGHJKMNQUVXZ]\d{2}$")
_DELIVERY_LINEAR_RE = re.compile(r"-\d{2}[A-Z]{3}\d{2}$")


class BybitStrategyPayloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeIcebergStrategyPayload:
    supported: bool
    payload: dict[str, Any] | None
    strategy_ref: str
    strategy_category: str | None
    reason: str | None


@dataclass(frozen=True)
class MockStrategyCreateResult:
    strategy_id: str
    status: str
    payload: dict[str, Any]


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _normalize_category(category: str) -> str:
    raw = str(category or "").strip().lower()
    if not raw:
        raise BybitStrategyPayloadError("category is required")
    return raw


def _normalize_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        raise BybitStrategyPayloadError("symbol is required")
    return raw


def _normalize_side(side: str) -> str:
    raw = str(side or "").strip().lower()

    if raw in {"buy", "long"}:
        return "Buy"

    if raw in {"sell", "short"}:
        return "Sell"

    raise BybitStrategyPayloadError(f"Unsupported side: {side}")


def _decimal_str(value: Decimal | int | str | None) -> str:
    return str(dec(value))


def _is_linear_future_symbol(symbol: str) -> bool:
    return bool(_DELIVERY_LINEAR_RE.search(symbol))


def _is_inverse_future_symbol(symbol: str) -> bool:
    return bool(_DELIVERY_INVERSE_RE.search(symbol))


def map_native_iceberg_strategy_category(
    *,
    category: str,
    symbol: str,
    settle_coin: str | None = None,
) -> tuple[bool, str | None, str | None]:
    normalized_category = _normalize_category(category)
    normalized_symbol = _normalize_symbol(symbol)
    normalized_settle = str(settle_coin or "").strip().upper()

    if normalized_category == "spot":
        return True, "UTA_SPOT", None

    if normalized_category == "option":
        return False, None, "Bybit native Iceberg Strategy does not support option category"

    if normalized_category == "linear":
        if normalized_settle == "USDC":
            if _is_linear_future_symbol(normalized_symbol):
                return False, None, "USDC futures mapping is not enabled in Stage 22.3"
            return True, "UTA_USDC", None

        if _is_linear_future_symbol(normalized_symbol):
            return True, "UTA_USDT_FUTURE", None

        return True, "UTA_USDT", None

    if normalized_category == "inverse":
        if _is_inverse_future_symbol(normalized_symbol):
            return True, "UTA_INVERSE_FUTURE", None

        return True, "UTA_INVERSE", None

    return False, None, f"Unsupported category for native iceberg: {normalized_category}"


def build_native_iceberg_strategy_payload(
    *,
    category: str,
    symbol: str,
    side: str,
    target_qty: Decimal | None,
    target_usdt: Decimal | None,
    last_price: Decimal,
    tick_size: Decimal,
    order_count: int,
    strategy_ref: str,
    settle_coin: str | None = None,
) -> NativeIcebergStrategyPayload:
    normalized_category = _normalize_category(category)
    normalized_symbol = _normalize_symbol(symbol)
    normalized_side = _normalize_side(side)

    supported, strategy_category, reason = map_native_iceberg_strategy_category(
        category=normalized_category,
        symbol=normalized_symbol,
        settle_coin=settle_coin,
    )

    if not supported:
        return NativeIcebergStrategyPayload(
            supported=False,
            payload=None,
            strategy_ref=strategy_ref,
            strategy_category=strategy_category,
            reason=reason,
        )

    target_qty_dec = dec(target_qty)
    target_usdt_dec = dec(target_usdt)
    last_price_dec = dec(last_price)
    tick_size_dec = dec(tick_size)

    if order_count <= 0:
        raise BybitStrategyPayloadError(f"order_count must be positive: {order_count}")

    if last_price_dec <= ZERO:
        raise BybitStrategyPayloadError(f"last_price must be positive: {last_price}")

    if normalized_side == "Buy":
        max_chase_raw = last_price_dec * Decimal("1.01")
        max_chase_price = round_price_to_tick(max_chase_raw, tick_size_dec, "Buy")
    else:
        max_chase_raw = last_price_dec * Decimal("0.99")
        max_chase_price = round_price_to_tick(max_chase_raw, tick_size_dec, "Sell")

    payload: dict[str, Any] = {
        "category": strategy_category,
        "symbol": normalized_symbol,
        "side": normalized_side,
        "strategyType": "iceberg",
        "orderCount": int(order_count),
        "maxChasePrice": _decimal_str(max_chase_price),
        "clientRef": strategy_ref,
        "postOnly": False,
    }

    if normalized_category == "spot" and normalized_side == "Buy" and target_usdt_dec > ZERO:
        payload["positionValue"] = _decimal_str(target_usdt_dec)
    else:
        if target_qty_dec <= ZERO:
            raise BybitStrategyPayloadError(f"target_qty must be positive: {target_qty}")
        payload["size"] = _decimal_str(target_qty_dec)

    return NativeIcebergStrategyPayload(
        supported=True,
        payload=payload,
        strategy_ref=strategy_ref,
        strategy_category=strategy_category,
        reason=None,
    )


def simulate_native_iceberg_strategy_create(
    *,
    payload: NativeIcebergStrategyPayload,
) -> MockStrategyCreateResult:
    if not payload.supported or payload.payload is None:
        raise BybitStrategyPayloadError(f"Cannot simulate unsupported native strategy: {payload.reason}")

    return MockStrategyCreateResult(
        strategy_id=make_mock_strategy_id(payload.strategy_ref),
        status="native_iceberg_processing",
        payload=payload.payload,
    )