from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.finalization import summarize_allocation_batch
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    EXECUTION_MODE_EARN_STAKE,
    EXECUTION_MODE_MARKET,
    EXECUTION_MODE_NATIVE_ICEBERG,
    EXECUTION_MODE_SLICED_IOC_FALLBACK,
    LEG_GROUP_DERIVATIVE,
    LEG_GROUP_EARN,
    LEG_GROUP_OPTION,
)
from app.models import Fund, FundAllocationBatch, FundAllocationLeg


class AllocationReportingError(RuntimeError):
    pass


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


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in data.items()}


def _get_batch(db: Session, *, allocation_batch_id: int) -> FundAllocationBatch:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == allocation_batch_id)
        .first()
    )

    if batch is None:
        raise AllocationReportingError(
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
        raise AllocationReportingError(
            f"Allocation batch not found: {allocation_batch_id}"
        )

    return batch


def _get_fund_code(db: Session, *, fund_id: int) -> str | None:
    fund = (
        db.query(Fund)
        .filter(Fund.id == fund_id)
        .first()
    )

    if fund is None:
        return None

    return fund.code


def _get_legs(db: Session, *, allocation_batch_id: int) -> list[FundAllocationLeg]:
    return (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.allocation_batch_id == allocation_batch_id)
        .order_by(FundAllocationLeg.leg_index.asc(), FundAllocationLeg.id.asc())
        .all()
    )


def _count_by_execution_mode(
    legs: list[FundAllocationLeg],
    *,
    execution_mode: str,
) -> int:
    return sum(1 for leg in legs if leg.execution_mode == execution_mode)


def _count_by_group(
    legs: list[FundAllocationLeg],
    *,
    leg_group: str,
) -> int:
    return sum(1 for leg in legs if leg.leg_group == leg_group)


def _earn_count(legs: list[FundAllocationLeg]) -> int:
    return sum(
        1
        for leg in legs
        if leg.leg_group == LEG_GROUP_EARN
        or leg.execution_mode == EXECUTION_MODE_EARN_STAKE
        or bool(leg.earn_order_id)
    )


def _market_count(legs: list[FundAllocationLeg]) -> int:
    return _count_by_execution_mode(
        legs,
        execution_mode=EXECUTION_MODE_MARKET,
    )


def _native_iceberg_count(legs: list[FundAllocationLeg]) -> int:
    return _count_by_execution_mode(
        legs,
        execution_mode=EXECUTION_MODE_NATIVE_ICEBERG,
    )


def _sliced_ioc_count(legs: list[FundAllocationLeg]) -> int:
    return _count_by_execution_mode(
        legs,
        execution_mode=EXECUTION_MODE_SLICED_IOC_FALLBACK,
    )


def _critical_errors_from_legs(legs: list[FundAllocationLeg]) -> list[str]:
    errors: list[str] = []

    for leg in legs:
        if leg.status == ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW:
            errors.append(
                (
                    f"leg_id={leg.id} leg_key={leg.leg_key} "
                    f"requires review: {leg.error or 'no error details'}"
                )
            )

    return errors


def _warnings_from_legs(legs: list[FundAllocationLeg]) -> list[str]:
    warnings: list[str] = []

    for leg in legs:
        if leg.margin_guard_status in {
            "failed",
            "uncertain",
            "short_option_liquidity_failed",
        }:
            warnings.append(
                (
                    f"leg_id={leg.id} leg_key={leg.leg_key} "
                    f"margin_guard_status={leg.margin_guard_status}"
                )
            )

    return warnings


def build_allocation_report(
    db: Session,
    *,
    allocation_batch_id: int,
) -> dict[str, Any]:
    batch = _get_batch(db, allocation_batch_id=allocation_batch_id)
    legs = _get_legs(db, allocation_batch_id=allocation_batch_id)

    summary = summarize_allocation_batch(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    fund_code = _get_fund_code(
        db,
        fund_id=batch.fund_id,
    )

    critical_errors = list(summary.critical_errors)
    critical_errors.extend(_critical_errors_from_legs(legs))

    warnings = list(summary.warnings)
    warnings.extend(_warnings_from_legs(legs))

    report = {
        "allocation_batch_id": batch.id,
        "settlement_batch_id": batch.settlement_batch_id,
        "fund_id": batch.fund_id,
        "fund_code": fund_code,
        "status": batch.status,
        "positive_net_usdt": batch.positive_net_usdt,
        "scale": batch.scale,
        "snapshot_ts": batch.snapshot_ts,
        "started_at": batch.created_at,
        "completed_at": batch.completed_at,
        "generated_at": utcnow(),
        "total_legs": summary.total_legs,
        "filled_legs": summary.filled_legs_count,
        "skipped_legs": summary.skipped_legs_count,
        "partial_legs": summary.partial_legs_count,
        "failed_legs": summary.failed_legs_count,
        "active_legs": summary.active_legs_count,
        "total_target_usdt": summary.total_target_usdt,
        "total_filled_usdt": summary.total_filled_usdt,
        "total_residual_usdt": summary.total_residual_usdt,
        "residual_earn_usdt": summary.residual_earn_usdt,
        "residual_cash_usdt": summary.residual_cash_usdt,
        "unhandled_residual_usdt": summary.unhandled_residual_usdt,
        "market_count": _market_count(legs),
        "native_iceberg_count": _native_iceberg_count(legs),
        "sliced_ioc_count": _sliced_ioc_count(legs),
        "earn_count": _earn_count(legs),
        "derivative_count": _count_by_group(
            legs,
            leg_group=LEG_GROUP_DERIVATIVE,
        ),
        "option_count": _count_by_group(
            legs,
            leg_group=LEG_GROUP_OPTION,
        ),
        "failed_requires_review_count": summary.failed_requires_review_count,
        "margin_guard_skip_count": summary.margin_guard_skip_count,
        "earn_unavailable_skip_count": summary.earn_unavailable_skip_count,
        "symbol_not_trading_skip_count": summary.symbol_not_trading_skip_count,
        "min_order_skip_count": summary.min_order_skip_count,
        "critical_errors": critical_errors,
        "warnings": warnings,
    }

    return _json_dict(report)


def store_allocation_report(
    db: Session,
    *,
    allocation_batch_id: int,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    batch = _get_batch_for_update(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    report = report or build_allocation_report(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    report = _json_dict(report)

    if hasattr(batch, "report_json"):
        batch.report_json = report
    else:
        snapshot_json = dict(batch.snapshot_json or {})
        snapshot_json["allocation_report"] = report
        batch.snapshot_json = snapshot_json

    batch.updated_at = utcnow()

    db.add(batch)
    db.flush()

    return report


def build_and_store_allocation_report(
    db: Session,
    *,
    allocation_batch_id: int,
) -> dict[str, Any]:
    report = build_allocation_report(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    return store_allocation_report(
        db,
        allocation_batch_id=allocation_batch_id,
        report=report,
    )