from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    EXECUTION_MODE_MARKET,
    EXECUTION_MODE_NATIVE_ICEBERG,
    EXECUTION_MODE_SLICED_IOC_FALLBACK,
    EXECUTION_MODE_EARN_STAKE,
    FINAL_ALLOCATION_LEG_STATUSES,
    PROCESSING_ALLOCATION_LEG_STATUSES,
)
from app.models import FundAllocationLeg


ZERO = Decimal("0")
ONE = Decimal("1")


class AllocationReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReconciledLegResult:
    allocation_leg_id: int
    before_status: str
    after_status: str
    action: str
    reason: str | None = None
    is_final: bool = False
    requires_review: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReconciliationResult:
    allocation_batch_id: int
    total_legs: int
    reconciled_count: int
    unchanged_count: int
    failed_requires_review_count: int
    unknown_state_count: int
    active_remaining_count: int
    leg_results: list[ReconciledLegResult]
    warnings: list[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return self.failed_requires_review_count == 0 and self.unknown_state_count == 0


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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _has_order_identifier(leg: FundAllocationLeg) -> bool:
    return bool(
        _normalize_text(leg.bybit_order_id)
        or _normalize_text(leg.order_link_id)
    )


def _has_strategy_identifier(leg: FundAllocationLeg) -> bool:
    return bool(_normalize_text(leg.strategy_id))


def _has_earn_identifier(leg: FundAllocationLeg) -> bool:
    return bool(_normalize_text(leg.earn_order_id))


def _looks_unknown_identifier(value: Any) -> bool:
    raw = _normalize_text(value).lower()
    if not raw:
        return False

    return (
        raw.startswith("unknown")
        or "unknown" in raw
        or raw.startswith("pending")
        or "unconfirmed" in raw
    )


def _leg_has_unknown_identifier(leg: FundAllocationLeg) -> bool:
    return any(
        _looks_unknown_identifier(value)
        for value in (
            leg.bybit_order_id,
            leg.order_link_id,
            leg.strategy_id,
            leg.earn_order_id,
        )
    )


def _target_usdt(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_usdt)


def _target_qty(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_qty)


def _filled_usdt_or_target(leg: FundAllocationLeg) -> Decimal:
    filled = dec(leg.filled_usdt)
    if filled > ZERO:
        return filled

    return _target_usdt(leg)


def _filled_qty_or_target(leg: FundAllocationLeg) -> Decimal | None:
    filled = dec(leg.filled_qty)
    if filled > ZERO:
        return filled

    target_qty = _target_qty(leg)
    if target_qty > ZERO:
        return target_qty

    return None


def _is_final_leg(leg: FundAllocationLeg) -> bool:
    return leg.status in FINAL_ALLOCATION_LEG_STATUSES


def _is_processing_leg(leg: FundAllocationLeg) -> bool:
    return leg.status in PROCESSING_ALLOCATION_LEG_STATUSES


def _result(
    leg: FundAllocationLeg,
    *,
    before_status: str,
    action: str,
    reason: str | None = None,
    requires_review: bool = False,
    diagnostics: dict[str, Any] | None = None,
) -> ReconciledLegResult:
    return ReconciledLegResult(
        allocation_leg_id=leg.id,
        before_status=before_status,
        after_status=leg.status,
        action=action,
        reason=reason,
        is_final=_is_final_leg(leg),
        requires_review=requires_review,
        diagnostics=diagnostics or {},
    )


def _leave_final_leg(leg: FundAllocationLeg) -> ReconciledLegResult:
    return _result(
        leg,
        before_status=leg.status,
        action="leave_final_leg_unchanged",
        reason=None,
        diagnostics={
            "status": leg.status,
            "execution_mode": leg.execution_mode,
        },
    )


def _leave_planned_leg(leg: FundAllocationLeg) -> ReconciledLegResult:
    return _result(
        leg,
        before_status=leg.status,
        action="leave_planned_leg_for_execution",
        reason=None,
        diagnostics={
            "status": leg.status,
            "execution_mode": leg.execution_mode,
        },
    )


def _mark_failed_requires_review(
    db: Session,
    leg: FundAllocationLeg,
    *,
    before_status: str,
    reason: str,
    diagnostics: dict[str, Any] | None = None,
) -> ReconciledLegResult:
    leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    leg.error = reason
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return _result(
        leg,
        before_status=before_status,
        action="mark_failed_requires_review",
        reason=reason,
        requires_review=True,
        diagnostics=diagnostics or {},
    )


def _mark_mock_order_filled(
    db: Session,
    leg: FundAllocationLeg,
    *,
    before_status: str,
    reason: str,
    diagnostics: dict[str, Any] | None = None,
) -> ReconciledLegResult:
    filled_usdt = _filled_usdt_or_target(leg)
    filled_qty = _filled_qty_or_target(leg)

    leg.status = ALLOCATION_LEG_STATUS_FILLED
    leg.execution_mode = leg.execution_mode or EXECUTION_MODE_MARKET

    if filled_qty is not None:
        leg.filled_qty = filled_qty

    leg.filled_usdt = filled_usdt
    leg.fill_ratio = ONE
    leg.residual_usdt = ZERO
    leg.confirmed_at = leg.confirmed_at or utcnow()
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return _result(
        leg,
        before_status=before_status,
        action="mock_reconciled_as_filled",
        reason=reason,
        diagnostics=diagnostics or {},
    )


def reconcile_market_order_leg_mock(
    db: Session,
    *,
    leg: FundAllocationLeg,
    client: Any = None,
) -> ReconciledLegResult:
    before_status = leg.status

    if _is_final_leg(leg):
        return _leave_final_leg(leg)

    if leg.status == ALLOCATION_LEG_STATUS_PLANNED:
        return _leave_planned_leg(leg)

    if leg.status != ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT:
        return _result(
            leg,
            before_status=before_status,
            action="not_market_order_processing_leg",
            reason=None,
        )

    if _leg_has_unknown_identifier(leg):
        return _mark_failed_requires_review(
            db,
            leg,
            before_status=before_status,
            reason="Market order state is unknown during mock reconciliation",
            diagnostics={
                "bybit_order_id": leg.bybit_order_id,
                "order_link_id": leg.order_link_id,
            },
        )

    if _has_order_identifier(leg):
        return _mark_mock_order_filled(
            db,
            leg,
            before_status=before_status,
            reason="Mock market order reconciled as filled",
            diagnostics={
                "bybit_order_id": leg.bybit_order_id,
                "order_link_id": leg.order_link_id,
            },
        )

    return _mark_failed_requires_review(
        db,
        leg,
        before_status=before_status,
        reason="Market order processing leg has no order identifier",
        diagnostics={
            "bybit_order_id": leg.bybit_order_id,
            "order_link_id": leg.order_link_id,
        },
    )


def reconcile_strategy_leg_mock(
    db: Session,
    *,
    leg: FundAllocationLeg,
    client: Any = None,
) -> ReconciledLegResult:
    before_status = leg.status

    if _is_final_leg(leg):
        return _leave_final_leg(leg)

    if leg.status == ALLOCATION_LEG_STATUS_PLANNED:
        return _leave_planned_leg(leg)

    if leg.status != ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING:
        return _result(
            leg,
            before_status=before_status,
            action="not_strategy_processing_leg",
            reason=None,
        )

    if _leg_has_unknown_identifier(leg):
        return _mark_failed_requires_review(
            db,
            leg,
            before_status=before_status,
            reason="Strategy order state is unknown during mock reconciliation",
            diagnostics={
                "strategy_id": leg.strategy_id,
                "order_link_id": leg.order_link_id,
            },
        )

    if _has_strategy_identifier(leg):
        leg.execution_mode = leg.execution_mode or EXECUTION_MODE_NATIVE_ICEBERG
        return _mark_mock_order_filled(
            db,
            leg,
            before_status=before_status,
            reason="Mock strategy order reconciled as filled",
            diagnostics={
                "strategy_id": leg.strategy_id,
            },
        )

    return _mark_failed_requires_review(
        db,
        leg,
        before_status=before_status,
        reason="Strategy processing leg has no strategy_id",
        diagnostics={
            "strategy_id": leg.strategy_id,
        },
    )


def reconcile_sliced_ioc_leg_mock(
    db: Session,
    *,
    leg: FundAllocationLeg,
    client: Any = None,
) -> ReconciledLegResult:
    before_status = leg.status

    if _is_final_leg(leg):
        return _leave_final_leg(leg)

    if leg.status == ALLOCATION_LEG_STATUS_PLANNED:
        return _leave_planned_leg(leg)

    if leg.status != ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING:
        return _result(
            leg,
            before_status=before_status,
            action="not_sliced_ioc_processing_leg",
            reason=None,
        )

    if _leg_has_unknown_identifier(leg):
        return _mark_failed_requires_review(
            db,
            leg,
            before_status=before_status,
            reason="Sliced IOC state is unknown during mock reconciliation",
            diagnostics={
                "bybit_order_id": leg.bybit_order_id,
                "order_link_id": leg.order_link_id,
                "strategy_id": leg.strategy_id,
            },
        )

    if _has_order_identifier(leg) or _has_strategy_identifier(leg):
        leg.execution_mode = leg.execution_mode or EXECUTION_MODE_SLICED_IOC_FALLBACK
        return _mark_mock_order_filled(
            db,
            leg,
            before_status=before_status,
            reason="Mock sliced IOC order reconciled as filled",
            diagnostics={
                "bybit_order_id": leg.bybit_order_id,
                "order_link_id": leg.order_link_id,
                "strategy_id": leg.strategy_id,
            },
        )

    return _mark_failed_requires_review(
        db,
        leg,
        before_status=before_status,
        reason="Sliced IOC processing leg has no order/strategy identifier",
        diagnostics={
            "bybit_order_id": leg.bybit_order_id,
            "order_link_id": leg.order_link_id,
            "strategy_id": leg.strategy_id,
        },
    )


def reconcile_earn_leg_mock(
    db: Session,
    *,
    leg: FundAllocationLeg,
    client: Any = None,
) -> ReconciledLegResult:
    before_status = leg.status

    if _is_final_leg(leg):
        return _leave_final_leg(leg)

    if leg.status == ALLOCATION_LEG_STATUS_PLANNED:
        return _leave_planned_leg(leg)

    if not _has_earn_identifier(leg):
        return _result(
            leg,
            before_status=before_status,
            action="not_earn_processing_leg",
            reason=None,
        )

    if _leg_has_unknown_identifier(leg):
        return _mark_failed_requires_review(
            db,
            leg,
            before_status=before_status,
            reason="Earn order state is unknown during mock reconciliation",
            diagnostics={
                "earn_order_id": leg.earn_order_id,
            },
        )

    if leg.status in {
        ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
        ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
        ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    }:
        leg.execution_mode = leg.execution_mode or EXECUTION_MODE_EARN_STAKE
        return _mark_mock_order_filled(
            db,
            leg,
            before_status=before_status,
            reason="Mock Earn order reconciled as filled",
            diagnostics={
                "earn_order_id": leg.earn_order_id,
            },
        )

    return _result(
        leg,
        before_status=before_status,
        action="earn_identifier_present_but_no_processing_status",
        reason=None,
        diagnostics={
            "earn_order_id": leg.earn_order_id,
            "status": leg.status,
        },
    )


def reconcile_processing_leg_mock(
    db: Session,
    *,
    leg: FundAllocationLeg,
    client: Any = None,
) -> ReconciledLegResult:
    if _is_final_leg(leg):
        return _leave_final_leg(leg)

    if leg.status == ALLOCATION_LEG_STATUS_PLANNED:
        return _leave_planned_leg(leg)

    if leg.status == ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT:
        if _has_earn_identifier(leg):
            return reconcile_earn_leg_mock(db, leg=leg, client=client)

        return reconcile_market_order_leg_mock(db, leg=leg, client=client)

    if leg.status == ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING:
        return reconcile_strategy_leg_mock(db, leg=leg, client=client)

    if leg.status == ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING:
        return reconcile_sliced_ioc_leg_mock(db, leg=leg, client=client)

    return _mark_failed_requires_review(
        db,
        leg,
        before_status=leg.status,
        reason=f"Unknown non-final allocation leg status during reconciliation: {leg.status}",
        diagnostics={
            "status": leg.status,
            "execution_mode": leg.execution_mode,
            "bybit_order_id": leg.bybit_order_id,
            "order_link_id": leg.order_link_id,
            "strategy_id": leg.strategy_id,
            "earn_order_id": leg.earn_order_id,
        },
    )


def reconcile_allocation_batch_mock(
    db: Session,
    *,
    allocation_batch_id: int,
    client: Any = None,
) -> ReconciliationResult:
    legs = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.allocation_batch_id == allocation_batch_id)
        .order_by(FundAllocationLeg.leg_index.asc(), FundAllocationLeg.id.asc())
        .all()
    )

    leg_results: list[ReconciledLegResult] = []
    warnings: list[str] = []
    errors: list[str] = []

    for leg in legs:
        try:
            result = reconcile_processing_leg_mock(
                db,
                leg=leg,
                client=client,
            )
            leg_results.append(result)

            if result.requires_review:
                errors.append(
                    f"leg_id={result.allocation_leg_id}: {result.reason or result.action}"
                )

        except Exception as exc:
            before_status = leg.status
            result = _mark_failed_requires_review(
                db,
                leg,
                before_status=before_status,
                reason=f"Reconciliation exception: {exc}",
                diagnostics={
                    "exception_type": type(exc).__name__,
                },
            )
            leg_results.append(result)
            errors.append(f"leg_id={leg.id}: reconciliation exception: {exc}")

    db.flush()

    failed_requires_review_count = sum(
        1
        for result in leg_results
        if result.after_status == ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    )
    unknown_state_count = sum(
        1
        for result in leg_results
        if result.requires_review
    )
    active_remaining_count = (
        db.query(FundAllocationLeg)
        .filter(
            FundAllocationLeg.allocation_batch_id == allocation_batch_id,
            FundAllocationLeg.status.in_(
                [
                    ALLOCATION_LEG_STATUS_PLANNED,
                    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
                    ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
                    ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
                ]
            ),
        )
        .count()
    )

    reconciled_count = sum(
        1
        for result in leg_results
        if result.action in {
            "mock_reconciled_as_filled",
            "mark_failed_requires_review",
        }
    )
    unchanged_count = len(leg_results) - reconciled_count

    return ReconciliationResult(
        allocation_batch_id=allocation_batch_id,
        total_legs=len(legs),
        reconciled_count=reconciled_count,
        unchanged_count=unchanged_count,
        failed_requires_review_count=failed_requires_review_count,
        unknown_state_count=unknown_state_count,
        active_remaining_count=active_remaining_count,
        leg_results=leg_results,
        warnings=warnings,
        errors=errors,
    )