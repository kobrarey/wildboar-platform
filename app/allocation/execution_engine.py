from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.bybit_orders import (
    MockExecutionResult,
    build_protected_market_order_payload,
    build_sliced_ioc_payloads,
    simulate_protected_market_order_execution,
    simulate_sliced_ioc_execution,
)
from app.allocation.bybit_strategy import (
    build_native_iceberg_strategy_payload,
    simulate_native_iceberg_strategy_create,
)
from app.allocation.execution_config import (
    AllocationExecutionConfig,
    get_allocation_execution_config,
)
from app.allocation.idempotency import (
    make_market_order_link_id,
    make_strategy_client_ref,
)
from app.allocation.instrument_info import (
    InstrumentInfo,
    InstrumentValidationResult,
    get_instrument_info,
    validate_instrument_for_leg,
)
from app.allocation.liquidity import (
    LiquidityCheckResult,
    check_liquidity_corridor,
    get_last_price,
    get_orderbook,
)
from app.allocation.statuses import (
    ACTIVE_ALLOCATION_LEG_STATUSES,
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING,
    EXECUTION_MODE_MARKET,
    EXECUTION_MODE_NATIVE_ICEBERG,
    EXECUTION_MODE_PLANNED,
    EXECUTION_MODE_SLICED_IOC_FALLBACK,
    EXECUTION_MODE_SKIPPED,
)
from app.models import FundAllocationLeg


ZERO = Decimal("0")


class AllocationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionDecision:
    allocation_leg_id: int
    allocation_batch_id: int
    status: str
    execution_mode: str
    action: str
    reason: str | None
    payload: dict[str, Any] | None
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


def _leg_category(leg: FundAllocationLeg) -> str:
    raw = _normalize_text(leg.category).lower()

    if raw:
        return raw

    if leg.leg_group == "spot":
        return "spot"

    if leg.leg_group == "option":
        return "option"

    if leg.leg_group == "derivative":
        symbol = _normalize_text(leg.symbol).upper()
        if symbol.endswith("USDT") or symbol.endswith("USDC"):
            return "linear"
        if symbol:
            return "inverse"

    return raw


def _leg_side(leg: FundAllocationLeg) -> str:
    raw = _normalize_text(leg.side).lower()

    if raw in {"buy", "long"}:
        return "Buy"

    if raw in {"sell", "short"}:
        return "Sell"

    if leg.leg_type in {
        "spot_buy",
        "buy_then_stake",
        "perp_increase",
        "future_increase",
        "long_option_increase",
    }:
        return "Buy"

    if leg.leg_type == "short_option_increase":
        return "Sell"

    return "Buy"


def _target_qty_for_liquidity(
    *,
    leg: FundAllocationLeg,
    last_price: Decimal,
) -> Decimal:
    target_qty = dec(leg.target_qty)

    if target_qty > ZERO:
        return target_qty

    target_usdt = dec(leg.target_usdt)
    last_price_dec = dec(last_price)

    if target_usdt > ZERO and last_price_dec > ZERO:
        return target_usdt / last_price_dec

    return ZERO


def _target_usdt(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_usdt)


def _liquidity_multiplier(
    *,
    leg: FundAllocationLeg,
    config: AllocationExecutionConfig,
) -> Decimal:
    if leg.leg_type == "short_option_increase":
        return config.short_option_liquidity_mult

    return Decimal("1.0")


def _mark_failed_requires_review(
    leg: FundAllocationLeg,
    *,
    error: str,
) -> None:
    leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    leg.execution_mode = EXECUTION_MODE_SKIPPED
    leg.error = error
    leg.residual_usdt = dec(leg.target_usdt)
    leg.updated_at = utcnow()


