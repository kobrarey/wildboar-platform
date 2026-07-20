from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_DOWN,
)
from typing import Any

from app.bybit.client import BybitV5Client


INSTRUMENTS_INFO_PATH = "/v5/market/instruments-info"
SUPPORTED_CATEGORIES = {
    "spot",
    "linear",
    "inverse",
    "option",
}
TRADING_INSTRUMENT_STATUSES = {
    "trading",
}


class BybitInstrumentInfoError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _public_get(
    client: BybitV5Client,
    path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    public_get = getattr(client, "public_get", None)

    if callable(public_get):
        return public_get(path, params)

    return client.get(path, params)


def _result_rows(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    result = payload.get("result")

    if not isinstance(result, dict):
        return []

    rows = result.get("list")

    if not isinstance(rows, list):
        return []

    return [
        row
        for row in rows
        if isinstance(row, dict)
    ]


def _dict_value(
    value: Any,
) -> dict[str, Any]:
    return (
        dict(value)
        if isinstance(value, dict)
        else {}
    )


def _text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _upper(
    value: Any,
) -> str | None:
    text = _text(value)

    return (
        text.upper()
        if text is not None
        else None
    )


def _decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise BybitInstrumentInfoError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise BybitInstrumentInfoError(
            f"{field_name} must not be float"
        )

    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise BybitInstrumentInfoError(
            f"{field_name} is not a valid Decimal: "
            f"value={value!r}"
        ) from exc

    if not result.is_finite():
        raise BybitInstrumentInfoError(
            f"{field_name} must be finite"
        )

    return result


def _positive_reason(
    *,
    value: Decimal | None,
    field_name: str,
) -> str | None:
    if value is None:
        return f"missing_{field_name}"

    if value <= 0:
        return f"non_positive_{field_name}"

    return None


@dataclass(frozen=True)
class BybitInstrumentInfo:
    category: str
    symbol: str
    status: str | None
    base_coin: str | None
    quote_coin: str | None
    settle_coin: str | None
    contract_type: str | None

    lot_size_filter: dict[str, Any]
    price_filter: dict[str, Any]

    qty_step: Decimal | None
    min_order_qty: Decimal | None
    min_notional_value: Decimal | None
    min_order_amt: Decimal | None
    max_market_order_qty: Decimal | None
    max_order_qty: Decimal | None
    base_precision: Decimal | None
    quote_precision: Decimal | None
    tick_size: Decimal | None

    captured_at: datetime
    preflight_complete: bool
    completeness_reasons: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def trading(self) -> bool:
        return (
            str(self.status or "").strip().lower()
            in TRADING_INSTRUMENT_STATUSES
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "symbol": self.symbol,
            "status": self.status,
            "baseCoin": self.base_coin,
            "quoteCoin": self.quote_coin,
            "settleCoin": self.settle_coin,
            "contractType": self.contract_type,
            "lotSizeFilter": dict(
                self.lot_size_filter
            ),
            "priceFilter": dict(
                self.price_filter
            ),
            "qtyStep": (
                str(self.qty_step)
                if self.qty_step is not None
                else None
            ),
            "minOrderQty": (
                str(self.min_order_qty)
                if self.min_order_qty is not None
                else None
            ),
            "minNotionalValue": (
                str(self.min_notional_value)
                if self.min_notional_value
                is not None
                else None
            ),
            "minOrderAmt": (
                str(self.min_order_amt)
                if self.min_order_amt is not None
                else None
            ),
            "maxMarketOrderQty": (
                str(self.max_market_order_qty)
                if self.max_market_order_qty
                is not None
                else None
            ),
            "maxOrderQty": (
                str(self.max_order_qty)
                if self.max_order_qty is not None
                else None
            ),
            "basePrecision": (
                str(self.base_precision)
                if self.base_precision is not None
                else None
            ),
            "quotePrecision": (
                str(self.quote_precision)
                if self.quote_precision is not None
                else None
            ),
            "tickSize": (
                str(self.tick_size)
                if self.tick_size is not None
                else None
            ),
            "captured_at": self.captured_at.isoformat(),
            "preflight_complete": (
                self.preflight_complete
            ),
            "completeness_reasons": list(
                self.completeness_reasons
            ),
            "trading": self.trading,
            "raw": dict(self.raw),
        }


def _required_filter_reasons(
    *,
    category: str,
    status: str | None,
    base_coin: str | None,
    quote_coin: str | None,
    settle_coin: str | None,
    contract_type: str | None,
    qty_step: Decimal | None,
    min_order_qty: Decimal | None,
    min_notional_value: Decimal | None,
    min_order_amt: Decimal | None,
    max_market_order_qty: Decimal | None,
    max_order_qty: Decimal | None,
    base_precision: Decimal | None,
    quote_precision: Decimal | None,
    tick_size: Decimal | None,
) -> tuple[str, ...]:
    reasons: list[str] = []

    if status is None:
        reasons.append("missing_status")

    if base_coin is None:
        reasons.append("missing_base_coin")

    if quote_coin is None:
        reasons.append("missing_quote_coin")

    if category in {
        "linear",
        "inverse",
        "option",
    }:
        if settle_coin is None:
            reasons.append("missing_settle_coin")

        if contract_type is None:
            reasons.append("missing_contract_type")

    for value, field_name in [
        (qty_step, "qty_step"),
        (min_order_qty, "min_order_qty"),
        (max_order_qty, "max_order_qty"),
        (tick_size, "tick_size"),
    ]:
        reason = _positive_reason(
            value=value,
            field_name=field_name,
        )
        if reason is not None:
            reasons.append(reason)

    if category == "spot":
        for value, field_name in [
            (base_precision, "base_precision"),
            (quote_precision, "quote_precision"),
            (min_order_amt, "min_order_amt"),
        ]:
            reason = _positive_reason(
                value=value,
                field_name=field_name,
            )
            if reason is not None:
                reasons.append(reason)

    if category in {
        "linear",
        "inverse",
    }:
        for value, field_name in [
            (
                min_notional_value,
                "min_notional_value",
            ),
            (
                max_market_order_qty,
                "max_market_order_qty",
            ),
        ]:
            reason = _positive_reason(
                value=value,
                field_name=field_name,
            )
            if reason is not None:
                reasons.append(reason)

    if category == "option":
        effective_max_market = (
            max_market_order_qty
            if max_market_order_qty is not None
            else max_order_qty
        )

        reason = _positive_reason(
            value=effective_max_market,
            field_name="max_market_order_qty",
        )
        if reason is not None:
            reasons.append(reason)

    return tuple(dict.fromkeys(reasons))


def query_instrument_info(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    captured_at: datetime | None = None,
) -> BybitInstrumentInfo:
    normalized_category = str(
        category or ""
    ).strip().lower()
    normalized_symbol = str(
        symbol or ""
    ).strip().upper()

    if normalized_category not in SUPPORTED_CATEGORIES:
        raise BybitInstrumentInfoError(
            "Unsupported Bybit instrument category: "
            f"{category!r}"
        )

    if not normalized_symbol:
        raise BybitInstrumentInfoError(
            "Bybit instrument symbol is empty"
        )

    payload = _public_get(
        client,
        INSTRUMENTS_INFO_PATH,
        {
            "category": normalized_category,
            "symbol": normalized_symbol,
        },
    )

    matching_row: dict[str, Any] | None = None

    for row in _result_rows(payload):
        row_symbol = _upper(
            row.get("symbol")
        )

        if row_symbol == normalized_symbol:
            matching_row = row
            break

    if matching_row is None:
        raise BybitInstrumentInfoError(
            "Bybit instrument info not found: "
            f"category={normalized_category}, "
            f"symbol={normalized_symbol}"
        )

    lot_size_filter = _dict_value(
        matching_row.get("lotSizeFilter")
    )
    price_filter = _dict_value(
        matching_row.get("priceFilter")
    )

    base_precision = _decimal(
        lot_size_filter.get("basePrecision"),
        field_name="basePrecision",
    )
    quote_precision = _decimal(
        lot_size_filter.get("quotePrecision"),
        field_name="quotePrecision",
    )

    qty_step_raw = lot_size_filter.get(
        "qtyStep"
    )

    if (
        qty_step_raw in (None, "")
        and normalized_category == "spot"
    ):
        qty_step_raw = lot_size_filter.get(
            "basePrecision"
        )

    qty_step = _decimal(
        qty_step_raw,
        field_name="qtyStep",
    )
    min_order_qty = _decimal(
        lot_size_filter.get("minOrderQty"),
        field_name="minOrderQty",
    )
    min_notional_value = _decimal(
        lot_size_filter.get(
            "minNotionalValue"
        ),
        field_name="minNotionalValue",
    )
    min_order_amt = _decimal(
        lot_size_filter.get("minOrderAmt"),
        field_name="minOrderAmt",
    )
    max_order_qty = _decimal(
        lot_size_filter.get("maxOrderQty"),
        field_name="maxOrderQty",
    )

    max_market_raw = (
        lot_size_filter.get(
            "maxMktOrderQty"
        )
        or lot_size_filter.get(
            "maxMarketOrderQty"
        )
    )

    if (
        max_market_raw in (None, "")
        and normalized_category == "option"
    ):
        max_market_raw = (
            lot_size_filter.get("maxOrderQty")
        )

    max_market_order_qty = _decimal(
        max_market_raw,
        field_name="maxMarketOrderQty",
    )
    tick_size = _decimal(
        price_filter.get("tickSize"),
        field_name="tickSize",
    )

    status = _text(
        matching_row.get("status")
    )
    base_coin = _upper(
        matching_row.get("baseCoin")
    )
    quote_coin = _upper(
        matching_row.get("quoteCoin")
    )
    settle_coin = _upper(
        matching_row.get("settleCoin")
    )
    contract_type = _text(
        matching_row.get("contractType")
        or matching_row.get("optionsType")
    )

    reasons = _required_filter_reasons(
        category=normalized_category,
        status=status,
        base_coin=base_coin,
        quote_coin=quote_coin,
        settle_coin=settle_coin,
        contract_type=contract_type,
        qty_step=qty_step,
        min_order_qty=min_order_qty,
        min_notional_value=min_notional_value,
        min_order_amt=min_order_amt,
        max_market_order_qty=(
            max_market_order_qty
        ),
        max_order_qty=max_order_qty,
        base_precision=base_precision,
        quote_precision=quote_precision,
        tick_size=tick_size,
    )

    return BybitInstrumentInfo(
        category=normalized_category,
        symbol=normalized_symbol,
        status=status,
        base_coin=base_coin,
        quote_coin=quote_coin,
        settle_coin=settle_coin,
        contract_type=contract_type,
        lot_size_filter=lot_size_filter,
        price_filter=price_filter,
        qty_step=qty_step,
        min_order_qty=min_order_qty,
        min_notional_value=(
            min_notional_value
        ),
        min_order_amt=min_order_amt,
        max_market_order_qty=(
            max_market_order_qty
        ),
        max_order_qty=max_order_qty,
        base_precision=base_precision,
        quote_precision=quote_precision,
        tick_size=tick_size,
        captured_at=captured_at or utcnow(),
        preflight_complete=not reasons,
        completeness_reasons=reasons,
        raw=dict(matching_row),
    )

@dataclass(frozen=True)
class NormalizedOrderQuantity:
    requested_qty: Decimal
    available_qty: Decimal | None
    capped_qty: Decimal
    normalized_qty: Decimal

    qty_step: Decimal
    min_order_qty: Decimal | None
    min_notional_value: Decimal | None
    min_order_amt: Decimal | None
    max_market_order_qty: Decimal | None

    price: Decimal | None
    normalized_notional: Decimal | None

    slices: tuple[Decimal, ...]
    eligible: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_qty": str(
                self.requested_qty
            ),
            "available_qty": (
                str(self.available_qty)
                if self.available_qty is not None
                else None
            ),
            "capped_qty": str(
                self.capped_qty
            ),
            "normalized_qty": str(
                self.normalized_qty
            ),
            "qty_step": str(
                self.qty_step
            ),
            "min_order_qty": (
                str(self.min_order_qty)
                if self.min_order_qty is not None
                else None
            ),
            "min_notional_value": (
                str(self.min_notional_value)
                if self.min_notional_value
                is not None
                else None
            ),
            "min_order_amt": (
                str(self.min_order_amt)
                if self.min_order_amt is not None
                else None
            ),
            "max_market_order_qty": (
                str(self.max_market_order_qty)
                if self.max_market_order_qty
                is not None
                else None
            ),
            "price": (
                str(self.price)
                if self.price is not None
                else None
            ),
            "normalized_notional": (
                str(self.normalized_notional)
                if self.normalized_notional
                is not None
                else None
            ),
            "slices": [
                str(value)
                for value in self.slices
            ],
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }


def _required_positive_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    result = _decimal(
        value,
        field_name=field_name,
    )

    if result is None or result <= 0:
        raise BybitInstrumentInfoError(
            f"{field_name} must be positive"
        )

    return result


def round_down_to_step(
    value: Any,
    *,
    step: Any,
) -> Decimal:
    quantity = _required_positive_decimal(
        value,
        field_name="quantity",
    )
    quantity_step = (
        _required_positive_decimal(
            step,
            field_name="qtyStep",
        )
    )

    step_count = (
        quantity / quantity_step
    ).to_integral_value(
        rounding=ROUND_DOWN
    )

    return step_count * quantity_step


def _optional_non_negative_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal | None:
    result = _decimal(
        value,
        field_name=field_name,
    )

    if result is None:
        return None

    if result < 0:
        raise BybitInstrumentInfoError(
            f"{field_name} must be non-negative"
        )

    return result


def _split_normalized_quantity(
    *,
    normalized_qty: Decimal,
    qty_step: Decimal,
    max_market_order_qty: Decimal | None,
    min_order_qty: Decimal | None,
) -> tuple[Decimal, ...]:
    if normalized_qty <= 0:
        return ()

    if (
        max_market_order_qty is None
        or normalized_qty
        <= max_market_order_qty
    ):
        return (normalized_qty,)

    normalized_max = round_down_to_step(
        max_market_order_qty,
        step=qty_step,
    )

    if normalized_max <= 0:
        raise BybitInstrumentInfoError(
            "maxMarketOrderQty becomes zero "
            "after qtyStep normalization"
        )

    if (
        min_order_qty is not None
        and normalized_max
        < min_order_qty
    ):
        raise BybitInstrumentInfoError(
            "maxMarketOrderQty is below "
            "minOrderQty"
        )

    remaining = normalized_qty
    slices: list[Decimal] = []

    while remaining > normalized_max:
        current = normalized_max
        remainder = remaining - current

        if (
            min_order_qty is not None
            and remainder > 0
            and remainder < min_order_qty
        ):
            quantity_to_move = (
                min_order_qty - remainder
            )
            move_steps = (
                quantity_to_move / qty_step
            ).to_integral_value(
                rounding=ROUND_DOWN
            )

            if (
                move_steps * qty_step
                < quantity_to_move
            ):
                move_steps += 1

            current = (
                current
                - move_steps * qty_step
            )
            remainder = (
                remaining - current
            )

        if current <= 0:
            raise BybitInstrumentInfoError(
                "Unable to split normalized qty "
                "into positive deterministic slices"
            )

        if (
            min_order_qty is not None
            and current < min_order_qty
        ):
            raise BybitInstrumentInfoError(
                "Deterministic slice is below "
                "minOrderQty"
            )

        slices.append(current)
        remaining = remainder

    if remaining > 0:
        slices.append(remaining)

    if sum(slices, Decimal("0")) != normalized_qty:
        raise BybitInstrumentInfoError(
            "Deterministic qty slices do not "
            "sum to normalized qty"
        )

    return tuple(slices)


