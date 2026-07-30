from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_FLOOR,
)
from typing import Any

from sqlalchemy.orm import Session

from app.models import FundChartDaily
from app.settlement.share_quantity import (
    ShareQuantityError,
    require_share_quantity_4dp_aligned,
)


USDT_CENT = Decimal("0.01")
ZERO = Decimal("0")
DAYS_IN_FEE_MONTH = Decimal("30")

NEGATIVE_NET_FEE_POLICY_VERSION = (
    "monthly_open_v1"
)

NEGATIVE_NET_WITHDRAWAL_AMOUNT_POLICY_VERSION = (
    "withdraw_min_round_up_v1"
)

BYBIT_WITHDRAWAL_FEE_TYPE = 0


class NegativeNetFeeError(RuntimeError):
    pass


class MonthOpenPriceMissingError(NegativeNetFeeError):
    pass


@dataclass(frozen=True)
class MonthOpenPriceResult:
    fund_id: int
    settlement_ts: datetime
    month_start: datetime
    price_usdt: Decimal
    source: str
    chart_daily_id: int | None
    chart_ts_utc: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "settlement_ts": self.settlement_ts.isoformat(),
            "month_start": self.month_start.isoformat(),
            "price_usdt": str(self.price_usdt),
            "source": self.source,
            "chart_daily_id": self.chart_daily_id,
            "chart_ts_utc": (
                self.chart_ts_utc.isoformat()
                if self.chart_ts_utc is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RedeemOrderFeeResult:
    gross_redeem_usdt: Decimal
    success_fee_usdt: Decimal
    management_fee_usdt: Decimal
    partial_month_fee_usdt: Decimal
    net_user_payout_usdt: Decimal
    net_price_usdt: Decimal
    fee_calc_month_open_price_usdt: Decimal
    fee_calc_days_in_month_period: int
    success_fee_rate: Decimal
    management_fee_rate: Decimal
    total_fee_usdt: Decimal
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)
        raw["diagnostics"] = _json_dict(raw["diagnostics"])
        return raw


@dataclass(frozen=True)
class NegativeNetBatchTargets:
    total_gross_redeem_usdt: Decimal
    total_net_user_payout_usdt: Decimal
    total_success_fee_usdt: Decimal
    total_management_fee_usdt: Decimal
    total_partial_month_fee_usdt: Decimal
    bybit_withdrawal_fee_usdt: Decimal
    required_master_usdt: Decimal
    withdrawal_request_amount_usdt: Decimal
    fee_calc_month_open_price_usdt: Decimal
    fee_calc_month_open_source: str
    fee_calc_days_in_month_period: int
    order_count: int
    settlement_wallet_residual_usdt: (
        Decimal
    ) = ZERO

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)
        return raw


def dec(
    value: Any,
    default: str = "0",
) -> Decimal:
    if value is None or value == "":
        result = Decimal(default)
    elif isinstance(value, Decimal):
        result = value
    elif isinstance(value, float):
        raise NegativeNetFeeError(
            "float values are forbidden in "
            "negative-net fee calculations"
        )
    else:
        try:
            result = Decimal(str(value))
        except Exception as exc:
            raise NegativeNetFeeError(
                "Invalid Decimal value: "
                f"{value}"
            ) from exc

    if not result.is_finite():
        raise NegativeNetFeeError(
            "NaN and Infinity are forbidden in "
            "negative-net fee calculations"
        )

    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}


def ensure_aware_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)

    return ts.astimezone(timezone.utc)


PAYOUT_TRUNCATION_POLICY = "floor_to_2_decimals_after_fees"


def truncate_usdt_to_cents_down(value: Decimal | str | int | None) -> Decimal:
    return dec(value).quantize(USDT_CENT, rounding=ROUND_FLOOR)


def floor_usdt_2(value: Decimal | str | int | None) -> Decimal:
    return truncate_usdt_to_cents_down(value)


def _require_positive(
    value: Decimal,
    *,
    field_name: str,
) -> Decimal:
    result = dec(value)

    if result <= ZERO:
        raise NegativeNetFeeError(
            f"{field_name} must be positive: "
            f"{result}"
        )

    return result