def _apply_validation_skip(
    leg: FundAllocationLeg,
    *,
    validation: InstrumentValidationResult,
) -> ExecutionDecision:
    leg.status = validation.status
    leg.execution_mode = validation.execution_mode
    leg.residual_usdt = dec(leg.target_usdt)
    leg.error = validation.error
    leg.required_qty = validation.rounded_qty
    leg.required_usdt = dec(leg.target_usdt)
    leg.updated_at = utcnow()

    return ExecutionDecision(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        status=leg.status,
        execution_mode=leg.execution_mode,
        action="skip_validation",
        reason=validation.error,
        payload=None,
        diagnostics={
            "validation_ok": validation.ok,
            "rounded_qty": str(validation.rounded_qty) if validation.rounded_qty is not None else None,
            "min_order_qty": str(validation.min_order_qty) if validation.min_order_qty is not None else None,
            "min_order_amt": str(validation.min_order_amt) if validation.min_order_amt is not None else None,
            "warnings": validation.warnings,
        },
    )


def _apply_liquidity_diagnostics(
    leg: FundAllocationLeg,
    *,
    liquidity: LiquidityCheckResult,
) -> None:
    leg.last_price = liquidity.last_price
    leg.best_bid = liquidity.best_bid
    leg.best_ask = liquidity.best_ask
    leg.corridor_pct = liquidity.corridor_pct
    leg.available_liquidity_qty = liquidity.available_liquidity_qty
    leg.available_liquidity_usdt = liquidity.available_liquidity_usdt
    leg.required_qty = liquidity.required_qty
    leg.required_usdt = liquidity.required_usdt


def _apply_mock_execution_result(
    leg: FundAllocationLeg,
    *,
    result: MockExecutionResult,
    execution_mode: str,
    order_link_id: str | None,
) -> None:
    leg.execution_mode = execution_mode
    leg.status = (
        ALLOCATION_LEG_STATUS_FILLED
        if result.status == "filled"
        else ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
    )
    leg.order_link_id = order_link_id or leg.order_link_id
    leg.bybit_order_id = result.order_id or leg.bybit_order_id
    leg.filled_qty = result.filled_qty
    leg.filled_usdt = result.filled_usdt
    leg.avg_fill_price = result.avg_fill_price
    leg.fill_ratio = result.fill_ratio
    leg.residual_usdt = result.residual_usdt
    leg.executed_suborders = result.executed_suborders
    leg.actual_cash_used_usdt = result.filled_usdt
    leg.error = None
    leg.sent_at = leg.sent_at or utcnow()
    leg.confirmed_at = utcnow()
    leg.updated_at = utcnow()


def _instrument_payload_diagnostics(
    *,
    info: InstrumentInfo,
    validation: InstrumentValidationResult,
    liquidity: LiquidityCheckResult | None,
) -> dict[str, Any]:
    return {
        "instrument": {
            "symbol": info.symbol,
            "category": info.category,
            "status": info.status,
            "tick_size": str(info.tick_size),
            "qty_step": str(info.qty_step),
            "min_order_qty": str(info.min_order_qty),
            "min_order_amt": str(info.min_order_amt) if info.min_order_amt is not None else None,
            "max_market_order_qty": (
                str(info.max_market_order_qty)
                if info.max_market_order_qty is not None
                else None
            ),
            "max_limit_order_qty": (
                str(info.max_limit_order_qty)
                if info.max_limit_order_qty is not None
                else None
            ),
        },
        "validation": {
            "ok": validation.ok,
            "rounded_qty": str(validation.rounded_qty) if validation.rounded_qty is not None else None,
            "warnings": validation.warnings,
        },
        "liquidity": None
        if liquidity is None
        else {
            "ok": liquidity.ok,
            "last_price": str(liquidity.last_price),
            "best_bid": str(liquidity.best_bid) if liquidity.best_bid is not None else None,
            "best_ask": str(liquidity.best_ask) if liquidity.best_ask is not None else None,
            "corridor_pct": str(liquidity.corridor_pct),
            "corridor_price": str(liquidity.corridor_price),
            "available_liquidity_qty": str(liquidity.available_liquidity_qty),
            "available_liquidity_usdt": str(liquidity.available_liquidity_usdt),
            "required_qty": str(liquidity.required_qty),
            "required_usdt": str(liquidity.required_usdt),
            "liquidity_multiplier": str(liquidity.liquidity_multiplier),
            "error": liquidity.error,
        },
    }


