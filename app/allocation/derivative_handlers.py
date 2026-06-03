from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.execution_engine import prepare_execution_for_leg
from app.allocation.execution_config import get_allocation_execution_config
from app.allocation.liquidity import (
    check_liquidity_corridor,
    get_last_price,
    get_orderbook,
)
from app.allocation.margin_guard import (
    MarginGuardResult,
    check_margin_guard,
    estimate_margin_impact_for_leg,
    get_account_risk_snapshot,
)
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
    DERIVATIVE_SUPPORTED_LEG_TYPES,
    EXECUTION_MODE_SKIPPED,
    LEG_TYPE_FUTURE_INCREASE,
    LEG_TYPE_LONG_OPTION_INCREASE,
    LEG_TYPE_PERP_INCREASE,
    LEG_TYPE_SHORT_OPTION_INCREASE,
)
from app.config import settings
from app.models import FundAllocationLeg


ZERO = Decimal("0")


class DerivativeHandlerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DerivativeHandlerDecision:
    allocation_leg_id: int
    allocation_batch_id: int
    status: str
    execution_mode: str
    action: str
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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _target_usdt(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_usdt)


def _target_qty(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_qty)


def _is_short_option(leg: FundAllocationLeg) -> bool:
    return leg.leg_type == LEG_TYPE_SHORT_OPTION_INCREASE


def _is_long_option(leg: FundAllocationLeg) -> bool:
    return leg.leg_type == LEG_TYPE_LONG_OPTION_INCREASE


def _is_option(leg: FundAllocationLeg) -> bool:
    return leg.leg_type in {
        LEG_TYPE_LONG_OPTION_INCREASE,
        LEG_TYPE_SHORT_OPTION_INCREASE,
    }


def _is_perp_or_future(leg: FundAllocationLeg) -> bool:
    return leg.leg_type in {
        LEG_TYPE_PERP_INCREASE,
        LEG_TYPE_FUTURE_INCREASE,
    }


def _decision(
    leg: FundAllocationLeg,
    *,
    action: str,
    reason: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> DerivativeHandlerDecision:
    return DerivativeHandlerDecision(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        status=leg.status,
        execution_mode=leg.execution_mode,
        action=action,
        reason=reason,
        diagnostics=diagnostics or {},
    )


def _get_leg_for_update(db: Session, *, allocation_leg_id: int) -> FundAllocationLeg:
    leg = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.id == allocation_leg_id)
        .with_for_update()
        .first()
    )

    if leg is None:
        raise DerivativeHandlerError(f"Allocation leg not found: {allocation_leg_id}")

    return leg


def _infer_category(leg: FundAllocationLeg) -> str:
    raw = _normalize_lower(leg.category)

    if _is_option(leg):
        return "option"

    if raw in {"linear", "inverse"}:
        return raw

    symbol = _normalize_text(leg.symbol).upper()

    if symbol.endswith("USDT") or symbol.endswith("USDC"):
        return "linear"

    if symbol:
        return "inverse"

    return raw


def _infer_side(leg: FundAllocationLeg) -> str:
    raw = _normalize_lower(leg.side)

    if raw in {"buy", "long"}:
        return "Buy"

    if raw in {"sell", "short"}:
        return "Sell"

    if _is_long_option(leg):
        return "Buy"

    if _is_short_option(leg):
        return "Sell"

    # For perp/future increase, Stage 22.2 should normally preserve existing side.
    # If side is missing in a mock leg, default to Buy rather than failing the batch.
    return "Buy"


def _normalize_leg_for_execution(leg: FundAllocationLeg) -> None:
    leg.category = _infer_category(leg)
    leg.side = _infer_side(leg)

    if _is_option(leg):
        leg.leg_group = "option"

    if _is_perp_or_future(leg):
        leg.leg_group = "derivative"


def _basic_symbol_skip(
    db: Session,
    leg: FundAllocationLeg,
    *,
    reason: str,
) -> DerivativeHandlerDecision:
    leg.status = ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING
    leg.execution_mode = EXECUTION_MODE_SKIPPED
    leg.residual_usdt = _target_usdt(leg)
    leg.actual_margin_change_usdt = ZERO
    leg.error = reason
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return _decision(
        leg,
        action="skip_symbol_validation",
        reason=reason,
        diagnostics={
            "residual_usdt": str(leg.residual_usdt),
        },
    )


def _guard_diagnostics(guard: MarginGuardResult) -> dict[str, Any]:
    return {
        "reason": guard.reason,
        "account_im_rate": str(guard.account_im_rate),
        "account_mm_rate": str(guard.account_mm_rate),
        "post_im_rate": str(guard.post_im_rate),
        "post_mm_rate": str(guard.post_mm_rate),
        "max_im_rate": str(guard.max_im_rate),
        "max_mm_rate": str(guard.max_mm_rate),
        "residual_usdt": str(guard.residual_usdt),
        "impact": {
            "notional_usdt": str(guard.impact.notional_usdt),
            "estimated_initial_margin_usdt": str(
                guard.impact.estimated_initial_margin_usdt
            ),
            "estimated_maintenance_margin_usdt": str(
                guard.impact.estimated_maintenance_margin_usdt
            ),
            "uncertain": guard.impact.uncertain,
            "model": guard.impact.diagnostics.get("model"),
        },
        "account_source": guard.account_risk.source,
        "account_valid": guard.account_risk.is_valid,
    }


