from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.allocation.idempotency import (
    make_mock_bybit_order_id,
    make_slice_order_link_id,
)
from app.allocation.instrument_info import round_price_to_tick, round_qty_down


ZERO = Decimal("0")
ONE = Decimal("1")


class BybitOrderPayloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtectedMarketOrderPayload:
    payload: dict[str, Any]
    order_link_id: str
    spot_buy_by_quote: bool


@dataclass(frozen=True)
class SlicedIocPayload:
    slice_no: int
    payload: dict[str, Any]
    order_link_id: str
    limit_price: Decimal
    qty: Decimal


@dataclass(frozen=True)
class MockExecutionResult:
    order_id: str | None
    status: str
    filled_qty: Decimal
    filled_usdt: Decimal
    avg_fill_price: Decimal | None
    fill_ratio: Decimal
    residual_usdt: Decimal
    executed_suborders: int
    raw: dict[str, Any]


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _normalize_side(side: str) -> str:
    raw = str(side or "").strip().lower()

    if raw in {"buy", "long"}:
        return "Buy"

    if raw in {"sell", "short"}:
        return "Sell"

    raise BybitOrderPayloadError(f"Unsupported side: {side}")


def _normalize_category(category: str) -> str:
    raw = str(category or "").strip().lower()
    if not raw:
        raise BybitOrderPayloadError("category is required")
    return raw


def _normalize_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        raise BybitOrderPayloadError("symbol is required")
    return raw


def _decimal_str(value: Decimal | int | str | None) -> str:
    return str(dec(value))


def compute_fill_ratio(
    *,
    target_qty: Decimal | None,
    target_usdt: Decimal | None,
    filled_qty: Decimal | None,
    filled_usdt: Decimal | None,
    spot_buy_by_quote: bool = False,
) -> Decimal:
    target_qty_dec = dec(target_qty)
    target_usdt_dec = dec(target_usdt)
    filled_qty_dec = dec(filled_qty)
    filled_usdt_dec = dec(filled_usdt)

    if spot_buy_by_quote or target_qty_dec <= ZERO:
        if target_usdt_dec <= ZERO:
            return ZERO
        return filled_usdt_dec / target_usdt_dec

    return filled_qty_dec / target_qty_dec


def _residual_usdt(
    *,
    target_usdt: Decimal,
    fill_ratio: Decimal,
) -> Decimal:
    ratio = max(ZERO, min(ONE, dec(fill_ratio)))
    return target_usdt * (ONE - ratio)


def build_protected_market_order_payload(
    *,
    category: str,
    symbol: str,
    side: str,
    target_qty: Decimal | None,
    target_usdt: Decimal | None,
    order_link_id: str,
    slippage_pct: Decimal,
) -> ProtectedMarketOrderPayload:
    normalized_category = _normalize_category(category)
    normalized_symbol = _normalize_symbol(symbol)
    normalized_side = _normalize_side(side)

    target_qty_dec = dec(target_qty)
    target_usdt_dec = dec(target_usdt)
    slippage_dec = dec(slippage_pct)

    if slippage_dec <= ZERO:
        raise BybitOrderPayloadError(f"slippage_pct must be positive: {slippage_pct}")

    spot_buy_by_quote = (
        normalized_category == "spot"
        and normalized_side == "Buy"
        and target_usdt_dec > ZERO
    )

    if spot_buy_by_quote:
        qty = target_usdt_dec
    else:
        qty = target_qty_dec

    if qty <= ZERO:
        raise BybitOrderPayloadError(
            f"Order quantity must be positive: category={normalized_category}, "
            f"side={normalized_side}, target_qty={target_qty_dec}, target_usdt={target_usdt_dec}"
        )

    payload: dict[str, Any] = {
        "category": normalized_category,
        "symbol": normalized_symbol,
        "side": normalized_side,
        "orderType": "Market",
        "qty": _decimal_str(qty),
        "orderLinkId": order_link_id,
        "slippageToleranceType": "Percent",
        "slippageTolerance": _decimal_str(slippage_dec),
    }

    if spot_buy_by_quote:
        payload["marketUnit"] = "quoteCoin"

    return ProtectedMarketOrderPayload(
        payload=payload,
        order_link_id=order_link_id,
        spot_buy_by_quote=spot_buy_by_quote,
    )