def count_active_strategy_or_sliced_legs(
    db: Session,
    allocation_batch_id: int,
) -> int:
    return (
        db.query(FundAllocationLeg)
        .filter(
            FundAllocationLeg.allocation_batch_id == allocation_batch_id,
            FundAllocationLeg.status.in_(ACTIVE_ALLOCATION_LEG_STATUSES),
        )
        .count()
    )


def can_start_new_strategy_leg(
    db: Session,
    allocation_batch_id: int,
    *,
    max_active: int | None = None,
) -> bool:
    config = get_allocation_execution_config()
    limit = int(max_active if max_active is not None else config.max_active_strategy_orders)

    active = count_active_strategy_or_sliced_legs(
        db,
        allocation_batch_id,
    )
    return active < limit


def _prepare_market(
    *,
    leg: FundAllocationLeg,
    info: InstrumentInfo,
    liquidity: LiquidityCheckResult,
    config: AllocationExecutionConfig,
) -> ExecutionDecision:
    order_link_id = leg.order_link_id or make_market_order_link_id(
        leg.allocation_batch_id,
        leg.id,
    )

    payload = build_protected_market_order_payload(
        category=info.category,
        symbol=info.symbol,
        side=_leg_side(leg),
        target_qty=liquidity.required_qty,
        target_usdt=dec(leg.target_usdt),
        order_link_id=order_link_id,
        slippage_pct=config.market_slippage_pct,
    )

    result = simulate_protected_market_order_execution(
        payload=payload,
        target_qty=liquidity.required_qty,
        target_usdt=dec(leg.target_usdt),
        avg_fill_price=liquidity.last_price,
        mock_fill_ratio=Decimal("1.0"),
        min_fill_ratio=config.min_fill_ratio,
    )

    _apply_mock_execution_result(
        leg,
        result=result,
        execution_mode=EXECUTION_MODE_MARKET,
        order_link_id=order_link_id,
    )

    return ExecutionDecision(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        status=leg.status,
        execution_mode=leg.execution_mode,
        action="mock_market_order",
        reason=None,
        payload=payload.payload,
        diagnostics={
            "mock_result": {
                "order_id": result.order_id,
                "fill_ratio": str(result.fill_ratio),
                "residual_usdt": str(result.residual_usdt),
            }
        },
    )


def _prepare_native_iceberg(
    db: Session,
    *,
    leg: FundAllocationLeg,
    info: InstrumentInfo,
    liquidity: LiquidityCheckResult,
    config: AllocationExecutionConfig,
) -> ExecutionDecision | None:
    strategy_ref = leg.order_link_id or make_strategy_client_ref(
        leg.allocation_batch_id,
        leg.id,
    )

    payload = build_native_iceberg_strategy_payload(
        category=info.category,
        symbol=info.symbol,
        side=_leg_side(leg),
        target_qty=liquidity.required_qty,
        target_usdt=dec(leg.target_usdt),
        last_price=liquidity.last_price,
        tick_size=info.tick_size,
        order_count=config.native_iceberg_order_count,
        strategy_ref=strategy_ref,
        settle_coin=info.quote_coin,
    )

    if not payload.supported:
        return None

    if not can_start_new_strategy_leg(
        db,
        leg.allocation_batch_id,
        max_active=config.max_active_strategy_orders,
    ):
        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode,
            action="wait_active_strategy_limit",
            reason="Max active strategy/sliced legs reached",
            payload=None,
            diagnostics={
                "max_active_strategy_orders": config.max_active_strategy_orders,
            },
        )

    if leg.strategy_id:
        leg.execution_mode = EXECUTION_MODE_NATIVE_ICEBERG
        leg.status = ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING
        leg.updated_at = utcnow()

        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode,
            action="idempotent_existing_strategy",
            reason=None,
            payload=payload.payload,
            diagnostics={
                "strategy_id": leg.strategy_id,
                "strategy_ref": strategy_ref,
            },
        )

    mock_result = simulate_native_iceberg_strategy_create(
        payload=payload,
    )

    leg.execution_mode = EXECUTION_MODE_NATIVE_ICEBERG
    leg.status = ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING
    leg.order_link_id = strategy_ref
    leg.strategy_id = mock_result.strategy_id
    leg.planned_suborders = config.native_iceberg_order_count
    leg.error = None
    leg.sent_at = leg.sent_at or utcnow()
    leg.updated_at = utcnow()

    return ExecutionDecision(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        status=leg.status,
        execution_mode=leg.execution_mode,
        action="mock_native_iceberg",
        reason=None,
        payload=payload.payload,
        diagnostics={
            "strategy_id": mock_result.strategy_id,
            "strategy_status": mock_result.status,
        },
    )