def _margin_guard_status(guard: MarginGuardResult) -> str:
    if guard.ok:
        return "passed"

    if guard.impact.uncertain or not guard.account_risk.is_valid:
        return "uncertain"

    return "failed"


def _apply_margin_guard_audit_to_leg(
    leg: FundAllocationLeg,
    *,
    guard: MarginGuardResult,
    status_override: str | None = None,
    error_override: str | None = None,
) -> None:
    leg.account_im_rate_before = guard.account_im_rate
    leg.account_mm_rate_before = guard.account_mm_rate
    leg.account_im_rate_after_est = guard.post_im_rate
    leg.account_mm_rate_after_est = guard.post_mm_rate

    leg.total_equity_usdt_before = guard.account_risk.total_equity_usdt
    leg.total_initial_margin_usdt_before = guard.account_risk.total_initial_margin_usdt
    leg.total_maintenance_margin_usdt_before = guard.account_risk.total_maintenance_margin_usdt

    leg.estimated_initial_margin_change_usdt = guard.impact.estimated_initial_margin_usdt
    leg.estimated_maintenance_margin_change_usdt = guard.impact.estimated_maintenance_margin_usdt

    leg.margin_guard_status = status_override or _margin_guard_status(guard)
    leg.margin_guard_error = error_override if error_override is not None else guard.reason


def _mark_guard_skip(
    db: Session,
    leg: FundAllocationLeg,
    *,
    guard: MarginGuardResult,
) -> DerivativeHandlerDecision:
    _apply_margin_guard_audit_to_leg(
        leg,
        guard=guard,
    )

    leg.status = guard.status
    leg.execution_mode = EXECUTION_MODE_SKIPPED
    leg.residual_usdt = guard.residual_usdt if guard.residual_usdt > ZERO else _target_usdt(leg)
    leg.actual_margin_change_usdt = ZERO
    leg.error = guard.reason
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    action = (
        "skip_min_order"
        if guard.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER
        else "skip_margin_guard"
    )

    return _decision(
        leg,
        action=action,
        reason=guard.reason,
        diagnostics=_guard_diagnostics(guard),
    )


def _apply_liquidity_diagnostics_to_leg(
    leg: FundAllocationLeg,
    *,
    liquidity: Any,
) -> None:
    leg.last_price = liquidity.last_price
    leg.best_bid = liquidity.best_bid
    leg.best_ask = liquidity.best_ask
    leg.corridor_pct = liquidity.corridor_pct
    leg.available_liquidity_qty = liquidity.available_liquidity_qty
    leg.available_liquidity_usdt = liquidity.available_liquidity_usdt
    leg.required_qty = liquidity.required_qty
    leg.required_usdt = liquidity.required_usdt


def _mark_short_option_liquidity_skip(
    db: Session,
    leg: FundAllocationLeg,
    *,
    reason: str,
    diagnostics: dict[str, Any] | None = None,
) -> DerivativeHandlerDecision:
    leg.status = ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD
    leg.execution_mode = EXECUTION_MODE_SKIPPED
    leg.residual_usdt = _target_usdt(leg)
    leg.filled_qty = None
    leg.filled_usdt = None
    leg.avg_fill_price = None
    leg.fill_ratio = None
    leg.fee_usdt = None
    leg.actual_cash_used_usdt = None
    leg.actual_margin_change_usdt = ZERO
    leg.margin_guard_status = "short_option_liquidity_failed"
    leg.margin_guard_error = reason
    leg.error = reason
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return _decision(
        leg,
        action="skip_short_option_liquidity_guard",
        reason=reason,
        diagnostics=diagnostics or {},
    )


def _check_short_option_strict_liquidity(
    db: Session,
    leg: FundAllocationLeg,
    *,
    client: Any,
    category: str,
    symbol: str,
    last_price: Decimal,
) -> DerivativeHandlerDecision | None:
    if not _is_short_option(leg):
        return None

    try:
        config = get_allocation_execution_config()

        orderbook = get_orderbook(
            client,
            category=category,
            symbol=symbol,
        )

        liquidity = check_liquidity_corridor(
            side="Sell",
            target_qty=_target_qty(leg),
            target_usdt=_target_usdt(leg),
            last_price=last_price,
            orderbook=orderbook,
            corridor_pct=config.liquidity_corridor_pct,
            liquidity_multiplier=settings.ALLOCATION_SHORT_OPTION_LIQUIDITY_MULT,
        )

        _apply_liquidity_diagnostics_to_leg(
            leg,
            liquidity=liquidity,
        )

        if not liquidity.ok:
            return _mark_short_option_liquidity_skip(
                db,
                leg,
                reason=liquidity.error or "Short option strict liquidity guard failed",
                diagnostics={
                    "last_price": str(liquidity.last_price),
                    "best_bid": str(liquidity.best_bid) if liquidity.best_bid is not None else None,
                    "best_ask": str(liquidity.best_ask) if liquidity.best_ask is not None else None,
                    "available_liquidity_qty": str(liquidity.available_liquidity_qty),
                    "required_qty": str(liquidity.required_qty),
                    "liquidity_multiplier": str(liquidity.liquidity_multiplier),
                    "corridor_pct": str(liquidity.corridor_pct),
                },
            )

        db.add(leg)
        db.flush()
        return None

    except Exception as exc:
        return _mark_short_option_liquidity_skip(
            db,
            leg,
            reason=f"Short option strict liquidity guard is uncertain: {exc}",
            diagnostics={
                "category": category,
                "symbol": symbol,
                "last_price": str(last_price),
            },
        )