def _require_non_negative(
    value: Decimal,
    *,
    field_name: str,
) -> Decimal:
    result = dec(value)

    if result < ZERO:
        raise NegativeNetFeeError(
            f"{field_name} must be non-negative: "
            f"{result}"
        )

    return result


def _require_cent_aligned(
    value: Decimal,
    *,
    field_name: str,
) -> Decimal:
    result = dec(value)

    try:
        aligned = result.quantize(USDT_CENT)
    except Exception as exc:
        raise NegativeNetFeeError(
            f"{field_name} cannot be represented "
            "as USDT cents"
        ) from exc

    if result != aligned:
        raise NegativeNetFeeError(
            f"{field_name} must have at most "
            f"2 decimal places: {result}"
        )

    return result


@dataclass(frozen=True)
class NegativeNetWithdrawalAmountResult:
    total_net_user_payout_usdt: Decimal
    withdrawal_request_amount_usdt: Decimal
    settlement_wallet_residual_usdt: Decimal
    withdraw_min_usdt: Decimal | None
    withdraw_max_usdt: Decimal | None
    min_accuracy: int | None
    fee_type: int
    policy_version: str = (
        NEGATIVE_NET_WITHDRAWAL_AMOUNT_POLICY_VERSION
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "feeType": self.fee_type,
            "total_net_user_payout_usdt": str(
                self.total_net_user_payout_usdt
            ),
            "withdrawal_request_amount_usdt": str(
                self.withdrawal_request_amount_usdt
            ),
            "settlement_wallet_residual_usdt": str(
                self.settlement_wallet_residual_usdt
            ),
            "withdrawMin": (
                str(self.withdraw_min_usdt)
                if self.withdraw_min_usdt
                is not None
                else None
            ),
            "withdrawMax": (
                str(self.withdraw_max_usdt)
                if self.withdraw_max_usdt
                is not None
                else None
            ),
            "minAccuracy": self.min_accuracy,
        }


def _parse_min_accuracy(
    value: int | str | Decimal | None,
) -> int | None:
    if value is None:
        return None

    if isinstance(value, (bool, float)):
        raise NegativeNetFeeError(
            "minAccuracy must be an integer"
        )

    parsed = dec(value)

    if parsed != parsed.to_integral_value():
        raise NegativeNetFeeError(
            "minAccuracy must be an integer: "
            f"{parsed}"
        )

    result = int(parsed)

    if result < 0 or result > 18:
        raise NegativeNetFeeError(
            "minAccuracy is outside supported "
            f"range 0..18: {result}"
        )

    return result


def _parse_fee_type(
    value: int | str | Decimal | None,
) -> int:
    if value is None:
        raise NegativeNetFeeError(
            "feeType is required and must be "
            "integer 0"
        )

    if isinstance(value, (bool, float)):
        raise NegativeNetFeeError(
            "feeType must be integer 0"
        )

    parsed = dec(value)

    if parsed != parsed.to_integral_value():
        raise NegativeNetFeeError(
            "feeType must be integer 0"
        )

    result = int(parsed)

    if result != BYBIT_WITHDRAWAL_FEE_TYPE:
        raise NegativeNetFeeError(
            "Negative-net Bybit withdrawal "
            "must use feeType=0"
        )

    return result