def _prepare_sliced_ioc(
    db: Session,
    *,
    leg: FundAllocationLeg,
    info: InstrumentInfo,
    liquidity: LiquidityCheckResult,
    config: AllocationExecutionConfig,
) -> ExecutionDecision:
    if not can_start_new_strategy_leg(
        db,
        leg.allocation_batch_id,
        max_active=config.max_active_strategy_orders,
    ):
        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode,
            action="wait_active_strategy_limit",
            reason="Max active strategy/sliced legs reached",
            payload=None,
            diagnostics={
                "max_active_strategy_orders": config.max_active_strategy_orders,
            },
        )

    payloads = build_sliced_ioc_payloads(
        allocation_batch_id=leg.allocation_batch_id,
        leg_id=leg.id,
        category=info.category,
        symbol=info.symbol,
        side=_leg_side(leg),
        target_qty=liquidity.required_qty,
        last_price=liquidity.last_price,
        best_bid=liquidity.best_bid,
        best_ask=liquidity.best_ask,
        tick_size=info.tick_size,
        qty_step=info.qty_step,
        slices=config.sliced_ioc_slices,
        corridor_pct=config.liquidity_corridor_pct,
        chase_bps=Decimal(config.sliced_ioc_chase_bps),
    )

    result = simulate_sliced_ioc_execution(
        payloads=payloads,
        target_qty=liquidity.required_qty,
        target_usdt=dec(leg.target_usdt),
        avg_fill_price=liquidity.last_price,
        min_fill_ratio=config.min_fill_ratio,
        per_slice_fill_ratio=Decimal("1.0"),
    )

    first_order_link_id = payloads[0].order_link_id if payloads else None

    _apply_mock_execution_result(
        leg,
        result=result,
        execution_mode=EXECUTION_MODE_SLICED_IOC_FALLBACK,
        order_link_id=first_order_link_id,
    )
    leg.planned_suborders = len(payloads)
    leg.executed_suborders = result.executed_suborders

    return ExecutionDecision(
        allocation_leg_id=leg.id,
        allocation_batch_id=leg.allocation_batch_id,
        status=leg.status,
        execution_mode=leg.execution_mode,
        action="mock_sliced_ioc",
        reason=None,
        payload={
            "payloads": [item.payload for item in payloads],
        },
        diagnostics={
            "executed_suborders": result.executed_suborders,
            "fill_ratio": str(result.fill_ratio),
            "residual_usdt": str(result.residual_usdt),
        },
    )


