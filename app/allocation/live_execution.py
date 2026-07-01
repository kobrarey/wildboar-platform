from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.live_earn_config import (
    allocation_earn_live_enabled,
    residual_earn_to_cash_when_live_disabled,
)
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED,
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
    ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
    ALLOCATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
    ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE,
    DERIVATIVE_SUPPORTED_LEG_GROUPS,
    DERIVATIVE_SUPPORTED_LEG_TYPES,
    EXECUTION_MODE_CASH_NOOP,
    EXECUTION_MODE_RESIDUAL_CASH,
    EXECUTION_MODE_SKIPPED,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.models import (
    Fund,
    FundAllocationBatch,
    FundAllocationLeg,
    FundRuntimeState,
)
from app.allocation.live_policy import (
    DERIVATIVE_OPTION_SKIP_REASON,
    classify_live_leg_policy,
    derivative_live_policy,
    buy_then_stake_live_policy,
)


ZERO = Decimal("0")


LIVE_SUPPORTED_LEG_TYPES = {
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_SPOT_BUY,
}

LIVE_EARN_LEG_TYPES = {
    LEG_TYPE_USDT_EARN_STAKE,
    LEG_TYPE_RESIDUAL_USDT_EARN,
}

LIVE_RESIDUAL_CASH_LEG_TYPES: set[str] = set()

LIVE_PREFLIGHT_PLANNED_STATUSES = {
    ALLOCATION_LEG_STATUS_PLANNED,
}

LIVE_PREFLIGHT_ALREADY_ACCEPTED_STATUSES = {
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
    ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
}

LIVE_PREFLIGHT_RECONCILABLE_PENDING_STATUSES = {
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
}

LIVE_PREFLIGHT_BLOCKING_STATUSES = {
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
}

LIVE_PREFLIGHT_UNSUPPORTED_PROCESSING_STATUSES = {
    ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
    ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
}

LIVE_PREFLIGHT_ALLOWED_ALREADY_PROCESSED_STATUSES = (
    LIVE_PREFLIGHT_ALREADY_ACCEPTED_STATUSES
    | LIVE_PREFLIGHT_RECONCILABLE_PENDING_STATUSES
)


class LiveAllocationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveAllocationPreflightIssue:
    allocation_leg_id: int | None
    leg_type: str | None
    leg_group: str | None
    symbol: str | None
    reason: str


@dataclass(frozen=True)
class LiveAllocationPreflightResult:
    ok: bool
    allocation_batch_id: int
    fund_id: int
    settlement_batch_id: int
    supported_leg_ids: list[int] = field(default_factory=list)
    residual_cash_leg_ids: list[int] = field(default_factory=list)
    issues: list[LiveAllocationPreflightIssue] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["diagnostics"] = _json_dict(raw["diagnostics"])
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
    return {str(k): _json_value(v) for k, v in data.items()}


def _get_batch_for_update(
    db: Session,
    *,
    allocation_batch_id: int,
) -> FundAllocationBatch:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == int(allocation_batch_id))
        .with_for_update()
        .first()
    )

    if batch is None:
        raise LiveAllocationExecutionError(
            f"Allocation batch not found: {allocation_batch_id}"
        )

    return batch


def _get_fund_for_update(
    db: Session,
    *,
    fund_id: int,
) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .with_for_update()
        .first()
    )

    if fund is None:
        raise LiveAllocationExecutionError(f"Fund not found: {fund_id}")

    return fund


def _get_runtime_state(
    db: Session,
    *,
    fund_id: int,
) -> FundRuntimeState | None:
    return (
        db.query(FundRuntimeState)
        .filter(FundRuntimeState.fund_id == int(fund_id))
        .first()
    )


def _get_batch_legs_for_update(
    db: Session,
    *,
    allocation_batch_id: int,
) -> list[FundAllocationLeg]:
    return (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.allocation_batch_id == int(allocation_batch_id))
        .order_by(FundAllocationLeg.leg_index.asc(), FundAllocationLeg.id.asc())
        .with_for_update()
        .all()
    )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _is_derivative_or_option_leg(leg: FundAllocationLeg) -> bool:
    leg_type = _normalize_text(leg.leg_type)
    leg_group = _normalize_text(leg.leg_group)

    return (
        leg_type in DERIVATIVE_SUPPORTED_LEG_TYPES
        or leg_group in DERIVATIVE_SUPPORTED_LEG_GROUPS
    )


