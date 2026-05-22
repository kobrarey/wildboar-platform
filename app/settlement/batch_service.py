from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Fund, FundOrder, FundSettlementBatch
from app.settlement.price_service import SettlementPriceError, fix_settlement_price_for_batch
from app.settlement.pricing_lock import PricingLockError, lock_pricing_for_fund, unlock_pricing_for_fund
from app.settlement.statuses import (
    BATCH_STATUS_CREATED,
    BATCH_STATUS_FAILED,
    BATCH_STATUS_GAS_CHECKING,
    BATCH_STATUS_NO_ORDERS,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_PENDING,
    ORDER_STATUS_SETTLING,
    PRICING_LOCK_REASON_SETTLEMENT,
)
from app.telegram import send_telegram_message


log = logging.getLogger("settlement.batch_service")

ZERO = Decimal("0")


class SettlementBatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SettlementBatchResult:
    fund_id: int
    fund_code: str
    settlement_date: date
    batch_id: int | None
    status: str
    orders_count: int
    buy_orders_count: int
    redeem_orders_count: int
    total_buy_usdt: Decimal
    total_redeem_shares: Decimal
    total_redeem_usdt: Decimal
    net_cash_usdt: Decimal
    planned_shares_to_issue: Decimal
    planned_shares_to_redeem: Decimal
    planned_net_shares_change: Decimal
    message: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def get_cutoff_ts(settlement_date: date) -> datetime:
    """
    Settlement cutoff:
        settlement_date 23:59:00 UTC
    """
    return datetime.combine(
        settlement_date,
        time(
            hour=int(settings.SETTLEMENT_CUTOFF_HOUR_UTC),
            minute=int(settings.SETTLEMENT_CUTOFF_MINUTE_UTC),
            second=0,
            tzinfo=timezone.utc,
        ),
    )


def get_default_settlement_date(now_utc: datetime | None = None) -> date:
    """
    Default worker behavior:
    if worker runs around 00:00 UTC, it processes the previous UTC settlement day.

    Example:
        run at 2026-05-17 00:00 UTC -> settlement_date = 2026-05-16
    """
    now = _as_utc(now_utc or utcnow())
    return (now - timedelta(days=1)).date()


def _get_fund_by_code(db: Session, fund_code: str) -> Fund:
    code = (fund_code or "").strip().lower()
    fund = db.query(Fund).filter(Fund.code == code).first()
    if fund is None:
        raise SettlementBatchError(f"Fund not found: {fund_code}")
    return fund


def _get_active_funds(db: Session, fund_codes: Iterable[str] | None = None) -> list[Fund]:
    q = db.query(Fund).filter(Fund.is_active == True)

    if fund_codes:
        codes = [(code or "").strip().lower() for code in fund_codes if (code or "").strip()]
        q = q.filter(Fund.code.in_(codes))

    return q.order_by(Fund.sort_order.asc(), Fund.id.asc()).all()


def _get_or_create_batch(
    db: Session,
    *,
    fund_id: int,
    settlement_date: date,
    cutoff_ts: datetime,
    settlement_ts: datetime,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(
            FundSettlementBatch.fund_id == fund_id,
            FundSettlementBatch.settlement_date == settlement_date,
        )
        .with_for_update()
        .first()
    )

    if batch is not None:
        return batch

    now = utcnow()

    batch = FundSettlementBatch(
        fund_id=fund_id,
        settlement_date=settlement_date,
        cutoff_ts=cutoff_ts,
        settlement_ts=settlement_ts,
        status=BATCH_STATUS_CREATED,
        total_buy_usdt=ZERO,
        total_redeem_shares=ZERO,
        total_redeem_usdt=ZERO,
        net_cash_usdt=ZERO,
        planned_shares_to_issue=ZERO,
        planned_shares_to_redeem=ZERO,
        planned_net_shares_change=ZERO,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()

    return batch


def _lock_pending_orders_for_batch(
    db: Session,
    *,
    fund_id: int,
    cutoff_ts: datetime,
) -> list[FundOrder]:
    """
    Lock pending, not-yet-batched orders for one fund up to cutoff_ts.

    Important:
    - settlement_batch_id IS NULL prevents one order entering two batches;
    - SELECT FOR UPDATE protects against concurrent settlement worker runs.
    """
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.fund_id == fund_id,
            FundOrder.status == ORDER_STATUS_PENDING,
            FundOrder.settlement_batch_id.is_(None),
            FundOrder.created_at <= cutoff_ts,
        )
        .order_by(FundOrder.created_at.asc(), FundOrder.id.asc())
        .with_for_update(skip_locked=True)
        .all()
    )