def calculate_negative_net_withdrawal_amount(
    *,
    total_net_user_payout_usdt: (
        Decimal | str
    ),
    withdraw_min_usdt: (
        Decimal | str | None
    ) = None,
    withdraw_max_usdt: (
        Decimal | str | None
    ) = None,
    min_accuracy: (
        int | str | Decimal | None
    ) = None,
    fee_type: (
        int | str | Decimal
    ) = BYBIT_WITHDRAWAL_FEE_TYPE,
) -> NegativeNetWithdrawalAmountResult:
    payout = _require_positive(
        dec(total_net_user_payout_usdt),
        field_name=(
            "total_net_user_payout_usdt"
        ),
    )

    parsed_fee_type = _parse_fee_type(
        fee_type
    )

    live_constraints_present = any(
        value is not None
        for value in (
            withdraw_min_usdt,
            withdraw_max_usdt,
            min_accuracy,
        )
    )

    if not live_constraints_present:
        return NegativeNetWithdrawalAmountResult(
            total_net_user_payout_usdt=payout,
            withdrawal_request_amount_usdt=(
                payout
            ),
            settlement_wallet_residual_usdt=(
                ZERO
            ),
            withdraw_min_usdt=None,
            withdraw_max_usdt=None,
            min_accuracy=None,
            fee_type=parsed_fee_type,
        )

    if withdraw_min_usdt is None:
        raise NegativeNetFeeError(
            "withdrawMin is required for live "
            "withdrawal amount calculation"
        )

    if min_accuracy is None:
        raise NegativeNetFeeError(
            "minAccuracy is required for live "
            "withdrawal amount calculation"
        )

    withdraw_min = _require_non_negative(
        dec(withdraw_min_usdt),
        field_name="withdrawMin",
    )

    withdraw_max: Decimal | None

    if withdraw_max_usdt is None:
        withdraw_max = None
    else:
        withdraw_max = _require_positive(
            dec(withdraw_max_usdt),
            field_name="withdrawMax",
        )

        if withdraw_max < withdraw_min:
            raise NegativeNetFeeError(
                "withdrawMax is below withdrawMin: "
                f"withdrawMin={withdraw_min}, "
                f"withdrawMax={withdraw_max}"
            )

    accuracy = _parse_min_accuracy(
        min_accuracy
    )

    if accuracy is None:
        raise NegativeNetFeeError(
            "minAccuracy is missing"
        )

    quantum = Decimal("1").scaleb(
        -accuracy
    )

    unrounded_amount = max(
        payout,
        withdraw_min,
    )

    try:
        withdrawal_amount = (
            unrounded_amount.quantize(
                quantum,
                rounding=ROUND_CEILING,
            )
        )
    except Exception as exc:
        raise NegativeNetFeeError(
            "Withdrawal amount cannot be "
            "represented using minAccuracy="
            f"{accuracy}"
        ) from exc

    if withdrawal_amount < payout:
        raise NegativeNetFeeError(
            "Withdrawal amount is below total "
            "net user payout"
        )

    if withdrawal_amount < withdraw_min:
        raise NegativeNetFeeError(
            "Withdrawal amount is below "
            "withdrawMin"
        )

    if (
        withdraw_max is not None
        and withdrawal_amount > withdraw_max
    ):
        raise NegativeNetFeeError(
            "Withdrawal amount is above "
            "withdrawMax: "
            f"amount={withdrawal_amount}, "
            f"withdrawMax={withdraw_max}"
        )

    if withdrawal_amount.quantize(
        quantum
    ) != withdrawal_amount:
        raise NegativeNetFeeError(
            "Withdrawal amount does not match "
            f"minAccuracy={accuracy}"
        )

    residual = (
        withdrawal_amount
        - payout
    )

    if residual < ZERO:
        raise NegativeNetFeeError(
            "Settlement wallet residual "
            "must be non-negative"
        )

    return NegativeNetWithdrawalAmountResult(
        total_net_user_payout_usdt=payout,
        withdrawal_request_amount_usdt=(
            withdrawal_amount
        ),
        settlement_wallet_residual_usdt=(
            residual
        ),
        withdraw_min_usdt=withdraw_min,
        withdraw_max_usdt=withdraw_max,
        min_accuracy=accuracy,
        fee_type=parsed_fee_type,
    )