def _issue_for_leg(leg: FundAllocationLeg, *, reason: str) -> LiveAllocationPreflightIssue:
    return LiveAllocationPreflightIssue(
        allocation_leg_id=int(leg.id),
        leg_type=leg.leg_type,
        leg_group=leg.leg_group,
        symbol=leg.symbol,
        reason=reason,
    )


def _leg_has_idempotency_reference(leg: FundAllocationLeg) -> bool:
    return bool(
        _normalize_text(leg.order_link_id)
        or _normalize_text(leg.bybit_order_id)
        or _normalize_text(leg.earn_order_id)
        or _normalize_text(leg.strategy_id)
    )


def preflight_live_allocation_batch(
    db: Session,
    *,
    allocation_batch_id: int,
    fund_code: str | None = None,
) -> LiveAllocationPreflightResult:
    """
    Live allocation preflight.

    Safety:
    - Does not execute external calls.
    - Does not call Bybit.
    - Does not mutate DB.
    - Must run before any live external allocation order.
    """
    batch = _get_batch_for_update(db, allocation_batch_id=allocation_batch_id)
    fund = _get_fund_for_update(db, fund_id=int(batch.fund_id))
    runtime_state = _get_runtime_state(db, fund_id=int(batch.fund_id))
    legs = _get_batch_legs_for_update(db, allocation_batch_id=int(batch.id))

    issues: list[LiveAllocationPreflightIssue] = []
    supported_leg_ids: list[int] = []
    residual_cash_leg_ids: list[int] = []

    if fund_code and str(fund.code).lower() != str(fund_code).lower():
        issues.append(
            LiveAllocationPreflightIssue(
                allocation_leg_id=None,
                leg_type=None,
                leg_group=None,
                symbol=None,
                reason=(
                    f"fund_code_mismatch: expected={fund_code}, "
                    f"actual={fund.code}"
                ),
            )
        )

    if not fund.is_active:
        issues.append(
            LiveAllocationPreflightIssue(
                allocation_leg_id=None,
                leg_type=None,
                leg_group=None,
                symbol=None,
                reason=f"fund_not_active: fund_id={fund.id}",
            )
        )

    if batch.status not in {
        ALLOCATION_BATCH_STATUS_PLAN_CREATED,
        ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
    }:
        issues.append(
            LiveAllocationPreflightIssue(
                allocation_leg_id=None,
                leg_type=None,
                leg_group=None,
                symbol=None,
                reason=f"unsupported_batch_status: {batch.status}",
            )
        )

    if not legs:
        issues.append(
            LiveAllocationPreflightIssue(
                allocation_leg_id=None,
                leg_type=None,
                leg_group=None,
                symbol=None,
                reason="allocation_batch_has_no_legs",
            )
        )

    for leg in legs:
        leg_type = _normalize_text(leg.leg_type)

        leg_status = _normalize_text(leg.status)

        if leg_status in LIVE_PREFLIGHT_BLOCKING_STATUSES:
            issues.append(
                _issue_for_leg(
                    leg,
                    reason=f"blocking_leg_status: {leg.status}",
                )
            )
            continue

        if leg_status in LIVE_PREFLIGHT_ALREADY_ACCEPTED_STATUSES:
            # Already processed successfully or safely residualized.
            # Do not block subsequent legs in the same mixed live batch.
            continue

        if leg_status in LIVE_PREFLIGHT_RECONCILABLE_PENDING_STATUSES:
            if _leg_has_idempotency_reference(leg):
                # Pending live order with deterministic reference must be handled
                # by the leg-specific reconciliation path, not by duplicate POST.
                supported_leg_ids.append(int(leg.id))
                continue

            issues.append(
                _issue_for_leg(
                    leg,
                    reason=f"pending_leg_missing_idempotency_reference: {leg.status}",
                )
            )
            continue

        if leg_status in LIVE_PREFLIGHT_UNSUPPORTED_PROCESSING_STATUSES:
            issues.append(
                _issue_for_leg(
                    leg,
                    reason=f"unsupported_live_processing_status: {leg.status}",
                )
            )
            continue

        if leg_status not in LIVE_PREFLIGHT_PLANNED_STATUSES:
            issues.append(
                _issue_for_leg(
                    leg,
                    reason=f"unsupported_leg_status: {leg.status}",
                )
            )
            continue

        policy_decision = classify_live_leg_policy(leg)

        if policy_decision.policy_skipped:
            residual_cash_leg_ids.append(int(leg.id))
            continue

        if policy_decision.fail_closed:
            issues.append(
                _issue_for_leg(
                    leg,
                    reason=policy_decision.reason or "live_policy_fail_closed",
                )
            )
            continue

        if leg_type == LEG_TYPE_BUY_THEN_STAKE:
            if not _normalize_text(leg.symbol):
                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="buy_then_stake_spot_only_symbol_required",
                    )
                )
                continue

            if dec(leg.target_usdt) <= ZERO and dec(leg.target_qty) <= ZERO:
                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="buy_then_stake_spot_only_target_required",
                    )
                )
                continue

            supported_leg_ids.append(int(leg.id))
            continue

        if leg_type in LIVE_SUPPORTED_LEG_TYPES:
            if leg_type == LEG_TYPE_SPOT_BUY and not _normalize_text(leg.symbol):
                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="spot_buy_symbol_required",
                    )
                )
                continue

            if dec(leg.target_usdt) <= ZERO and dec(leg.target_qty) <= ZERO:
                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="target_usdt_or_target_qty_required",
                    )
                )
                continue

            supported_leg_ids.append(int(leg.id))
            continue

        if leg_type in LIVE_EARN_LEG_TYPES:
            if leg_type == LEG_TYPE_RESIDUAL_USDT_EARN and not allocation_earn_live_enabled():
                if residual_earn_to_cash_when_live_disabled():
                    residual_cash_leg_ids.append(int(leg.id))
                    continue

                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="residual_earn_live_disabled_and_cash_fallback_disabled",
                    )
                )
                continue

            if not allocation_earn_live_enabled():
                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="allocation_earn_live_disabled",
                    )
                )
                continue

            if dec(leg.target_usdt) <= ZERO and dec(leg.target_qty) <= ZERO:
                issues.append(
                    _issue_for_leg(
                        leg,
                        reason="earn_target_usdt_or_target_qty_required",
                    )
                )
                continue

            # Stage 25.3B only classifies live Earn as supported by static preflight.
            # The real product whitelist/min/max/precision check is done by
            # app.allocation.live_earn_orders.build_live_earn_stake_order_plan
            # before any POST.
            supported_leg_ids.append(int(leg.id))
            continue

        if leg_type in LIVE_RESIDUAL_CASH_LEG_TYPES:
            residual_cash_leg_ids.append(int(leg.id))
            continue

        issues.append(
            _issue_for_leg(
                leg,
                reason=f"unsupported_live_allocation_leg_type: {leg.leg_type}",
            )
        )

    ok = not issues

    return LiveAllocationPreflightResult(
        ok=ok,
        allocation_batch_id=int(batch.id),
        fund_id=int(batch.fund_id),
        settlement_batch_id=int(batch.settlement_batch_id),
        supported_leg_ids=supported_leg_ids,
        residual_cash_leg_ids=residual_cash_leg_ids,
        issues=issues,
        diagnostics={
            "fund_code": fund.code,
            "batch_status": batch.status,
            "total_legs": len(legs),
            "supported_legs": len(supported_leg_ids),
            "residual_cash_legs": len(residual_cash_leg_ids),
            "allowed_already_processed_statuses": sorted(
                LIVE_PREFLIGHT_ALREADY_ACCEPTED_STATUSES
            ),
            "allowed_reconcilable_pending_statuses": sorted(
                LIVE_PREFLIGHT_RECONCILABLE_PENDING_STATUSES
            ),
            "blocking_statuses": sorted(LIVE_PREFLIGHT_BLOCKING_STATUSES),
            "unsupported_processing_statuses": sorted(
                LIVE_PREFLIGHT_UNSUPPORTED_PROCESSING_STATUSES
            ),
            "pricing_locked": (
                None if runtime_state is None else bool(runtime_state.pricing_locked)
            ),
            "earn_live_enabled": allocation_earn_live_enabled(),
            "residual_earn_to_cash_when_live_disabled": residual_earn_to_cash_when_live_disabled(),
            "allocation_derivative_live_policy": derivative_live_policy(),
            "allocation_buy_then_stake_live_policy": buy_then_stake_live_policy(),
            "external_calls": 0,
        },
    )


