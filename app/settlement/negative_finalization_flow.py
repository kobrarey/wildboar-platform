from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativeSaleBatch,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    UserFundPosition,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    FINALIZATION_BATCH_STATUS_ACCOUNTING_FINALIZED,
    FINALIZATION_BATCH_STATUS_ACCOUNTING_PROCESSING,
    FINALIZATION_BATCH_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_CREATED,
    FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    FINALIZATION_BATCH_STATUS_PRICING_UNLOCKED,
    FINALIZATION_BATCH_STATUS_VALIDATING,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SUCCESS,
    PRICING_LOCK_REASON_SETTLEMENT,
)


ZERO = Decimal("0")


class NegativeFinalizationFlowError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativeFinalizationResult:
    ok: bool
    finalization_batch_id: int | None
    settlement_batch_id: int
    payout_batch_id: int | None
    bybit_flow_id: int | None
    sale_batch_id: int | None
    fund_id: int | None
    fund_code: str | None
    status_before: str | None
    status_after: str | None
    settlement_status_before: str | None
    settlement_status_after: str | None
    buy_order_count: int | None = None
    redeem_order_count: int | None = None
    success_order_count: int | None = None
    shares_outstanding_before: str | None = None
    shares_outstanding_after: str | None = None
    idempotent: bool = False
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(asdict(self))


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


def dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q10(value: Any) -> Decimal:
    return dec(value).quantize(Decimal("0.0000000001"))


def _same_decimal(left: Any, right: Any) -> bool:
    return q10(left) == q10(right)


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
        raise NegativeFinalizationFlowError(
            f"Settlement batch not found: {settlement_batch_id}"
        )
    return batch


