from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.statuses import (
    ACTIVE_ALLOCATION_LEG_STATUSES,
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED,
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_EARN,
    ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
    ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
)
from app.config import settings
from app.models import FundAllocationBatch, FundAllocationLeg


ZERO = Decimal("0")


class AllocationFinalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AllocationBatchSummary:
    allocation_batch_id: int
    settlement_batch_id: int
    fund_id: int
    status_before: str

    total_legs: int
    filled_legs_count: int
    skipped_legs_count: int
    partial_legs_count: int
    failed_legs_count: int
    active_legs_count: int

    total_target_usdt: Decimal
    total_filled_usdt: Decimal
    total_residual_usdt: Decimal
    residual_source_usdt: Decimal
    residual_earn_usdt: Decimal
    residual_cash_usdt: Decimal
    unhandled_residual_usdt: Decimal

    failed_requires_review_count: int
    margin_guard_skip_count: int
    earn_unavailable_skip_count: int
    symbol_not_trading_skip_count: int
    min_order_skip_count: int

    has_failed_requires_review: bool
    has_active_legs: bool
    has_material_residual_cash: bool
    has_material_residual_earn: bool
    has_unhandled_material_residual: bool

    materiality_threshold_usdt: Decimal
    warnings: list[str] = field(default_factory=list)
    critical_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)

        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)

        return raw


@dataclass(frozen=True)
class AllocationFinalizationResult:
    allocation_batch_id: int
    status_before: str
    status_after: str
    ok: bool
    completed_at_set: bool
    reason: str | None
    summary: AllocationBatchSummary

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["summary"] = self.summary.to_dict()
        return raw


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _positive(value: Any) -> Decimal:
    amount = dec(value)
    return amount if amount > ZERO else ZERO


def _leg_target_usdt(leg: FundAllocationLeg) -> Decimal:
    return _positive(leg.target_usdt)


def _leg_filled_usdt(leg: FundAllocationLeg) -> Decimal:
    return _positive(leg.filled_usdt)


def _leg_residual_usdt(leg: FundAllocationLeg) -> Decimal:
    return _positive(leg.residual_usdt)


def _is_skipped_status(status: str) -> bool:
    return str(status or "").startswith("skipped_")


def _residual_earn_value(leg: FundAllocationLeg) -> Decimal:
    if leg.status != ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED:
        return ZERO

    filled = _leg_filled_usdt(leg)
    if filled > ZERO:
        return filled

    target = _leg_target_usdt(leg)
    if target > ZERO:
        return target

    return ZERO


def _residual_cash_value(leg: FundAllocationLeg) -> Decimal:
    if leg.status != ALLOCATION_LEG_STATUS_RESIDUAL_CASH:
        return ZERO

    residual = _leg_residual_usdt(leg)
    if residual > ZERO:
        return residual

    target = _leg_target_usdt(leg)
    if target > ZERO:
        return target

    filled = _leg_filled_usdt(leg)
    if filled > ZERO:
        return filled

    return ZERO


def _is_residual_result_leg(leg: FundAllocationLeg) -> bool:
    return leg.status in {
        ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
        ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    }


def _materiality_threshold() -> Decimal:
    threshold = dec(settings.ALLOCATION_RESIDUAL_MIN_MATERIALITY_USDT)

    if threshold < ZERO:
        return ZERO

    return threshold


def _get_batch(db: Session, *, allocation_batch_id: int) -> FundAllocationBatch:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == allocation_batch_id)
        .first()
    )

    if batch is None:
        raise AllocationFinalizationError(
            f"Allocation batch not found: {allocation_batch_id}"
        )

    return batch


def _get_batch_for_update(db: Session, *, allocation_batch_id: int) -> FundAllocationBatch:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == allocation_batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise AllocationFinalizationError(
            f"Allocation batch not found: {allocation_batch_id}"
        )

    return batch


