from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.snapshot_service import (
    AllocationSnapshot,
    AllocationSnapshotHolding,
    STABLECOINS,
    dec,
    json_safe,
)
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
    ALLOCATION_BATCH_STATUS_PLANNED,
    ALLOCATION_BATCH_STATUS_SNAPSHOT_CREATED,
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE,
    EXECUTION_MODE_PLANNED,
    HOLDING_GROUP_CASH,
    HOLDING_GROUP_EARN,
    HOLDING_GROUP_FUNDING_WALLET,
    HOLDING_GROUP_FUTURE,
    HOLDING_GROUP_LONG_OPTION,
    HOLDING_GROUP_OTHER,
    HOLDING_GROUP_PERP,
    HOLDING_GROUP_SHORT_OPTION,
    HOLDING_GROUP_SPOT,
    LEG_GROUP_CASH,
    LEG_GROUP_DERIVATIVE,
    LEG_GROUP_EARN,
    LEG_GROUP_OPTION,
    LEG_GROUP_OTHER,
    LEG_GROUP_SPOT,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_FUTURE_INCREASE,
    LEG_TYPE_LONG_OPTION_INCREASE,
    LEG_TYPE_OTHER,
    LEG_TYPE_PERP_INCREASE,
    LEG_TYPE_SHORT_OPTION_INCREASE,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.models import (
    Fund,
    FundAllocationBatch,
    FundAllocationLeg,
    FundOrder,
    FundSettlementBatch,
    UserFundPosition,
)


ZERO = Decimal("0")
ONE = Decimal("1")


class AllocationPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class AllocationPlanSummary:
    allocation_batch_id: int
    settlement_batch_id: int
    fund_id: int
    fund_code: str
    positive_net_usdt: Decimal
    settlement_nav_usdt: Decimal | None
    snapshot_total_equity_usdt: Decimal
    base_nav_for_scale_usdt: Decimal
    scale: Decimal
    legs_count: int
    legs_by_group: dict[str, int]
    status: str
    warnings: list[str]


@dataclass(frozen=True)
class PlannedAllocationLeg:
    leg_index: int
    leg_key: str
    leg_group: str
    leg_type: str
    coin: str | None
    symbol: str | None
    category: str | None
    side: str | None
    location: str | None
    current_size: Decimal | None
    current_usd_value: Decimal | None
    current_notional_usd: Decimal | None
    source_weight: Decimal | None
    target_usdt: Decimal | None
    target_qty: Decimal | None
    status: str
    error: str | None


MUTABLE_BATCH_STATUSES = {
    ALLOCATION_BATCH_STATUS_PLANNED,
    ALLOCATION_BATCH_STATUS_SNAPSHOT_CREATED,
    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
    ALLOCATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
}

MUTABLE_LEG_STATUSES = {
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE,
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_stablecoin(coin: str | None) -> bool:
    return (coin or "").upper() in STABLECOINS


def _positive(value: Decimal | None) -> Decimal:
    x = dec(value)
    return x if x > ZERO else ZERO


def _source_value_for_holding(holding: AllocationSnapshotHolding) -> Decimal:
    if holding.leg_group in {
        HOLDING_GROUP_PERP,
        HOLDING_GROUP_FUTURE,
        HOLDING_GROUP_LONG_OPTION,
        HOLDING_GROUP_SHORT_OPTION,
    }:
        return _positive(holding.notional_usd)

    return _positive(holding.usd_value)


def _target_qty_from_size(holding: AllocationSnapshotHolding, scale: Decimal) -> Decimal | None:
    size = holding.size
    if size is None:
        return None

    return abs(dec(size)) * scale


def _safe_component(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "_"

    allowed = []
    for ch in text:
        if ch.isalnum() or ch in {"_", "-", "."}:
            allowed.append(ch)
        else:
            allowed.append("_")

    return "".join(allowed)[:48] or "_"


def make_leg_key(
    *,
    leg_group: str,
    leg_type: str,
    location: str | None,
    category: str | None,
    symbol: str | None,
    coin: str | None,
    side: str | None,
    index_source: int,
) -> str:
    raw = ":".join(
        [
            _safe_component(leg_group),
            _safe_component(leg_type),
            _safe_component(location),
            _safe_component(category),
            _safe_component(symbol),
            _safe_component(coin),
            _safe_component(side),
            str(index_source),
        ]
    )

    if len(raw) <= 160:
        return raw

    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{raw[:143]}:{digest}"


def _get_settlement_batch(db: Session, *, batch_id: int) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise AllocationPlanError(f"Settlement batch not found: {batch_id}")

    return batch


def _get_fund_code(db: Session, *, fund_id: int) -> str:
    fund = db.query(Fund).filter(Fund.id == fund_id).first()
    if fund is None:
        return str(fund_id)

    return fund.code


def _base_nav_for_scale(
    *,
    batch: FundSettlementBatch,
    snapshot: AllocationSnapshot,
) -> tuple[Decimal, list[str]]:
    warnings: list[str] = []

    positive_net_usdt = dec(batch.net_cash_usdt)
    settlement_nav_usdt = dec(batch.nav_usdt)
    snapshot_total_equity = dec(snapshot.total_equity_usdt)

    if settlement_nav_usdt > ZERO:
        base_nav = settlement_nav_usdt
    else:
        base_nav = snapshot_total_equity - positive_net_usdt
        warnings.append(
            "settlement_nav_usdt is missing or <= 0; fallback base_nav_for_scale_usdt "
            "uses snapshot_total_equity_usdt - positive_net_usdt."
        )

    if base_nav <= ZERO:
        raise AllocationPlanError(
            "base_nav_for_scale_usdt must be positive. "
            f"settlement_nav_usdt={settlement_nav_usdt}, "
            f"snapshot_total_equity_usdt={snapshot_total_equity}, "
            f"positive_net_usdt={positive_net_usdt}"
        )

    if settlement_nav_usdt > ZERO:
        base_from_snapshot = snapshot_total_equity - positive_net_usdt
        diff_abs = abs(base_from_snapshot - settlement_nav_usdt)
        material_threshold = max(Decimal("10"), settlement_nav_usdt * Decimal("0.01"))

        if diff_abs > material_threshold:
            warnings.append(
                "snapshot_total_equity_usdt - positive_net_usdt differs materially "
                "from settlement_nav_usdt. "
                f"snapshot_base={base_from_snapshot}, "
                f"settlement_nav_usdt={settlement_nav_usdt}, "
                f"diff_abs={diff_abs}, "
                f"threshold={material_threshold}"
            )

    return base_nav, warnings


def _adjusted_source_values(
    *,
    snapshot: AllocationSnapshot,
    positive_net_usdt: Decimal,
) -> tuple[dict[int, Decimal], Decimal, Decimal]:
    """
    If post-deposit USDT cash includes positive_net_usdt, remove that inflow
    from source values before source_weight/target calculation.

    Deduction order:
    1. USDT cash in UNIFIED;
    2. other stable cash;
    3. stable funding wallet cash.

    This keeps Stage 22.2 from allocating the new inflow to itself.
    """
    out: dict[int, Decimal] = {}
    remaining_inflow = _positive(positive_net_usdt)
    raw_cash_usdt = ZERO
    adjusted_cash_usdt = ZERO

    cash_indexes: list[int] = []
    other_indexes: list[int] = []

    for idx, holding in enumerate(snapshot.holdings, start=1):
        source_value = _source_value_for_holding(holding)
        out[idx] = source_value

        if holding.leg_group == HOLDING_GROUP_CASH and _is_stablecoin(holding.coin):
            cash_indexes.append(idx)
            raw_cash_usdt += source_value
        elif holding.leg_group == HOLDING_GROUP_FUNDING_WALLET and _is_stablecoin(holding.coin):
            cash_indexes.append(idx)
            raw_cash_usdt += source_value
        else:
            other_indexes.append(idx)

    # Deduct positive net from USDT cash first.
    cash_indexes.sort(
        key=lambda i: (
            0 if (snapshot.holdings[i - 1].coin or "").upper() == "USDT" else 1,
            0 if snapshot.holdings[i - 1].leg_group == HOLDING_GROUP_CASH else 1,
            i,
        )
    )

    for idx in cash_indexes:
        value = out[idx]
        if remaining_inflow > ZERO and value > ZERO:
            deduction = min(value, remaining_inflow)
            value = value - deduction
            remaining_inflow -= deduction

        out[idx] = value
        adjusted_cash_usdt += value

    return out, raw_cash_usdt, adjusted_cash_usdt


def _planned_leg_from_holding(
    *,
    holding: AllocationSnapshotHolding,
    source_value: Decimal,
    scale: Decimal,
    base_nav_for_scale_usdt: Decimal,
    leg_index: int,
    index_source: int,
) -> PlannedAllocationLeg:
    leg_group = LEG_GROUP_OTHER
    leg_type = LEG_TYPE_OTHER
    target_usdt: Decimal | None = source_value * scale
    target_qty: Decimal | None = None
    error: str | None = None

    coin = holding.coin
    symbol = holding.symbol
    category = holding.category
    side = holding.side
    location = holding.location

    if source_value <= ZERO:
        status = ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE
        target_usdt = ZERO
        target_qty = ZERO if holding.size is not None else None
    else:
        status = ALLOCATION_LEG_STATUS_PLANNED

    if holding.leg_group in {HOLDING_GROUP_CASH, HOLDING_GROUP_FUNDING_WALLET} and _is_stablecoin(coin):
        leg_group = LEG_GROUP_CASH
        leg_type = LEG_TYPE_STABLE_CASH
        target_qty = target_usdt

    elif holding.leg_group == HOLDING_GROUP_SPOT:
        leg_group = LEG_GROUP_SPOT
        leg_type = LEG_TYPE_SPOT_BUY
        target_qty = _target_qty_from_size(holding, scale)

    elif holding.leg_group == HOLDING_GROUP_EARN:
        leg_group = LEG_GROUP_EARN
        if _is_stablecoin(coin):
            leg_type = LEG_TYPE_USDT_EARN_STAKE
            target_qty = target_usdt
        else:
            leg_type = LEG_TYPE_BUY_THEN_STAKE
            target_qty = _target_qty_from_size(holding, scale)

    elif holding.leg_group == HOLDING_GROUP_PERP:
        leg_group = LEG_GROUP_DERIVATIVE
        leg_type = LEG_TYPE_PERP_INCREASE
        target_qty = _target_qty_from_size(holding, scale)

    elif holding.leg_group == HOLDING_GROUP_FUTURE:
        leg_group = LEG_GROUP_DERIVATIVE
        leg_type = LEG_TYPE_FUTURE_INCREASE
        target_qty = _target_qty_from_size(holding, scale)

    elif holding.leg_group == HOLDING_GROUP_LONG_OPTION:
        leg_group = LEG_GROUP_OPTION
        leg_type = LEG_TYPE_LONG_OPTION_INCREASE
        target_qty = _target_qty_from_size(holding, scale)

    elif holding.leg_group == HOLDING_GROUP_SHORT_OPTION:
        leg_group = LEG_GROUP_OPTION
        leg_type = LEG_TYPE_SHORT_OPTION_INCREASE
        target_qty = _target_qty_from_size(holding, scale)

    elif holding.leg_group == HOLDING_GROUP_OTHER:
        leg_group = LEG_GROUP_OTHER
        leg_type = LEG_TYPE_OTHER
        error = "Unsupported holding group; planned as other/no execution in Stage 22.2."

    source_weight = None
    if base_nav_for_scale_usdt > ZERO and source_value > ZERO:
        source_weight = source_value / base_nav_for_scale_usdt

    leg_key = make_leg_key(
        leg_group=leg_group,
        leg_type=leg_type,
        location=location,
        category=category,
        symbol=symbol,
        coin=coin,
        side=side,
        index_source=index_source,
    )

    return PlannedAllocationLeg(
        leg_index=leg_index,
        leg_key=leg_key,
        leg_group=leg_group,
        leg_type=leg_type,
        coin=coin,
        symbol=symbol,
        category=category,
        side=side,
        location=location,
        current_size=holding.size,
        current_usd_value=holding.usd_value,
        current_notional_usd=holding.notional_usd,
        source_weight=source_weight,
        target_usdt=target_usdt,
        target_qty=target_qty,
        status=status,
        error=error,
    )


def _build_planned_legs(
    *,
    snapshot: AllocationSnapshot,
    positive_net_usdt: Decimal,
    scale: Decimal,
    base_nav_for_scale_usdt: Decimal,
) -> tuple[list[PlannedAllocationLeg], Decimal, Decimal]:
    source_values, raw_cash_usdt, adjusted_cash_usdt = _adjusted_source_values(
        snapshot=snapshot,
        positive_net_usdt=positive_net_usdt,
    )

    legs: list[PlannedAllocationLeg] = []
    leg_index = 1

    for source_index, holding in enumerate(snapshot.holdings, start=1):
        source_value = source_values[source_index]

        leg = _planned_leg_from_holding(
            holding=holding,
            source_value=source_value,
            scale=scale,
            base_nav_for_scale_usdt=base_nav_for_scale_usdt,
            leg_index=leg_index,
            index_source=source_index,
        )
        legs.append(leg)
        leg_index += 1

    return legs, raw_cash_usdt, adjusted_cash_usdt


def _get_or_create_allocation_batch(
    db: Session,
    *,
    settlement_batch: FundSettlementBatch,
) -> FundAllocationBatch:
    allocation_batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.settlement_batch_id == settlement_batch.id)
        .with_for_update()
        .first()
    )

    if allocation_batch is not None:
        if allocation_batch.status not in MUTABLE_BATCH_STATUSES:
            raise AllocationPlanError(
                f"Allocation batch {allocation_batch.id} has non-mutable status: "
                f"{allocation_batch.status}"
            )
        return allocation_batch

    allocation_batch = FundAllocationBatch(
        settlement_batch_id=settlement_batch.id,
        fund_id=settlement_batch.fund_id,
        positive_net_usdt=dec(settlement_batch.net_cash_usdt),
        settlement_nav_usdt=settlement_batch.nav_usdt,
        status=ALLOCATION_BATCH_STATUS_PLANNED,
    )
    db.add(allocation_batch)
    db.flush()

    return allocation_batch


