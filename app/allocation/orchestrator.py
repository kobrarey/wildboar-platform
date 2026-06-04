from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.alerts import (
    AllocationAlertResult,
    send_critical_allocation_alerts_mock,
)
from app.allocation.derivative_handlers import handle_derivative_leg_mock
from app.allocation.execution_engine import prepare_execution_for_leg
from app.allocation.finalization import (
    AllocationFinalizationResult,
    finalize_allocation_batch,
)
from app.allocation.reconciliation import (
    ReconciliationResult,
    reconcile_allocation_batch_mock,
)
from app.allocation.reporting import build_and_store_allocation_report
from app.allocation.residual_service import (
    ResidualDecision,
    process_residual_leg_mock,
)
from app.allocation.spot_earn_handlers import handle_spot_earn_leg_mock
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_PLANNED,
    DERIVATIVE_SUPPORTED_LEG_TYPES,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    RETRYABLE_ALLOCATION_BATCH_STATUSES,
    SPOT_EARN_SUPPORTED_LEG_TYPES,
)
from app.config import settings
from app.models import FundAllocationBatch, FundAllocationLeg, FundSettlementBatch


ZERO = Decimal("0")


class PositiveNetAllocationOrchestratorError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedLegProcessResult:
    allocation_leg_id: int
    allocation_batch_id: int
    leg_type: str
    action: str
    status: str | None
    execution_mode: str | None
    ok: bool
    reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _json_dict(asdict(self))


@dataclass(frozen=True)
class PositiveNetAllocationResult:
    allocation_batch_id: int
    settlement_batch_id: int
    fund_id: int
    status_before: str
    status_after: str
    ok: bool

    reconciliation_before: ReconciliationResult
    reconciliation_after: ReconciliationResult
    planned_leg_results: list[PlannedLegProcessResult]
    residual_decision: ResidualDecision
    finalization: AllocationFinalizationResult
    report: dict[str, Any]
    alert_result: AllocationAlertResult

    reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_batch_id": self.allocation_batch_id,
            "settlement_batch_id": self.settlement_batch_id,
            "fund_id": self.fund_id,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "ok": self.ok,
            "reconciliation_before": _json_dict(asdict(self.reconciliation_before)),
            "reconciliation_after": _json_dict(asdict(self.reconciliation_after)),
            "planned_leg_results": [
                result.to_dict()
                for result in self.planned_leg_results
            ],
            "residual_decision": _json_dict(asdict(self.residual_decision)),
            "finalization": self.finalization.to_dict(),
            "report": _json_dict(self.report),
            "alert_result": self.alert_result.to_dict(),
            "reason": self.reason,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


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
    return {str(key): _json_value(value) for key, value in data.items()}


def _client_post_calls(client: Any) -> list[Any]:
    calls = getattr(client, "post_calls", None)

    if calls is None:
        return []

    try:
        return list(calls)
    except Exception:
        return []


def _assert_no_post_calls(client: Any) -> None:
    post_calls = _client_post_calls(client)

    if post_calls:
        raise PositiveNetAllocationOrchestratorError(
            f"POST calls are forbidden in Stage 22.6 mock allocation: {post_calls}"
        )


def _get_batch_for_update(
    db: Session,
    *,
    allocation_batch_id: int,
) -> FundAllocationBatch:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == allocation_batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise PositiveNetAllocationOrchestratorError(
            f"Allocation batch not found: {allocation_batch_id}"
        )

    return batch


def _get_settlement_batch_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    settlement_batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == settlement_batch_id)
        .with_for_update()
        .first()
    )

    if settlement_batch is None:
        raise PositiveNetAllocationOrchestratorError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return settlement_batch


def _validate_mock_only_mode() -> None:
    if not settings.POSITIVE_NET_ALLOCATION_MOCK_ONLY:
        raise PositiveNetAllocationOrchestratorError(
            "Stage 22.6 orchestrator is mock-only. "
            "POSITIVE_NET_ALLOCATION_MOCK_ONLY must remain true."
        )


def _validate_batch_is_retryable(batch: FundAllocationBatch) -> None:
    if batch.status not in RETRYABLE_ALLOCATION_BATCH_STATUSES:
        raise PositiveNetAllocationOrchestratorError(
            (
                "Allocation batch status is not retryable for Stage 22.6 mock "
                f"orchestrator: batch_id={batch.id}, status={batch.status}"
            )
        )


def _validate_positive_net_settlement(settlement_batch: FundSettlementBatch) -> None:
    net_cash = dec(getattr(settlement_batch, "net_cash_usdt", None))

    if net_cash <= ZERO:
        raise PositiveNetAllocationOrchestratorError(
            (
                "Allocation batch does not belong to a positive-net settlement: "
                f"settlement_batch_id={settlement_batch.id}, net_cash_usdt={net_cash}"
            )
        )


