from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from sqlalchemy.orm import Session

from app.models import FundChartDaily


USDT_CENT = Decimal("0.01")
ZERO = Decimal("0")
DAYS_IN_FEE_MONTH = Decimal("30")


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

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)
        return raw


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


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


def floor_usdt_2(value: Decimal | str | int | float | None) -> Decimal:
    return dec(value).quantize(USDT_CENT, rounding=ROUND_FLOOR)


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
    p_t = dec(settlement_price_usdt)
    n_s = dec(redeem_shares)
    p_b = dec(month_open_price_usdt)

    if p_t <= ZERO:
        raise NegativeNetFeeError(
            f"settlement_price_usdt must be positive: {p_t}"
        )

    if n_s <= ZERO:
        raise NegativeNetFeeError(
            f"redeem_shares must be positive: {n_s}"
        )

    if p_b <= ZERO:
        raise NegativeNetFeeError(
            f"month_open_price_usdt must be positive: {p_b}"
        )

    n_d = get_days_in_month_period(settlement_ts)

    success_fee_rate = get_success_fee_rate(fund_code)
    management_fee_rate = get_management_fee_rate(fund_code)

    gross_redeem_usdt = p_t * n_s

    success_profit_per_share = p_t - p_b
    if success_profit_per_share > ZERO and success_fee_rate > ZERO:
        success_fee_usdt = success_profit_per_share * success_fee_rate * n_s
    else:
        success_fee_usdt = ZERO

    management_fee_usdt = (
        p_t
        * n_s
        * management_fee_rate
        * (Decimal(n_d) / DAYS_IN_FEE_MONTH)
    )

    # Stage 23.1.1:
    # partial_month_fee_usdt is the total redeem fee shown in trade history:
    # success fee + partial-month management fee.
    partial_month_fee_usdt = success_fee_usdt + management_fee_usdt

    total_fee_usdt = partial_month_fee_usdt
    net_user_payout_usdt = floor_usdt_2(gross_redeem_usdt - total_fee_usdt)
    net_price_usdt = p_t - (total_fee_usdt / n_s)

    return RedeemOrderFeeResult(
        gross_redeem_usdt=gross_redeem_usdt,
        success_fee_usdt=success_fee_usdt,
        management_fee_usdt=management_fee_usdt,
        partial_month_fee_usdt=partial_month_fee_usdt,
        net_user_payout_usdt=net_user_payout_usdt,
        net_price_usdt=net_price_usdt,
        fee_calc_month_open_price_usdt=p_b,
        fee_calc_days_in_month_period=n_d,
        success_fee_rate=success_fee_rate,
        management_fee_rate=management_fee_rate,
        total_fee_usdt=total_fee_usdt,
        diagnostics={
            "fund_code": fund_code,
            "settlement_price_usdt": p_t,
            "redeem_shares": n_s,
            "month_open_price_usdt": p_b,
            "success_profit_per_share": success_profit_per_share,
            "days_in_month_period": n_d,
        },
    )


def calculate_negative_net_batch_targets(
    *,
    order_fee_results: list[RedeemOrderFeeResult],
    bybit_withdrawal_fee_usdt: Decimal | str,
    month_open_result: MonthOpenPriceResult,
) -> NegativeNetBatchTargets:
    bybit_fee = dec(bybit_withdrawal_fee_usdt)

    if bybit_fee < ZERO:
        raise NegativeNetFeeError(
            f"bybit_withdrawal_fee_usdt must be non-negative: {bybit_fee}"
        )

    total_gross_redeem_usdt = sum(
        (item.gross_redeem_usdt for item in order_fee_results),
        ZERO,
    )
    total_success_fee_usdt = sum(
        (item.success_fee_usdt for item in order_fee_results),
        ZERO,
    )
    total_management_fee_usdt = sum(
        (item.management_fee_usdt for item in order_fee_results),
        ZERO,
    )
    total_partial_month_fee_usdt = sum(
        (item.partial_month_fee_usdt for item in order_fee_results),
        ZERO,
    )

    # Per Stage 23.1 policy: floor is applied per order before summing.
    total_net_user_payout_usdt = sum(
        (item.net_user_payout_usdt for item in order_fee_results),
        ZERO,
    )

    required_master_usdt = (
        total_net_user_payout_usdt
        + bybit_fee
        + total_partial_month_fee_usdt
    )

    withdrawal_request_amount_usdt = total_net_user_payout_usdt

    days_values = {
        item.fee_calc_days_in_month_period
        for item in order_fee_results
    }
    if len(days_values) > 1:
        raise NegativeNetFeeError(
            f"Inconsistent fee_calc_days_in_month_period values: {sorted(days_values)}"
        )

    fee_days = (
        next(iter(days_values))
        if days_values
        else get_days_in_month_period(month_open_result.settlement_ts)
    )

    return NegativeNetBatchTargets(
        total_gross_redeem_usdt=total_gross_redeem_usdt,
        total_net_user_payout_usdt=total_net_user_payout_usdt,
        total_success_fee_usdt=total_success_fee_usdt,
        total_management_fee_usdt=total_management_fee_usdt,
        total_partial_month_fee_usdt=total_partial_month_fee_usdt,
        bybit_withdrawal_fee_usdt=bybit_fee,
        required_master_usdt=required_master_usdt,
        withdrawal_request_amount_usdt=withdrawal_request_amount_usdt,
        fee_calc_month_open_price_usdt=month_open_result.price_usdt,
        fee_calc_month_open_source=month_open_result.source,
        fee_calc_days_in_month_period=fee_days,
        order_count=len(order_fee_results),
    )
