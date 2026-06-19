from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.bybit.client import BybitV5Client


class BybitOrderExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class BybitOrderResult:
    category: str
    symbol: str
    order_id: str | None
    order_link_id: str
    status: str | None
    side: str | None
    order_type: str | None
    qty: Decimal | None
    cum_exec_qty: Decimal | None
    cum_exec_value: Decimal | None
    avg_price: Decimal | None
    raw: dict[str, Any]


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result_dict(payload)
    rows = result.get("list")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _order_result_from_raw(
    *,
    category: str,
    symbol: str,
    order_link_id: str,
    raw: dict[str, Any],
) -> BybitOrderResult:
    return BybitOrderResult(
        category=str(raw.get("category") or category),
        symbol=str(raw.get("symbol") or symbol),
        order_id=raw.get("orderId"),
        order_link_id=str(raw.get("orderLinkId") or order_link_id),
        status=raw.get("orderStatus"),
        side=raw.get("side"),
        order_type=raw.get("orderType"),
        qty=_dec(raw.get("qty")),
        cum_exec_qty=_dec(raw.get("cumExecQty")),
        cum_exec_value=_dec(raw.get("cumExecValue")),
        avg_price=_dec(raw.get("avgPrice")),
        raw=dict(raw),
    )


def create_market_sell_order(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    qty: Decimal,
    order_link_id: str,
    reduce_only: bool | None = None,
    market_unit: str | None = None,
) -> BybitOrderResult:
    payload: dict[str, Any] = {
        "category": category,
        "symbol": symbol,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(qty),
        "orderLinkId": order_link_id,
    }

    if reduce_only is not None:
        payload["reduceOnly"] = bool(reduce_only)

    if market_unit:
        payload["marketUnit"] = market_unit

    response = client.post("/v5/order/create", payload)
    result = _result_dict(response)

    raw = {
        **result,
        "category": category,
        "symbol": symbol,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(qty),
        "orderLinkId": order_link_id,
    }

    return _order_result_from_raw(
        category=category,
        symbol=symbol,
        order_link_id=order_link_id,
        raw=raw,
    )


def query_order_by_link_id(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    order_link_id: str,
) -> BybitOrderResult | None:
    response = client.get(
        "/v5/order/realtime",
        {
            "category": category,
            "symbol": symbol,
            "orderLinkId": order_link_id,
        },
    )

    rows = _result_list(response)
    if not rows:
        return None

    return _order_result_from_raw(
        category=category,
        symbol=symbol,
        order_link_id=order_link_id,
        raw=rows[0],
    )