def mark_allocation_batch_failed_requires_review(
    db: Session,
    *,
    allocation_batch_id: int,
    error: str,
    diagnostics: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> FundAllocationBatch:
    now = now or utcnow()

    batch = _get_batch_for_update(db, allocation_batch_id=allocation_batch_id)
    batch.status = ALLOCATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.report_json = _json_dict(
        {
            "stage25_2_live_preflight": {
                "ok": False,
                "error": error,
                "diagnostics": diagnostics or {},
                "external_calls": 0,
            }
        }
    )
    batch.updated_at = now

    db.add(batch)
    db.flush()

    return batch


def mark_policy_skipped_leg_without_external_call(
    db: Session,
    *,
    allocation_leg_id: int,
    reason: str,
    now: datetime | None = None,
) -> FundAllocationLeg:
    now = now or utcnow()

    leg = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .with_for_update()
        .first()
    )

    if leg is None:
        raise LiveAllocationExecutionError(
            f"Allocation leg not found: {allocation_leg_id}"
        )

    target_usdt = dec(leg.target_usdt)

    leg.status = ALLOCATION_LEG_STATUS_RESIDUAL_CASH
    leg.execution_mode = EXECUTION_MODE_SKIPPED
    leg.residual_usdt = target_usdt
    leg.actual_cash_used_usdt = ZERO
    leg.actual_margin_change_usdt = ZERO
    leg.error = reason
    leg.confirmed_at = now
    leg.updated_at = now

    db.add(leg)
    db.flush()

    return leg