def _mark_batch_processing(batch: FundAllocationBatch) -> None:
    now = utcnow()

    if batch.status != ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING:
        batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING
        batch.error = None
        batch.completed_at = None

    if batch.allocation_started_at is None:
        batch.allocation_started_at = now

    batch.updated_at = now


def _mark_batch_failed(
    db: Session,
    batch: FundAllocationBatch,
    *,
    reason: str,
) -> None:
    batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW
    batch.error = reason
    batch.completed_at = None
    batch.updated_at = utcnow()

    db.add(batch)
    db.flush()


def _planned_non_residual_legs(
    db: Session,
    *,
    allocation_batch_id: int,
) -> list[FundAllocationLeg]:
    return (
        db.query(FundAllocationLeg)
        .filter(
            FundAllocationLeg.allocation_batch_id == allocation_batch_id,
            FundAllocationLeg.status == ALLOCATION_LEG_STATUS_PLANNED,
            FundAllocationLeg.leg_type != LEG_TYPE_RESIDUAL_USDT_EARN,
        )
        .order_by(FundAllocationLeg.leg_index.asc(), FundAllocationLeg.id.asc())
        .all()
    )


def _decision_attr(decision: Any, name: str, default: Any = None) -> Any:
    return getattr(decision, name, default)


def _planned_result_from_decision(
    leg: FundAllocationLeg,
    *,
    decision: Any,
    ok: bool = True,
) -> PlannedLegProcessResult:
    return PlannedLegProcessResult(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        leg_type=str(leg.leg_type or ""),
        action=str(_decision_attr(decision, "action", "unknown")),
        status=_decision_attr(decision, "status", None),
        execution_mode=_decision_attr(decision, "execution_mode", None),
        ok=ok,
        reason=_decision_attr(decision, "reason", None),
        diagnostics=_decision_attr(decision, "diagnostics", {}) or {},
    )


def _mark_leg_failed_requires_review(
    db: Session,
    leg: FundAllocationLeg,
    *,
    reason: str,
) -> PlannedLegProcessResult:
    leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    leg.error = reason
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return PlannedLegProcessResult(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        leg_type=str(leg.leg_type or ""),
        action="mark_failed_requires_review",
        status=leg.status,
        execution_mode=leg.execution_mode,
        ok=False,
        reason=reason,
        diagnostics={},
    )


