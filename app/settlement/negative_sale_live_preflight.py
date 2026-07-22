from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.bybit.client import BybitV5Client
from app.bybit.instruments import (
    BybitInstrumentInfoError,
    NormalizedOrderQuantity,
    normalize_order_quantity,
    query_instrument_info,
)
from app.bybit.transferable_balance import (
    BybitTransferableBalanceError,
    query_unified_transferable_balance,
)


ZERO = Decimal("0")

DERIVATIVE_CATEGORIES = {
    "linear",
    "inverse",
    "option",
}

SUPPORTED_CATEGORIES = {
    "spot",
    *DERIVATIVE_CATEGORIES,
}


class NegativeSaleLivePreflightError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class NegativeSaleLivePreflight:
    category: str
    symbol: str

    position_side: str | None
    close_side: str
    position_idx: int | None

    reduce_only: bool | None
    market_unit: str | None

    requested_qty: Decimal
    available_qty: Decimal
    price: Decimal

    normalized_qty: Decimal
    normalized_notional: Decimal | None
    slices: tuple[Decimal, ...]

    instrument_snapshot: dict[str, Any]
    position_snapshot: dict[str, Any]
    quantity_preflight: dict[str, Any]

    captured_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "symbol": self.symbol,
            "position_side": (
                self.position_side
            ),
            "close_side": self.close_side,
            "position_idx": (
                self.position_idx
            ),
            "reduce_only": self.reduce_only,
            "market_unit": self.market_unit,
            "requested_qty": str(
                self.requested_qty
            ),
            "available_qty": str(
                self.available_qty
            ),
            "price": str(self.price),
            "normalized_qty": str(
                self.normalized_qty
            ),
            "normalized_notional": (
                str(
                    self.normalized_notional
                )
                if self.normalized_notional
                is not None
                else None
            ),
            "slices": [
                str(value)
                for value in self.slices
            ],
            "instrument_snapshot": dict(
                self.instrument_snapshot
            ),
            "position_snapshot": dict(
                self.position_snapshot
            ),
            "quantity_preflight": dict(
                self.quantity_preflight
            ),
            "captured_at": (
                self.captured_at.isoformat()
            ),
            "read_only": True,
            "no_order_post": True,
            "no_transfer": True,
            "no_withdrawal": True,
            "no_bsc_action": True,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(
    value: Any,
    *,
    field_name: str,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleLivePreflightError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleLivePreflightError(
            f"{field_name} must not be float"
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
        raise NegativeSaleLivePreflightError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleLivePreflightError(
            f"{field_name} must be finite"
        )

    if positive and result <= ZERO:
        raise NegativeSaleLivePreflightError(
            f"{field_name} must be positive"
        )

    if non_negative and result < ZERO:
        raise NegativeSaleLivePreflightError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _normalize_side(
    value: Any,
    *,
    field_name: str,
    required: bool,
) -> str | None:
    text = str(
        value or ""
    ).strip().lower()

    if not text:
        if required:
            raise (
                NegativeSaleLivePreflightError(
                    f"{field_name} is required"
                )
            )

        return None

    if text == "buy":
        return "Buy"

    if text == "sell":
        return "Sell"

    raise NegativeSaleLivePreflightError(
        f"{field_name} must be Buy or Sell"
    )


def _opposite_side(
    side: str,
) -> str:
    if side == "Buy":
        return "Sell"

    if side == "Sell":
        return "Buy"

    raise NegativeSaleLivePreflightError(
        f"Unsupported position side: {side}"
    )


def _optional_position_idx(
    value: Any,
) -> int | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise NegativeSaleLivePreflightError(
            "position_idx must not be bool"
        )

    try:
        result = int(str(value))
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleLivePreflightError(
            "position_idx is not integer"
        ) from exc

    if result not in {0, 1, 2}:
        raise NegativeSaleLivePreflightError(
            "position_idx must be 0, 1, or 2"
        )

    return result


def _response_rows(
    payload: Any,
    *,
    source: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise NegativeSaleLivePreflightError(
            f"{source} response must be dict"
        )

    ret_code = payload.get("retCode")

    if ret_code not in {
        None,
        0,
        "0",
    }:
        raise NegativeSaleLivePreflightError(
            f"{source} request failed: "
            f"retCode={ret_code}, "
            f"retMsg={payload.get('retMsg')}"
        )

    result = payload.get("result")

    if not isinstance(result, dict):
        raise NegativeSaleLivePreflightError(
            f"{source}.result must be dict"
        )

    rows = result.get("list")

    if not isinstance(rows, list):
        raise NegativeSaleLivePreflightError(
            f"{source}.result.list "
            "must be list"
        )

    return [
        dict(row)
        for row in rows
        if isinstance(row, dict)
    ]


def _public_get(
    client: BybitV5Client,
    path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    public_get = getattr(
        client,
        "public_get",
        None,
    )

    if callable(public_get):
        return public_get(
            path,
            params,
        )

    return client.get(
        path,
        params,
    )


def _query_execution_price(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    close_side: str,
) -> tuple[
    Decimal,
    dict[str, Any],
]:
    payload = _public_get(
        client,
        "/v5/market/tickers",
        {
            "category": category,
            "symbol": symbol,
        },
    )

    rows = [
        row
        for row in _response_rows(
            payload,
            source="market_tickers",
        )
        if str(
            row.get("symbol")
            or ""
        ).strip().upper()
        == symbol
    ]

    if len(rows) != 1:
        raise NegativeSaleLivePreflightError(
            "Ticker response must contain "
            "exactly one matching symbol: "
            f"{category}:{symbol}"
        )

    row = rows[0]

    price_field = (
        "bid1Price"
        if close_side == "Sell"
        else "ask1Price"
    )

    price = _decimal(
        row.get(price_field),
        field_name=price_field,
        positive=True,
    )

    return price, row


def _validate_instrument(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    captured_at: datetime,
):
    try:
        instrument = query_instrument_info(
            client,
            category=category,
            symbol=symbol,
            captured_at=captured_at,
        )
    except BybitInstrumentInfoError as exc:
        raise NegativeSaleLivePreflightError(
            "Live instrument query failed: "
            f"{exc}"
        ) from exc

    if not instrument.trading:
        raise NegativeSaleLivePreflightError(
            "Live instrument is not trading: "
            f"{category}:{symbol}"
        )

    if not instrument.preflight_complete:
        raise NegativeSaleLivePreflightError(
            "Live instrument filters are "
            "incomplete: "
            f"{category}:{symbol}, "
            "reasons="
            f"{list(instrument.completeness_reasons)}"
        )

    return instrument


def _validate_normalized_quantity(
    quantity: NormalizedOrderQuantity,
    *,
    category: str,
    symbol: str,
) -> None:
    if (
        not quantity.eligible
        or quantity.normalized_qty
        <= ZERO
        or not quantity.slices
    ):
        raise NegativeSaleLivePreflightError(
            "Live quantity normalization "
            "failed: "
            f"{category}:{symbol}, "
            f"reasons={list(quantity.reasons)}"
        )

    if (
        sum(
            quantity.slices,
            ZERO,
        )
        != quantity.normalized_qty
    ):
        raise NegativeSaleLivePreflightError(
            "Live quantity slices do not "
            "sum to normalized quantity"
        )


def _expected_position_side(
    *,
    planned_position_side: Any,
    planned_close_side: Any,
) -> str:
    expected_side = _normalize_side(
        planned_position_side,
        field_name="planned_position_side",
        required=False,
    )

    planned_close = _normalize_side(
        planned_close_side,
        field_name="planned_close_side",
        required=False,
    )

    if expected_side is None:
        if planned_close is None:
            raise (
                NegativeSaleLivePreflightError(
                    "Derivative preflight needs "
                    "planned position side or "
                    "planned close side"
                )
            )

        expected_side = _opposite_side(
            planned_close
        )

    expected_close = _opposite_side(
        expected_side
    )

    if (
        planned_close is not None
        and planned_close != expected_close
    ):
        raise NegativeSaleLivePreflightError(
            "Planned close side contradicts "
            "planned position side"
        )

    return expected_side


def _validate_side_position_idx(
    *,
    position_side: str,
    position_idx: int,
) -> None:
    if (
        position_side == "Buy"
        and position_idx not in {0, 1}
    ):
        raise NegativeSaleLivePreflightError(
            "Buy position must have "
            "positionIdx 0 or 1"
        )

    if (
        position_side == "Sell"
        and position_idx not in {0, 2}
    ):
        raise NegativeSaleLivePreflightError(
            "Sell position must have "
            "positionIdx 0 or 2"
        )


def _select_live_derivative_position(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    planned_position_side: Any,
    planned_close_side: Any,
    planned_position_idx: Any,
) -> tuple[
    dict[str, Any],
    str,
    str,
    int,
    Decimal,
]:
    expected_side = (
        _expected_position_side(
            planned_position_side=(
                planned_position_side
            ),
            planned_close_side=(
                planned_close_side
            ),
        )
    )
    expected_idx = (
        _optional_position_idx(
            planned_position_idx
        )
    )

    payload = client.get(
        "/v5/position/list",
        {
            "category": category,
            "symbol": symbol,
        },
    )

    candidates: list[
        tuple[
            dict[str, Any],
            str,
            int,
            Decimal,
        ]
    ] = []

    for row in _response_rows(
        payload,
        source="position_list",
    ):
        row_symbol = str(
            row.get("symbol")
            or ""
        ).strip().upper()

        if row_symbol != symbol:
            continue

        size = _decimal(
            row.get("size"),
            field_name="position.size",
            non_negative=True,
        )

        if size <= ZERO:
            continue

        side = _normalize_side(
            row.get("side"),
            field_name="position.side",
            required=True,
        )
        assert side is not None

        position_idx = (
            _optional_position_idx(
                row.get("positionIdx")
            )
        )

        if position_idx is None:
            raise (
                NegativeSaleLivePreflightError(
                    "Live derivative position "
                    "has no positionIdx"
                )
            )

        _validate_side_position_idx(
            position_side=side,
            position_idx=position_idx,
        )

        if side != expected_side:
            continue

        if (
            expected_idx is not None
            and position_idx
            != expected_idx
        ):
            continue

        candidates.append(
            (
                row,
                side,
                position_idx,
                size,
            )
        )

    if len(candidates) != 1:
        raise NegativeSaleLivePreflightError(
            "Cannot identify exactly one "
            "live derivative position: "
            f"{category}:{symbol}, "
            f"expected_side={expected_side}, "
            f"expected_position_idx="
            f"{expected_idx}, "
            f"matches={len(candidates)}"
        )

    (
        row,
        position_side,
        position_idx,
        available_qty,
    ) = candidates[0]

    position_status = str(
        row.get("positionStatus")
        or ""
    ).strip().lower()

    if position_status != "normal":
        raise NegativeSaleLivePreflightError(
            "Live derivative position is "
            "not Normal: "
            f"{category}:{symbol}, "
            f"positionStatus="
            f"{row.get('positionStatus')!r}"
        )

    close_side = _opposite_side(
        position_side
    )

    return (
        row,
        position_side,
        close_side,
        position_idx,
        available_qty,
    )


def _build_derivative_preflight(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    requested_qty: Decimal,
    planned_position_side: Any,
    planned_close_side: Any,
    planned_position_idx: Any,
    captured_at: datetime,
) -> NegativeSaleLivePreflight:
    (
        position_row,
        position_side,
        close_side,
        position_idx,
        available_qty,
    ) = _select_live_derivative_position(
        client,
        category=category,
        symbol=symbol,
        planned_position_side=(
            planned_position_side
        ),
        planned_close_side=(
            planned_close_side
        ),
        planned_position_idx=(
            planned_position_idx
        ),
    )

    instrument = _validate_instrument(
        client,
        category=category,
        symbol=symbol,
        captured_at=captured_at,
    )

    price, ticker_row = (
        _query_execution_price(
            client,
            category=category,
            symbol=symbol,
            close_side=close_side,
        )
    )

    try:
        quantity = normalize_order_quantity(
            instrument=instrument,
            requested_qty=requested_qty,
            available_qty=available_qty,
            price=price,
        )
    except BybitInstrumentInfoError as exc:
        raise NegativeSaleLivePreflightError(
            "Live derivative quantity "
            f"normalization failed: {exc}"
        ) from exc

    _validate_normalized_quantity(
        quantity,
        category=category,
        symbol=symbol,
    )

    position_snapshot = {
        "source_endpoint": (
            "/v5/position/list"
        ),
        "captured_at": (
            captured_at.isoformat()
        ),
        "position_side": position_side,
        "close_side": close_side,
        "position_idx": position_idx,
        "available_qty": str(
            available_qty
        ),
        "position_status": (
            position_row.get(
                "positionStatus"
            )
        ),
        "mark_price": (
            position_row.get("markPrice")
        ),
        "ticker": dict(ticker_row),
        "raw_position": dict(
            position_row
        ),
    }

    return NegativeSaleLivePreflight(
        category=category,
        symbol=symbol,
        position_side=position_side,
        close_side=close_side,
        position_idx=position_idx,
        reduce_only=True,
        market_unit=None,
        requested_qty=requested_qty,
        available_qty=available_qty,
        price=price,
        normalized_qty=(
            quantity.normalized_qty
        ),
        normalized_notional=(
            quantity.normalized_notional
        ),
        slices=quantity.slices,
        instrument_snapshot=(
            instrument.to_dict()
        ),
        position_snapshot=(
            position_snapshot
        ),
        quantity_preflight=(
            quantity.to_dict()
        ),
        captured_at=captured_at,
    )


def _build_spot_preflight(
    client: BybitV5Client,
    *,
    symbol: str,
    requested_qty: Decimal,
    planned_close_side: Any,
    captured_at: datetime,
) -> NegativeSaleLivePreflight:
    planned_close = _normalize_side(
        planned_close_side,
        field_name="planned_close_side",
        required=False,
    )

    if (
        planned_close is not None
        and planned_close != "Sell"
    ):
        raise NegativeSaleLivePreflightError(
            "Spot negative-sale close side "
            "must be Sell"
        )

    instrument = _validate_instrument(
        client,
        category="spot",
        symbol=symbol,
        captured_at=captured_at,
    )

    base_coin = str(
        instrument.base_coin
        or ""
    ).strip().upper()

    if not base_coin:
        raise NegativeSaleLivePreflightError(
            "Spot instrument has no base coin"
        )

    try:
        balance = (
            query_unified_transferable_balance(
                client,
                coin=base_coin,
                destination_account_type=(
                    "FUND"
                ),
            )
        )
    except BybitTransferableBalanceError as exc:
        raise NegativeSaleLivePreflightError(
            "Live spot transferable balance "
            f"query failed: {exc}"
        ) from exc

    available_qty = (
        balance
        .confirmed_transferable_amount
    )

    price, ticker_row = (
        _query_execution_price(
            client,
            category="spot",
            symbol=symbol,
            close_side="Sell",
        )
    )

    try:
        quantity = normalize_order_quantity(
            instrument=instrument,
            requested_qty=requested_qty,
            available_qty=available_qty,
            price=price,
        )
    except BybitInstrumentInfoError as exc:
        raise NegativeSaleLivePreflightError(
            "Live spot quantity "
            f"normalization failed: {exc}"
        ) from exc

    _validate_normalized_quantity(
        quantity,
        category="spot",
        symbol=symbol,
    )

    position_snapshot = {
        "source_endpoint": (
            balance.source_endpoint
        ),
        "captured_at": (
            captured_at.isoformat()
        ),
        "base_coin": base_coin,
        "close_side": "Sell",
        "available_qty": str(
            available_qty
        ),
        "transferable_balance": (
            balance.to_dict()
        ),
        "ticker": dict(ticker_row),
    }

    return NegativeSaleLivePreflight(
        category="spot",
        symbol=symbol,
        position_side=None,
        close_side="Sell",
        position_idx=None,
        reduce_only=None,
        market_unit="baseCoin",
        requested_qty=requested_qty,
        available_qty=available_qty,
        price=price,
        normalized_qty=(
            quantity.normalized_qty
        ),
        normalized_notional=(
            quantity.normalized_notional
        ),
        slices=quantity.slices,
        instrument_snapshot=(
            instrument.to_dict()
        ),
        position_snapshot=(
            position_snapshot
        ),
        quantity_preflight=(
            quantity.to_dict()
        ),
        captured_at=captured_at,
    )


def build_live_negative_sale_preflight(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    requested_qty: Any,
    planned_position_side: Any = None,
    planned_close_side: Any = None,
    planned_position_idx: Any = None,
    captured_at: datetime | None = None,
) -> NegativeSaleLivePreflight:
    normalized_category = str(
        category or ""
    ).strip().lower()
    normalized_symbol = str(
        symbol or ""
    ).strip().upper()

    if (
        normalized_category
        not in SUPPORTED_CATEGORIES
    ):
        raise NegativeSaleLivePreflightError(
            "Unsupported live sale category: "
            f"{category!r}"
        )

    if not normalized_symbol:
        raise NegativeSaleLivePreflightError(
            "Live sale symbol is empty"
        )

    requested = _decimal(
        requested_qty,
        field_name="requested_qty",
        positive=True,
    )

    effective_now = (
        captured_at or utcnow()
    )

    if effective_now.tzinfo is None:
        effective_now = (
            effective_now.replace(
                tzinfo=timezone.utc
            )
        )

    effective_now = (
        effective_now.astimezone(
            timezone.utc
        )
    )

    if normalized_category == "spot":
        return _build_spot_preflight(
            client,
            symbol=normalized_symbol,
            requested_qty=requested,
            planned_close_side=(
                planned_close_side
            ),
            captured_at=effective_now,
        )

    return _build_derivative_preflight(
        client,
        category=normalized_category,
        symbol=normalized_symbol,
        requested_qty=requested,
        planned_position_side=(
            planned_position_side
        ),
        planned_close_side=(
            planned_close_side
        ),
        planned_position_idx=(
            planned_position_idx
        ),
        captured_at=effective_now,
    )