def _upsert_leg(
    db: Session,
    *,
    allocation_batch: FundAllocationBatch,
    settlement_batch: FundSettlementBatch,
    planned_leg: PlannedAllocationLeg,
) -> FundAllocationLeg:
    existing = (
        db.query(FundAllocationLeg)
        .filter(
            FundAllocationLeg.allocation_batch_id == allocation_batch.id,
            FundAllocationLeg.leg_key == planned_leg.leg_key,
        )
        .with_for_update()
        .first()
    )

    if existing is not None:
        if existing.status not in MUTABLE_LEG_STATUSES:
            return existing

        row = existing
    else:
        row = FundAllocationLeg(
            allocation_batch_id=allocation_batch.id,
            settlement_batch_id=settlement_batch.id,
            fund_id=settlement_batch.fund_id,
            leg_key=planned_leg.leg_key,
            leg_index=planned_leg.leg_index,
        )

    row.leg_index = planned_leg.leg_index
    row.leg_group = planned_leg.leg_group
    row.leg_type = planned_leg.leg_type
    row.coin = planned_leg.coin
    row.symbol = planned_leg.symbol
    row.category = planned_leg.category
    row.side = planned_leg.side
    row.location = planned_leg.location

    row.current_size = planned_leg.current_size
    row.current_usd_value = planned_leg.current_usd_value
    row.current_notional_usd = planned_leg.current_notional_usd
    row.source_weight = planned_leg.source_weight

    row.target_usdt = planned_leg.target_usdt
    row.target_qty = planned_leg.target_qty

    row.execution_mode = EXECUTION_MODE_PLANNED
    row.status = planned_leg.status
    row.error = planned_leg.error
    row.updated_at = utcnow()

    db.add(row)
    db.flush()

    return row


