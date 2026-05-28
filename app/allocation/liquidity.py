from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.bybit.client import BybitV5Client


ZERO = Decimal("0")


class LiquidityError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderbookLevel:
    price: Decimal
    qty: Decimal


@dataclass(frozen=True)
class OrderbookSnapshot:
    category: str
    symbol: str
    bids: list[OrderbookLevel]
    asks: list[OrderbookLevel]
    raw: dict[str, Any]

    @property
    def best_bid(self) -> Decimal | None:
        if not self.bids:
            return None
        return self.bids[0].price

    @property
    def best_ask(self) -> Decimal | None:
        if not self.asks:
            return None
        return self.asks[0].price


@dataclass(frozen=True)
class LiquidityCheckResult:
    ok: bool
    side: str
    last_price: Decimal
    best_bid: Decimal | None
    best_ask: Decimal | None
    corridor_pct: Decimal
    corridor_price: Decimal
    available_liquidity_qty: Decimal
    available_liquidity_usdt: Decimal
    required_qty: Decimal
    required_usdt: Decimal
    liquidity_multiplier: Decimal
    error: str | None


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _public_get(client: BybitV5Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
    public_get = getattr(client, "public_get", None)
    if callable(public_get):
        return public_get(path, params)

    return client.get(path, params)


def _result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result_dict(payload)
    rows = result.get("list")

    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    return []


def _parse_orderbook_level(value: Any) -> OrderbookLevel | None:
    if isinstance(value, list | tuple):
        if len(value) < 2:
            return None

        price = dec(value[0])
        qty = dec(value[1])

    elif isinstance(value, dict):
        price = dec(value.get("price") or value.get("p"))
        qty = dec(value.get("qty") or value.get("size") or value.get("q"))

    else:
        return None

    if price <= ZERO or qty <= ZERO:
        return None

    return OrderbookLevel(price=price, qty=qty)


def _sort_bids(levels: list[OrderbookLevel]) -> list[OrderbookLevel]:
    return sorted(levels, key=lambda x: x.price, reverse=True)


def _sort_asks(levels: list[OrderbookLevel]) -> list[OrderbookLevel]:
    return sorted(levels, key=lambda x: x.price)


def get_last_price(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
) -> Decimal:
    normalized_category = _normalize_text(category).lower()
    normalized_symbol = _normalize_text(symbol).upper()

    if not normalized_category:
        raise LiquidityError("category is required")

    if not normalized_symbol:
        raise LiquidityError("symbol is required")

    payload = _public_get(
        client,
        "/v5/market/tickers",
        {
            "category": normalized_category,
            "symbol": normalized_symbol,
        },
    )

    rows = _result_list(payload)
    if not rows:
        raise LiquidityError(
            f"Ticker not found: category={normalized_category}, symbol={normalized_symbol}"
        )

    for row in rows:
        row_symbol = _normalize_text(row.get("symbol")).upper()
        if row_symbol != normalized_symbol:
            continue

        last_price = dec(
            row.get("lastPrice")
            or row.get("last_price")
            or row.get("markPrice")
            or row.get("indexPrice")
        )

        if last_price <= ZERO:
            raise LiquidityError(
                f"Ticker last price is invalid: category={normalized_category}, "
                f"symbol={normalized_symbol}, last_price={last_price}"
            )

        return last_price

    raise LiquidityError(
        f"Ticker symbol not found in response: category={normalized_category}, "
        f"symbol={normalized_symbol}"
    )


def get_orderbook(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    limit: int = 200,
) -> OrderbookSnapshot:
    normalized_category = _normalize_text(category).lower()
    normalized_symbol = _normalize_text(symbol).upper()

    if not normalized_category:
        raise LiquidityError("category is required")

    if not normalized_symbol:
        raise LiquidityError("symbol is required")

    payload = _public_get(
        client,
        "/v5/market/orderbook",
        {
            "category": normalized_category,
            "symbol": normalized_symbol,
            "limit": int(limit),
        },
    )

    result = _result_dict(payload)

    bids_raw = result.get("b") or result.get("bids") or []
    asks_raw = result.get("a") or result.get("asks") or []

    if not isinstance(bids_raw, list):
        bids_raw = []

    if not isinstance(asks_raw, list):
        asks_raw = []

    bids = []
    for row in bids_raw:
        level = _parse_orderbook_level(row)
        if level is not None:
            bids.append(level)

    asks = []
    for row in asks_raw:
        level = _parse_orderbook_level(row)
        if level is not None:
            asks.append(level)

    return OrderbookSnapshot(
        category=normalized_category,
        symbol=normalized_symbol,
        bids=_sort_bids(bids),
        asks=_sort_asks(asks),
        raw=payload,
    )


def _normalize_side(side: str) -> str:
    normalized = _normalize_text(side).lower()

    if normalized in {"buy", "long"}:
        return "Buy"

    if normalized in {"sell", "short"}:
        return "Sell"

    raise LiquidityError(f"Unsupported side: {side}")


def _corridor_fraction(corridor_pct: Decimal) -> Decimal:
    pct = dec(corridor_pct)
    if pct < ZERO:
        raise LiquidityError(f"corridor_pct must be non-negative: {corridor_pct}")

    return pct / Decimal("100")


def check_liquidity_corridor(
    *,
    side: str,
    target_qty: Decimal,
    target_usdt: Decimal,
    last_price: Decimal,
    orderbook: OrderbookSnapshot,
    corridor_pct: Decimal,
    liquidity_multiplier: Decimal = Decimal("1.0"),
) -> LiquidityCheckResult:
    normalized_side = _normalize_side(side)

    target_qty_dec = dec(target_qty)
    target_usdt_dec = dec(target_usdt)
    last_price_dec = dec(last_price)
    multiplier_dec = dec(liquidity_multiplier, default="1.0")

    if target_qty_dec <= ZERO:
        return LiquidityCheckResult(
            ok=False,
            side=normalized_side,
            last_price=last_price_dec,
            best_bid=orderbook.best_bid,
            best_ask=orderbook.best_ask,
            corridor_pct=dec(corridor_pct),
            corridor_price=ZERO,
            available_liquidity_qty=ZERO,
            available_liquidity_usdt=ZERO,
            required_qty=ZERO,
            required_usdt=target_usdt_dec,
            liquidity_multiplier=multiplier_dec,
            error=f"target_qty must be positive: {target_qty_dec}",
        )

    if last_price_dec <= ZERO:
        raise LiquidityError(f"last_price must be positive: {last_price_dec}")

    if multiplier_dec <= ZERO:
        raise LiquidityError(f"liquidity_multiplier must be positive: {multiplier_dec}")

    required_qty = target_qty_dec * multiplier_dec
    required_usdt = target_usdt_dec * multiplier_dec

    frac = _corridor_fraction(dec(corridor_pct))

    if normalized_side == "Buy":
        corridor_price = last_price_dec * (Decimal("1") + frac)
        eligible_levels = [
            level for level in orderbook.asks
            if level.price <= corridor_price
        ]
    else:
        corridor_price = last_price_dec * (Decimal("1") - frac)
        eligible_levels = [
            level for level in orderbook.bids
            if level.price >= corridor_price
        ]

    available_qty = sum((level.qty for level in eligible_levels), ZERO)
    available_usdt = sum((level.qty * level.price for level in eligible_levels), ZERO)

    ok = available_qty >= required_qty
    error = None
    if not ok:
        error = (
            f"Insufficient liquidity inside {corridor_pct}% corridor: "
            f"available_qty={available_qty}, required_qty={required_qty}, "
            f"available_usdt={available_usdt}, required_usdt={required_usdt}"
        )

    return LiquidityCheckResult(
        ok=ok,
        side=normalized_side,
        last_price=last_price_dec,
        best_bid=orderbook.best_bid,
        best_ask=orderbook.best_ask,
        corridor_pct=dec(corridor_pct),
        corridor_price=corridor_price,
        available_liquidity_qty=available_qty,
        available_liquidity_usdt=available_usdt,
        required_qty=required_qty,
        required_usdt=required_usdt,
        liquidity_multiplier=multiplier_dec,
        error=error,
    )