def _lock_completed_payout_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativePayoutBatch:
    batch = (
        db.query(FundNegativePayoutBatch)
        .filter(FundNegativePayoutBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if batch is None:
        raise NegativeFinalizationFlowError(
            f"Negative payout batch not found for settlement_batch_id={settlement_batch_id}"
        )
    if batch.status != "completed":
        raise NegativeFinalizationFlowError("Negative payout batch must be completed")
    return batch


def _lock_bybit_flow(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeBybitFlow | None:
    return (
        db.query(FundNegativeBybitFlow)
        .filter(FundNegativeBybitFlow.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )


def _lock_sale_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeSaleBatch | None:
    return (
        db.query(FundNegativeSaleBatch)
        .filter(FundNegativeSaleBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )


def _lock_existing_finalization(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeFinalizationBatch | None:
    return (
        db.query(FundNegativeFinalizationBatch)
        .filter(FundNegativeFinalizationBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )


def _get_fund(db: Session, *, fund_id: int) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .with_for_update()
        .first()
    )
    if fund is None:
        raise NegativeFinalizationFlowError(f"Fund not found: {fund_id}")
    return fund


def _lock_position(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPosition:
    position = (
        db.query(UserFundPosition)
        .filter(UserFundPosition.user_id == int(user_id))
        .filter(UserFundPosition.fund_id == int(fund_id))
        .with_for_update()
        .first()
    )
    if position is None:
        position = UserFundPosition(
            user_id=int(user_id),
            fund_id=int(fund_id),
            shares=ZERO,
            shares_reserved=ZERO,
        )
        db.add(position)
        db.flush()
    return position


def _load_finalizable_orders(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    excluded_statuses = {
        ORDER_STATUS_SUCCESS,
        ORDER_STATUS_FAILED,
        ORDER_STATUS_FAILED_REQUIRES_REVIEW,
        ORDER_STATUS_CANCELLED,
    }

    orders = (
        db.query(FundOrder)
        .filter(FundOrder.settlement_batch_id == int(settlement_batch_id))
        .filter(~FundOrder.status.in_(excluded_statuses))
        .order_by(FundOrder.user_id.asc(), FundOrder.id.asc())
        .with_for_update()
        .all()
    )

    if not orders:
        raise NegativeFinalizationFlowError("No finalizable orders found")

    return orders


def _new_or_existing_finalization(
    db: Session,
    *,
    existing: FundNegativeFinalizationBatch | None,
    settlement_batch: FundSettlementBatch,
    payout_batch: FundNegativePayoutBatch,
    bybit_flow: FundNegativeBybitFlow | None,
    sale_batch: FundNegativeSaleBatch | None,
    fund: Fund,
) -> FundNegativeFinalizationBatch:
    if existing is not None:
        return existing

    price = q10(settlement_batch.settlement_price_usdt)
    shares_before = q10(
        settlement_batch.shares_outstanding_before
        if settlement_batch.shares_outstanding_before is not None
        else fund.shares_outstanding_current
    )

    row = FundNegativeFinalizationBatch(
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(payout_batch.id),
        bybit_flow_id=int(bybit_flow.id) if bybit_flow is not None else None,
        sale_batch_id=int(sale_batch.id) if sale_batch is not None else None,
        fund_id=int(fund.id),
        status=FINALIZATION_BATCH_STATUS_CREATED,
        settlement_price_usdt=price,
        shares_outstanding_before=shares_before,
    )
    db.add(row)
    db.flush()
    return row


def _set_failed(
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund | None,
    status_before: str | None,
    settlement_status_before: str | None,
    error: str,
    now,
    diagnostics: dict[str, Any] | None = None,
) -> NegativeFinalizationResult:
    finalization.status = FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    finalization.error = error
    finalization.updated_at = now
    finalization.reconciliation_json = _json_dict(
        {
            "ok": False,
            "error": error,
            "diagnostics": diagnostics or {},
        }
    )
    finalization.report_json = _json_dict(
        {
            "stage": "25",
            "ok": False,
            "error": error,
            "final_state": FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
        }
    )

    settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    settlement_batch.error = error
    settlement_batch.updated_at = now

    return NegativeFinalizationResult(
        ok=False,
        finalization_batch_id=int(finalization.id) if finalization.id is not None else None,
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(finalization.payout_batch_id) if finalization.payout_batch_id is not None else None,
        bybit_flow_id=int(finalization.bybit_flow_id) if finalization.bybit_flow_id is not None else None,
        sale_batch_id=int(finalization.sale_batch_id) if finalization.sale_batch_id is not None else None,
        fund_id=int(finalization.fund_id) if finalization.fund_id is not None else None,
        fund_code=str(fund.code) if fund is not None else None,
        status_before=status_before,
        status_after=finalization.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        buy_order_count=finalization.buy_order_count,
        redeem_order_count=finalization.redeem_order_count,
        success_order_count=finalization.success_order_count,
        shares_outstanding_before=str(finalization.shares_outstanding_before),
        shares_outstanding_after=(
            str(finalization.shares_outstanding_after)
            if finalization.shares_outstanding_after is not None
            else None
        ),
        error=error,
        diagnostics=diagnostics or {},
    )


def execute_negative_finalization_flow(
    db: Session,
    *,
    settlement_batch_id: int,
    now=None,
) -> NegativeFinalizationResult:
    now = now or utcnow()

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    payout_batch = _lock_completed_payout_batch(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )
    bybit_flow = _lock_bybit_flow(db, settlement_batch_id=int(settlement_batch.id))
    sale_batch = _lock_sale_batch(db, settlement_batch_id=int(settlement_batch.id))
    fund = _get_fund(db, fund_id=int(settlement_batch.fund_id))

    existing = _lock_existing_finalization(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    status_before = str(existing.status) if existing is not None else None

    try:
        if settlement_batch.status not in {
            BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
            BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED,
            BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
        }:
            raise NegativeFinalizationFlowError(
                "Settlement batch must be negative_net_payouts_confirmed before finalization"
            )

        if existing is not None and existing.status == FINALIZATION_BATCH_STATUS_COMPLETED:
            return NegativeFinalizationResult(
                ok=True,
                finalization_batch_id=int(existing.id),
                settlement_batch_id=int(settlement_batch.id),
                payout_batch_id=int(existing.payout_batch_id),
                bybit_flow_id=int(existing.bybit_flow_id) if existing.bybit_flow_id is not None else None,
                sale_batch_id=int(existing.sale_batch_id) if existing.sale_batch_id is not None else None,
                fund_id=int(existing.fund_id),
                fund_code=str(fund.code),
                status_before=status_before,
                status_after=existing.status,
                settlement_status_before=settlement_status_before,
                settlement_status_after=settlement_batch.status,
                buy_order_count=existing.buy_order_count,
                redeem_order_count=existing.redeem_order_count,
                success_order_count=existing.success_order_count,
                shares_outstanding_before=str(existing.shares_outstanding_before),
                shares_outstanding_after=str(existing.shares_outstanding_after),
                idempotent=True,
                diagnostics={"idempotent": True},
            )

        price = q10(settlement_batch.settlement_price_usdt)
        if price <= ZERO:
            raise NegativeFinalizationFlowError("Settlement price must be positive")

        finalization = _new_or_existing_finalization(
            db,
            existing=existing,
            settlement_batch=settlement_batch,
            payout_batch=payout_batch,
            bybit_flow=bybit_flow,
            sale_batch=sale_batch,
            fund=fund,
        )
        status_before = str(finalization.status)

        finalization.status = FINALIZATION_BATCH_STATUS_VALIDATING
        finalization.finalization_started_at = finalization.finalization_started_at or now
        finalization.updated_at = now
        db.add(finalization)
        db.flush()

        orders = _load_finalizable_orders(db, settlement_batch_id=int(settlement_batch.id))

        buy_orders = [order for order in orders if order.side == ORDER_SIDE_BUY]
        redeem_orders = [order for order in orders if order.side == ORDER_SIDE_REDEEM]

        positions_before: dict[str, Any] = {}
        positions_after: dict[str, Any] = {}
        order_updates: list[dict[str, Any]] = []

        total_buy_usdt = ZERO
        total_buy_shares = ZERO
        total_redeem_shares = ZERO

        finalization.status = FINALIZATION_BATCH_STATUS_ACCOUNTING_PROCESSING
        finalization.updated_at = now

        for order in orders:
            if order.side not in {ORDER_SIDE_BUY, ORDER_SIDE_REDEEM}:
                raise NegativeFinalizationFlowError(f"Unsupported order side: {order.side}")

            position = _lock_position(
                db,
                user_id=int(order.user_id),
                fund_id=int(order.fund_id),
            )

            position_key = f"{int(position.user_id)}:{int(position.fund_id)}"
            if position_key not in positions_before:
                positions_before[position_key] = {
                    "user_id": int(position.user_id),
                    "fund_id": int(position.fund_id),
                    "shares": dec(position.shares),
                    "shares_reserved": dec(position.shares_reserved),
                }

            shares_before = dec(position.shares)
            reserved_before = dec(position.shares_reserved)

            if order.side == ORDER_SIDE_BUY:
                amount_usdt = q10(order.amount_usdt)
                if amount_usdt <= ZERO:
                    raise NegativeFinalizationFlowError(f"Buy order {order.id} amount must be positive")

                shares = q10(order.shares if order.shares is not None else amount_usdt / price)
                if shares <= ZERO:
                    raise NegativeFinalizationFlowError(f"Buy order {order.id} shares must be positive")

                order.shares = shares
                order.price_usdt = price
                position.shares = shares_before + shares

                total_buy_usdt += amount_usdt
                total_buy_shares += shares

            else:
                shares = q10(order.shares)
                if shares <= ZERO:
                    raise NegativeFinalizationFlowError(f"Redeem order {order.id} shares must be positive")

                if reserved_before < shares:
                    raise NegativeFinalizationFlowError(
                        f"Redeem order {order.id} reserved shares are insufficient"
                    )

                if shares_before < shares:
                    raise NegativeFinalizationFlowError(
                        f"Redeem order {order.id} position shares are insufficient"
                    )

                position.shares = shares_before - shares
                position.shares_reserved = reserved_before - shares
                order.price_usdt = price

                total_redeem_shares += shares

            order.status = ORDER_STATUS_SUCCESS
            order.executed_at = now
            order.error = None

            position.shares = q10(position.shares)
            position.shares_reserved = q10(position.shares_reserved)

            positions_after[position_key] = {
                "user_id": int(position.user_id),
                "fund_id": int(position.fund_id),
                "shares": dec(position.shares),
                "shares_reserved": dec(position.shares_reserved),
            }

            order_updates.append(
                {
                    "order_id": int(order.id),
                    "user_id": int(order.user_id),
                    "side": order.side,
                    "amount_usdt": dec(order.amount_usdt),
                    "shares": dec(order.shares),
                    "price_usdt": dec(order.price_usdt),
                    "status": order.status,
                }
            )

            db.add(position)
            db.add(order)

        planned_net_shares_change = q10(total_buy_shares - total_redeem_shares)
        shares_before = q10(
            settlement_batch.shares_outstanding_before
            if settlement_batch.shares_outstanding_before is not None
            else fund.shares_outstanding_current
        )
        shares_after = q10(shares_before + planned_net_shares_change)

        if shares_after < ZERO:
            raise NegativeFinalizationFlowError("Shares outstanding after finalization is negative")

        if not _same_decimal(planned_net_shares_change, settlement_batch.planned_net_shares_change):
            raise NegativeFinalizationFlowError(
                "Actual net shares change does not match settlement planned_net_shares_change"
            )

        fund.shares_outstanding_current = shares_after

        finalization.buy_order_count = len(buy_orders)
        finalization.redeem_order_count = len(redeem_orders)
        finalization.success_order_count = len(orders)
        finalization.total_buy_usdt = q10(total_buy_usdt)
        finalization.total_buy_shares = q10(total_buy_shares)
        finalization.total_redeem_shares = q10(total_redeem_shares)
        finalization.planned_net_shares_change = q10(settlement_batch.planned_net_shares_change)
        finalization.actual_net_shares_change = planned_net_shares_change
        finalization.total_net_user_payout_usdt = q10(settlement_batch.total_net_user_payout_usdt)
        finalization.total_partial_month_fee_usdt = q10(settlement_batch.total_partial_month_fee_usdt)
        finalization.shares_outstanding_before = shares_before
        finalization.shares_outstanding_after = shares_after
        finalization.positions_before_json = _json_dict(positions_before)
        finalization.positions_after_json = _json_dict(positions_after)
        finalization.order_updates_json = _json_dict({"orders": order_updates})
        finalization.fund_update_json = _json_dict(
            {
                "fund_id": int(fund.id),
                "shares_outstanding_before": shares_before,
                "shares_outstanding_after": shares_after,
            }
        )
        finalization.accounting_json = _json_dict(
            {
                "ok": True,
                "buy_order_count": len(buy_orders),
                "redeem_order_count": len(redeem_orders),
                "success_order_count": len(orders),
                "total_buy_usdt": total_buy_usdt,
                "total_buy_shares": total_buy_shares,
                "total_redeem_shares": total_redeem_shares,
                "planned_net_shares_change": q10(settlement_batch.planned_net_shares_change),
                "actual_net_shares_change": planned_net_shares_change,
            }
        )
        finalization.status = FINALIZATION_BATCH_STATUS_ACCOUNTING_FINALIZED
        finalization.accounting_finalized_at = now
        finalization.updated_at = now

        settlement_batch.accounting_finalized_at = now
        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED
        settlement_batch.updated_at = now

        runtime_state = (
            db.query(FundRuntimeState)
            .filter(FundRuntimeState.fund_id == int(fund.id))
            .with_for_update()
            .first()
        )
        if runtime_state is not None:
            if (
                runtime_state.pricing_locked
                and runtime_state.pricing_lock_reason == PRICING_LOCK_REASON_SETTLEMENT
                and int(runtime_state.pricing_lock_batch_id or 0) == int(settlement_batch.id)
            ):
                runtime_state.pricing_locked = False
                runtime_state.pricing_lock_reason = None
                runtime_state.pricing_lock_batch_id = None
                runtime_state.pricing_unlocked_at = now
                runtime_state.updated_at = now
                db.add(runtime_state)

        settlement_batch.pricing_unlocked_at = now
        settlement_batch.status = BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
        settlement_batch.updated_at = now

        finalization.status = FINALIZATION_BATCH_STATUS_PRICING_UNLOCKED
        finalization.pricing_unlocked_at = now
        finalization.status = FINALIZATION_BATCH_STATUS_COMPLETED
        finalization.completed_at = now
        finalization.reconciliation_json = _json_dict(
            {
                "ok": True,
                "accounting_finalized": True,
                "pricing_unlocked": True,
                "settlement_completed": True,
            }
        )
        finalization.report_json = _json_dict(
            {
                "stage": "25",
                "ok": True,
                "status": FINALIZATION_BATCH_STATUS_COMPLETED,
                "settlement_status": BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
                "shares_outstanding_before": shares_before,
                "shares_outstanding_after": shares_after,
                "buy_order_count": len(buy_orders),
                "redeem_order_count": len(redeem_orders),
                "success_order_count": len(orders),
            }
        )
        finalization.updated_at = now

        db.add(fund)
        db.add(finalization)
        db.add(settlement_batch)
        db.flush()

        return NegativeFinalizationResult(
            ok=True,
            finalization_batch_id=int(finalization.id),
            settlement_batch_id=int(settlement_batch.id),
            payout_batch_id=int(payout_batch.id),
            bybit_flow_id=int(bybit_flow.id) if bybit_flow is not None else None,
            sale_batch_id=int(sale_batch.id) if sale_batch is not None else None,
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=finalization.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            buy_order_count=len(buy_orders),
            redeem_order_count=len(redeem_orders),
            success_order_count=len(orders),
            shares_outstanding_before=str(shares_before),
            shares_outstanding_after=str(shares_after),
            diagnostics={
                "accounting_finalized": True,
                "pricing_unlocked": True,
            },
        )

    except NegativeFinalizationFlowError as exc:
        if "finalization" not in locals() or finalization is None:
            raise

        return _set_failed(
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )