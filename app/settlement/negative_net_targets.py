from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import Fund, FundOrder, FundSettlementBatch
from app.settlement.negative_net_fees import (
    MonthOpenPriceMissingError,
    NegativeNetBatchTargets,
    NegativeNetFeeError,
    RedeemOrderFeeResult,
    calculate_negative_net_batch_targets,
    calculate_redeem_order_fees,
    dec,
    get_month_open_price,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
)


ORDER_SIDE_REDEEM = "redeem"
REDEEM_ORDER_TARGET_STATUSES = {
    "pending",
    "processing",
}


class NegativeNetTargetError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativeNetOrderTargetResult:
    order_id: int
    gross_redeem_usdt: Decimal
    success_fee_usdt: Decimal
    management_fee_usdt: Decimal
    partial_month_fee_usdt: Decimal
    net_user_payout_usdt: Decimal
    net_price_usdt: Decimal
    success_fee_rate: Decimal
    management_fee_rate: Decimal

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)
        return raw


@dataclass(frozen=True)
class NegativeNetTargetResult:
    ok: bool
    settlement_batch_id: int
    fund_id: int
    fund_code: str
    status_before: str
    status_after: str
    order_count: int
    bybit_withdrawal_fee_usdt: Decimal
    batch_targets: NegativeNetBatchTargets | None
    order_results: list[NegativeNetOrderTargetResult]
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "settlement_batch_id": self.settlement_batch_id,
            "fund_id": self.fund_id,
            "fund_code": self.fund_code,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "order_count": self.order_count,
            "bybit_withdrawal_fee_usdt": str(self.bybit_withdrawal_fee_usdt),
            "batch_targets": (
                self.batch_targets.to_dict()
                if self.batch_targets is not None
                else None
            ),
            "order_results": [
                item.to_dict()
                for item in self.order_results
            ],
            "error": self.error,
            "diagnostics": _json_dict(self.diagnostics),
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _lock_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )

    if batch is None:
        raise NegativeNetTargetError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return batch


def _get_fund(
    db: Session,
    *,
    fund_id: int,
) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .first()
    )

    if fund is None:
        raise NegativeNetTargetError(
            f"Fund not found: {fund_id}"
        )

    return fund


def _validate_batch_for_negative_net_targets(batch: FundSettlementBatch) -> None:
    if batch.status != BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION:
        raise NegativeNetTargetError(
            (
                "Settlement batch must be awaiting_negative_net_execution: "
                f"batch_id={batch.id}, status={batch.status}"
            )
        )

    if dec(batch.net_cash_usdt) >= Decimal("0"):
        raise NegativeNetTargetError(
            (
                "Settlement batch is not negative-net: "
                f"batch_id={batch.id}, net_cash_usdt={batch.net_cash_usdt}"
            )
        )

    if batch.settlement_ts is None:
        raise NegativeNetTargetError(
            f"Settlement batch settlement_ts is missing: batch_id={batch.id}"
        )

    if dec(batch.settlement_price_usdt) <= Decimal("0"):
        raise NegativeNetTargetError(
            (
                "Settlement batch settlement_price_usdt must be positive: "
                f"batch_id={batch.id}, settlement_price_usdt={batch.settlement_price_usdt}"
            )
        )


