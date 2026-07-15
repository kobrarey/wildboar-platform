from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import Fund, FundOrder, FundSettlementBatch, UserFundPosition
from app.settlement.share_quantity import (
    BuyShareQuantity,
    ShareQuantityError,
    calculate_successful_buy_share_quantity,
    require_share_quantity_4dp_aligned,
)
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


class SettlementShareQuantityError(
    SettlementAccountingError
):
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


@dataclass(frozen=True)
class AccountingSharePlan:
    buy_quantities_by_order_id: dict[
        int,
        BuyShareQuantity,
    ]
    total_buy_usdt: Decimal
    total_buy_shares: Decimal
    total_redeem_shares: Decimal
    total_redeem_usdt: Decimal
    net_cash_usdt: Decimal
    actual_net_shares_change: Decimal
    fund_shares_before: Decimal
    fund_shares_after: Decimal

    @property
    def buyer_shares_total(self) -> Decimal:
        return self.total_buy_shares

    @property
    def redeem_shares_total(self) -> Decimal:
        return self.total_redeem_shares


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _require_4dp(
    value: Any,
    *,
    field_name: str,
    allow_negative: bool = False,
) -> Decimal:
    try:
        return require_share_quantity_4dp_aligned(
            value,
            field_name=field_name,
            allow_negative=allow_negative,
        )
    except ShareQuantityError as exc:
        raise SettlementShareQuantityError(
            str(exc)
        ) from exc


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