def _estimate_last_price_for_margin(
    client: Any,
    *,
    category: str,
    symbol: str | None,
) -> Decimal:
    if not symbol:
        return ZERO

    try:
        return get_last_price(
            client,
            category=category,
            symbol=symbol,
        )
    except Exception:
        return ZERO


def _apply_margin_after_execution(
    leg: FundAllocationLeg,
    *,
    guard: MarginGuardResult,
) -> None:
    if leg.status not in {
        ALLOCATION_LEG_STATUS_FILLED,
        ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
        ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
        ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    }:
        return

    fill_ratio = dec(leg.fill_ratio)
    if fill_ratio <= ZERO:
        fill_ratio = Decimal("1")

    if fill_ratio > Decimal("1"):
        fill_ratio = Decimal("1")

    leg.actual_margin_change_usdt = (
        guard.impact.estimated_initial_margin_usdt * fill_ratio
    )

    leg.updated_at = utcnow()


def _engine_decision_diagnostics(
    *,
    engine_action: str,
    engine_status: str,
    engine_mode: str,
    guard: MarginGuardResult,
) -> dict[str, Any]:
    return {
        "engine_action": engine_action,
        "engine_status": engine_status,
        "engine_execution_mode": engine_mode,
        "margin_guard": _guard_diagnostics(guard),
    }


def handle_derivative_leg_mock(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> DerivativeHandlerDecision:
    """
    Stage 22.5 derivative/options handler.

    Allowed:
    - mocked account risk read;
    - mocked market data read;
    - margin guard;
    - Stage 22.3 execution_engine mock path;
    - fund_allocation_legs status/diagnostic updates.

    Forbidden:
    - real Bybit orders;
    - real Strategy orders;
    - real options/perps/futures orders;
    - transfers;
    - Earn stake;
    - settlement accounting finalization.
    """
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type not in DERIVATIVE_SUPPORTED_LEG_TYPES:
        raise DerivativeHandlerError(
            f"Unsupported derivative leg_type: leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status != ALLOCATION_LEG_STATUS_PLANNED:
        return _decision(
            leg,
            action="skip_non_planned_status",
            reason=f"Leg status is not planned: {leg.status}",
        )

    if not _normalize_text(leg.symbol):
        return _basic_symbol_skip(
            db,
            leg,
            reason="Derivative/option leg symbol is required",
        )

    _normalize_leg_for_execution(leg)
    db.add(leg)
    db.flush()

    category = _normalize_lower(leg.category)
    symbol = _normalize_text(leg.symbol).upper()

    account_risk = get_account_risk_snapshot(client)
    last_price = _estimate_last_price_for_margin(
        client,
        category=category,
        symbol=symbol,
    )

    impact = estimate_margin_impact_for_leg(
        leg,
        account_risk,
        mock_market_data={
            "last_price": last_price,
        },
    )

    guard = check_margin_guard(
        account_risk=account_risk,
        impact=impact,
        max_im_rate=settings.ALLOCATION_MAX_IM_RATE,
        max_mm_rate=settings.ALLOCATION_MAX_MM_RATE,
        is_short_option=_is_short_option(leg),
    )

    if not guard.ok:
        return _mark_guard_skip(
            db,
            leg,
            guard=guard,
        )

    _apply_margin_guard_audit_to_leg(
        leg,
        guard=guard,
    )
    db.add(leg)
    db.flush()

    short_option_liquidity_decision = _check_short_option_strict_liquidity(
        db,
        leg,
        client=client,
        category=category,
        symbol=symbol,
        last_price=last_price,
    )

    if short_option_liquidity_decision is not None:
        return short_option_liquidity_decision

    engine_decision = prepare_execution_for_leg(
        db,
        allocation_leg_id=allocation_leg_id,
        client=client,
        mock_mode=True,
    )

    db.refresh(leg)

    # For normal validation/liquidity skips, execution_engine already sets residual_usdt.
    # We only add margin diagnostics when the mock engine produced an execution state.
    _apply_margin_after_execution(
        leg,
        guard=guard,
    )

    db.add(leg)
    db.flush()

    return _decision(
        leg,
        action="derivative_engine_mock_execution",
        reason=engine_decision.reason,
        diagnostics=_engine_decision_diagnostics(
            engine_action=engine_decision.action,
            engine_status=engine_decision.status,
            engine_mode=engine_decision.execution_mode,
            guard=guard,
        ),
    )