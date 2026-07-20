from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.bybit.instruments import (
    BybitInstrumentInfo,
    NormalizedOrderQuantity,
    normalize_order_quantity,
)
from app.settlement.negative_sale_snapshot import (
    NegativeSaleAsset,
)


ZERO = Decimal("0")

DERIVATIVE_REDUCTION_POLICY_VERSION = (
    "proportional_net_share_reduction_v1"
)

DERIVATIVE_ASSET_TYPES = {
    "perp_future",
    "long_option",
    "short_option",
}


class NegativeSalePlanningPolicyError(
    RuntimeError
):
    pass


def _decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSalePlanningPolicyError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSalePlanningPolicyError(
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
        raise NegativeSalePlanningPolicyError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSalePlanningPolicyError(
            f"{field_name} must be finite"
        )

    return result


def _optional_decimal(
    value: Any,
) -> Decimal | None:
    if value is None or value == "":
        return None

    return _decimal(
        value,
        field_name="optional_decimal",
    )


def _optional_text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def _optional_upper(
    value: Any,
) -> str | None:
    text = _optional_text(value)

    return (
        text.upper()
        if text is not None
        else None
    )


def _bool(
    value: Any,
    *,
    default: bool,
) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return default

    text = str(value).strip().lower()

    if text in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if text in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    return default


def _captured_at(
    value: Any,
) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            timezone.utc
        )

    if value:
        try:
            parsed = datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00",
                )
            )

            if parsed.tzinfo is None:
                return parsed.replace(
                    tzinfo=timezone.utc
                )

            return parsed.astimezone(
                timezone.utc
            )
        except ValueError:
            pass

    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProportionalDerivativeReduction:
    policy_version: str
    planned_net_shares_change: Decimal
    net_shares_to_redeem: Decimal
    shares_outstanding_before: Decimal
    net_redeem_ratio: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "policy_version": (
                self.policy_version
            ),
            "planned_net_shares_change": str(
                self.planned_net_shares_change
            ),
            "net_shares_to_redeem": str(
                self.net_shares_to_redeem
            ),
            "shares_outstanding_before": str(
                self.shares_outstanding_before
            ),
            "net_redeem_ratio": str(
                self.net_redeem_ratio
            ),
        }


def compute_proportional_derivative_reduction(
    *,
    planned_net_shares_change: Any,
    shares_outstanding_before: Any,
) -> ProportionalDerivativeReduction:
    planned_change = _decimal(
        planned_net_shares_change,
        field_name=(
            "planned_net_shares_change"
        ),
    )
    shares_before = _decimal(
        shares_outstanding_before,
        field_name=(
            "shares_outstanding_before"
        ),
    )

    net_shares_to_redeem = max(
        -planned_change,
        ZERO,
    )

    if net_shares_to_redeem > ZERO:
        if shares_before <= ZERO:
            raise NegativeSalePlanningPolicyError(
                "shares_outstanding_before "
                "must be positive for net redeem"
            )

        if (
            net_shares_to_redeem
            > shares_before
        ):
            raise NegativeSalePlanningPolicyError(
                "net_shares_to_redeem exceeds "
                "shares_outstanding_before"
            )

        ratio = (
            net_shares_to_redeem
            / shares_before
        )
    else:
        ratio = ZERO

    if ratio < ZERO or ratio > Decimal("1"):
        raise NegativeSalePlanningPolicyError(
            "net_redeem_ratio must be "
            "between 0 and 1"
        )

    return ProportionalDerivativeReduction(
        policy_version=(
            DERIVATIVE_REDUCTION_POLICY_VERSION
        ),
        planned_net_shares_change=(
            planned_change
        ),
        net_shares_to_redeem=(
            net_shares_to_redeem
        ),
        shares_outstanding_before=(
            shares_before
        ),
        net_redeem_ratio=ratio,
    )


def is_derivative_asset(
    asset: NegativeSaleAsset,
) -> bool:
    return (
        asset.asset_type
        in DERIVATIVE_ASSET_TYPES
    )


def normalize_position_side(
    value: Any,
) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lower()

    if text in {
        "buy",
        "long",
    }:
        return "long"

    if text in {
        "sell",
        "short",
    }:
        return "short"

    return None


def derivative_close_side(
    position_side: Any,
) -> str:
    normalized = normalize_position_side(
        position_side
    )

    if normalized == "long":
        return "Sell"

    if normalized == "short":
        return "Buy"

    raise NegativeSalePlanningPolicyError(
        "Derivative position side must be "
        "long/short or Buy/Sell"
    )


def derivative_raw_target_qty(
    *,
    current_size: Any,
    net_redeem_ratio: Any,
) -> Decimal:
    size = _decimal(
        current_size,
        field_name="current_size",
    )
    ratio = _decimal(
        net_redeem_ratio,
        field_name="net_redeem_ratio",
    )

    if size < ZERO:
        raise NegativeSalePlanningPolicyError(
            "current_size must be non-negative"
        )

    if ratio < ZERO or ratio > Decimal("1"):
        raise NegativeSalePlanningPolicyError(
            "net_redeem_ratio must be "
            "between 0 and 1"
        )

    return size * ratio