def _process_single_planned_leg_mock(
    db: Session,
    *,
    leg: FundAllocationLeg,
    client: Any,
) -> PlannedLegProcessResult:
    leg_type = str(leg.leg_type or "")

    try:
        if leg_type in SPOT_EARN_SUPPORTED_LEG_TYPES:
            decision = handle_spot_earn_leg_mock(
                db,
                allocation_leg_id=leg.id,
                client=client,
            )
        elif leg_type in DERIVATIVE_SUPPORTED_LEG_TYPES:
            decision = handle_derivative_leg_mock(
                db,
                allocation_leg_id=leg.id,
                client=client,
            )
        else:
            decision = prepare_execution_for_leg(
                db,
                allocation_leg_id=leg.id,
                client=client,
                mock_mode=True,
            )

        _assert_no_post_calls(client)

        db.refresh(leg)

        return _planned_result_from_decision(
            leg,
            decision=decision,
            ok=True,
        )

    except Exception as exc:
        return _mark_leg_failed_requires_review(
            db,
            leg,
            reason=(
                f"Planned leg mock processing failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        )


def process_planned_legs_mock(
    db: Session,
    *,
    allocation_batch_id: int,
    client: Any,
) -> list[PlannedLegProcessResult]:
    planned_legs = _planned_non_residual_legs(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    results: list[PlannedLegProcessResult] = []

    for leg in planned_legs:
        result = _process_single_planned_leg_mock(
            db,
            leg=leg,
            client=client,
        )
        results.append(result)

        _assert_no_post_calls(client)

    return results


def process_positive_net_allocation_batch_mock(
    db: Session,
    *,
    allocation_batch_id: int,
    client: Any,
) -> PositiveNetAllocationResult:
    """
    Stage 22.6 positive-net allocation orchestrator.

    This service coordinates existing mock-only Stage 22 services:
    - reconciliation;
    - spot/Earn execution handlers;
    - derivative/options handlers;
    - residual engine;
    - finalization;
    - reporting;
    - critical alert policy.

    Forbidden:
    - accounting mutation;
    - user position mutation;
    - fund shares mutation;
    - settlement accounting mutation;
    - real external execution.
    """
    _validate_mock_only_mode()

    batch = _get_batch_for_update(
        db,
        allocation_batch_id=allocation_batch_id,
    )
    status_before = batch.status

    settlement_batch = _get_settlement_batch_for_update(
        db,
        settlement_batch_id=batch.settlement_batch_id,
    )

    _validate_batch_is_retryable(batch)
    _validate_positive_net_settlement(settlement_batch)

    _mark_batch_processing(batch)

    if batch.reconciliation_started_at is None:
        batch.reconciliation_started_at = utcnow()

    db.add(batch)
    db.flush()

    warnings: list[str] = []
    errors: list[str] = []

    try:
        reconciliation_before = reconcile_allocation_batch_mock(
            db,
            allocation_batch_id=batch.id,
            client=client,
        )
        _assert_no_post_calls(client)

        planned_leg_results = process_planned_legs_mock(
            db,
            allocation_batch_id=batch.id,
            client=client,
        )
        _assert_no_post_calls(client)

        residual_decision = process_residual_leg_mock(
            db,
            allocation_batch_id=batch.id,
            client=client,
        )
        _assert_no_post_calls(client)

        reconciliation_after = reconcile_allocation_batch_mock(
            db,
            allocation_batch_id=batch.id,
            client=client,
        )
        _assert_no_post_calls(client)

        batch.reconciliation_completed_at = utcnow()
        batch.updated_at = batch.reconciliation_completed_at
        db.add(batch)
        db.flush()

        finalization = finalize_allocation_batch(
            db,
            allocation_batch_id=batch.id,
        )

        report = build_and_store_allocation_report(
            db,
            allocation_batch_id=batch.id,
        )

        alert_result = send_critical_allocation_alerts_mock(
            db,
            report=report,
            mock_only=True,
        )

        if alert_result.alerts:
            batch.alert_sent_at = utcnow()
            batch.updated_at = batch.alert_sent_at
            db.add(batch)
            db.flush()

        _assert_no_post_calls(client)

        db.refresh(batch)

        if reconciliation_before.warnings:
            warnings.extend(reconciliation_before.warnings)

        if reconciliation_after.warnings:
            warnings.extend(reconciliation_after.warnings)

        if finalization.summary.warnings:
            warnings.extend(finalization.summary.warnings)

        errors.extend(reconciliation_before.errors)
        errors.extend(reconciliation_after.errors)
        errors.extend(finalization.summary.critical_errors)

        return PositiveNetAllocationResult(
            allocation_batch_id=batch.id,
            settlement_batch_id=batch.settlement_batch_id,
            fund_id=batch.fund_id,
            status_before=status_before,
            status_after=batch.status,
            ok=finalization.ok,
            reconciliation_before=reconciliation_before,
            reconciliation_after=reconciliation_after,
            planned_leg_results=planned_leg_results,
            residual_decision=residual_decision,
            finalization=finalization,
            report=report,
            alert_result=alert_result,
            reason=finalization.reason,
            warnings=warnings,
            errors=errors,
        )

    except Exception as exc:
        reason = f"Positive-net allocation orchestrator failed: {type(exc).__name__}: {exc}"
        _mark_batch_failed(
            db,
            batch,
            reason=reason,
        )

        reconciliation_before = reconcile_allocation_batch_mock(
            db,
            allocation_batch_id=batch.id,
            client=client,
        )

        reconciliation_after = reconciliation_before

        residual_decision = ResidualDecision(
            allocation_batch_id=batch.id,
            residual_leg_id=None,
            status=None,
            execution_mode=None,
            action="orchestrator_failed_before_residual_processing",
            total_residual_usdt=ZERO,
            source_leg_ids=[],
            reason=reason,
            diagnostics={},
        )

        finalization = finalize_allocation_batch(
            db,
            allocation_batch_id=batch.id,
        )

        report = build_and_store_allocation_report(
            db,
            allocation_batch_id=batch.id,
        )

        alert_result = send_critical_allocation_alerts_mock(
            db,
            report=report,
            mock_only=True,
        )

        if alert_result.alerts:
            batch.alert_sent_at = utcnow()
            batch.updated_at = batch.alert_sent_at
            db.add(batch)
            db.flush()

        db.refresh(batch)

        return PositiveNetAllocationResult(
            allocation_batch_id=batch.id,
            settlement_batch_id=batch.settlement_batch_id,
            fund_id=batch.fund_id,
            status_before=status_before,
            status_after=batch.status,
            ok=False,
            reconciliation_before=reconciliation_before,
            reconciliation_after=reconciliation_after,
            planned_leg_results=[],
            residual_decision=residual_decision,
            finalization=finalization,
            report=report,
            alert_result=alert_result,
            reason=reason,
            warnings=warnings,
            errors=[reason],
        )