def _calculate_batch_fields(
    *,
    orders: list[FundOrder],
    settlement_price_usdt: Decimal,
) -> dict[str, Decimal]:
    buy_orders = [order for order in orders if order.side == ORDER_SIDE_BUY]
    redeem_orders = [order for order in orders if order.side == ORDER_SIDE_REDEEM]

    total_buy_usdt = sum((_dec(order.amount_usdt) for order in buy_orders), ZERO)
    total_redeem_shares = sum((_dec(order.shares) for order in redeem_orders), ZERO)

    if settlement_price_usdt <= 0:
        raise SettlementBatchError(f"Invalid settlement_price_usdt={settlement_price_usdt}")

    total_redeem_usdt = total_redeem_shares * settlement_price_usdt
    net_cash_usdt = total_buy_usdt - total_redeem_usdt

    planned_shares_to_issue = total_buy_usdt / settlement_price_usdt if total_buy_usdt > 0 else ZERO
    planned_shares_to_redeem = total_redeem_shares
    planned_net_shares_change = planned_shares_to_issue - planned_shares_to_redeem

    return {
        "total_buy_usdt": total_buy_usdt,
        "total_redeem_shares": total_redeem_shares,
        "total_redeem_usdt": total_redeem_usdt,
        "net_cash_usdt": net_cash_usdt,
        "planned_shares_to_issue": planned_shares_to_issue,
        "planned_shares_to_redeem": planned_shares_to_redeem,
        "planned_net_shares_change": planned_net_shares_change,
    }


def _apply_batch_calculation(
    *,
    batch: FundSettlementBatch,
    fields: dict[str, Decimal],
) -> None:
    now = utcnow()

    batch.total_buy_usdt = fields["total_buy_usdt"]
    batch.total_redeem_shares = fields["total_redeem_shares"]
    batch.total_redeem_usdt = fields["total_redeem_usdt"]
    batch.net_cash_usdt = fields["net_cash_usdt"]
    batch.planned_shares_to_issue = fields["planned_shares_to_issue"]
    batch.planned_shares_to_redeem = fields["planned_shares_to_redeem"]
    batch.planned_net_shares_change = fields["planned_net_shares_change"]
    batch.status = BATCH_STATUS_GAS_CHECKING
    batch.updated_at = now


def _attach_orders_to_batch(
    *,
    orders: list[FundOrder],
    batch: FundSettlementBatch,
) -> None:
    now = utcnow()

    for order in orders:
        order.settlement_batch_id = batch.id
        order.status = ORDER_STATUS_SETTLING
        order.settlement_locked_at = now
        order.error = None


def _mark_batch_failed(
    batch: FundSettlementBatch,
    *,
    error: str,
) -> None:
    now = utcnow()

    batch.status = BATCH_STATUS_FAILED
    batch.error = error
    batch.updated_at = now


def _send_batch_alert(text: str) -> None:
    try:
        send_telegram_message(text)
    except Exception as exc:
        log.warning("Settlement Telegram alert failed: %s", exc)