def _validate_order_fee_result(
    item: RedeemOrderFeeResult,
    *,
    index: int,
) -> None:
    gross = _require_positive(
        item.gross_redeem_usdt,
        field_name=(
            f"order_fee_results[{index}]."
            "gross_redeem_usdt"
        ),
    )
    success_fee = _require_non_negative(
        item.success_fee_usdt,
        field_name=(
            f"order_fee_results[{index}]."
            "success_fee_usdt"
        ),
    )
    management_fee = _require_non_negative(
        item.management_fee_usdt,
        field_name=(
            f"order_fee_results[{index}]."
            "management_fee_usdt"
        ),
    )
    partial_fee = _require_non_negative(
        item.partial_month_fee_usdt,
        field_name=(
            f"order_fee_results[{index}]."
            "partial_month_fee_usdt"
        ),
    )

    if partial_fee != (
        success_fee + management_fee
    ):
        raise NegativeNetFeeError(
            "Order fee arithmetic mismatch: "
            f"index={index}, "
            f"partial_month_fee_usdt={partial_fee}, "
            f"success_plus_management="
            f"{success_fee + management_fee}"
        )

    if partial_fee > gross:
        raise NegativeNetFeeError(
            "Order total fee exceeds gross redeem: "
            f"index={index}, "
            f"gross={gross}, fee={partial_fee}"
        )

    raw_net = gross - partial_fee

    _require_positive(
        raw_net,
        field_name=(
            f"order_fee_results[{index}]."
            "raw_net_user_payout_usdt"
        ),
    )

    net_payout = _require_positive(
        item.net_user_payout_usdt,
        field_name=(
            f"order_fee_results[{index}]."
            "net_user_payout_usdt"
        ),
    )

    _require_cent_aligned(
        net_payout,
        field_name=(
            f"order_fee_results[{index}]."
            "net_user_payout_usdt"
        ),
    )

    if net_payout > raw_net:
        raise NegativeNetFeeError(
            "Floored net payout exceeds raw payout: "
            f"index={index}, "
            f"net={net_payout}, raw_net={raw_net}"
        )

    _require_positive(
        item.net_price_usdt,
        field_name=(
            f"order_fee_results[{index}]."
            "net_price_usdt"
        ),
    )


def get_success_fee_rate(fund_code: str) -> Decimal:
    code = str(fund_code or "").strip().lower()

    if code in {"btc_fund", "wb_test"}:
        return Decimal("0.10")

    if code == "defi_sniper":
        return Decimal("0.20")

    return Decimal("0")


def get_management_fee_rate(fund_code: str) -> Decimal:
    code = str(fund_code or "").strip().lower()

    if code == "defi_sniper":
        return Decimal("0.0017")

    return Decimal("0.00083")


def get_days_in_month_period(settlement_ts: datetime) -> int:
    return int(ensure_aware_utc(settlement_ts).day)


def _month_start_utc(settlement_ts: datetime) -> datetime:
    ts = ensure_aware_utc(settlement_ts)
    return datetime(ts.year, ts.month, 1, tzinfo=timezone.utc)


def _next_month_start_utc(month_start: datetime) -> datetime:
    month_start = ensure_aware_utc(month_start)

    if month_start.month == 12:
        return datetime(month_start.year + 1, 1, 1, tzinfo=timezone.utc)

    return datetime(month_start.year, month_start.month + 1, 1, tzinfo=timezone.utc)


def get_month_open_price(
    db: Session,
    *,
    fund_id: int,
    settlement_ts: datetime,
) -> MonthOpenPriceResult:
    settlement_ts = ensure_aware_utc(settlement_ts)
    month_start = _month_start_utc(settlement_ts)
    next_month_start = _next_month_start_utc(month_start)

    current_month_row = (
        db.query(FundChartDaily)
        .filter(
            FundChartDaily.fund_id == int(fund_id),
            FundChartDaily.ts_utc >= month_start,
            FundChartDaily.ts_utc < next_month_start,
        )
        .order_by(FundChartDaily.ts_utc.asc(), FundChartDaily.id.asc())
        .first()
    )

    if current_month_row is not None:
        return MonthOpenPriceResult(
            fund_id=int(fund_id),
            settlement_ts=settlement_ts,
            month_start=month_start,
            price_usdt=dec(current_month_row.open),
            source="current_month_open",
            chart_daily_id=current_month_row.id,
            chart_ts_utc=ensure_aware_utc(current_month_row.ts_utc),
        )

    fallback_row = (
        db.query(FundChartDaily)
        .filter(
            FundChartDaily.fund_id == int(fund_id),
            FundChartDaily.ts_utc < month_start,
        )
        .order_by(FundChartDaily.ts_utc.desc(), FundChartDaily.id.desc())
        .first()
    )

    if fallback_row is not None:
        return MonthOpenPriceResult(
            fund_id=int(fund_id),
            settlement_ts=settlement_ts,
            month_start=month_start,
            price_usdt=dec(fallback_row.close),
            source="fallback_last_before_month_start",
            chart_daily_id=fallback_row.id,
            chart_ts_utc=ensure_aware_utc(fallback_row.ts_utc),
        )

    raise MonthOpenPriceMissingError(
        (
            "Month open price is missing and no fallback daily chart price exists: "
            f"fund_id={fund_id}, month_start={month_start.isoformat()}"
        )
    )


def calculate_redeem_order_fees(
    *,
    fund_code: str,
    settlement_price_usdt: Decimal | str,
    redeem_shares: Decimal | str,
    month_open_price_usdt: Decimal | str,
    settlement_ts: datetime,
) -> RedeemOrderFeeResult:
    p_t = _require_positive(
        dec(settlement_price_usdt),
        field_name="settlement_price_usdt",
    )
    p_b = _require_positive(
        dec(month_open_price_usdt),
        field_name="month_open_price_usdt",
    )

    try:
        n_s = require_share_quantity_4dp_aligned(
            redeem_shares,
            field_name="redeem_shares",
        )
    except ShareQuantityError as exc:
        raise NegativeNetFeeError(
            str(exc)
        ) from exc

    _require_positive(
        n_s,
        field_name="redeem_shares",
    )

    n_d = get_days_in_month_period(
        settlement_ts
    )

    if n_d <= 0 or n_d > 31:
        raise NegativeNetFeeError(
            "fee_calc_days_in_month_period "
            f"is invalid: {n_d}"
        )

    success_fee_rate = _require_non_negative(
        get_success_fee_rate(fund_code),
        field_name="success_fee_rate",
    )
    management_fee_rate = _require_non_negative(
        get_management_fee_rate(fund_code),
        field_name="management_fee_rate",
    )

    gross_redeem_usdt = _require_positive(
        p_t * n_s,
        field_name="gross_redeem_usdt",
    )

    success_profit_per_share = p_t - p_b

    if (
        success_profit_per_share > ZERO
        and success_fee_rate > ZERO
    ):
        success_fee_usdt = (
            success_profit_per_share
            * success_fee_rate
            * n_s
        )
    else:
        success_fee_usdt = ZERO

    success_fee_usdt = _require_non_negative(
        success_fee_usdt,
        field_name="success_fee_usdt",
    )

    management_fee_usdt = (
        p_t
        * n_s
        * management_fee_rate
        * (
            Decimal(n_d)
            / DAYS_IN_FEE_MONTH
        )
    )
    management_fee_usdt = (
        _require_non_negative(
            management_fee_usdt,
            field_name="management_fee_usdt",
        )
    )

    partial_month_fee_usdt = (
        success_fee_usdt
        + management_fee_usdt
    )
    partial_month_fee_usdt = (
        _require_non_negative(
            partial_month_fee_usdt,
            field_name=(
                "partial_month_fee_usdt"
            ),
        )
    )

    total_fee_usdt = (
        partial_month_fee_usdt
    )

    if total_fee_usdt > gross_redeem_usdt:
        raise NegativeNetFeeError(
            "Redeem total fee exceeds gross redeem: "
            f"gross={gross_redeem_usdt}, "
            f"fee={total_fee_usdt}"
        )

    raw_net_user_payout_usdt = (
        gross_redeem_usdt
        - total_fee_usdt
    )
    _require_positive(
        raw_net_user_payout_usdt,
        field_name=(
            "raw_net_user_payout_usdt"
        ),
    )

    net_user_payout_usdt = (
        truncate_usdt_to_cents_down(
            raw_net_user_payout_usdt
        )
    )
    _require_positive(
        net_user_payout_usdt,
        field_name="net_user_payout_usdt",
    )
    _require_cent_aligned(
        net_user_payout_usdt,
        field_name="net_user_payout_usdt",
    )

    if (
        net_user_payout_usdt
        > raw_net_user_payout_usdt
    ):
        raise NegativeNetFeeError(
            "Floored net payout exceeds raw payout"
        )

    payout_truncation_remainder_usdt = (
        raw_net_user_payout_usdt
        - net_user_payout_usdt
    )
    _require_non_negative(
        payout_truncation_remainder_usdt,
        field_name=(
            "payout_truncation_remainder_usdt"
        ),
    )

    if (
        payout_truncation_remainder_usdt
        >= USDT_CENT
    ):
        raise NegativeNetFeeError(
            "Payout truncation remainder must "
            "be below one cent: "
            f"{payout_truncation_remainder_usdt}"
        )

    net_price_usdt = (
        p_t
        - (
            total_fee_usdt
            / n_s
        )
    )
    _require_positive(
        net_price_usdt,
        field_name="net_price_usdt",
    )

    result = RedeemOrderFeeResult(
        gross_redeem_usdt=(
            gross_redeem_usdt
        ),
        success_fee_usdt=(
            success_fee_usdt
        ),
        management_fee_usdt=(
            management_fee_usdt
        ),
        partial_month_fee_usdt=(
            partial_month_fee_usdt
        ),
        net_user_payout_usdt=(
            net_user_payout_usdt
        ),
        net_price_usdt=net_price_usdt,
        fee_calc_month_open_price_usdt=p_b,
        fee_calc_days_in_month_period=n_d,
        success_fee_rate=success_fee_rate,
        management_fee_rate=(
            management_fee_rate
        ),
        total_fee_usdt=total_fee_usdt,
        diagnostics={
            "fee_policy_version": (
                NEGATIVE_NET_FEE_POLICY_VERSION
            ),
            "fund_code": fund_code,
            "settlement_price_usdt": p_t,
            "redeem_shares": n_s,
            "month_open_price_usdt": p_b,
            "success_profit_per_share": (
                success_profit_per_share
            ),
            "days_in_month_period": n_d,
            "gross_redeem_usdt": (
                gross_redeem_usdt
            ),
            "success_fee_usdt": (
                success_fee_usdt
            ),
            "management_fee_usdt": (
                management_fee_usdt
            ),
            "total_fee_usdt": (
                total_fee_usdt
            ),
            "raw_net_user_payout_usdt": (
                raw_net_user_payout_usdt
            ),
            "payout_truncation_policy": (
                PAYOUT_TRUNCATION_POLICY
            ),
            "payout_truncation_remainder_usdt": (
                payout_truncation_remainder_usdt
            ),
            "net_user_payout_usdt": (
                net_user_payout_usdt
            ),
        },
    )

    _validate_order_fee_result(
        result,
        index=0,
    )

    return result