def _load_redeem_orders_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id == int(settlement_batch_id),
            FundOrder.side == ORDER_SIDE_REDEEM,
            FundOrder.status.in_(list(REDEEM_ORDER_TARGET_STATUSES)),
        )
        .order_by(FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _apply_fee_result_to_order(
    *,
    order: FundOrder,
    fee_result: RedeemOrderFeeResult,
) -> NegativeNetOrderTargetResult:
    order.gross_redeem_usdt = fee_result.gross_redeem_usdt
    order.success_fee_usdt = fee_result.success_fee_usdt
    order.management_fee_usdt = fee_result.management_fee_usdt
    order.partial_month_fee_usdt = fee_result.partial_month_fee_usdt
    order.net_user_payout_usdt = fee_result.net_user_payout_usdt
    order.net_price_usdt = fee_result.net_price_usdt
    order.fee_calc_month_open_price_usdt = fee_result.fee_calc_month_open_price_usdt
    order.fee_calc_days_in_month_period = fee_result.fee_calc_days_in_month_period
    order.success_fee_rate = fee_result.success_fee_rate
    order.management_fee_rate = fee_result.management_fee_rate

    return NegativeNetOrderTargetResult(
        order_id=order.id,
        gross_redeem_usdt=fee_result.gross_redeem_usdt,
        success_fee_usdt=fee_result.success_fee_usdt,
        management_fee_usdt=fee_result.management_fee_usdt,
        partial_month_fee_usdt=fee_result.partial_month_fee_usdt,
        net_user_payout_usdt=fee_result.net_user_payout_usdt,
        net_price_usdt=fee_result.net_price_usdt,
        success_fee_rate=fee_result.success_fee_rate,
        management_fee_rate=fee_result.management_fee_rate,
    )


def _apply_batch_targets(
    *,
    batch: FundSettlementBatch,
    targets: NegativeNetBatchTargets,
    calculated_at: datetime,
) -> None:
    batch.total_gross_redeem_usdt = targets.total_gross_redeem_usdt
    batch.total_net_user_payout_usdt = targets.total_net_user_payout_usdt
    batch.total_success_fee_usdt = targets.total_success_fee_usdt
    batch.total_management_fee_usdt = targets.total_management_fee_usdt
    batch.total_partial_month_fee_usdt = targets.total_partial_month_fee_usdt
    batch.bybit_withdrawal_fee_usdt = targets.bybit_withdrawal_fee_usdt
    batch.required_master_usdt = targets.required_master_usdt
    batch.withdrawal_request_amount_usdt = targets.withdrawal_request_amount_usdt
    batch.negative_net_target_calculated_at = calculated_at
    batch.fee_calc_month_open_price_usdt = targets.fee_calc_month_open_price_usdt
    batch.fee_calc_month_open_source = targets.fee_calc_month_open_source
    batch.fee_calc_days_in_month_period = targets.fee_calc_days_in_month_period
    batch.status = BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED
    batch.error = None
    batch.updated_at = calculated_at


def _mark_batch_failed_requires_review(
    db: Session,
    *,
    batch: FundSettlementBatch,
    status_before: str,
    fund: Fund,
    bybit_withdrawal_fee_usdt: Decimal,
    error: str,
    diagnostics: dict[str, Any],
    now: datetime,
) -> NegativeNetTargetResult:
    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now

    db.add(batch)
    db.flush()

    return NegativeNetTargetResult(
        ok=False,
        settlement_batch_id=batch.id,
        fund_id=batch.fund_id,
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=batch.status,
        order_count=0,
        bybit_withdrawal_fee_usdt=bybit_withdrawal_fee_usdt,
        batch_targets=None,
        order_results=[],
        error=error,
        diagnostics=diagnostics,
    )


def calculate_and_store_negative_net_targets(
    db: Session,
    *,
    settlement_batch_id: int,
    bybit_withdrawal_fee_usdt: Decimal | str,
    now: datetime | None = None,
) -> NegativeNetTargetResult:
    """
    Stage 23.1 negative-net target calculation.

    Safety policy:
    - no Bybit calls;
    - no Bybit transfers/withdrawals;
    - no BSC transfers;
    - no accounting finalization;
    - no user position mutation;
    - no shares_outstanding_current mutation;
    - no order success finalization.
    """
    now = now or utcnow()
    bybit_fee = dec(bybit_withdrawal_fee_usdt)

    if bybit_fee < Decimal("0"):
        raise NegativeNetTargetError(
            f"bybit_withdrawal_fee_usdt must be non-negative: {bybit_fee}"
        )

    batch = _lock_settlement_batch(
        db,
        settlement_batch_id=settlement_batch_id,
    )
    status_before = str(batch.status)

    fund = _get_fund(
        db,
        fund_id=batch.fund_id,
    )

    try:
        _validate_batch_for_negative_net_targets(batch)

        month_open = get_month_open_price(
            db,
            fund_id=batch.fund_id,
            settlement_ts=batch.settlement_ts,
        )

        redeem_orders = _load_redeem_orders_for_update(
            db,
            settlement_batch_id=batch.id,
        )

        if not redeem_orders:
            raise NegativeNetTargetError(
                f"No redeem orders found for negative-net batch: batch_id={batch.id}"
            )

        order_fee_results: list[RedeemOrderFeeResult] = []
        order_results: list[NegativeNetOrderTargetResult] = []

        for order in redeem_orders:
            fee_result = calculate_redeem_order_fees(
                fund_code=str(fund.code),
                settlement_price_usdt=batch.settlement_price_usdt,
                redeem_shares=order.shares,
                month_open_price_usdt=month_open.price_usdt,
                settlement_ts=batch.settlement_ts,
            )

            order_fee_results.append(fee_result)

            order_result = _apply_fee_result_to_order(
                order=order,
                fee_result=fee_result,
            )
            order_results.append(order_result)

            db.add(order)

        targets = calculate_negative_net_batch_targets(
            order_fee_results=order_fee_results,
            bybit_withdrawal_fee_usdt=bybit_fee,
            month_open_result=month_open,
        )

        _apply_batch_targets(
            batch=batch,
            targets=targets,
            calculated_at=now,
        )

        db.add(batch)
        db.flush()

        return NegativeNetTargetResult(
            ok=True,
            settlement_batch_id=batch.id,
            fund_id=batch.fund_id,
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=batch.status,
            order_count=len(redeem_orders),
            bybit_withdrawal_fee_usdt=bybit_fee,
            batch_targets=targets,
            order_results=order_results,
            error=None,
            diagnostics={
                "month_open": month_open.to_dict(),
                "no_real_bybit_calls": True,
                "no_bsc_transfers": True,
                "no_accounting_finalization": True,
            },
        )

    except MonthOpenPriceMissingError as exc:
        return _mark_batch_failed_requires_review(
            db,
            batch=batch,
            status_before=status_before,
            fund=fund,
            bybit_withdrawal_fee_usdt=bybit_fee,
            error=f"month_open_price_missing: {exc}",
            diagnostics={
                "error_type": type(exc).__name__,
                "controlled_failure": True,
            },
            now=now,
        )

    except NegativeNetFeeError as exc:
        return _mark_batch_failed_requires_review(
            db,
            batch=batch,
            status_before=status_before,
            fund=fund,
            bybit_withdrawal_fee_usdt=bybit_fee,
            error=f"negative_net_fee_error: {exc}",
            diagnostics={
                "error_type": type(exc).__name__,
                "controlled_failure": True,
            },
            now=now,
        )
