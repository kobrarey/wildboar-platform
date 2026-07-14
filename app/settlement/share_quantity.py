from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from typing import Any


SHARE_QUANTUM = Decimal("0.0001")
ZERO = Decimal("0")

BUY_SHARE_QUANTITY_BELOW_MINIMUM_ERROR = (
    "buy_share_quantity_below_minimum_quantum"
)

_DECIMAL_INPUT_RE = re.compile(
    r"^\+?(?:\d+(?:[.,]\d*)?|[.,]\d+)$"
)


class ShareQuantityError(ValueError):
    pass


class RedeemSharePrecisionError(ShareQuantityError):
    pass


@dataclass(frozen=True)
class BuyShareQuantity:
    full_investment_usdt: Decimal
    settlement_price_usdt: Decimal
    theoretical_shares: Decimal
    issued_shares: Decimal
    rounding_effect_shares: Decimal
    rounding_effect_usdt_at_settlement_price: Decimal

    def audit_dict(self) -> dict[str, Any]:
        return {
            "theoretical_buy_shares": self.theoretical_shares,
            "issued_buy_shares": self.issued_shares,
            "share_rounding_mode": "round_down_4dp",
            "rounding_effect_shares": self.rounding_effect_shares,
            "rounding_effect_usdt_at_settlement_price": (
                self.rounding_effect_usdt_at_settlement_price
            ),
            "full_investment_usdt": self.full_investment_usdt,
            "rounding_effect_is_informational_only": True,
            "rounding_effect_retained_in_fund_nav": True,
            "rounding_effect_refundable": False,
            "rounding_effect_is_fee": False,
        }


def _as_finite_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if value is None:
        raise ShareQuantityError(f"{field_name}_is_required")

    try:
        if isinstance(value, Decimal):
            result = value
        else:
            result = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        raise ShareQuantityError(f"{field_name}_is_invalid")

    if not result.is_finite():
        raise ShareQuantityError(f"{field_name}_must_be_finite")

    return result


def _as_non_negative_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    result = _as_finite_decimal(
        value,
        field_name=field_name,
    )

    if result < ZERO:
        raise ShareQuantityError(
            f"{field_name}_must_not_be_negative"
        )

    return result


def calculate_buy_share_quantity(
    *,
    amount_usdt: Any,
    settlement_price_usdt: Any,
) -> BuyShareQuantity:
    amount = _as_non_negative_decimal(
        amount_usdt,
        field_name="amount_usdt",
    )
    price = _as_non_negative_decimal(
        settlement_price_usdt,
        field_name="settlement_price_usdt",
    )

    if price <= ZERO:
        raise ShareQuantityError(
            "settlement_price_usdt_must_be_positive"
        )

    with localcontext() as context:
        context.prec = 60

        theoretical_shares = amount / price
        issued_shares = theoretical_shares.quantize(
            SHARE_QUANTUM,
            rounding=ROUND_DOWN,
        )
        rounding_effect_shares = theoretical_shares - issued_shares
        rounding_effect_usdt = amount - (issued_shares * price)

    if issued_shares < ZERO:
        raise ShareQuantityError(
            "issued_shares_must_not_be_negative"
        )

    if rounding_effect_shares < ZERO:
        raise ShareQuantityError(
            "rounding_effect_shares_must_not_be_negative"
        )

    if rounding_effect_usdt < ZERO:
        raise ShareQuantityError(
            "rounding_effect_usdt_must_not_be_negative"
        )

    if rounding_effect_shares >= SHARE_QUANTUM:
        raise ShareQuantityError(
            "rounding_effect_shares_exceeds_quantum"
        )

    return BuyShareQuantity(
        full_investment_usdt=amount,
        settlement_price_usdt=price,
        theoretical_shares=theoretical_shares,
        issued_shares=issued_shares,
        rounding_effect_shares=rounding_effect_shares,
        rounding_effect_usdt_at_settlement_price=rounding_effect_usdt,
    )


def calculate_successful_buy_share_quantity(
    *,
    amount_usdt: Any,
    settlement_price_usdt: Any,
) -> BuyShareQuantity:
    quantity = calculate_buy_share_quantity(
        amount_usdt=amount_usdt,
        settlement_price_usdt=settlement_price_usdt,
    )

    if quantity.issued_shares < SHARE_QUANTUM:
        raise ShareQuantityError(
            BUY_SHARE_QUANTITY_BELOW_MINIMUM_ERROR
        )

    return quantity


def require_share_quantity_4dp_aligned(
    value: Any,
    *,
    field_name: str,
    allow_negative: bool = False,
) -> Decimal:
    if allow_negative:
        quantity = _as_finite_decimal(
            value,
            field_name=field_name,
        )
    else:
        quantity = _as_non_negative_decimal(
            value,
            field_name=field_name,
        )

    with localcontext() as context:
        context.prec = 60
        aligned = quantity.quantize(
            SHARE_QUANTUM,
            rounding=ROUND_DOWN,
        )

    if quantity != aligned:
        raise ShareQuantityError(
            f"{field_name}_not_4dp_aligned"
        )

    return quantity


def validate_redeem_share_input_precision(
    raw_value: Any,
) -> Decimal:
    if raw_value is None:
        raise ShareQuantityError("redeem_shares_is_required")

    text = str(raw_value).strip()

    if not text or not _DECIMAL_INPUT_RE.fullmatch(text):
        raise ShareQuantityError("redeem_shares_is_invalid")

    separator_index = max(
        text.rfind("."),
        text.rfind(","),
    )

    fractional_digits = (
        len(text) - separator_index - 1
        if separator_index >= 0
        else 0
    )

    if fractional_digits > 4:
        raise RedeemSharePrecisionError(
            "redeem_shares_precision_exceeded"
        )

    quantity = _as_non_negative_decimal(
        text,
        field_name="redeem_shares",
    )

    if quantity <= ZERO:
        raise ShareQuantityError(
            "redeem_shares_must_be_positive"
        )

    require_share_quantity_4dp_aligned(
        quantity,
        field_name="redeem_shares",
    )

    return quantity