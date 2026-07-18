from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    UserFundPosition,
    UserFundPositionStats,
)


ZERO = Decimal("0")
Q10 = Decimal("0.0000000001")


class PositionCostBasisError(RuntimeError):
    pass


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


def _q10(value: Any) -> Decimal:
    return _dec(value).quantize(Q10)


def get_position_stats_for_update(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPositionStats | None:
    return (
        db.query(UserFundPositionStats)
        .filter(
            UserFundPositionStats.user_id == int(user_id),
            UserFundPositionStats.fund_id == int(fund_id),
        )
        .with_for_update()
        .first()
    )


def validate_position_cost_basis(
    db: Session,
    *,
    position: UserFundPosition | None,
    user_id: int,
    fund_id: int,
) -> UserFundPositionStats | None:
    shares = _dec(
        position.shares
        if position is not None
        else ZERO
    )

    stats = get_position_stats_for_update(
        db,
        user_id=int(user_id),
        fund_id=int(fund_id),
    )

    avg_entry_price = _dec(
        stats.avg_entry_price_usdt
        if stats is not None
        else ZERO
    )

    if shares < ZERO:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(user_id)}:"
            f"fund_id={int(fund_id)}:"
            f"shares={shares}:"
            f"avg_entry_price_usdt={avg_entry_price}"
        )

    if shares == ZERO:
        if stats is not None and avg_entry_price != ZERO:
            raise PositionCostBasisError(
                "missing_or_invalid_position_cost_basis:"
                f"user_id={int(user_id)}:"
                f"fund_id={int(fund_id)}:"
                "zero_position_with_nonzero_average:"
                f"avg_entry_price_usdt={avg_entry_price}"
            )

        return stats

    if stats is None or avg_entry_price <= ZERO:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(user_id)}:"
            f"fund_id={int(fund_id)}:"
            f"shares={shares}:"
            f"avg_entry_price_usdt={avg_entry_price}"
        )

    return stats


def _get_or_create_zero_position_stats(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
    now: datetime,
) -> UserFundPositionStats:
    stats = get_position_stats_for_update(
        db,
        user_id=int(user_id),
        fund_id=int(fund_id),
    )

    if stats is not None:
        return stats

    stats = UserFundPositionStats(
        user_id=int(user_id),
        fund_id=int(fund_id),
        avg_entry_price_usdt=ZERO,
        updated_at=now,
    )

    db.add(stats)
    db.flush()

    return stats


def apply_buy_cost_basis(
    db: Session,
    *,
    position: UserFundPosition,
    amount_usdt: Decimal,
    issued_shares: Decimal,
    now: datetime,
) -> Decimal:
    amount = _dec(amount_usdt)
    new_shares = _dec(issued_shares)
    old_shares = _dec(position.shares)

    if amount <= ZERO:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(position.user_id)}:"
            f"fund_id={int(position.fund_id)}:"
            f"invalid_buy_amount_usdt={amount}"
        )

    if new_shares <= ZERO:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(position.user_id)}:"
            f"fund_id={int(position.fund_id)}:"
            f"invalid_issued_shares={new_shares}"
        )

    stats = validate_position_cost_basis(
        db,
        position=position,
        user_id=int(position.user_id),
        fund_id=int(position.fund_id),
    )

    if stats is None:
        stats = _get_or_create_zero_position_stats(
            db,
            user_id=int(position.user_id),
            fund_id=int(position.fund_id),
            now=now,
        )

    old_average = _dec(stats.avg_entry_price_usdt)
    total_shares_after = old_shares + new_shares

    if total_shares_after <= ZERO:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(position.user_id)}:"
            f"fund_id={int(position.fund_id)}:"
            f"invalid_total_shares_after={total_shares_after}"
        )

    old_cost_basis = (
        old_average * old_shares
        if old_shares > ZERO
        else ZERO
    )

    new_average = _q10(
        (
            old_cost_basis
            + amount
        )
        / total_shares_after
    )

    if new_average <= ZERO:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(position.user_id)}:"
            f"fund_id={int(position.fund_id)}:"
            f"invalid_new_average={new_average}"
        )

    stats.avg_entry_price_usdt = new_average
    stats.updated_at = now

    db.add(stats)
    db.flush()

    return new_average


def apply_redeem_cost_basis(
    db: Session,
    *,
    position: UserFundPosition,
    redeem_shares: Decimal,
    now: datetime,
) -> Decimal:
    shares_before = _dec(position.shares)
    shares_to_redeem = _dec(redeem_shares)

    stats = validate_position_cost_basis(
        db,
        position=position,
        user_id=int(position.user_id),
        fund_id=int(position.fund_id),
    )

    if stats is None:
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(position.user_id)}:"
            f"fund_id={int(position.fund_id)}:"
            "stats_missing_for_redeem"
        )

    if (
        shares_to_redeem <= ZERO
        or shares_to_redeem > shares_before
    ):
        raise PositionCostBasisError(
            "missing_or_invalid_position_cost_basis:"
            f"user_id={int(position.user_id)}:"
            f"fund_id={int(position.fund_id)}:"
            f"shares_before={shares_before}:"
            f"redeem_shares={shares_to_redeem}"
        )

    shares_after = shares_before - shares_to_redeem

    if shares_after == ZERO:
        stats.avg_entry_price_usdt = ZERO

    stats.updated_at = now

    db.add(stats)
    db.flush()

    return _dec(stats.avg_entry_price_usdt)