def mark_leg_residual_cash_without_external_call(
    db: Session,
    *,
    allocation_leg_id: int,
    reason: str,
    now: datetime | None = None,
) -> FundAllocationLeg:
    now = now or utcnow()

    leg = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .with_for_update()
        .first()
    )

    if leg is None:
        raise LiveAllocationExecutionError(
            f"Allocation leg not found: {allocation_leg_id}"
        )

    target_usdt = dec(leg.target_usdt)

    leg.status = ALLOCATION_LEG_STATUS_RESIDUAL_CASH
    leg.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
    leg.residual_usdt = target_usdt
    leg.actual_cash_used_usdt = ZERO
    leg.error = reason
    leg.confirmed_at = now
    leg.updated_at = now

    db.add(leg)
    db.flush()

    return leg


def mark_stable_cash_leg_filled_without_external_call(
    db: Session,
    *,
    allocation_leg_id: int,
    reason: str = "stable_cash_live_noop",
    now: datetime | None = None,
) -> FundAllocationLeg:
    now = now or utcnow()

    leg = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .with_for_update()
        .first()
    )

    if leg is None:
        raise LiveAllocationExecutionError(
            f"Allocation leg not found: {allocation_leg_id}"
        )

    target_usdt = dec(leg.target_usdt)

    leg.status = ALLOCATION_LEG_STATUS_FILLED
    leg.execution_mode = EXECUTION_MODE_CASH_NOOP
    leg.filled_qty = target_usdt
    leg.filled_usdt = target_usdt
    leg.actual_cash_used_usdt = ZERO
    leg.residual_usdt = ZERO
    leg.error = None
    leg.confirmed_at = now
    leg.updated_at = now

    db.add(leg)
    db.flush()

    return leg