def build_sliced_ioc_payloads(
    *,
    allocation_batch_id: int,
    leg_id: int,
    category: str,
    symbol: str,
    side: str,
    target_qty: Decimal,
    last_price: Decimal,
    best_bid: Decimal | None,
    best_ask: Decimal | None,
    tick_size: Decimal,
    qty_step: Decimal,
    slices: int,
    corridor_pct: Decimal,
    chase_bps: Decimal,
) -> list[SlicedIocPayload]:
    normalized_category = _normalize_category(category)
    normalized_symbol = _normalize_symbol(symbol)
    normalized_side = _normalize_side(side)

    target_qty_dec = dec(target_qty)
    last_price_dec = dec(last_price)
    tick_size_dec = dec(tick_size)
    qty_step_dec = dec(qty_step)
    slices_int = int(slices)
    corridor_fraction = dec(corridor_pct) / Decimal("100")
    chase_fraction = dec(chase_bps) / Decimal("10000")

    if slices_int <= 0:
        raise BybitOrderPayloadError(f"slices must be positive: {slices}")

    if target_qty_dec <= ZERO:
        raise BybitOrderPayloadError(f"target_qty must be positive: {target_qty}")

    if last_price_dec <= ZERO:
        raise BybitOrderPayloadError(f"last_price must be positive: {last_price}")

    if normalized_side == "Buy":
        if best_ask is None:
            raise BybitOrderPayloadError("best_ask is required for Buy sliced IOC")

        corridor_limit = last_price_dec * (ONE + corridor_fraction)
        chase_limit = dec(best_ask) * (ONE + chase_fraction)
        raw_limit_price = min(chase_limit, corridor_limit)
        limit_price = round_price_to_tick(raw_limit_price, tick_size_dec, "Buy")

    else:
        if best_bid is None:
            raise BybitOrderPayloadError("best_bid is required for Sell sliced IOC")

        corridor_limit = last_price_dec * (ONE - corridor_fraction)
        chase_limit = dec(best_bid) * (ONE - chase_fraction)
        raw_limit_price = max(chase_limit, corridor_limit)
        limit_price = round_price_to_tick(raw_limit_price, tick_size_dec, "Sell")

    if limit_price <= ZERO:
        raise BybitOrderPayloadError(f"limit_price must be positive: {limit_price}")

    raw_slice_qty = target_qty_dec / Decimal(slices_int)

    payloads: list[SlicedIocPayload] = []
    remaining_qty = target_qty_dec

    for slice_no in range(1, slices_int + 1):
        if remaining_qty <= ZERO:
            break

        if slice_no == slices_int:
            slice_qty = round_qty_down(remaining_qty, qty_step_dec)
        else:
            slice_qty = round_qty_down(raw_slice_qty, qty_step_dec)

        if slice_qty <= ZERO:
            continue

        remaining_qty -= slice_qty

        order_link_id = make_slice_order_link_id(
            allocation_batch_id,
            leg_id,
            slice_no,
        )

        payload = {
            "category": normalized_category,
            "symbol": normalized_symbol,
            "side": normalized_side,
            "orderType": "Limit",
            "timeInForce": "IOC",
            "qty": _decimal_str(slice_qty),
            "price": _decimal_str(limit_price),
            "orderLinkId": order_link_id,
        }

        payloads.append(
            SlicedIocPayload(
                slice_no=slice_no,
                payload=payload,
                order_link_id=order_link_id,
                limit_price=limit_price,
                qty=slice_qty,
            )
        )

    if not payloads:
        raise BybitOrderPayloadError("No sliced IOC payloads were created after qty rounding")

    return payloads