def prepare_execution_for_leg(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
    mock_mode: bool = True,
) -> ExecutionDecision:
    """
    Stage 22.3 execution decision engine.

    In Stage 22.3 this function is allowed to:
    - read mocked instrument/orderbook/ticker data;
    - build market/strategy/IOC payloads;
    - simulate fills;
    - update fund_allocation_legs diagnostics/status fields.

    It is not allowed to:
    - submit real Bybit orders;
    - submit real Strategy API orders;
    - do Earn stake;
    - do transfers.
    """
    if not mock_mode:
        raise AllocationExecutionError(
            "Real allocation execution is blocked in Stage 22.3. Use mock_mode=True."
        )

    config = get_allocation_execution_config()

    leg = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.id == allocation_leg_id)
        .with_for_update()
        .first()
    )

    if leg is None:
        raise AllocationExecutionError(f"Allocation leg not found: {allocation_leg_id}")

    if leg.status != ALLOCATION_LEG_STATUS_PLANNED:
        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode or EXECUTION_MODE_PLANNED,
            action="skip_non_planned_status",
            reason=f"Leg status is not planned: {leg.status}",
            payload=None,
            diagnostics={},
        )

    if not leg.symbol:
        leg.status = ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
        leg.execution_mode = EXECUTION_MODE_SKIPPED
        leg.residual_usdt = dec(leg.target_usdt)
        leg.error = "Leg has no symbol; external execution is out of scope for Stage 22.3."
        leg.updated_at = utcnow()

        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode,
            action="skip_no_symbol",
            reason=leg.error,
            payload=None,
            diagnostics={},
        )

    category = _leg_category(leg)
    if not category:
        _mark_failed_requires_review(
            leg,
            error="Cannot infer execution category for allocation leg.",
        )

        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode,
            action="failed_category_missing",
            reason=leg.error,
            payload=None,
            diagnostics={},
        )

    try:
        info = get_instrument_info(
            client,
            category=category,
            symbol=leg.symbol,
        )

        validation = validate_instrument_for_leg(
            leg,
            info,
        )

        if not validation.ok:
            decision = _apply_validation_skip(
                leg,
                validation=validation,
            )
            db.add(leg)
            db.flush()
            return decision

        last_price = get_last_price(
            client,
            category=info.category,
            symbol=info.symbol,
        )
        orderbook = get_orderbook(
            client,
            category=info.category,
            symbol=info.symbol,
        )

        target_qty = _target_qty_for_liquidity(
            leg=leg,
            last_price=last_price,
        )

        liquidity = check_liquidity_corridor(
            side=_leg_side(leg),
            target_qty=target_qty,
            target_usdt=_target_usdt(leg),
            last_price=last_price,
            orderbook=orderbook,
            corridor_pct=config.liquidity_corridor_pct,
            liquidity_multiplier=_liquidity_multiplier(
                leg=leg,
                config=config,
            ),
        )

        _apply_liquidity_diagnostics(
            leg,
            liquidity=liquidity,
        )

        if liquidity.ok:
            decision = _prepare_market(
                leg=leg,
                info=info,
                liquidity=liquidity,
                config=config,
            )
            db.add(leg)
            db.flush()
            decision.diagnostics.update(
                _instrument_payload_diagnostics(
                    info=info,
                    validation=validation,
                    liquidity=liquidity,
                )
            )
            return decision

        native_decision = _prepare_native_iceberg(
            db,
            leg=leg,
            info=info,
            liquidity=liquidity,
            config=config,
        )

        if native_decision is not None:
            db.add(leg)
            db.flush()
            native_decision.diagnostics.update(
                _instrument_payload_diagnostics(
                    info=info,
                    validation=validation,
                    liquidity=liquidity,
                )
            )
            return native_decision

        decision = _prepare_sliced_ioc(
            db,
            leg=leg,
            info=info,
            liquidity=liquidity,
            config=config,
        )
        db.add(leg)
        db.flush()
        decision.diagnostics.update(
            _instrument_payload_diagnostics(
                info=info,
                validation=validation,
                liquidity=liquidity,
            )
        )
        return decision

    except Exception as exc:
        _mark_failed_requires_review(
            leg,
            error=str(exc),
        )
        db.add(leg)
        db.flush()

        return ExecutionDecision(
            allocation_leg_id=leg.id,
            allocation_batch_id=leg.allocation_batch_id,
            status=leg.status,
            execution_mode=leg.execution_mode,
            action="failed_requires_review",
            reason=str(exc),
            payload=None,
            diagnostics={},
        )