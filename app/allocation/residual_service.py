from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.idempotency import make_residual_earn_leg_key
from app.allocation.spot_earn_handlers import handle_usdt_earn_stake_leg_mock
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    EXECUTION_MODE_RESIDUAL_CASH,
    EXECUTION_MODE_RESIDUAL_EARN,
    LEG_GROUP_EARN,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    RESIDUAL_SOURCE_STATUSES,
)
from app.config import settings
from app.models import FundAllocationBatch, FundAllocationLeg


ZERO = Decimal("0")


class ResidualServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResidualSource:
    leg_id: int
    leg_key: str
    leg_type: str
    status: str
    residual_usdt: Decimal


@dataclass(frozen=True)
class ResidualSummary:
    allocation_batch_id: int
    total_residual_usdt: Decimal
    materiality_usdt: Decimal
    sources: list[ResidualSource]

    @property
    def source_leg_ids(self) -> list[int]:
        return [source.leg_id for source in self.sources]

    @property
    def is_material(self) -> bool:
        return self.total_residual_usdt > self.materiality_usdt


@dataclass(frozen=True)
class ResidualDecision:
    allocation_batch_id: int
    residual_leg_id: int | None
    status: str | None
    execution_mode: str | None
    action: str
    total_residual_usdt: Decimal
    source_leg_ids: list[int]
    reason: str | None
    diagnostics: dict[str, Any]


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


def _materiality_usdt() -> Decimal:
    return dec(settings.ALLOCATION_RESIDUAL_MIN_MATERIALITY_USDT)


def _residual_leg_key(allocation_batch_id: int) -> str:
    return make_residual_earn_leg_key(allocation_batch_id)


def _is_residual_leg(leg: FundAllocationLeg) -> bool:
    return leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN


def _eligible_residual_source(leg: FundAllocationLeg) -> bool:
    if _is_residual_leg(leg):
        return False

    residual = dec(leg.residual_usdt)
    if residual <= ZERO:
        return False

    if leg.status in RESIDUAL_SOURCE_STATUSES:
        return True

    # Stage 22.4 rule: any leg with residual_usdt > 0 can feed residual engine.
    # This covers filled legs with max cap leftovers and future Stage 22.5 residuals.
    return True


def _next_residual_leg_index(db: Session, *, allocation_batch_id: int) -> int:
    max_index = (
        db.query(FundAllocationLeg.leg_index)
        .filter(FundAllocationLeg.allocation_batch_id == allocation_batch_id)
        .order_by(FundAllocationLeg.leg_index.desc())
        .first()
    )

    if max_index is None:
        return 1

    return int(max_index[0]) + 1


def _get_allocation_batch(db: Session, *, allocation_batch_id: int) -> FundAllocationBatch:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == allocation_batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise ResidualServiceError(f"Allocation batch not found: {allocation_batch_id}")

    return batch


def collect_residual_usdt_for_batch(
    db: Session,
    *,
    allocation_batch_id: int,
) -> ResidualSummary:
    legs = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.allocation_batch_id == allocation_batch_id)
        .order_by(FundAllocationLeg.leg_index.asc(), FundAllocationLeg.id.asc())
        .all()
    )

    sources: list[ResidualSource] = []
    total = ZERO

    for leg in legs:
        if not _eligible_residual_source(leg):
            continue

        residual = dec(leg.residual_usdt)
        total += residual

        sources.append(
            ResidualSource(
                leg_id=leg.id,
                leg_key=leg.leg_key,
                leg_type=leg.leg_type,
                status=leg.status,
                residual_usdt=residual,
            )
        )

    return ResidualSummary(
        allocation_batch_id=allocation_batch_id,
        total_residual_usdt=total,
        materiality_usdt=_materiality_usdt(),
        sources=sources,
    )


def find_residual_leg(
    db: Session,
    *,
    allocation_batch_id: int,
) -> FundAllocationLeg | None:
    leg_key = _residual_leg_key(allocation_batch_id)

    return (
        db.query(FundAllocationLeg)
        .filter(
            FundAllocationLeg.allocation_batch_id == allocation_batch_id,
            FundAllocationLeg.leg_key == leg_key,
        )
        .with_for_update()
        .first()
    )