def normalize_order_quantity(
    *,
    instrument: BybitInstrumentInfo,
    requested_qty: Any,
    available_qty: Any = None,
    price: Any = None,
) -> NormalizedOrderQuantity:
    if not instrument.preflight_complete:
        return NormalizedOrderQuantity(
            requested_qty=_required_positive_decimal(
                requested_qty,
                field_name="requested_qty",
            ),
            available_qty=None,
            capped_qty=Decimal("0"),
            normalized_qty=Decimal("0"),
            qty_step=(
                instrument.qty_step
                or Decimal("0")
            ),
            min_order_qty=(
                instrument.min_order_qty
            ),
            min_notional_value=(
                instrument.min_notional_value
            ),
            min_order_amt=(
                instrument.min_order_amt
            ),
            max_market_order_qty=(
                instrument.max_market_order_qty
            ),
            price=None,
            normalized_notional=None,
            slices=(),
            eligible=False,
            reasons=tuple(
                instrument.completeness_reasons
            ),
        )

    requested = _required_positive_decimal(
        requested_qty,
        field_name="requested_qty",
    )
    qty_step = _required_positive_decimal(
        instrument.qty_step,
        field_name="qtyStep",
    )

    parsed_available = (
        _optional_non_negative_decimal(
            available_qty,
            field_name="available_qty",
        )
        if available_qty is not None
        else None
    )

    capped = (
        min(requested, parsed_available)
        if parsed_available is not None
        else requested
    )

    reasons: list[str] = []

    if capped <= 0:
        normalized = Decimal("0")
        reasons.append(
            "available_qty_not_positive"
        )
    else:
        normalized = round_down_to_step(
            capped,
            step=qty_step,
        )

    if normalized <= 0:
        reasons.append(
            "qty_zero_after_round_down"
        )

    min_order_qty = (
        instrument.min_order_qty
    )

    if (
        normalized > 0
        and min_order_qty is not None
        and normalized < min_order_qty
    ):
        reasons.append(
            "qty_below_min_order_qty"
        )

    parsed_price = (
        _optional_non_negative_decimal(
            price,
            field_name="price",
        )
        if price is not None
        else None
    )

    normalized_notional = (
        normalized * parsed_price
        if (
            normalized > 0
            and parsed_price is not None
        )
        else None
    )

    minimum_notional_values = [
        value
        for value in (
            instrument.min_notional_value,
            instrument.min_order_amt,
        )
        if value is not None
    ]

    if minimum_notional_values:
        if (
            parsed_price is None
            or parsed_price <= 0
        ):
            reasons.append(
                "price_required_for_minimum_notional"
            )
        else:
            for minimum in (
                minimum_notional_values
            ):
                if (
                    normalized_notional
                    is None
                    or normalized_notional
                    < minimum
                ):
                    reasons.append(
                        "notional_below_"
                        f"{minimum}"
                    )

    slices: tuple[Decimal, ...] = ()

    if not reasons:
        try:
            slices = (
                _split_normalized_quantity(
                    normalized_qty=normalized,
                    qty_step=qty_step,
                    max_market_order_qty=(
                        instrument
                        .max_market_order_qty
                    ),
                    min_order_qty=(
                        min_order_qty
                    ),
                )
            )
        except BybitInstrumentInfoError as exc:
            reasons.append(str(exc))

    if not reasons:
        for index, slice_qty in enumerate(
            slices
        ):
            if (
                slice_qty / qty_step
            ) != (
                slice_qty / qty_step
            ).to_integral_value():
                reasons.append(
                    "slice_not_qty_step_aligned:"
                    f"{index}"
                )

            if (
                min_order_qty is not None
                and slice_qty
                < min_order_qty
            ):
                reasons.append(
                    "slice_below_min_order_qty:"
                    f"{index}"
                )

            if (
                parsed_price is not None
                and parsed_price > 0
            ):
                slice_notional = (
                    slice_qty * parsed_price
                )

                for minimum in (
                    minimum_notional_values
                ):
                    if slice_notional < minimum:
                        reasons.append(
                            "slice_below_minimum_"
                            f"notional:{index}"
                        )

    return NormalizedOrderQuantity(
        requested_qty=requested,
        available_qty=parsed_available,
        capped_qty=capped,
        normalized_qty=normalized,
        qty_step=qty_step,
        min_order_qty=min_order_qty,
        min_notional_value=(
            instrument.min_notional_value
        ),
        min_order_amt=(
            instrument.min_order_amt
        ),
        max_market_order_qty=(
            instrument.max_market_order_qty
        ),
        price=parsed_price,
        normalized_notional=(
            normalized_notional
        ),
        slices=slices,
        eligible=not reasons,
        reasons=tuple(
            dict.fromkeys(reasons)
        ),
    )