def create_no_orders_batch(
    db: Session,
    *,
    fund: Fund,
    settlement_date: date,
    cutoff_ts: datetime,
    settlement_ts: datetime,
) -> SettlementBatchResult:
    batch = _get_or_create_batch(
        db,
        fund_id=fund.id,
        settlement_date=settlement_date,
        cutoff_ts=cutoff_ts,
        settlement_ts=settlement_ts,
    )

    if batch.status != BATCH_STATUS_NO_ORDERS:
        batch.status = BATCH_STATUS_NO_ORDERS
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()

    return SettlementBatchResult(
        fund_id=fund.id,
        fund_code=fund.code,
        settlement_date=settlement_date,
        batch_id=batch.id,
        status=BATCH_STATUS_NO_ORDERS,
        orders_count=0,
        buy_orders_count=0,
        redeem_orders_count=0,
        total_buy_usdt=ZERO,
        total_redeem_shares=ZERO,
        total_redeem_usdt=ZERO,
        net_cash_usdt=ZERO,
        planned_shares_to_issue=ZERO,
        planned_shares_to_redeem=ZERO,
        planned_net_shares_change=ZERO,
        message="No pending orders for settlement date.",
    )


def create_settlement_batch_for_fund(
    db: Session,
    *,
    fund: Fund,
    settlement_date: date,
    create_no_orders: bool = False,
) -> SettlementBatchResult:
    """
    Create and calculate Stage 21 settlement batch for one fund.

    Stage 21 behavior:
    - locks pricing per fund;
    - fixes settlement price from latest fresh fund_nav_minute;
    - calculates planned batch fields;
    - moves pending orders to settling;
    - does NOT call Bybit;
    - does NOT send on-chain transfers;
    - does NOT finalize orders as success;
    - does NOT change shares_outstanding_current;
    - does NOT change user_fund_positions.shares.
    """
    cutoff_ts = get_cutoff_ts(settlement_date)
    settlement_ts = cutoff_ts

    orders = _lock_pending_orders_for_batch(
        db,
        fund_id=fund.id,
        cutoff_ts=cutoff_ts,
    )

    if not orders:
        if create_no_orders:
            return create_no_orders_batch(
                db,
                fund=fund,
                settlement_date=settlement_date,
                cutoff_ts=cutoff_ts,
                settlement_ts=settlement_ts,
            )

        return SettlementBatchResult(
            fund_id=fund.id,
            fund_code=fund.code,
            settlement_date=settlement_date,
            batch_id=None,
            status=BATCH_STATUS_NO_ORDERS,
            orders_count=0,
            buy_orders_count=0,
            redeem_orders_count=0,
            total_buy_usdt=ZERO,
            total_redeem_shares=ZERO,
            total_redeem_usdt=ZERO,
            net_cash_usdt=ZERO,
            planned_shares_to_issue=ZERO,
            planned_shares_to_redeem=ZERO,
            planned_net_shares_change=ZERO,
            message="No pending orders; batch skipped.",
        )

    batch = _get_or_create_batch(
        db,
        fund_id=fund.id,
        settlement_date=settlement_date,
        cutoff_ts=cutoff_ts,
        settlement_ts=settlement_ts,
    )

    if batch.status not in {BATCH_STATUS_CREATED, BATCH_STATUS_FAILED}:
        return SettlementBatchResult(
            fund_id=fund.id,
            fund_code=fund.code,
            settlement_date=settlement_date,
            batch_id=batch.id,
            status=batch.status,
            orders_count=0,
            buy_orders_count=0,
            redeem_orders_count=0,
            total_buy_usdt=_dec(batch.total_buy_usdt),
            total_redeem_shares=_dec(batch.total_redeem_shares),
            total_redeem_usdt=_dec(batch.total_redeem_usdt),
            net_cash_usdt=_dec(batch.net_cash_usdt),
            planned_shares_to_issue=_dec(batch.planned_shares_to_issue),
            planned_shares_to_redeem=_dec(batch.planned_shares_to_redeem),
            planned_net_shares_change=_dec(batch.planned_net_shares_change),
            message="Existing non-created batch found; no changes made.",
        )

    try:
        lock_pricing_for_fund(
            db,
            fund_id=fund.id,
            batch_id=batch.id,
            reason=PRICING_LOCK_REASON_SETTLEMENT,
        )

        now = utcnow()
        batch.status = "pricing_locked"
        batch.pricing_locked_at = now
        batch.error = None
        batch.updated_at = now
        db.add(batch)
        db.flush()

        price_snapshot = fix_settlement_price_for_batch(db, batch=batch)

        fields = _calculate_batch_fields(
            orders=orders,
            settlement_price_usdt=price_snapshot.settlement_price_usdt,
        )

        _apply_batch_calculation(batch=batch, fields=fields)
        _attach_orders_to_batch(orders=orders, batch=batch)

        db.add(batch)
        for order in orders:
            db.add(order)

        db.flush()

        buy_orders_count = sum(1 for order in orders if order.side == ORDER_SIDE_BUY)
        redeem_orders_count = sum(1 for order in orders if order.side == ORDER_SIDE_REDEEM)

        return SettlementBatchResult(
            fund_id=fund.id,
            fund_code=fund.code,
            settlement_date=settlement_date,
            batch_id=batch.id,
            status=batch.status,
            orders_count=len(orders),
            buy_orders_count=buy_orders_count,
            redeem_orders_count=redeem_orders_count,
            total_buy_usdt=fields["total_buy_usdt"],
            total_redeem_shares=fields["total_redeem_shares"],
            total_redeem_usdt=fields["total_redeem_usdt"],
            net_cash_usdt=fields["net_cash_usdt"],
            planned_shares_to_issue=fields["planned_shares_to_issue"],
            planned_shares_to_redeem=fields["planned_shares_to_redeem"],
            planned_net_shares_change=fields["planned_net_shares_change"],
            message="Batch created and moved to gas_checking.",
        )

    except (SettlementPriceError, PricingLockError, SettlementBatchError) as exc:
        error_text = str(exc)
        _mark_batch_failed(batch, error=error_text)

        try:
            unlock_pricing_for_fund(
                db,
                fund_id=fund.id,
                batch_id=batch.id,
            )
            batch.pricing_unlocked_at = utcnow()
            batch.updated_at = utcnow()
        except Exception as unlock_exc:
            log.exception("Failed to unlock pricing after batch failure: %s", unlock_exc)
            batch.error = f"{error_text}; pricing unlock failed: {unlock_exc}"

        db.add(batch)
        db.flush()

        _send_batch_alert(
            "❌ Settlement batch failed\n"
            f"Fund: {fund.code}\n"
            f"Settlement date: {settlement_date.isoformat()}\n"
            f"Batch ID: {batch.id}\n"
            f"Error: {batch.error or error_text}"
        )

        raise


def run_settlement_batches_once(
    db: Session,
    *,
    settlement_date: date | None = None,
    fund_codes: Iterable[str] | None = None,
    create_no_orders: bool = False,
    commit: bool = True,
) -> list[SettlementBatchResult]:
    """
    Process settlement batch creation/calculation for selected active funds.

    Default settlement_date:
        previous UTC day, because worker is expected to run after midnight UTC.

    If commit=False:
        caller can wrap this in a rollback smoke-test.
    """
    actual_settlement_date = settlement_date or get_default_settlement_date()
    funds = _get_active_funds(db, fund_codes=fund_codes)

    results: list[SettlementBatchResult] = []

    try:
        for fund in funds:
            result = create_settlement_batch_for_fund(
                db,
                fund=fund,
                settlement_date=actual_settlement_date,
                create_no_orders=create_no_orders,
            )
            results.append(result)

        if commit:
            db.commit()
        else:
            db.flush()

        return results

    except Exception:
        db.rollback()
        raise