def _get_legs(db: Session, *, allocation_batch_id: int) -> list[FundAllocationLeg]:
    return (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.allocation_batch_id == allocation_batch_id)
        .order_by(FundAllocationLeg.leg_index.asc(), FundAllocationLeg.id.asc())
        .all()
    )


def summarize_allocation_batch(
    db: Session,
    *,
    allocation_batch_id: int,
) -> AllocationBatchSummary:
    batch = _get_batch(db, allocation_batch_id=allocation_batch_id)
    legs = _get_legs(db, allocation_batch_id=allocation_batch_id)

    materiality = _materiality_threshold()

    total_target_usdt = sum((_leg_target_usdt(leg) for leg in legs), ZERO)
    total_filled_usdt = sum((_leg_filled_usdt(leg) for leg in legs), ZERO)

    residual_earn_usdt = sum((_residual_earn_value(leg) for leg in legs), ZERO)
    residual_cash_usdt = sum((_residual_cash_value(leg) for leg in legs), ZERO)

    residual_source_usdt = sum(
        (
            _leg_residual_usdt(leg)
            for leg in legs
            if not _is_residual_result_leg(leg)
        ),
        ZERO,
    )

    handled_residual_usdt = residual_earn_usdt + residual_cash_usdt
    total_residual_usdt = max(residual_source_usdt, handled_residual_usdt)

    unhandled_residual_usdt = ZERO
    if residual_source_usdt > handled_residual_usdt:
        unhandled_residual_usdt = residual_source_usdt - handled_residual_usdt

    total_legs = len(legs)

    filled_legs_count = sum(
        1 for leg in legs if leg.status == ALLOCATION_LEG_STATUS_FILLED
    )
    skipped_legs_count = sum(
        1 for leg in legs if _is_skipped_status(leg.status)
    )
    partial_legs_count = sum(
        1
        for leg in legs
        if leg.status == ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
    )
    failed_legs_count = sum(
        1
        for leg in legs
        if leg.status == ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    )
    active_legs_count = sum(
        1
        for leg in legs
        if leg.status in ACTIVE_ALLOCATION_LEG_STATUSES
    )

    failed_requires_review_count = failed_legs_count
    margin_guard_skip_count = sum(
        1
        for leg in legs
        if leg.status == ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD
    )
    earn_unavailable_skip_count = sum(
        1
        for leg in legs
        if leg.status == ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE
    )
    symbol_not_trading_skip_count = sum(
        1
        for leg in legs
        if leg.status == ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING
    )
    min_order_skip_count = sum(
        1
        for leg in legs
        if leg.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER
    )

    warnings: list[str] = []
    critical_errors: list[str] = []

    has_failed_requires_review = failed_requires_review_count > 0
    has_active_legs = active_legs_count > 0
    has_material_residual_cash = residual_cash_usdt > materiality
    has_material_residual_earn = residual_earn_usdt > materiality
    has_unhandled_material_residual = unhandled_residual_usdt > materiality

    if has_failed_requires_review:
        critical_errors.append(
            f"{failed_requires_review_count} allocation leg(s) require review"
        )

    if has_active_legs:
        critical_errors.append(
            f"{active_legs_count} allocation leg(s) are still active/not-final"
        )

    if has_unhandled_material_residual:
        critical_errors.append(
            f"Unhandled residual above materiality: {unhandled_residual_usdt}"
        )

    if has_material_residual_cash:
        warnings.append(
            f"Material residual cash remains: {residual_cash_usdt}"
        )

    if has_material_residual_earn:
        warnings.append(
            f"Residual was placed into USDT Earn: {residual_earn_usdt}"
        )

    return AllocationBatchSummary(
        allocation_batch_id=batch.id,
        settlement_batch_id=batch.settlement_batch_id,
        fund_id=batch.fund_id,
        status_before=batch.status,
        total_legs=total_legs,
        filled_legs_count=filled_legs_count,
        skipped_legs_count=skipped_legs_count,
        partial_legs_count=partial_legs_count,
        failed_legs_count=failed_legs_count,
        active_legs_count=active_legs_count,
        total_target_usdt=total_target_usdt,
        total_filled_usdt=total_filled_usdt,
        total_residual_usdt=total_residual_usdt,
        residual_source_usdt=residual_source_usdt,
        residual_earn_usdt=residual_earn_usdt,
        residual_cash_usdt=residual_cash_usdt,
        unhandled_residual_usdt=unhandled_residual_usdt,
        failed_requires_review_count=failed_requires_review_count,
        margin_guard_skip_count=margin_guard_skip_count,
        earn_unavailable_skip_count=earn_unavailable_skip_count,
        symbol_not_trading_skip_count=symbol_not_trading_skip_count,
        min_order_skip_count=min_order_skip_count,
        has_failed_requires_review=has_failed_requires_review,
        has_active_legs=has_active_legs,
        has_material_residual_cash=has_material_residual_cash,
        has_material_residual_earn=has_material_residual_earn,
        has_unhandled_material_residual=has_unhandled_material_residual,
        materiality_threshold_usdt=materiality,
        warnings=warnings,
        critical_errors=critical_errors,
    )


