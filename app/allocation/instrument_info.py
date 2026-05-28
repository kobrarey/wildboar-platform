from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any

from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
    EXECUTION_MODE_PLANNED,
    EXECUTION_MODE_SKIPPED,
)
from app.bybit.client import BybitV5Client


ZERO = Decimal("0")


class InstrumentInfoError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    category: str
    status: str
    base_coin: str | None
    quote_coin: str | None

    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal
    max_order_qty: Decimal | None
    min_order_amt: Decimal | None
    max_market_order_qty: Decimal | None
    max_limit_order_qty: Decimal | None

    raw: dict[str, Any]


@dataclass(frozen=True)
class InstrumentValidationResult:
    ok: bool
    status: str
    execution_mode: str
    rounded_qty: Decimal | None
    rounded_price: Decimal | None
    min_order_qty: Decimal | None
    min_order_amt: Decimal | None
    error: str | None
    warnings: list[str]


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _nullable_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None

    out = dec(value)
    return out


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, dict):
        return []

    rows = result.get("list")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    return []


def _public_get(client: BybitV5Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    public_get = getattr(client, "public_get", None)
    if callable(public_get):
        return public_get(path, params)

    return client.get(path, params)


def parse_instrument_info_row(
    row: dict[str, Any],
    *,
    fallback_category: str,
) -> InstrumentInfo:
    if not isinstance(row, dict):
        raise InstrumentInfoError("Instrument info row must be a dict")

    price_filter = row.get("priceFilter")
    if not isinstance(price_filter, dict):
        price_filter = {}

    lot_filter = row.get("lotSizeFilter")
    if not isinstance(lot_filter, dict):
        lot_filter = {}

    symbol = _normalize_text(row.get("symbol")).upper()
    if not symbol:
        raise InstrumentInfoError("Instrument info row does not contain symbol")

    category = _normalize_text(row.get("category") or fallback_category).lower()
    status = _normalize_text(row.get("status"))

    tick_size = dec(price_filter.get("tickSize"))
    qty_step = dec(lot_filter.get("qtyStep"))
    min_order_qty = dec(lot_filter.get("minOrderQty"))

    return InstrumentInfo(
        symbol=symbol,
        category=category,
        status=status,
        base_coin=_normalize_text(row.get("baseCoin")).upper() or None,
        quote_coin=_normalize_text(row.get("quoteCoin")).upper() or None,
        tick_size=tick_size,
        qty_step=qty_step,
        min_order_qty=min_order_qty,
        max_order_qty=_nullable_dec(lot_filter.get("maxOrderQty")),
        min_order_amt=_nullable_dec(lot_filter.get("minOrderAmt")),
        max_market_order_qty=_nullable_dec(lot_filter.get("maxMarketOrderQty")),
        max_limit_order_qty=_nullable_dec(lot_filter.get("maxLimitOrderQty")),
        raw=row,
    )


def get_instrument_info(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
) -> InstrumentInfo:
    normalized_category = _normalize_text(category).lower()
    normalized_symbol = _normalize_text(symbol).upper()

    if not normalized_category:
        raise InstrumentInfoError("category is required")

    if not normalized_symbol:
        raise InstrumentInfoError("symbol is required")

    payload = _public_get(
        client,
        "/v5/market/instruments-info",
        {
            "category": normalized_category,
            "symbol": normalized_symbol,
        },
    )

    rows = _result_list(payload)
    if not rows:
        raise InstrumentInfoError(
            f"Instrument not found: category={normalized_category}, symbol={normalized_symbol}"
        )

    for row in rows:
        row_symbol = _normalize_text(row.get("symbol")).upper()
        if row_symbol == normalized_symbol:
            return parse_instrument_info_row(
                row,
                fallback_category=normalized_category,
            )

    raise InstrumentInfoError(
        f"Instrument not found in response list: category={normalized_category}, symbol={normalized_symbol}"
    )


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    value = dec(value)
    step = dec(step)

    if value <= ZERO:
        return ZERO

    if step <= ZERO:
        return value

    units = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    value = dec(value)
    step = dec(step)

    if value <= ZERO:
        return ZERO

    if step <= ZERO:
        return value

    units = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def round_qty_down(qty: Decimal | int | str, qty_step: Decimal | int | str) -> Decimal:
    """
    Quantity is always rounded down. Never round up above the risk budget.
    """
    return _floor_to_step(dec(qty), dec(qty_step))


def round_price_to_tick(
    price: Decimal | int | str,
    tick_size: Decimal | int | str,
    side: str,
) -> Decimal:
    """
    Buy price: round down, so price is not above corridor.
    Sell price: round up, so price is not below corridor.
    """
    price_dec = dec(price)
    tick_dec = dec(tick_size)
    normalized_side = _normalize_text(side).lower()

    if normalized_side in {"sell", "short"}:
        return _ceil_to_step(price_dec, tick_dec)

    return _floor_to_step(price_dec, tick_dec)


def _leg_target_qty(leg: Any) -> Decimal:
    return dec(getattr(leg, "target_qty", None))


def _leg_target_usdt(leg: Any) -> Decimal:
    return dec(getattr(leg, "target_usdt", None))


def _leg_symbol(leg: Any) -> str:
    return _normalize_text(getattr(leg, "symbol", None)).upper()


def _leg_category(leg: Any) -> str:
    return _normalize_text(getattr(leg, "category", None)).lower()


def _leg_group(leg: Any) -> str:
    return _normalize_text(getattr(leg, "leg_group", None)).lower()


def _leg_type(leg: Any) -> str:
    return _normalize_text(getattr(leg, "leg_type", None)).lower()


def _is_spot_buy_by_quote_leg(leg: Any, info: InstrumentInfo) -> bool:
    leg_group = _leg_group(leg)
    leg_type = _leg_type(leg)

    return (
        info.category == "spot"
        and leg_group == "spot"
        and leg_type == "spot_buy"
        and _leg_target_usdt(leg) > ZERO
    )


def validate_instrument_for_leg(
    leg: Any,
    info: InstrumentInfo,
) -> InstrumentValidationResult:
    warnings: list[str] = []

    if info.status.lower() != "trading":
        return InstrumentValidationResult(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
            execution_mode=EXECUTION_MODE_SKIPPED,
            rounded_qty=None,
            rounded_price=None,
            min_order_qty=info.min_order_qty,
            min_order_amt=info.min_order_amt,
            error=f"Instrument is not Trading: symbol={info.symbol}, status={info.status}",
            warnings=[],
        )

    leg_symbol = _leg_symbol(leg)
    if leg_symbol and leg_symbol != info.symbol:
        return InstrumentValidationResult(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
            execution_mode=EXECUTION_MODE_SKIPPED,
            rounded_qty=None,
            rounded_price=None,
            min_order_qty=info.min_order_qty,
            min_order_amt=info.min_order_amt,
            error=f"Leg symbol does not match instrument info: leg={leg_symbol}, info={info.symbol}",
            warnings=[],
        )

    leg_category = _leg_category(leg)
    if leg_category and leg_category not in {"wallet", "funding", "earn"}:
        if leg_category != info.category:
            warnings.append(
                f"Leg category differs from instrument category: leg={leg_category}, info={info.category}"
            )

    target_qty = _leg_target_qty(leg)
    target_usdt = _leg_target_usdt(leg)

    rounded_qty: Decimal | None = None

    if _is_spot_buy_by_quote_leg(leg, info):
        # Spot market buy can use quoteCoin amount in bybit_orders.py.
        # Quantity is not required here, but minOrderAmt must pass.
        if info.min_order_amt is not None and target_usdt < info.min_order_amt:
            return InstrumentValidationResult(
                ok=False,
                status=ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
                execution_mode=EXECUTION_MODE_SKIPPED,
                rounded_qty=None,
                rounded_price=None,
                min_order_qty=info.min_order_qty,
                min_order_amt=info.min_order_amt,
                error=(
                    f"Spot quote amount below minOrderAmt: "
                    f"target_usdt={target_usdt}, min_order_amt={info.min_order_amt}"
                ),
                warnings=warnings,
            )

    else:
        rounded_qty = round_qty_down(target_qty, info.qty_step)

        if rounded_qty <= ZERO:
            return InstrumentValidationResult(
                ok=False,
                status=ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
                execution_mode=EXECUTION_MODE_SKIPPED,
                rounded_qty=rounded_qty,
                rounded_price=None,
                min_order_qty=info.min_order_qty,
                min_order_amt=info.min_order_amt,
                error=f"Rounded quantity is zero: target_qty={target_qty}, qty_step={info.qty_step}",
                warnings=warnings,
            )

        if info.min_order_qty > ZERO and rounded_qty < info.min_order_qty:
            return InstrumentValidationResult(
                ok=False,
                status=ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
                execution_mode=EXECUTION_MODE_SKIPPED,
                rounded_qty=rounded_qty,
                rounded_price=None,
                min_order_qty=info.min_order_qty,
                min_order_amt=info.min_order_amt,
                error=(
                    f"Rounded quantity below minOrderQty: "
                    f"rounded_qty={rounded_qty}, min_order_qty={info.min_order_qty}"
                ),
                warnings=warnings,
            )

    if info.max_order_qty is not None and rounded_qty is not None and rounded_qty > info.max_order_qty:
        warnings.append(
            f"rounded_qty exceeds maxOrderQty: rounded_qty={rounded_qty}, max_order_qty={info.max_order_qty}"
        )

    if (
        info.max_market_order_qty is not None
        and rounded_qty is not None
        and rounded_qty > info.max_market_order_qty
    ):
        warnings.append(
            "rounded_qty exceeds maxMarketOrderQty; execution engine should use "
            f"native iceberg or sliced IOC: rounded_qty={rounded_qty}, "
            f"max_market_order_qty={info.max_market_order_qty}"
        )

    if (
        info.max_limit_order_qty is not None
        and rounded_qty is not None
        and rounded_qty > info.max_limit_order_qty
    ):
        warnings.append(
            "rounded_qty exceeds maxLimitOrderQty; execution engine should split order: "
            f"rounded_qty={rounded_qty}, max_limit_order_qty={info.max_limit_order_qty}"
        )

    return InstrumentValidationResult(
        ok=True,
        status=ALLOCATION_LEG_STATUS_PLANNED,
        execution_mode=EXECUTION_MODE_PLANNED,
        rounded_qty=rounded_qty,
        rounded_price=None,
        min_order_qty=info.min_order_qty,
        min_order_amt=info.min_order_amt,
        error=None,
        warnings=warnings,
    )