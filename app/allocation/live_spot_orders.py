from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.bybit_orders import (
    ProtectedMarketOrderPayload,
    build_protected_market_order_payload,
    compute_fill_ratio,
)
from app.allocation.execution_config import get_allocation_execution_config
from app.allocation.idempotency import make_market_order_link_id
from app.allocation.instrument_info import (
    InstrumentInfo,
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
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    ALLOCATION_LEG_STATUS_PLANNED,
    EXECUTION_MODE_MARKET,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_SPOT_BUY,
)
from app.models import FundAllocationLeg
from app.allocation.live_policy import (
    BUY_THEN_STAKE_SPOT_ONLY_REASON,
    BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY,
    buy_then_stake_live_policy,
)
from app.operation_guard.hooks import require_bybit_allocation_trade_order_guard


ZERO = Decimal("0")
ONE = Decimal("1")


class LiveSpotOrderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveSpotOrderPlan:
    allocation_leg_id: int
    allocation_batch_id: int
    fund_id: int
    settlement_batch_id: int
    order_link_id: str
    payload: dict[str, Any]
    category: str
    symbol: str
    target_usdt: Decimal
    target_qty: Decimal
    required_qty: Decimal
    required_usdt: Decimal
    instrument: dict[str, Any]
    liquidity: dict[str, Any]


@dataclass(frozen=True)
class LiveSpotOrderResult:
    allocation_leg_id: int
    allocation_batch_id: int
    ok: bool
    status: str
    order_link_id: str | None
    bybit_order_id: str | None
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


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _leg_category(leg: FundAllocationLeg) -> str:
    raw = _normalize_text(leg.category).lower()
    if leg.leg_type == LEG_TYPE_BUY_THEN_STAKE:
        return "spot"

    if raw:
        return raw

    if _normalize_text(leg.leg_group).lower() == "spot":
        return "spot"

    return raw


def _leg_side(leg: FundAllocationLeg) -> str:
    raw = _normalize_text(leg.side).lower()

    if raw in {"buy", "long"}:
        return "Buy"

    if raw in {"sell", "short"}:
        return "Sell"

    if leg.leg_type in {LEG_TYPE_SPOT_BUY, LEG_TYPE_BUY_THEN_STAKE}:
        return "Buy"

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


def _get_leg_for_update(
    db: Session,
    *,
    allocation_leg_id: int,
) -> FundAllocationLeg:
    leg = (
        db.query(FundAllocationLeg)
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .with_for_update()
        .first()
    )

    if leg is None:
        raise LiveSpotOrderError(f"Allocation leg not found: {allocation_leg_id}")

    return leg


def _instrument_diagnostics(info: InstrumentInfo) -> dict[str, Any]:
    return {
        "symbol": info.symbol,
        "category": info.category,
        "status": info.status,
        "base_coin": info.base_coin,
        "quote_coin": info.quote_coin,
        "tick_size": info.tick_size,
        "qty_step": info.qty_step,
        "min_order_qty": info.min_order_qty,
        "min_order_amt": info.min_order_amt,
        "max_market_order_qty": info.max_market_order_qty,
        "max_limit_order_qty": info.max_limit_order_qty,
    }


def _liquidity_diagnostics(liquidity: LiquidityCheckResult) -> dict[str, Any]:
    return {
        "ok": liquidity.ok,
        "last_price": liquidity.last_price,
        "best_bid": liquidity.best_bid,
        "best_ask": liquidity.best_ask,
        "corridor_pct": liquidity.corridor_pct,
        "corridor_price": liquidity.corridor_price,
        "available_liquidity_qty": liquidity.available_liquidity_qty,
        "available_liquidity_usdt": liquidity.available_liquidity_usdt,
        "required_qty": liquidity.required_qty,
        "required_usdt": liquidity.required_usdt,
        "liquidity_multiplier": liquidity.liquidity_multiplier,
        "error": liquidity.error,
    }