def calculate_negative_net_batch_targets(
    *,
    order_fee_results: list[
        RedeemOrderFeeResult
    ],
    bybit_withdrawal_fee_usdt: (
        Decimal | str
    ),
    month_open_result: (
        MonthOpenPriceResult
    ),
    withdraw_min_usdt: (
        Decimal | str | None
    ) = None,
    withdraw_max_usdt: (
        Decimal | str | None
    ) = None,
    min_accuracy: (
        int | str | Decimal | None
    ) = None,
    fee_type: (
        int | str | Decimal
    ) = BYBIT_WITHDRAWAL_FEE_TYPE,
) -> NegativeNetBatchTargets:
    if not order_fee_results:
        raise NegativeNetFeeError(
            "At least one redeem fee result "
            "is required"
        )

    for index, item in enumerate(
        order_fee_results
    ):
        _validate_order_fee_result(
            item,
            index=index,
        )

    bybit_fee = _require_non_negative(
        dec(bybit_withdrawal_fee_usdt),
        field_name=(
            "bybit_withdrawal_fee_usdt"
        ),
    )

    total_gross_redeem_usdt = sum(
        (
            item.gross_redeem_usdt
            for item in order_fee_results
        ),
        ZERO,
    )
    total_success_fee_usdt = sum(
        (
            item.success_fee_usdt
            for item in order_fee_results
        ),
        ZERO,
    )
    total_management_fee_usdt = sum(
        (
            item.management_fee_usdt
            for item in order_fee_results
        ),
        ZERO,
    )
    total_partial_month_fee_usdt = sum(
        (
            item.partial_month_fee_usdt
            for item in order_fee_results
        ),
        ZERO,
    )
    total_net_user_payout_usdt = sum(
        (
            item.net_user_payout_usdt
            for item in order_fee_results
        ),
        ZERO,
    )

    _require_positive(
        total_gross_redeem_usdt,
        field_name=(
            "total_gross_redeem_usdt"
        ),
    )
    _require_non_negative(
        total_success_fee_usdt,
        field_name=(
            "total_success_fee_usdt"
        ),
    )
    _require_non_negative(
        total_management_fee_usdt,
        field_name=(
            "total_management_fee_usdt"
        ),
    )
    _require_non_negative(
        total_partial_month_fee_usdt,
        field_name=(
            "total_partial_month_fee_usdt"
        ),
    )
    _require_positive(
        total_net_user_payout_usdt,
        field_name=(
            "total_net_user_payout_usdt"
        ),
    )
    _require_cent_aligned(
        total_net_user_payout_usdt,
        field_name=(
            "total_net_user_payout_usdt"
        ),
    )

    expected_total_fee = (
        total_success_fee_usdt
        + total_management_fee_usdt
    )

    if (
        total_partial_month_fee_usdt
        != expected_total_fee
    ):
        raise NegativeNetFeeError(
            "Batch fee arithmetic mismatch: "
            "total_partial_month_fee_usdt="
            f"{total_partial_month_fee_usdt}, "
            "success_plus_management="
            f"{expected_total_fee}"
        )

    if (
        total_partial_month_fee_usdt
        > total_gross_redeem_usdt
    ):
        raise NegativeNetFeeError(
            "Batch total fee exceeds gross redeem"
        )

    withdrawal_amount_result = (
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                total_net_user_payout_usdt
            ),
            withdraw_min_usdt=(
                withdraw_min_usdt
            ),
            withdraw_max_usdt=(
                withdraw_max_usdt
            ),
            min_accuracy=min_accuracy,
            fee_type=fee_type,
        )
    )

    withdrawal_request_amount_usdt = (
        withdrawal_amount_result
        .withdrawal_request_amount_usdt
    )

    settlement_wallet_residual_usdt = (
        withdrawal_amount_result
        .settlement_wallet_residual_usdt
    )

    required_master_usdt = (
        withdrawal_request_amount_usdt
        + bybit_fee
        + total_partial_month_fee_usdt
    )

    _require_positive(
        required_master_usdt,
        field_name="required_master_usdt",
    )

    days_values = {
        int(
            item.fee_calc_days_in_month_period
        )
        for item in order_fee_results
    }

    if len(days_values) > 1:
        raise NegativeNetFeeError(
            "Inconsistent "
            "fee_calc_days_in_month_period "
            f"values: {sorted(days_values)}"
        )

    fee_days = next(iter(days_values))

    if fee_days <= 0 or fee_days > 31:
        raise NegativeNetFeeError(
            "fee_calc_days_in_month_period "
            f"is invalid: {fee_days}"
        )

    month_open_price = _require_positive(
        month_open_result.price_usdt,
        field_name=(
            "fee_calc_month_open_price_usdt"
        ),
    )

    return NegativeNetBatchTargets(
        total_gross_redeem_usdt=(
            total_gross_redeem_usdt
        ),
        total_net_user_payout_usdt=(
            total_net_user_payout_usdt
        ),
        total_success_fee_usdt=(
            total_success_fee_usdt
        ),
        total_management_fee_usdt=(
            total_management_fee_usdt
        ),
        total_partial_month_fee_usdt=(
            total_partial_month_fee_usdt
        ),
        bybit_withdrawal_fee_usdt=(
            bybit_fee
        ),
        required_master_usdt=(
            required_master_usdt
        ),
        withdrawal_request_amount_usdt=(
            withdrawal_request_amount_usdt
        ),
        settlement_wallet_residual_usdt=(
            settlement_wallet_residual_usdt
        ),
        fee_calc_month_open_price_usdt=(
            month_open_price
        ),
        fee_calc_month_open_source=(
            month_open_result.source
        ),
        fee_calc_days_in_month_period=(
            fee_days
        ),
        order_count=len(
            order_fee_results
        ),
    )