def create_or_update_residual_leg(
    db: Session,
    *,
    allocation_batch_id: int,
) -> tuple[FundAllocationLeg | None, ResidualSummary]:
    batch = _get_allocation_batch(db, allocation_batch_id=allocation_batch_id)
    summary = collect_residual_usdt_for_batch(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    existing = find_residual_leg(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    if not summary.is_material:
        if existing is not None and existing.status == ALLOCATION_LEG_STATUS_PLANNED:
            existing.status = ALLOCATION_LEG_STATUS_RESIDUAL_CASH
            existing.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
            existing.target_usdt = summary.total_residual_usdt
            existing.residual_usdt = summary.total_residual_usdt
            existing.actual_cash_used_usdt = ZERO
            existing.error = (
                f"Residual below materiality: total={summary.total_residual_usdt}, "
                f"materiality={summary.materiality_usdt}"
            )
            existing.confirmed_at = utcnow()
            existing.updated_at = utcnow()
            db.add(existing)
            db.flush()

        return existing, summary

    if existing is None:
        existing = FundAllocationLeg(
            allocation_batch_id=batch.id,
            settlement_batch_id=batch.settlement_batch_id,
            fund_id=batch.fund_id,
            parent_leg_id=None,
            leg_index=_next_residual_leg_index(db, allocation_batch_id=batch.id),
            leg_key=_residual_leg_key(batch.id),
            leg_group=LEG_GROUP_EARN,
            leg_type=LEG_TYPE_RESIDUAL_USDT_EARN,
            coin="USDT",
            symbol=None,
            category=settings.ALLOCATION_USDT_EARN_CATEGORY,
            side=None,
            location="UNIFIED",
            current_size=None,
            current_usd_value=None,
            current_notional_usd=None,
            source_weight=None,
            target_usdt=summary.total_residual_usdt,
            target_qty=None,
            execution_mode=EXECUTION_MODE_RESIDUAL_EARN,
            planned_suborders=None,
            executed_suborders=None,
            order_link_id=None,
            bybit_order_id=None,
            strategy_id=None,
            earn_order_id=None,
            transfer_id=None,
            last_price=None,
            best_bid=None,
            best_ask=None,
            corridor_pct=None,
            available_liquidity_qty=None,
            available_liquidity_usdt=None,
            required_qty=None,
            required_usdt=summary.total_residual_usdt,
            filled_qty=None,
            filled_usdt=None,
            avg_fill_price=None,
            fill_ratio=None,
            fee_usdt=None,
            actual_cash_used_usdt=None,
            actual_margin_change_usdt=None,
            residual_usdt=summary.total_residual_usdt,
            status=ALLOCATION_LEG_STATUS_PLANNED,
            error=None,
            created_at=utcnow(),
            updated_at=utcnow(),
            sent_at=None,
            confirmed_at=None,
        )
        db.add(existing)
        db.flush()

    else:
        if existing.status in {
            ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
            ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
            ALLOCATION_LEG_STATUS_FILLED,
        }:
            return existing, summary

        existing.target_usdt = summary.total_residual_usdt
        existing.required_usdt = summary.total_residual_usdt
        existing.residual_usdt = summary.total_residual_usdt
        existing.coin = "USDT"
        existing.category = settings.ALLOCATION_USDT_EARN_CATEGORY
        existing.execution_mode = EXECUTION_MODE_RESIDUAL_EARN
        existing.status = ALLOCATION_LEG_STATUS_PLANNED
        existing.error = None
        existing.updated_at = utcnow()

        db.add(existing)
        db.flush()

    return existing, summary


def process_residual_leg_mock(
    db: Session,
    *,
    allocation_batch_id: int,
    client: Any,
) -> ResidualDecision:
    residual_leg, summary = create_or_update_residual_leg(
        db,
        allocation_batch_id=allocation_batch_id,
    )

    if residual_leg is None:
        return ResidualDecision(
            allocation_batch_id=allocation_batch_id,
            residual_leg_id=None,
            status=None,
            execution_mode=None,
            action="residual_below_materiality",
            total_residual_usdt=summary.total_residual_usdt,
            source_leg_ids=summary.source_leg_ids,
            reason=(
                f"Residual below materiality: total={summary.total_residual_usdt}, "
                f"materiality={summary.materiality_usdt}"
            ),
            diagnostics={
                "materiality_usdt": str(summary.materiality_usdt),
                "sources_count": len(summary.sources),
            },
        )

    if residual_leg.status in {
        ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
        ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
        ALLOCATION_LEG_STATUS_FILLED,
    }:
        return ResidualDecision(
            allocation_batch_id=allocation_batch_id,
            residual_leg_id=residual_leg.id,
            status=residual_leg.status,
            execution_mode=residual_leg.execution_mode,
            action="residual_leg_already_final",
            total_residual_usdt=summary.total_residual_usdt,
            source_leg_ids=summary.source_leg_ids,
            reason=None,
            diagnostics={
                "leg_key": residual_leg.leg_key,
            },
        )

    handler_decision = handle_usdt_earn_stake_leg_mock(
        db,
        allocation_leg_id=residual_leg.id,
        client=client,
        is_residual_leg=True,
    )

    db.refresh(residual_leg)

    return ResidualDecision(
        allocation_batch_id=allocation_batch_id,
        residual_leg_id=residual_leg.id,
        status=residual_leg.status,
        execution_mode=residual_leg.execution_mode,
        action=handler_decision.action,
        total_residual_usdt=summary.total_residual_usdt,
        source_leg_ids=summary.source_leg_ids,
        reason=handler_decision.reason,
        diagnostics={
            "leg_key": residual_leg.leg_key,
            "handler_diagnostics": handler_decision.diagnostics,
            "sources_count": len(summary.sources),
        },
    )