def _apply_market_diagnostics_to_leg(
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


def build_live_spot_market_order_plan(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> LiveSpotOrderPlan:
    """
    Build a protected market order plan for a spot allocation leg.

    Safety:
    - No POST.
    - No external write action.
    - Uses public/read market data only through provided client.
    - Mutates leg with deterministic order_link_id + diagnostics, but caller controls commit.
    """
    config = get_allocation_execution_config()
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type == LEG_TYPE_BUY_THEN_STAKE:
        if buy_then_stake_live_policy() != BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY:
            raise LiveSpotOrderError(
                f"buy_then_stake live policy is not spot_only: leg_id={leg.id}"
            )

        leg.category = "spot"
        leg.error = BUY_THEN_STAKE_SPOT_ONLY_REASON

    elif leg.leg_type != LEG_TYPE_SPOT_BUY:
        raise LiveSpotOrderError(
            f"Unsupported live spot leg_type: leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status not in {
        ALLOCATION_LEG_STATUS_PLANNED,
        ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    }:
        raise LiveSpotOrderError(
            f"Unsupported live spot leg status: leg_id={leg.id}, status={leg.status}"
        )

    category = _leg_category(leg)
    if category != "spot":
        raise LiveSpotOrderError(
            f"Live spot adapter supports only category=spot: leg_id={leg.id}, category={category}"
        )

    if not _normalize_text(leg.symbol):
        raise LiveSpotOrderError(f"Live spot leg has empty symbol: leg_id={leg.id}")

    info = get_instrument_info(
        client,
        category=category,
        symbol=str(leg.symbol),
    )

    validation = validate_instrument_for_leg(leg, info)
    if not validation.ok:
        raise LiveSpotOrderError(
            f"Live spot instrument validation failed: {validation.error}"
        )

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
        target_usdt=dec(leg.target_usdt),
        last_price=last_price,
        orderbook=orderbook,
        corridor_pct=config.liquidity_corridor_pct,
        liquidity_multiplier=Decimal("1.0"),
    )

    _apply_market_diagnostics_to_leg(
        leg,
        liquidity=liquidity,
    )

    if not liquidity.ok:
        raise LiveSpotOrderError(
            f"Live spot liquidity check failed: {liquidity.error}"
        )

    order_link_id = leg.order_link_id or make_market_order_link_id(
        int(leg.allocation_batch_id),
        int(leg.id),
    )

    order_payload: ProtectedMarketOrderPayload = build_protected_market_order_payload(
        category=info.category,
        symbol=info.symbol,
        side=_leg_side(leg),
        target_qty=liquidity.required_qty,
        target_usdt=dec(leg.target_usdt),
        order_link_id=order_link_id,
        slippage_pct=config.market_slippage_pct,
    )

    leg.order_link_id = order_link_id
    leg.execution_mode = EXECUTION_MODE_MARKET
    leg.status = ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT
    leg.error = (
        BUY_THEN_STAKE_SPOT_ONLY_REASON
        if leg.leg_type == LEG_TYPE_BUY_THEN_STAKE
        else None
    )
    leg.sent_at = leg.sent_at or utcnow()
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return LiveSpotOrderPlan(
        allocation_leg_id=int(leg.id),
        allocation_batch_id=int(leg.allocation_batch_id),
        fund_id=int(leg.fund_id),
        settlement_batch_id=int(leg.settlement_batch_id),
        order_link_id=order_link_id,
        payload=order_payload.payload,
        category=info.category,
        symbol=info.symbol,
        target_usdt=dec(leg.target_usdt),
        target_qty=dec(leg.target_qty),
        required_qty=liquidity.required_qty,
        required_usdt=liquidity.required_usdt,
        instrument=_json_dict(_instrument_diagnostics(info)),
        liquidity=_json_dict(_liquidity_diagnostics(liquidity)),
    )


def _extract_order_list(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = (response or {}).get("result") or {}
    items = result.get("list") or []
    if not isinstance(items, list):
        return []

    return [item for item in items if isinstance(item, dict)]


def fetch_bybit_order_by_link_id(
    client: Any,
    *,
    category: str,
    symbol: str,
    order_link_id: str,
) -> dict[str, Any] | None:
    """
    Read-only idempotency lookup by deterministic orderLinkId.

    Safety:
    - No POST.
    - Used before any duplicate send.
    """
    params = {
        "category": category,
        "symbol": symbol,
        "orderLinkId": order_link_id,
    }

    for path in ("/v5/order/realtime", "/v5/order/history"):
        try:
            response = client.get(path, params)
        except Exception:
            continue

        orders = _extract_order_list(response)
        for order in orders:
            if str(order.get("orderLinkId") or "") == str(order_link_id):
                return order

    return None


def submit_bybit_spot_market_order(
    client: Any,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post("/v5/order/create", payload)

    result = response.get("result") or {}
    if not isinstance(result, dict):
        raise LiveSpotOrderError("Bybit order create response result is not object")

    return result


def _order_id(order: dict[str, Any] | None) -> str | None:
    if not order:
        return None

    value = order.get("orderId")
    return None if value is None else str(value)


def _order_status(order: dict[str, Any] | None) -> str:
    if not order:
        return ""

    return str(order.get("orderStatus") or "")


def _avg_price(order: dict[str, Any]) -> Decimal | None:
    avg = dec(order.get("avgPrice"))
    if avg > ZERO:
        return avg

    price = dec(order.get("price"))
    if price > ZERO:
        return price

    return None


def _filled_qty(order: dict[str, Any]) -> Decimal:
    return dec(
        order.get("cumExecQty")
        or order.get("cumFilledQty")
        or order.get("filledQty")
    )


def _filled_usdt(order: dict[str, Any]) -> Decimal:
    return dec(
        order.get("cumExecValue")
        or order.get("cumFilledValue")
        or order.get("filledValue")
    )


def _fee_usdt(order: dict[str, Any]) -> Decimal | None:
    fee = dec(order.get("cumExecFee"))
    return fee if fee > ZERO else None


def apply_bybit_order_to_leg(
    leg: FundAllocationLeg,
    *,
    order: dict[str, Any],
    min_fill_ratio: Decimal | None = None,
) -> LiveSpotOrderResult:
    min_ratio = dec(min_fill_ratio if min_fill_ratio is not None else Decimal("0.90"))
    status = _order_status(order)
    filled_qty = _filled_qty(order)
    filled_usdt = _filled_usdt(order)
    avg_price = _avg_price(order)
    fee_usdt = _fee_usdt(order)

    fill_ratio = compute_fill_ratio(
        target_qty=dec(leg.required_qty) or dec(leg.target_qty),
        target_usdt=dec(leg.target_usdt),
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        spot_buy_by_quote=True,
    )
    residual_usdt = max(ZERO, dec(leg.target_usdt) * (ONE - min(ONE, fill_ratio)))

    leg.bybit_order_id = _order_id(order) or leg.bybit_order_id
    leg.filled_qty = filled_qty
    leg.filled_usdt = filled_usdt
    leg.avg_fill_price = avg_price
    leg.fill_ratio = fill_ratio
    leg.fee_usdt = fee_usdt
    leg.actual_cash_used_usdt = filled_usdt
    leg.residual_usdt = residual_usdt
    leg.updated_at = utcnow()

    terminal_rejected = status in {
        "Rejected",
        "Cancelled",
        "Deactivated",
    }

    terminal_filled = status in {
        "Filled",
        "PartiallyFilledCanceled",
    }

    if status == "Filled" or (terminal_filled and fill_ratio >= min_ratio):
        leg.status = (
            ALLOCATION_LEG_STATUS_FILLED
            if residual_usdt <= Decimal("0.00000001")
            else ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
        )
        leg.error = (
            BUY_THEN_STAKE_SPOT_ONLY_REASON
            if leg.leg_type == LEG_TYPE_BUY_THEN_STAKE
            else None
        )
        leg.confirmed_at = utcnow()
        action = "reconciled_filled"
        ok = True
        reason = None
    elif terminal_rejected or (terminal_filled and fill_ratio < min_ratio):
        leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.error = (
            f"Bybit order terminal status requires review: "
            f"status={status}, fill_ratio={fill_ratio}"
        )
        action = "reconciled_failed_requires_review"
        ok = False
        reason = leg.error
    else:
        leg.status = ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT
        leg.error = None
        action = "reconciled_pending"
        ok = True
        reason = "order_still_pending"

    return LiveSpotOrderResult(
        allocation_leg_id=int(leg.id),
        allocation_batch_id=int(leg.allocation_batch_id),
        ok=ok,
        status=leg.status,
        order_link_id=leg.order_link_id,
        bybit_order_id=leg.bybit_order_id,
        action=action,
        reason=reason,
        diagnostics={
            "bybit_order_status": status,
            "fill_ratio": str(fill_ratio),
            "filled_qty": str(filled_qty),
            "filled_usdt": str(filled_usdt),
            "residual_usdt": str(residual_usdt),
            "avg_fill_price": str(avg_price) if avg_price is not None else None,
            "fee_usdt": str(fee_usdt) if fee_usdt is not None else None,
        },
    )


def reconcile_live_spot_market_leg_by_link_id(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> LiveSpotOrderResult:
    config = get_allocation_execution_config()
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if not leg.order_link_id:
        return LiveSpotOrderResult(
            allocation_leg_id=int(leg.id),
            allocation_batch_id=int(leg.allocation_batch_id),
            ok=True,
            status=leg.status,
            order_link_id=None,
            bybit_order_id=leg.bybit_order_id,
            action="no_order_link_id",
            reason="no_order_link_id",
            diagnostics={},
        )

    order = fetch_bybit_order_by_link_id(
        client,
        category=_leg_category(leg),
        symbol=str(leg.symbol),
        order_link_id=str(leg.order_link_id),
    )

    if order is None:
        return LiveSpotOrderResult(
            allocation_leg_id=int(leg.id),
            allocation_batch_id=int(leg.allocation_batch_id),
            ok=True,
            status=leg.status,
            order_link_id=leg.order_link_id,
            bybit_order_id=leg.bybit_order_id,
            action="order_not_found_by_link_id",
            reason="order_not_found_by_link_id",
            diagnostics={"order_link_id": leg.order_link_id},
        )

    result = apply_bybit_order_to_leg(
        leg,
        order=order,
        min_fill_ratio=config.min_fill_ratio,
    )

    db.add(leg)
    db.flush()

    return result


def require_trade_guard_for_plan(
    db: Session,
    *,
    plan: LiveSpotOrderPlan,
) -> dict[str, Any]:
    request_id = f"allocation-trade:{plan.allocation_batch_id}:{plan.allocation_leg_id}:{plan.order_link_id}"

    decision = require_bybit_allocation_trade_order_guard(
        db,
        fund_id=plan.fund_id,
        settlement_batch_id=plan.settlement_batch_id,
        amount_usdt=plan.required_usdt,
        request_id=request_id,
        metadata={
            "allocation_batch_id": plan.allocation_batch_id,
            "allocation_leg_id": plan.allocation_leg_id,
            "order_link_id": plan.order_link_id,
            "category": plan.category,
            "symbol": plan.symbol,
            "execution_mode": EXECUTION_MODE_MARKET,
            "external_endpoint": "/v5/order/create",
        },
    )

    return decision.to_dict()


def mark_live_spot_order_create_failed(
    db: Session,
    *,
    allocation_leg_id: int,
    error: str,
) -> LiveSpotOrderResult:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    leg.error = error
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return LiveSpotOrderResult(
        allocation_leg_id=int(leg.id),
        allocation_batch_id=int(leg.allocation_batch_id),
        ok=False,
        status=leg.status,
        order_link_id=leg.order_link_id,
        bybit_order_id=leg.bybit_order_id,
        action="spot_order_create_failed_requires_review",
        reason=error,
        diagnostics={
            "order_link_id": leg.order_link_id,
        },
    )