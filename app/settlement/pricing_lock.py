from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import FundRuntimeState
from app.settlement.statuses import PRICING_LOCK_REASON_SETTLEMENT


class PricingLockError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_runtime_state_for_update(
    db: Session,
    *,
    fund_id: int,
) -> FundRuntimeState | None:
    return (
        db.query(FundRuntimeState)
        .filter(FundRuntimeState.fund_id == fund_id)
        .with_for_update()
        .first()
    )


def get_runtime_state(
    db: Session,
    *,
    fund_id: int,
) -> FundRuntimeState | None:
    return (
        db.query(FundRuntimeState)
        .filter(FundRuntimeState.fund_id == fund_id)
        .first()
    )


def is_pricing_locked(
    db: Session,
    *,
    fund_id: int,
) -> bool:
    state = get_runtime_state(db, fund_id=fund_id)
    return bool(state and state.pricing_locked)


def lock_pricing_for_fund(
    db: Session,
    *,
    fund_id: int,
    batch_id: int,
    reason: str = PRICING_LOCK_REASON_SETTLEMENT,
) -> FundRuntimeState:
    """
    Lock NAV/chart writes for one fund.

    Does not commit.
    Caller controls transaction boundary.
    """
    now = utcnow()

    state = get_runtime_state_for_update(db, fund_id=fund_id)

    if state is None:
        state = FundRuntimeState(
            fund_id=fund_id,
            pricing_locked=True,
            pricing_lock_reason=reason,
            pricing_lock_batch_id=batch_id,
            pricing_locked_at=now,
            pricing_unlocked_at=None,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
        db.flush()
        return state

    if state.pricing_locked and state.pricing_lock_batch_id not in (None, batch_id):
        raise PricingLockError(
            f"Fund {fund_id} is already pricing-locked by batch "
            f"{state.pricing_lock_batch_id}; refusing lock by batch {batch_id}"
        )

    state.pricing_locked = True
    state.pricing_lock_reason = reason
    state.pricing_lock_batch_id = batch_id
    state.pricing_locked_at = state.pricing_locked_at or now
    state.pricing_unlocked_at = None
    state.updated_at = now

    db.add(state)
    db.flush()
    return state


def unlock_pricing_for_fund(
    db: Session,
    *,
    fund_id: int,
    batch_id: int,
) -> FundRuntimeState | None:
    """
    Unlock NAV/chart writes for one fund.

    Does not commit.
    Caller controls transaction boundary.
    """
    now = utcnow()

    state = get_runtime_state_for_update(db, fund_id=fund_id)

    if state is None:
        return None

    if state.pricing_locked and state.pricing_lock_batch_id not in (None, batch_id):
        raise PricingLockError(
            f"Fund {fund_id} is pricing-locked by batch "
            f"{state.pricing_lock_batch_id}; refusing unlock by batch {batch_id}"
        )

    state.pricing_locked = False
    state.pricing_lock_reason = None
    state.pricing_lock_batch_id = None
    state.pricing_unlocked_at = now
    state.updated_at = now

    db.add(state)
    db.flush()
    return state