def refresh_live_allocation_batch_progress(
    db: Session,
    *,
    allocation_batch_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utcnow()

    batch = _get_batch_for_update(db, allocation_batch_id=allocation_batch_id)
    legs = _get_batch_legs_for_update(
        db,
        allocation_batch_id=int(allocation_batch_id),
    )

    active_statuses = {
        ALLOCATION_LEG_STATUS_PLANNED,
        ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
        ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
        ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    }

    filled_statuses = {
        ALLOCATION_LEG_STATUS_FILLED,
        ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    }

    residual_cash_statuses = {
        ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
        ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    }

    failed_statuses = {
        ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    }

    total_count = len(legs)
    filled_count = 0
    skipped_count = 0
    partial_count = 0
    failed_count = 0
    active_count = 0

    total_target_usdt = ZERO
    total_filled_usdt = ZERO
    total_residual_usdt = ZERO
    residual_cash_usdt = ZERO
    residual_earn_usdt = ZERO

    for leg in legs:
        status = str(leg.status or "")

        total_target_usdt += dec(leg.target_usdt)
        total_filled_usdt += dec(leg.filled_usdt)
        total_residual_usdt += dec(leg.residual_usdt)

        if status in active_statuses:
            active_count += 1

        if status in filled_statuses:
            filled_count += 1

        if status in {
            ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
            ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
            ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
            ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE,
        }:
            skipped_count += 1
            residual_cash_usdt += dec(leg.residual_usdt)

        if status == ALLOCATION_LEG_STATUS_RESIDUAL_CASH:
            skipped_count += 1
            residual_cash_usdt += dec(leg.residual_usdt)

        if status == ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED:
            partial_count += 1
            residual_cash_usdt += dec(leg.residual_usdt)

        if status == ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED:
            residual_earn_usdt += dec(leg.filled_usdt)

        if status in failed_statuses:
            failed_count += 1

    batch.total_legs_count = total_count
    batch.filled_legs_count = filled_count
    batch.skipped_legs_count = skipped_count
    batch.partial_legs_count = partial_count
    batch.failed_legs_count = failed_count
    batch.active_legs_count = active_count

    batch.total_target_usdt = total_target_usdt
    batch.total_filled_usdt = total_filled_usdt
    batch.total_residual_usdt = total_residual_usdt
    batch.residual_cash_usdt = residual_cash_usdt
    batch.residual_earn_usdt = residual_earn_usdt

    if failed_count > 0:
        batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW
        batch.error = "allocation_leg_failed_requires_review"
        batch.completed_at = None
    elif active_count > 0:
        batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING
        batch.error = None
    elif residual_cash_usdt > ZERO or partial_count > 0 or skipped_count > 0:
        batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH
        batch.error = None
        batch.completed_at = batch.completed_at or now
    else:
        batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED
        batch.error = None
        batch.completed_at = batch.completed_at or now

    batch.report_json = _json_dict(
        {
            "stage25_2_live_batch_progress": {
                "total_legs_count": total_count,
                "filled_legs_count": filled_count,
                "skipped_legs_count": skipped_count,
                "partial_legs_count": partial_count,
                "failed_legs_count": failed_count,
                "active_legs_count": active_count,
                "total_target_usdt": total_target_usdt,
                "total_filled_usdt": total_filled_usdt,
                "total_residual_usdt": total_residual_usdt,
                "residual_cash_usdt": residual_cash_usdt,
                "residual_earn_usdt": residual_earn_usdt,
                "batch_status": batch.status,
                "external_calls": "not_performed_by_progress_refresh",
            }
        }
    )
    batch.updated_at = now

    db.add(batch)
    db.flush()

    return {
        "allocation_batch_id": int(batch.id),
        "status": batch.status,
        "total_legs_count": total_count,
        "filled_legs_count": filled_count,
        "skipped_legs_count": skipped_count,
        "partial_legs_count": partial_count,
        "failed_legs_count": failed_count,
        "active_legs_count": active_count,
        "total_target_usdt": str(total_target_usdt),
        "total_filled_usdt": str(total_filled_usdt),
        "total_residual_usdt": str(total_residual_usdt),
        "residual_cash_usdt": str(residual_cash_usdt),
        "residual_earn_usdt": str(residual_earn_usdt),
    }