def _build_accounting_share_plan(
    db: Session,
    *,
    batch: FundSettlementBatch,
    fund: Fund,
    buy_orders: list[FundOrder],
    redeem_orders: list[FundOrder],
    validate_positions: bool = True,
) -> AccountingSharePlan:
    settlement_price = _dec(
        batch.settlement_price_usdt
    )

    if settlement_price <= ZERO:
        raise SettlementShareQuantityError(
            "settlement_price_usdt_invalid"
        )

    fund_shares_before = _require_4dp(
        fund.shares_outstanding_current,
        field_name="fund_shares_outstanding_current",
    )
    snapshot_shares_before = _require_4dp(
        batch.shares_outstanding_before,
        field_name="settlement_shares_outstanding_before",
    )

    if fund_shares_before != snapshot_shares_before:
        raise SettlementShareQuantityError(
            "fund_shares_outstanding_current_mismatch"
        )

    checked_positions: set[tuple[int, int]] = set()

    def validate_existing_position(
        *,
        user_id: int,
        fund_id: int,
    ) -> UserFundPosition | None:
        key = (int(user_id), int(fund_id))

        position = _get_position_for_update(
            db,
            user_id=int(user_id),
            fund_id=int(fund_id),
        )

        if position is None:
            return None

        if key not in checked_positions:
            _require_4dp(
                position.shares,
                field_name=(
                    f"position_{user_id}_{fund_id}_shares"
                ),
            )
            _require_4dp(
                getattr(
                    position,
                    "shares_reserved",
                    ZERO,
                ),
                field_name=(
                    f"position_{user_id}_{fund_id}"
                    "_shares_reserved"
                ),
            )
            checked_positions.add(key)

        return position

    buy_quantities_by_order_id: dict[
        int,
        BuyShareQuantity,
    ] = {}

    total_buy_usdt = ZERO
    total_buy_shares = ZERO

    for order in buy_orders:
        try:
            quantity = (
                calculate_successful_buy_share_quantity(
                    amount_usdt=order.amount_usdt,
                    settlement_price_usdt=(
                        settlement_price
                    ),
                )
            )
        except ShareQuantityError as exc:
            raise SettlementShareQuantityError(
                f"buy_order_{order.id}:{exc}"
            ) from exc

        if order.shares is not None:
            stored_shares = _require_4dp(
                order.shares,
                field_name=(
                    f"buy_order_{order.id}_shares"
                ),
            )

            if (
                stored_shares
                != quantity.issued_shares
            ):
                raise SettlementShareQuantityError(
                    f"buy_order_{order.id}:"
                    "planned_shares_mismatch"
                )

        if validate_positions:
            validate_existing_position(
                user_id=int(order.user_id),
                fund_id=int(batch.fund_id),
            )

        buy_quantities_by_order_id[
            int(order.id)
        ] = quantity

        total_buy_usdt += (
            quantity.full_investment_usdt
        )
        total_buy_shares += quantity.issued_shares

    total_buy_shares = _require_4dp(
        total_buy_shares,
        field_name="total_buy_shares",
    )

    total_redeem_shares = ZERO
    redeem_totals_by_position: dict[
        tuple[int, int],
        Decimal,
    ] = {}
    redeem_positions: dict[
        tuple[int, int],
        UserFundPosition,
    ] = {}

    for order in redeem_orders:
        redeem_shares = _require_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )

        if validate_positions:
            key = (
                int(order.user_id),
                int(batch.fund_id),
            )

            position = validate_existing_position(
                user_id=key[0],
                fund_id=key[1],
            )

            if position is None:
                raise SettlementShareQuantityError(
                    f"redeem_order_{order.id}:"
                    "position_not_found"
                )

            redeem_positions[key] = position
            redeem_totals_by_position[key] = (
                redeem_totals_by_position.get(
                    key,
                    ZERO,
                )
                + redeem_shares
            )

        total_redeem_shares += redeem_shares

    total_redeem_shares = _require_4dp(
        total_redeem_shares,
        field_name="total_redeem_shares",
    )

    if validate_positions:
        for key, required_shares in (
            redeem_totals_by_position.items()
        ):
            position = redeem_positions[key]

            position_shares = _dec(
                position.shares
            )
            position_reserved = _dec(
                getattr(
                    position,
                    "shares_reserved",
                    ZERO,
                )
            )

            if position_shares < required_shares:
                raise SettlementShareQuantityError(
                    "redeem_position_shares_insufficient"
                )

            if position_reserved < required_shares:
                raise SettlementShareQuantityError(
                    "redeem_position_reserved_insufficient"
                )

    total_redeem_usdt = (
        total_redeem_shares
        * settlement_price
    )
    net_cash_usdt = (
        total_buy_usdt
        - total_redeem_usdt
    )

    planned_issue = _require_4dp(
        batch.planned_shares_to_issue,
        field_name="planned_shares_to_issue",
    )
    planned_redeem = _require_4dp(
        batch.planned_shares_to_redeem,
        field_name="planned_shares_to_redeem",
    )
    planned_net_change = _require_4dp(
        batch.planned_net_shares_change,
        field_name="planned_net_shares_change",
        allow_negative=True,
    )
    batch_total_redeem = _require_4dp(
        batch.total_redeem_shares,
        field_name="batch_total_redeem_shares",
    )

    if planned_issue != total_buy_shares:
        raise SettlementShareQuantityError(
            "planned_shares_to_issue_mismatch"
        )

    if planned_redeem != total_redeem_shares:
        raise SettlementShareQuantityError(
            "planned_shares_to_redeem_mismatch"
        )

    if batch_total_redeem != total_redeem_shares:
        raise SettlementShareQuantityError(
            "batch_total_redeem_shares_mismatch"
        )

    if _dec(batch.total_buy_usdt) != total_buy_usdt:
        raise SettlementShareQuantityError(
            "batch_total_buy_usdt_mismatch"
        )

    if (
        _dec(batch.total_redeem_usdt)
        != total_redeem_usdt
    ):
        raise SettlementShareQuantityError(
            "batch_total_redeem_usdt_mismatch"
        )

    if _dec(batch.net_cash_usdt) != net_cash_usdt:
        raise SettlementShareQuantityError(
            "batch_net_cash_usdt_mismatch"
        )

    actual_net_change = _require_4dp(
        total_buy_shares - total_redeem_shares,
        field_name="actual_net_shares_change",
        allow_negative=True,
    )

    if planned_net_change != actual_net_change:
        raise SettlementShareQuantityError(
            "planned_net_shares_change_mismatch"
        )

    fund_shares_after = _require_4dp(
        fund_shares_before + actual_net_change,
        field_name="fund_shares_outstanding_after",
    )

    return AccountingSharePlan(
        buy_quantities_by_order_id=(
            buy_quantities_by_order_id
        ),
        total_buy_usdt=total_buy_usdt,
        total_buy_shares=total_buy_shares,
        total_redeem_shares=total_redeem_shares,
        total_redeem_usdt=total_redeem_usdt,
        net_cash_usdt=net_cash_usdt,
        actual_net_shares_change=actual_net_change,
        fund_shares_before=fund_shares_before,
        fund_shares_after=fund_shares_after,
    )


