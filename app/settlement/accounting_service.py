from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import Fund, FundOrder, FundSettlementBatch, UserFundPosition
from app.settlement.pricing_lock import PricingLockError, unlock_pricing_for_fund
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SUCCESS,
)


ZERO = Decimal("0")


class SettlementAccountingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountingFinalizationResult:
    batch_id: int
    fund_id: int
    buy_orders_count: int
    redeem_orders_count: int
    buyer_shares_issued: Decimal
    redeem_shares_burned: Decimal
    redeem_usdt_total: Decimal
    fund_shares_before: Decimal
    fund_shares_after: Decimal
    pricing_unlocked: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _get_position_for_update(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPosition | None:
    return (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id == user_id,
            UserFundPosition.fund_id == fund_id,
        )
        .with_for_update()
        .first()
    )


def _get_or_create_position_for_buyer(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPosition:
    position = _get_position_for_update(
        db,
        user_id=user_id,
        fund_id=fund_id,
    )

    if position is not None:
        return position

    position = UserFundPosition(
        user_id=user_id,
        fund_id=fund_id,
        shares=ZERO,
        shares_reserved=ZERO,
    )
    db.add(position)
    db.flush()
    return position


def _mark_batch_failed_requires_review(
    db: Session,
    *,
    batch: FundSettlementBatch,
    error: str,
    orders: list[FundOrder],
) -> None:
    now = utcnow()

    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now

    for order in orders:
        if order.status != ORDER_STATUS_SUCCESS:
            order.status = ORDER_STATUS_FAILED_REQUIRES_REVIEW
            order.error = error
            db.add(order)

    db.add(batch)
    db.flush()


def _validate_batch_ready_for_accounting(batch: FundSettlementBatch) -> None:
    if batch.accounting_finalized_at is not None:
        raise SettlementAccountingError(
            f"Batch {batch.id} accounting already finalized at {batch.accounting_finalized_at}"
        )

    price = _dec(batch.settlement_price_usdt)
    if price <= 0:
        raise SettlementAccountingError(
            f"Batch {batch.id} has invalid settlement_price_usdt={batch.settlement_price_usdt}"
        )

    if batch.pricing_unlocked_at is not None:
        raise SettlementAccountingError(
            f"Batch {batch.id} pricing already unlocked before accounting finalization"
        )


def _load_orders_for_accounting(
    db: Session,
    *,
    batch_id: int,
) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(FundOrder.settlement_batch_id == batch_id)
        .order_by(FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _finalize_buy_order(
    db: Session,
    *,
    order: FundOrder,
    batch: FundSettlementBatch,
    settlement_price: Decimal,
    now: datetime,
) -> Decimal:
    amount_usdt = _dec(order.amount_usdt)
    if amount_usdt <= 0:
        raise SettlementAccountingError(
            f"Buy order {order.id} has invalid amount_usdt={order.amount_usdt}"
        )

    buyer_shares = amount_usdt / settlement_price
    if buyer_shares <= 0:
        raise SettlementAccountingError(
            f"Buy order {order.id} calculated non-positive buyer_shares={buyer_shares}"
        )

    position = _get_or_create_position_for_buyer(
        db,
        user_id=order.user_id,
        fund_id=batch.fund_id,
    )

    position.shares = _dec(position.shares) + buyer_shares

    order.shares = buyer_shares
    order.price_usdt = settlement_price
    order.status = ORDER_STATUS_SUCCESS
    order.executed_at = now
    order.error = None

    db.add(position)
    db.add(order)
    db.flush()

    return buyer_shares


def _finalize_redeem_order(
    db: Session,
    *,
    order: FundOrder,
    batch: FundSettlementBatch,
    settlement_price: Decimal,
    now: datetime,
) -> tuple[Decimal, Decimal]:
    redeem_shares = _dec(order.shares)
    if redeem_shares <= 0:
        raise SettlementAccountingError(
            f"Redeem order {order.id} has invalid shares={order.shares}"
        )

    redeem_usdt = redeem_shares * settlement_price

    position = _get_position_for_update(
        db,
        user_id=order.user_id,
        fund_id=batch.fund_id,
    )

    if position is None:
        raise SettlementAccountingError(
            f"Redeem order {order.id} has no user_fund_position"
        )

    shares_before = _dec(position.shares)
    reserved_before = _dec(getattr(position, "shares_reserved", 0))

    if shares_before < redeem_shares:
        raise SettlementAccountingError(
            f"Redeem order {order.id} would make shares negative: "
            f"shares={shares_before}, redeem={redeem_shares}"
        )

    if reserved_before < redeem_shares:
        raise SettlementAccountingError(
            f"Redeem order {order.id} would make shares_reserved negative: "
            f"shares_reserved={reserved_before}, redeem={redeem_shares}"
        )

    position.shares = shares_before - redeem_shares
    position.shares_reserved = reserved_before - redeem_shares

    if _dec(position.shares) < 0 or _dec(position.shares_reserved) < 0:
        raise SettlementAccountingError(
            f"Redeem order {order.id} produced negative position values"
        )

    order.amount_usdt = redeem_usdt
    order.price_usdt = settlement_price
    order.status = ORDER_STATUS_SUCCESS
    order.executed_at = now
    order.error = None

    db.add(position)
    db.add(order)
    db.flush()

    return redeem_shares, redeem_usdt


def finalize_positive_net_accounting(
    db: Session,
    *,
    batch_id: int,
    unlock_pricing: bool = True,
) -> AccountingFinalizationResult:
    """
    Finalize accounting for a positive-net settlement batch.

    Does not call Bybit.
    Does not send on-chain transfers.
    Does not commit.

    Caller must call this only after:
    - all seller payouts are confirmed;
    - positive net transfer to Bybit is skipped or confirmed;
    - Bybit deposit is confirmed when net_cash_usdt > 0;
    - optional Bybit internal transfer is completed or safely skipped.
    """
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise SettlementAccountingError(f"Batch not found: {batch_id}")

    _validate_batch_ready_for_accounting(batch)

    orders = _load_orders_for_accounting(db, batch_id=batch.id)
    if not orders:
        raise SettlementAccountingError(f"Batch {batch.id} has no orders")

    fund = (
        db.query(Fund)
        .filter(Fund.id == batch.fund_id)
        .with_for_update()
        .first()
    )

    if fund is None:
        raise SettlementAccountingError(f"Fund not found for batch {batch.id}")

    settlement_price = _dec(batch.settlement_price_usdt)
    now = utcnow()

    buy_orders = [order for order in orders if order.side == ORDER_SIDE_BUY]
    redeem_orders = [order for order in orders if order.side == ORDER_SIDE_REDEEM]

    buyer_shares_total = ZERO
    redeem_shares_total = ZERO
    redeem_usdt_total = ZERO

    fund_shares_before = _dec(fund.shares_outstanding_current)

    try:
        for order in buy_orders:
            buyer_shares_total += _finalize_buy_order(
                db,
                order=order,
                batch=batch,
                settlement_price=settlement_price,
                now=now,
            )

        for order in redeem_orders:
            redeem_shares, redeem_usdt = _finalize_redeem_order(
                db,
                order=order,
                batch=batch,
                settlement_price=settlement_price,
                now=now,
            )
            redeem_shares_total += redeem_shares
            redeem_usdt_total += redeem_usdt

        planned_net_change = _dec(batch.planned_net_shares_change)
        fund.shares_outstanding_current = fund_shares_before + planned_net_change

        if _dec(fund.shares_outstanding_current) < 0:
            raise SettlementAccountingError(
                f"Fund {fund.id} shares_outstanding_current would become negative"
            )

        batch.status = BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED
        batch.accounting_finalized_at = now
        batch.updated_at = now
        batch.error = None

        db.add(fund)
        db.add(batch)
        db.flush()

        pricing_unlocked = False
        if unlock_pricing:
            unlock_pricing_for_fund(
                db,
                fund_id=batch.fund_id,
                batch_id=batch.id,
            )
            batch.pricing_unlocked_at = now
            batch.updated_at = now
            db.add(batch)
            db.flush()
            pricing_unlocked = True

        return AccountingFinalizationResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            buy_orders_count=len(buy_orders),
            redeem_orders_count=len(redeem_orders),
            buyer_shares_issued=buyer_shares_total,
            redeem_shares_burned=redeem_shares_total,
            redeem_usdt_total=redeem_usdt_total,
            fund_shares_before=fund_shares_before,
            fund_shares_after=_dec(fund.shares_outstanding_current),
            pricing_unlocked=pricing_unlocked,
        )

    except (SettlementAccountingError, PricingLockError) as exc:
        _mark_batch_failed_requires_review(
            db,
            batch=batch,
            error=str(exc),
            orders=orders,
        )
        raise