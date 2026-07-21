from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
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

    source: str = "unknown"
    leaves_qty: Decimal | None = None
    reject_reason: str | None = None
    cancel_type: str | None = None
    reduce_only: bool | None = None
    position_idx: int | None = None
    market_unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class BybitExecutionFill:
    exec_id: str
    order_id: str | None
    order_link_id: str | None
    category: str
    symbol: str
    side: str | None

    exec_qty: Decimal
    exec_price: Decimal | None
    exec_value: Decimal | None

    exec_fee: Decimal | None
    fee_currency: str | None
    exec_time: str | None
    leaves_qty: Decimal | None

    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _json_value(item)
            for item in value
        ]

    return value


def _dec(
    value: Any,
) -> Decimal | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise BybitOrderExecutionError(
            "Decimal field must not be bool"
        )

    if isinstance(value, float):
        raise BybitOrderExecutionError(
            "Decimal field must not be float"
        )

    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise BybitOrderExecutionError(
            f"Invalid decimal value: {value!r}"
        ) from exc

    if not result.is_finite():
        raise BybitOrderExecutionError(
            "Decimal field must be finite"
        )

    return result


def _required_positive_dec(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    result = _dec(value)

    if result is None or result <= 0:
        raise BybitOrderExecutionError(
            f"{field_name} must be positive"
        )

    return result


def _optional_int(
    value: Any,
) -> int | None:
    if value is None or value == "":
        return None

    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise BybitOrderExecutionError(
            f"Invalid integer value: {value!r}"
        ) from exc


def _optional_bool(
    value: Any,
) -> bool | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in {
        "true",
        "1",
        "yes",
    }:
        return True

    if text in {
        "false",
        "0",
        "no",
    }:
        return False

    return None


def _required_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    text = str(value or "").strip()

    if not text:
        raise BybitOrderExecutionError(
            f"{field_name} must not be empty"
        )

    return text


def _normalized_category(
    value: Any,
) -> str:
    category = _required_text(
        value,
        field_name="category",
    ).lower()

    if category not in {
        "spot",
        "linear",
        "inverse",
        "option",
    }:
        raise BybitOrderExecutionError(
            "Unsupported Bybit category: "
            f"{category}"
        )

    return category


def _normalized_symbol(
    value: Any,
) -> str:
    return _required_text(
        value,
        field_name="symbol",
    ).upper()


def _normalized_side(
    value: Any,
) -> str:
    side = _required_text(
        value,
        field_name="side",
    ).lower()

    if side == "buy":
        return "Buy"

    if side == "sell":
        return "Sell"

    raise BybitOrderExecutionError(
        "side must be Buy or Sell"
    )


def _result_dict(
    payload: dict[str, Any],
) -> dict[str, Any]:
    result = payload.get("result")

    return (
        result
        if isinstance(result, dict)
        else {}
    )


def _result_list(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    result = _result_dict(payload)
    rows = result.get("list")

    if not isinstance(rows, list):
        return []

    return [
        dict(row)
        for row in rows
        if isinstance(row, dict)
    ]


def _order_result_from_raw(
    *,
    category: str,
    symbol: str,
    order_link_id: str,
    raw: dict[str, Any],
    source: str,
) -> BybitOrderResult:
    order_id_raw = raw.get("orderId")
    order_link_id_raw = raw.get(
        "orderLinkId"
    )

    return BybitOrderResult(
        category=str(
            raw.get("category")
            or category
        ).lower(),
        symbol=str(
            raw.get("symbol")
            or symbol
        ).upper(),
        order_id=(
            str(order_id_raw)
            if order_id_raw
            else None
        ),
        order_link_id=str(
            order_link_id_raw
            or order_link_id
        ),
        status=(
            raw.get("orderStatus")
            or raw.get("status")
        ),
        side=raw.get("side"),
        order_type=raw.get("orderType"),
        qty=_dec(raw.get("qty")),
        cum_exec_qty=_dec(
            raw.get("cumExecQty")
        ),
        cum_exec_value=_dec(
            raw.get("cumExecValue")
        ),
        avg_price=_dec(
            raw.get("avgPrice")
        ),
        raw=dict(raw),
        source=source,
        leaves_qty=_dec(
            raw.get("leavesQty")
        ),
        reject_reason=(
            raw.get("rejectReason")
            or raw.get("reject_reason")
        ),
        cancel_type=(
            raw.get("cancelType")
            or raw.get("cancel_type")
        ),
        reduce_only=_optional_bool(
            raw.get("reduceOnly")
        ),
        position_idx=_optional_int(
            raw.get("positionIdx")
        ),
        market_unit=raw.get(
            "marketUnit"
        ),
    )


def build_market_order_payload(
    *,
    category: str,
    symbol: str,
    side: str,
    qty: Decimal,
    order_link_id: str,
    reduce_only: bool | None = None,
    position_idx: int | None = None,
    market_unit: str | None = None,
) -> dict[str, Any]:
    normalized_category = (
        _normalized_category(category)
    )
    normalized_symbol = (
        _normalized_symbol(symbol)
    )
    normalized_side = _normalized_side(
        side
    )
    normalized_qty = (
        _required_positive_dec(
            qty,
            field_name="qty",
        )
    )
    normalized_order_link_id = (
        _required_text(
            order_link_id,
            field_name="order_link_id",
        )
    )

    if len(normalized_order_link_id) > 36:
        raise BybitOrderExecutionError(
            "order_link_id exceeds Bybit "
            "36-character limit"
        )

    payload: dict[str, Any] = {
        "category": normalized_category,
        "symbol": normalized_symbol,
        "side": normalized_side,
        "orderType": "Market",
        "qty": str(normalized_qty),
        "orderLinkId": (
            normalized_order_link_id
        ),
    }

    if reduce_only is not None:
        payload["reduceOnly"] = bool(
            reduce_only
        )

    if position_idx is not None:
        normalized_position_idx = int(
            position_idx
        )

        if normalized_position_idx not in {
            0,
            1,
            2,
        }:
            raise BybitOrderExecutionError(
                "position_idx must be 0, 1, or 2"
            )

        payload["positionIdx"] = (
            normalized_position_idx
        )

    if market_unit is not None:
        normalized_market_unit = (
            _required_text(
                market_unit,
                field_name="market_unit",
            )
        )

        if normalized_category != "spot":
            raise BybitOrderExecutionError(
                "marketUnit is supported only "
                "for spot orders"
            )

        if normalized_market_unit not in {
            "baseCoin",
            "quoteCoin",
        }:
            raise BybitOrderExecutionError(
                "market_unit must be "
                "baseCoin or quoteCoin"
            )

        payload["marketUnit"] = (
            normalized_market_unit
        )

    if normalized_category == "spot":
        if reduce_only:
            raise BybitOrderExecutionError(
                "Spot market order must not "
                "use reduceOnly"
            )

        if position_idx is not None:
            raise BybitOrderExecutionError(
                "Spot market order must not "
                "use positionIdx"
            )

    return payload


def validate_market_order_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BybitOrderExecutionError(
            "Market order payload must be "
            "a dict"
        )

    exact_payload = dict(payload)

    required_keys = {
        "category",
        "symbol",
        "side",
        "orderType",
        "qty",
        "orderLinkId",
    }
    allowed_keys = required_keys | {
        "reduceOnly",
        "positionIdx",
        "marketUnit",
    }

    missing_keys = (
        required_keys
        - set(exact_payload)
    )
    if missing_keys:
        raise BybitOrderExecutionError(
            "Market order payload is "
            "missing required keys: "
            + ", ".join(
                sorted(missing_keys)
            )
        )

    unexpected_keys = (
        set(exact_payload)
        - allowed_keys
    )
    if unexpected_keys:
        raise BybitOrderExecutionError(
            "Market order payload has "
            "unexpected keys: "
            + ", ".join(
                sorted(unexpected_keys)
            )
        )

    if (
        exact_payload["orderType"]
        != "Market"
    ):
        raise BybitOrderExecutionError(
            "orderType must be Market"
        )

    if (
        "reduceOnly" in exact_payload
        and not isinstance(
            exact_payload["reduceOnly"],
            bool,
        )
    ):
        raise BybitOrderExecutionError(
            "reduceOnly must be bool"
        )

    if (
        "positionIdx" in exact_payload
        and isinstance(
            exact_payload["positionIdx"],
            bool,
        )
    ):
        raise BybitOrderExecutionError(
            "positionIdx must not be bool"
        )

    canonical_payload = (
        build_market_order_payload(
            category=exact_payload[
                "category"
            ],
            symbol=exact_payload[
                "symbol"
            ],
            side=exact_payload["side"],
            qty=exact_payload["qty"],
            order_link_id=(
                exact_payload[
                    "orderLinkId"
                ]
            ),
            reduce_only=(
                exact_payload.get(
                    "reduceOnly"
                )
            ),
            position_idx=(
                exact_payload.get(
                    "positionIdx"
                )
            ),
            market_unit=(
                exact_payload.get(
                    "marketUnit"
                )
            ),
        )
    )

    if exact_payload != canonical_payload:
        raise BybitOrderExecutionError(
            "Persisted market order "
            "payload is not canonical"
        )

    return exact_payload


def create_market_order_from_payload(
    client: BybitV5Client,
    *,
    payload: dict[str, Any],
) -> BybitOrderResult:
    exact_payload = (
        validate_market_order_payload(
            payload
        )
    )

    response = client.post(
        "/v5/order/create",
        exact_payload,
    )
    result = _result_dict(response)

    raw = {
        **exact_payload,
        **result,
    }

    return _order_result_from_raw(
        category=exact_payload[
            "category"
        ],
        symbol=exact_payload["symbol"],
        order_link_id=(
            exact_payload[
                "orderLinkId"
            ]
        ),
        raw=raw,
        source="create_ack",
    )


def create_market_order(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    side: str,
    qty: Decimal,
    order_link_id: str,
    reduce_only: bool | None = None,
    position_idx: int | None = None,
    market_unit: str | None = None,
) -> BybitOrderResult:
    payload = build_market_order_payload(
        category=category,
        symbol=symbol,
        side=side,
        qty=qty,
        order_link_id=order_link_id,
        reduce_only=reduce_only,
        position_idx=position_idx,
        market_unit=market_unit,
    )

    return create_market_order_from_payload(
        client,
        payload=payload,
    )


def create_market_sell_order(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    qty: Decimal,
    order_link_id: str,
    reduce_only: bool | None = None,
    position_idx: int | None = None,
    market_unit: str | None = None,
) -> BybitOrderResult:
    """
    Compatibility wrapper.

    New execution code must call
    create_market_order() with an explicit side.
    """

    return create_market_order(
        client,
        category=category,
        symbol=symbol,
        side="Sell",
        qty=qty,
        order_link_id=order_link_id,
        reduce_only=reduce_only,
        position_idx=position_idx,
        market_unit=market_unit,
    )


def query_order_realtime(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
) -> BybitOrderResult | None:
    normalized_category = (
        _normalized_category(category)
    )
    normalized_symbol = (
        _normalized_symbol(symbol)
    )

    if not order_id and not order_link_id:
        raise BybitOrderExecutionError(
            "order_id or order_link_id "
            "is required"
        )

    params: dict[str, Any] = {
        "category": normalized_category,
        "symbol": normalized_symbol,
    }

    if order_id:
        params["orderId"] = str(order_id)
    else:
        params["orderLinkId"] = str(
            order_link_id
        )

    response = client.get(
        "/v5/order/realtime",
        params,
    )
    rows = _result_list(response)

    if not rows:
        return None

    raw = rows[0]

    return _order_result_from_raw(
        category=normalized_category,
        symbol=normalized_symbol,
        order_link_id=str(
            raw.get("orderLinkId")
            or order_link_id
            or ""
        ),
        raw=raw,
        source="realtime",
    )


def query_order_history(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
) -> BybitOrderResult | None:
    normalized_category = (
        _normalized_category(category)
    )
    normalized_symbol = (
        _normalized_symbol(symbol)
    )

    if not order_id and not order_link_id:
        raise BybitOrderExecutionError(
            "order_id or order_link_id "
            "is required"
        )

    params: dict[str, Any] = {
        "category": normalized_category,
        "symbol": normalized_symbol,
    }

    if order_id:
        params["orderId"] = str(order_id)
    else:
        params["orderLinkId"] = str(
            order_link_id
        )

    rows = client.paginate_get(
        "/v5/order/history",
        params,
    )

    if not rows:
        return None

    selected: dict[str, Any] | None = None

    if order_id:
        for row in rows:
            if str(
                row.get("orderId") or ""
            ) == str(order_id):
                selected = row
                break

    if selected is None and order_link_id:
        for row in rows:
            if str(
                row.get("orderLinkId") or ""
            ) == str(order_link_id):
                selected = row
                break

    if selected is None:
        selected = rows[0]

    return _order_result_from_raw(
        category=normalized_category,
        symbol=normalized_symbol,
        order_link_id=str(
            selected.get("orderLinkId")
            or order_link_id
            or ""
        ),
        raw=selected,
        source="history",
    )


def query_order_by_link_id(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    order_link_id: str,
) -> BybitOrderResult | None:
    return query_order_realtime(
        client,
        category=category,
        symbol=symbol,
        order_link_id=order_link_id,
    )


def query_execution_fills(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
) -> list[BybitExecutionFill]:
    normalized_category = (
        _normalized_category(category)
    )
    normalized_symbol = (
        _normalized_symbol(symbol)
    )

    if not order_id and not order_link_id:
        raise BybitOrderExecutionError(
            "order_id or order_link_id "
            "is required"
        )

    params: dict[str, Any] = {
        "category": normalized_category,
        "symbol": normalized_symbol,
    }

    if order_id:
        params["orderId"] = str(order_id)
    else:
        params["orderLinkId"] = str(
            order_link_id
        )

    rows = client.paginate_get(
        "/v5/execution/list",
        params,
    )

    fills: list[BybitExecutionFill] = []
    seen_exec_ids: set[str] = set()

    for row in rows:
        exec_id = str(
            row.get("execId") or ""
        ).strip()

        if not exec_id:
            continue

        if exec_id in seen_exec_ids:
            continue

        row_order_id = str(
            row.get("orderId") or ""
        ).strip()
        row_order_link_id = str(
            row.get("orderLinkId") or ""
        ).strip()

        if (
            order_id
            and row_order_id
            and row_order_id != str(order_id)
        ):
            continue

        if (
            order_link_id
            and row_order_link_id
            and row_order_link_id
            != str(order_link_id)
        ):
            continue

        exec_qty = _dec(
            row.get("execQty")
        )

        if exec_qty is None or exec_qty <= 0:
            continue

        seen_exec_ids.add(exec_id)

        fills.append(
            BybitExecutionFill(
                exec_id=exec_id,
                order_id=(
                    row_order_id or None
                ),
                order_link_id=(
                    row_order_link_id
                    or None
                ),
                category=normalized_category,
                symbol=str(
                    row.get("symbol")
                    or normalized_symbol
                ).upper(),
                side=row.get("side"),
                exec_qty=exec_qty,
                exec_price=_dec(
                    row.get("execPrice")
                ),
                exec_value=_dec(
                    row.get("execValue")
                ),
                exec_fee=_dec(
                    row.get("execFee")
                ),
                fee_currency=(
                    row.get("feeCurrency")
                    or row.get("feeCoin")
                ),
                exec_time=(
                    str(row.get("execTime"))
                    if row.get("execTime")
                    is not None
                    else None
                ),
                leaves_qty=_dec(
                    row.get("leavesQty")
                ),
                raw=dict(row),
            )
        )

    fills.sort(
        key=lambda item: (
            item.exec_time or "",
            item.exec_id,
        )
    )

    return fills