def validate_settlement_share_state_before_external(
    db: Session,
    *,
    batch: FundSettlementBatch,
    mark_failed: bool = True,
) -> AccountingSharePlan:
    orders = _load_orders_for_accounting(
        db,
        batch_id=int(batch.id),
    )

    if not orders:
        raise SettlementShareQuantityError(
            f"Batch {batch.id} has no orders"
        )

    fund = (
        db.query(Fund)
        .filter(Fund.id == int(batch.fund_id))
        .with_for_update()
        .first()
    )

    if fund is None:
        raise SettlementShareQuantityError(
            f"Fund not found for batch {batch.id}"
        )

    buy_orders = [
        order
        for order in orders
        if order.side == ORDER_SIDE_BUY
    ]
    redeem_orders = [
        order
        for order in orders
        if order.side == ORDER_SIDE_REDEEM
    ]

    try:
        return _build_accounting_share_plan(
            db,
            batch=batch,
            fund=fund,
            buy_orders=buy_orders,
            redeem_orders=redeem_orders,
            validate_positions=True,
        )
    except SettlementShareQuantityError as exc:
        if mark_failed:
            _mark_batch_failed_requires_review(
                db,
                batch=batch,
                error=str(exc),
                orders=orders,
            )
        raise


def validate_positive_net_share_preflight(
    db: Session,
    *,
    batch: FundSettlementBatch,
) -> AccountingSharePlan:
    return validate_settlement_share_state_before_external(
        db,
        batch=batch,
        mark_failed=True,
    )


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
    buyer_shares: Decimal,
    now: datetime,
) -> Decimal:
    amount_usdt = _dec(order.amount_usdt)

    if amount_usdt <= 0:
        raise SettlementAccountingError(
            f"Buy order {order.id} has invalid "
            f"amount_usdt={order.amount_usdt}"
        )

    position = _get_or_create_position_for_buyer(
        db,
        user_id=order.user_id,
        fund_id=batch.fund_id,
    )

    position.shares = (
        _dec(position.shares) + buyer_shares
    )

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

    buy_orders = [
        order
        for order in orders
        if order.side == ORDER_SIDE_BUY
    ]
    redeem_orders = [
        order
        for order in orders
        if order.side == ORDER_SIDE_REDEEM
    ]

    buyer_shares_total = ZERO
    redeem_shares_total = ZERO
    redeem_usdt_total = ZERO

    try:
        share_plan = _build_accounting_share_plan(
            db,
            batch=batch,
            fund=fund,
            buy_orders=buy_orders,
            redeem_orders=redeem_orders,
        )

        fund_shares_before = (
            share_plan.fund_shares_before
        )

        for order in buy_orders:
            buyer_shares_total += _finalize_buy_order(
                db,
                order=order,
                batch=batch,
                settlement_price=settlement_price,
                buyer_shares=(
                    share_plan
                    .buy_quantities_by_order_id[
                        int(order.id)
                    ]
                    .issued_shares
                ),
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

        fund.shares_outstanding_current = (
            share_plan.fund_shares_after
        )

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