def _decide_final_status(summary: AllocationBatchSummary) -> tuple[str, bool, str | None]:
    if summary.has_failed_requires_review:
        return (
            ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
            False,
            "One or more allocation legs require review",
        )

    if summary.has_active_legs and settings.ALLOCATION_FINALIZATION_REQUIRE_NO_ACTIVE_LEGS:
        return (
            ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
            False,
            "Allocation batch still has active/not-final legs",
        )

    if summary.has_unhandled_material_residual:
        return (
            ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
            False,
            "Allocation batch has unhandled material residual",
        )

    if summary.has_material_residual_cash:
        return (
            ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
            True,
            "Allocation completed with material residual cash",
        )

    if summary.has_material_residual_earn:
        return (
            ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_EARN,
            True,
            "Allocation completed with residual placed into USDT Earn",
        )

    if summary.total_residual_usdt <= summary.materiality_threshold_usdt:
        return (
            ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED,
            True,
            "Allocation completed with no material residual",
        )

    return (
        ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
        False,
        "Allocation finalization reached inconsistent residual state",
    )


def _apply_summary_to_batch(
    batch: FundAllocationBatch,
    *,
    summary: AllocationBatchSummary,
) -> None:
    batch.total_legs_count = summary.total_legs
    batch.filled_legs_count = summary.filled_legs_count
    batch.skipped_legs_count = summary.skipped_legs_count
    batch.partial_legs_count = summary.partial_legs_count
    batch.failed_legs_count = summary.failed_legs_count
    batch.active_legs_count = summary.active_legs_count

    batch.total_target_usdt = summary.total_target_usdt
    batch.total_filled_usdt = summary.total_filled_usdt
    batch.total_residual_usdt = summary.total_residual_usdt
    batch.residual_earn_usdt = summary.residual_earn_usdt
    batch.residual_cash_usdt = summary.residual_cash_usdt


def finalize_allocation_batch(
    db: Session,
    *,
    allocation_batch_id: int,
) -> AllocationFinalizationResult:
    batch = _get_batch_for_update(db, allocation_batch_id=allocation_batch_id)
    status_before = batch.status

    summary = summarize_allocation_batch(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    status_after, is_completed, reason = _decide_final_status(summary)

    _apply_summary_to_batch(
        batch,
        summary=summary,
    )

    batch.status = status_after

    if is_completed:
        batch.completed_at = batch.completed_at or utcnow()
        batch.error = None
    else:
        batch.completed_at = None
        batch.error = reason

    batch.updated_at = utcnow()

    db.add(batch)
    db.flush()

    return AllocationFinalizationResult(
        allocation_batch_id=batch.id,
        status_before=status_before,
        status_after=status_after,
        ok=is_completed,
        completed_at_set=batch.completed_at is not None,
        reason=reason,
        summary=summary,
    )