def instrument_from_asset(
    asset: NegativeSaleAsset,
) -> BybitInstrumentInfo | None:
    data = asset.instrument_info

    if not isinstance(data, dict) or not data:
        return None

    lot_size_filter = data.get(
        "lotSizeFilter"
    )
    price_filter = data.get(
        "priceFilter"
    )

    completeness_reasons_raw = data.get(
        "completeness_reasons"
    )

    completeness_reasons = tuple(
        str(value)
        for value in (
            completeness_reasons_raw
            if isinstance(
                completeness_reasons_raw,
                list | tuple,
            )
            else []
        )
    )

    category = str(
        data.get("category")
        or asset.category
        or ""
    ).strip().lower()
    symbol = str(
        data.get("symbol")
        or asset.symbol
        or ""
    ).strip().upper()

    if not category or not symbol:
        return None

    return BybitInstrumentInfo(
        category=category,
        symbol=symbol,
        status=_optional_text(
            data.get("status")
        ),
        base_coin=_optional_upper(
            data.get("baseCoin")
        ),
        quote_coin=_optional_upper(
            data.get("quoteCoin")
        ),
        settle_coin=_optional_upper(
            data.get("settleCoin")
        ),
        contract_type=_optional_text(
            data.get("contractType")
        ),
        lot_size_filter=(
            dict(lot_size_filter)
            if isinstance(
                lot_size_filter,
                dict,
            )
            else {}
        ),
        price_filter=(
            dict(price_filter)
            if isinstance(
                price_filter,
                dict,
            )
            else {}
        ),
        qty_step=_optional_decimal(
            data.get("qtyStep")
        ),
        min_order_qty=_optional_decimal(
            data.get("minOrderQty")
        ),
        min_notional_value=(
            _optional_decimal(
                data.get(
                    "minNotionalValue"
                )
            )
        ),
        min_order_amt=_optional_decimal(
            data.get("minOrderAmt")
        ),
        max_market_order_qty=(
            _optional_decimal(
                data.get(
                    "maxMarketOrderQty"
                )
            )
        ),
        max_order_qty=_optional_decimal(
            data.get("maxOrderQty")
        ),
        base_precision=_optional_decimal(
            data.get("basePrecision")
        ),
        quote_precision=(
            _optional_decimal(
                data.get("quotePrecision")
            )
        ),
        tick_size=_optional_decimal(
            data.get("tickSize")
        ),
        captured_at=_captured_at(
            data.get("captured_at")
        ),
        preflight_complete=_bool(
            data.get(
                "preflight_complete"
            ),
            default=False,
        ),
        completeness_reasons=(
            completeness_reasons
        ),
        raw=(
            dict(data.get("raw"))
            if isinstance(
                data.get("raw"),
                dict,
            )
            else {}
        ),
    )


def conservative_asset_price(
    *,
    asset: NegativeSaleAsset,
    close_side: str,
) -> Decimal | None:
    raw = (
        asset.raw
        if isinstance(asset.raw, dict)
        else {}
    )

    normalized_close_side = str(
        close_side
    ).strip().lower()

    if normalized_close_side == "sell":
        preferred_keys = (
            "best_bid",
            "bestBid",
            "bid1Price",
            "mark_price",
            "markPrice",
            "last_price",
            "lastPrice",
        )
    else:
        preferred_keys = (
            "best_ask",
            "bestAsk",
            "ask1Price",
            "mark_price",
            "markPrice",
            "last_price",
            "lastPrice",
        )

    for key in preferred_keys:
        value = _optional_decimal(
            raw.get(key)
        )

        if value is not None and value > ZERO:
            return value

    quantity = (
        asset.qty
        if asset.qty is not None
        else asset.size
    )

    if (
        quantity is not None
        and _decimal(
            quantity,
            field_name="asset_quantity",
        )
        > ZERO
        and asset.usd_value > ZERO
    ):
        return (
            asset.usd_value
            / _decimal(
                quantity,
                field_name="asset_quantity",
            )
        )

    return None


def normalize_asset_order_quantity(
    *,
    asset: NegativeSaleAsset,
    requested_qty: Any,
    available_qty: Any,
    close_side: str,
) -> NormalizedOrderQuantity:
    instrument = instrument_from_asset(
        asset
    )

    if instrument is None:
        raise NegativeSalePlanningPolicyError(
            "Instrument snapshot is missing "
            f"for {asset.category}:{asset.symbol}"
        )

    price = conservative_asset_price(
        asset=asset,
        close_side=close_side,
    )

    return normalize_order_quantity(
        instrument=instrument,
        requested_qty=requested_qty,
        available_qty=available_qty,
        price=price,
    )