def simulate_protected_market_order_execution(
    *,
    payload: ProtectedMarketOrderPayload,
    target_qty: Decimal | None,
    target_usdt: Decimal | None,
    avg_fill_price: Decimal,
    mock_fill_ratio: Decimal = Decimal("1.0"),
    min_fill_ratio: Decimal = Decimal("0.90"),
) -> MockExecutionResult:
    ratio = max(ZERO, min(ONE, dec(mock_fill_ratio)))
    target_qty_dec = dec(target_qty)
    target_usdt_dec = dec(target_usdt)
    avg_price_dec = dec(avg_fill_price)

    if payload.spot_buy_by_quote:
        filled_usdt = target_usdt_dec * ratio
        filled_qty = ZERO
        if avg_price_dec > ZERO:
            filled_qty = filled_usdt / avg_price_dec
    else:
        filled_qty = target_qty_dec * ratio
        filled_usdt = target_usdt_dec * ratio

    fill_ratio = compute_fill_ratio(
        target_qty=target_qty_dec,
        target_usdt=target_usdt_dec,
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        spot_buy_by_quote=payload.spot_buy_by_quote,
    )

    status = "filled" if fill_ratio >= dec(min_fill_ratio) else "partial_filled_residualized"

    return MockExecutionResult(
        order_id=make_mock_bybit_order_id(payload.order_link_id),
        status=status,
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        avg_fill_price=avg_price_dec if avg_price_dec > ZERO else None,
        fill_ratio=fill_ratio,
        residual_usdt=_residual_usdt(
            target_usdt=target_usdt_dec,
            fill_ratio=fill_ratio,
        ),
        executed_suborders=1,
        raw={
            "mode": "mock_protected_market",
            "payload": payload.payload,
        },
    )


def simulate_sliced_ioc_execution(
    *,
    payloads: list[SlicedIocPayload],
    target_qty: Decimal,
    target_usdt: Decimal,
    avg_fill_price: Decimal,
    min_fill_ratio: Decimal,
    per_slice_fill_ratio: Decimal = Decimal("1.0"),
) -> MockExecutionResult:
    target_qty_dec = dec(target_qty)
    target_usdt_dec = dec(target_usdt)
    avg_price_dec = dec(avg_fill_price)
    slice_ratio = max(ZERO, min(ONE, dec(per_slice_fill_ratio)))

    filled_qty = ZERO
    executed_suborders = 0
    used_payloads: list[dict[str, Any]] = []

    for item in payloads:
        if compute_fill_ratio(
            target_qty=target_qty_dec,
            target_usdt=target_usdt_dec,
            filled_qty=filled_qty,
            filled_usdt=filled_qty * avg_price_dec,
            spot_buy_by_quote=False,
        ) >= dec(min_fill_ratio):
            break

        slice_filled_qty = item.qty * slice_ratio
        filled_qty += slice_filled_qty
        executed_suborders += 1
        used_payloads.append(item.payload)

    filled_usdt = filled_qty * avg_price_dec

    fill_ratio = compute_fill_ratio(
        target_qty=target_qty_dec,
        target_usdt=target_usdt_dec,
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        spot_buy_by_quote=False,
    )

    status = "filled" if fill_ratio >= dec(min_fill_ratio) else "partial_filled_residualized"

    return MockExecutionResult(
        order_id=None,
        status=status,
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        avg_fill_price=avg_price_dec if avg_price_dec > ZERO else None,
        fill_ratio=fill_ratio,
        residual_usdt=_residual_usdt(
            target_usdt=target_usdt_dec,
            fill_ratio=fill_ratio,
        ),
        executed_suborders=executed_suborders,
        raw={
            "mode": "mock_sliced_ioc",
            "used_payloads": used_payloads,
        },
    )