def _summarize_legs(legs: list[PlannedAllocationLeg]) -> dict[str, int]:
    out: dict[str, int] = {}
    for leg in legs:
        out[leg.leg_group] = out.get(leg.leg_group, 0) + 1
    return out


def build_allocation_plan_for_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
    snapshot: AllocationSnapshot,
) -> AllocationPlanSummary:
    """
    Stage 22.2 allocation plan builder.

    Writes only:
    - fund_allocation_batches
    - fund_allocation_legs

    Does not:
    - place Bybit orders;
    - stake Earn;
    - do on-chain transfers;
    - change fund_orders;
    - change user_fund_positions;
    - change funds.shares_outstanding_current.
    """
    now = utcnow()

    settlement_batch = _get_settlement_batch(db, batch_id=settlement_batch_id)
    fund_code = _get_fund_code(db, fund_id=settlement_batch.fund_id)

    if snapshot.fund_id != settlement_batch.fund_id:
        raise AllocationPlanError(
            f"Snapshot fund_id={snapshot.fund_id} does not match "
            f"settlement batch fund_id={settlement_batch.fund_id}"
        )

    positive_net_usdt = dec(settlement_batch.net_cash_usdt)

    allocation_batch = _get_or_create_allocation_batch(
        db,
        settlement_batch=settlement_batch,
    )

    try:
        if positive_net_usdt <= ZERO:
            raise AllocationPlanError(
                f"positive_net_usdt must be > 0 for allocation plan, got {positive_net_usdt}"
            )

        base_nav_for_scale_usdt, warnings = _base_nav_for_scale(
            batch=settlement_batch,
            snapshot=snapshot,
        )

        scale = positive_net_usdt / base_nav_for_scale_usdt

        planned_legs, raw_cash_usdt, adjusted_cash_usdt = _build_planned_legs(
            snapshot=snapshot,
            positive_net_usdt=positive_net_usdt,
            scale=scale,
            base_nav_for_scale_usdt=base_nav_for_scale_usdt,
        )

        snapshot_json = snapshot.to_dict()
        snapshot_json["allocation_adjustments"] = {
            "positive_net_usdt": str(positive_net_usdt),
            "settlement_nav_usdt": str(dec(settlement_batch.nav_usdt)),
            "snapshot_total_equity_usdt": str(snapshot.total_equity_usdt),
            "base_nav_for_scale_usdt": str(base_nav_for_scale_usdt),
            "scale": str(scale),
            "raw_cash_usdt": str(raw_cash_usdt),
            "adjusted_cash_usdt": str(adjusted_cash_usdt),
            "warnings": warnings,
        }

        allocation_batch.snapshot_ts = snapshot.snapshot_ts
        allocation_batch.positive_net_usdt = positive_net_usdt
        allocation_batch.settlement_nav_usdt = settlement_batch.nav_usdt
        allocation_batch.snapshot_total_equity_usdt = snapshot.total_equity_usdt
        allocation_batch.base_nav_for_scale_usdt = base_nav_for_scale_usdt
        allocation_batch.scale = scale
        allocation_batch.snapshot_source = snapshot.snapshot_source
        allocation_batch.snapshot_json = json_safe(snapshot_json)
        allocation_batch.status = ALLOCATION_BATCH_STATUS_SNAPSHOT_CREATED
        allocation_batch.error = "\n".join(warnings) if warnings else None
        allocation_batch.updated_at = now

        db.add(allocation_batch)
        db.flush()

        for planned_leg in planned_legs:
            _upsert_leg(
                db,
                allocation_batch=allocation_batch,
                settlement_batch=settlement_batch,
                planned_leg=planned_leg,
            )

        allocation_batch.status = ALLOCATION_BATCH_STATUS_PLAN_CREATED
        allocation_batch.updated_at = utcnow()
        db.add(allocation_batch)
        db.flush()

        return AllocationPlanSummary(
            allocation_batch_id=allocation_batch.id,
            settlement_batch_id=settlement_batch.id,
            fund_id=settlement_batch.fund_id,
            fund_code=fund_code,
            positive_net_usdt=positive_net_usdt,
            settlement_nav_usdt=settlement_batch.nav_usdt,
            snapshot_total_equity_usdt=snapshot.total_equity_usdt,
            base_nav_for_scale_usdt=base_nav_for_scale_usdt,
            scale=scale,
            legs_count=len(planned_legs),
            legs_by_group=_summarize_legs(planned_legs),
            status=allocation_batch.status,
            warnings=warnings,
        )

    except Exception as exc:
        allocation_batch.status = ALLOCATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
        allocation_batch.error = str(exc)
        allocation_batch.updated_at = utcnow()
        db.add(allocation_batch)
        db.flush()
        raise


def count_existing_state_for_safety(db: Session, *, fund_id: int) -> dict[str, Any]:
    """
    Helper for rollback tests: captures state that Stage 22.2 must not mutate.
    """
    fund = db.query(Fund).filter(Fund.id == fund_id).first()

    return {
        "fund_shares_outstanding_current": str(fund.shares_outstanding_current) if fund else None,
        "fund_orders_count": db.query(FundOrder).filter(FundOrder.fund_id == fund_id).count(),
        "user_fund_positions_count": (
            db.query(UserFundPosition)
            .filter(UserFundPosition.fund_id == fund_id)
            .count()
        ),
    }