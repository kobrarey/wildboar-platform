from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.earn_orders import build_earn_stake_payload
from app.allocation.earn_products import (
    EarnProductUnavailableError,
    get_earn_product_info,
    validate_earn_product_for_stake,
)
from app.allocation.idempotency import make_earn_order_link_id
from app.allocation.live_earn_config import require_live_earn_whitelisted
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    ALLOCATION_LEG_STATUS_PLANNED,
    EXECUTION_MODE_EARN_STAKE,
    EXECUTION_MODE_RESIDUAL_EARN,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.operation_guard.hooks import require_bybit_allocation_earn_order_guard
from app.models import Fund, FundAllocationLeg


ZERO = Decimal("0")


class LiveEarnOrderError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveEarnOrderPlan:
    allocation_leg_id: int
    allocation_batch_id: int
    fund_id: int
    settlement_batch_id: int
    fund_code: str
    earn_order_id: str
    order_link_id: str
    payload: dict[str, Any]
    category: str
    product_id: str
    coin: str
    amount: Decimal
    stake_amount: Decimal
    residual_amount: Decimal
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class LiveEarnOrderResult:
    ok: bool
    action: str
    allocation_leg_id: int
    allocation_batch_id: int
    status: str
    execution_mode: str | None
    earn_order_id: str | None
    order_link_id: str | None
    bybit_order_id: str | None
    product_id: str | None
    coin: str | None
    category: str | None
    staked_qty: Decimal
    staked_usdt: Decimal
    residual_usdt: Decimal
    raw: dict[str, Any]


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


def _normalize_coin(value: Any) -> str:
    return _normalize_text(value).upper()


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
        raise LiveEarnOrderError(f"Allocation leg not found: {allocation_leg_id}")

    return leg


def _get_fund(
    db: Session,
    *,
    fund_id: int,
) -> Fund:
    fund = db.query(Fund).filter(Fund.id == int(fund_id)).first()

    if fund is None:
        raise LiveEarnOrderError(f"Fund not found: {fund_id}")

    return fund


def _earn_amount_for_leg(leg: FundAllocationLeg) -> Decimal:
    target_usdt = dec(leg.target_usdt)
    target_qty = dec(leg.target_qty)

    if _normalize_coin(leg.coin) == "USDT":
        return target_usdt if target_usdt > ZERO else target_qty

    return target_qty if target_qty > ZERO else target_usdt


def _earn_category_for_leg(leg: FundAllocationLeg, product_category: str) -> str:
    leg_category = _normalize_text(leg.category)
    if leg_category and leg_category.lower() not in {"earn", "spot"}:
        return leg_category

    return product_category


def build_live_earn_stake_order_plan(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
    default_category: str,
) -> LiveEarnOrderPlan:
    """
    Build and persist a live Earn stake plan.

    Safety:
    - GET product lookup only.
    - No POST.
    - Persists deterministic earn_order_id/order_link_id before future POST.
    - Validates explicit whitelist before future POST.
    """
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type not in {LEG_TYPE_USDT_EARN_STAKE, LEG_TYPE_RESIDUAL_USDT_EARN}:
        raise LiveEarnOrderError(
            f"Unsupported live Earn leg_type: leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status not in {ALLOCATION_LEG_STATUS_PLANNED, ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT}:
        raise LiveEarnOrderError(
            f"Unsupported live Earn leg status: leg_id={leg.id}, status={leg.status}"
        )

    fund = _get_fund(db, fund_id=int(leg.fund_id))

    coin = _normalize_coin(leg.coin) or "USDT"
    category = _earn_category_for_leg(leg, default_category)
    amount = _earn_amount_for_leg(leg)

    try:
        product = get_earn_product_info(
            client,
            coin=coin,
            category=category,
        )
    except EarnProductUnavailableError as exc:
        raise LiveEarnOrderError(str(exc)) from exc

    validation = validate_earn_product_for_stake(
        product=product,
        amount=amount,
    )

    if not validation.ok:
        raise LiveEarnOrderError(validation.error or "earn_product_validation_failed")

    whitelist = require_live_earn_whitelisted(
        fund_code=str(fund.code),
        coin=product.coin,
        category=product.category,
        product_id=product.product_id,
        amount=validation.stake_amount,
    )

    if not whitelist.ok:
        raise LiveEarnOrderError(f"earn_whitelist_blocked: {whitelist.reason}")

    order_link_id = leg.order_link_id or make_earn_order_link_id(
        int(leg.allocation_batch_id),
        int(leg.id),
    )

    payload = build_earn_stake_payload(
        category=product.category,
        product_id=product.product_id,
        coin=product.coin,
        amount=validation.stake_amount,
        order_link_id=order_link_id,
        account_type="UNIFIED",
    )

    leg.order_link_id = order_link_id
    leg.earn_order_id = leg.earn_order_id or order_link_id
    leg.execution_mode = (
        EXECUTION_MODE_RESIDUAL_EARN
        if leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN
        else EXECUTION_MODE_EARN_STAKE
    )
    leg.status = ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT
    leg.earn_product_id = product.product_id
    leg.earn_product_category = product.category
    leg.earn_product_status = product.status
    leg.earn_min_stake_amount = product.min_stake_amount
    leg.earn_max_stake_amount = product.max_stake_amount
    leg.earn_precision = dec(product.precision)
    leg.required_qty = validation.stake_amount
    leg.required_usdt = validation.stake_amount if product.coin == "USDT" else dec(leg.required_usdt)
    leg.residual_usdt = validation.residual_amount if product.coin == "USDT" else dec(leg.residual_usdt)
    leg.error = "\n".join(validation.warnings) if validation.warnings else None
    leg.sent_at = leg.sent_at or utcnow()
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return LiveEarnOrderPlan(
        allocation_leg_id=int(leg.id),
        allocation_batch_id=int(leg.allocation_batch_id),
        fund_id=int(leg.fund_id),
        settlement_batch_id=int(leg.settlement_batch_id),
        fund_code=str(fund.code),
        earn_order_id=str(leg.earn_order_id),
        order_link_id=order_link_id,
        payload=payload.payload,
        category=product.category,
        product_id=product.product_id,
        coin=product.coin,
        amount=amount,
        stake_amount=validation.stake_amount,
        residual_amount=validation.residual_amount,
        diagnostics=_json_dict(
            {
                "product": {
                    "product_id": product.product_id,
                    "category": product.category,
                    "coin": product.coin,
                    "status": product.status,
                    "min_stake_amount": product.min_stake_amount,
                    "max_stake_amount": product.max_stake_amount,
                    "precision": product.precision,
                },
                "validation": {
                    "ok": validation.ok,
                    "original_amount": validation.original_amount,
                    "rounded_amount": validation.rounded_amount,
                    "stake_amount": validation.stake_amount,
                    "residual_amount": validation.residual_amount,
                    "warnings": validation.warnings,
                },
                "whitelist": whitelist.diagnostics,
                "external_post_calls": 0,
            }
        ),
    )


def mark_live_earn_plan_failed_requires_review(
    db: Session,
    *,
    allocation_leg_id: int,
    error: str,
) -> FundAllocationLeg:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    leg.error = error
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return leg


SUCCESS_EARN_ORDER_STATUSES = {
    "SUCCESS",
    "SUCCEEDED",
    "COMPLETED",
    "COMPLETE",
    "FILLED",
    "DONE",
}

PENDING_EARN_ORDER_STATUSES = {
    "PENDING",
    "PROCESSING",
    "CREATED",
    "NEW",
    "UNKNOWN",
}

FAILED_EARN_ORDER_STATUSES = {
    "FAILED",
    "FAIL",
    "REJECTED",
    "CANCELLED",
    "CANCELED",
}


def _result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _candidate_order_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result_dict(payload)

    for key in ("list", "rows", "data", "orders"):
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    for key in ("order", "item"):
        value = result.get(key)
        if isinstance(value, dict):
            return [value]

    if result:
        if any(key in result for key in ("orderId", "orderLinkId", "productId", "status")):
            return [result]

    return []


def _find_order_by_link_id(
    rows: list[dict[str, Any]],
    *,
    order_link_id: str,
    product_id: str | None = None,
) -> dict[str, Any] | None:
    for row in rows:
        row_link_id = _normalize_text(
            row.get("orderLinkId")
            or row.get("order_link_id")
            or row.get("clientOrderId")
            or row.get("client_order_id")
            or row.get("localRef")
        )

        if row_link_id != order_link_id:
            continue

        if product_id:
            row_product_id = _normalize_text(
                row.get("productId")
                or row.get("product_id")
            )
            if row_product_id and row_product_id != product_id:
                continue

        return row

    return None


def _earn_order_external_id(order: dict[str, Any]) -> str | None:
    value = (
        order.get("orderId")
        or order.get("order_id")
        or order.get("earnOrderId")
        or order.get("earn_order_id")
    )
    return _normalize_text(value) or None


def _earn_order_link_id(order: dict[str, Any]) -> str | None:
    value = (
        order.get("orderLinkId")
        or order.get("order_link_id")
        or order.get("clientOrderId")
        or order.get("client_order_id")
        or order.get("localRef")
    )
    return _normalize_text(value) or None


def _earn_order_status(order: dict[str, Any]) -> str:
    return _normalize_text(
        order.get("status")
        or order.get("orderStatus")
        or order.get("state")
        or order.get("order_state")
        or "UNKNOWN"
    )


def _earn_order_product_id(order: dict[str, Any]) -> str | None:
    value = order.get("productId") or order.get("product_id")
    return _normalize_text(value) or None


def _earn_order_coin(order: dict[str, Any]) -> str | None:
    value = order.get("coin") or order.get("currency") or order.get("token")
    normalized = _normalize_coin(value)
    return normalized or None


def _earn_order_amount(order: dict[str, Any]) -> Decimal:
    return dec(
        order.get("orderValue")
        or order.get("amount")
        or order.get("stakeAmount")
        or order.get("qty")
        or order.get("quantity")
    )


def fetch_bybit_earn_order_by_link_id(
    client: Any,
    *,
    order_link_id: str,
    product_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Read-only Earn order reconciliation.

    Official Bybit V5 path:
    GET /v5/earn/order

    Safety:
    - no POST;
    - uses deterministic local orderLinkId;
    - optional productId narrowing.
    """
    params: dict[str, Any] = {
        "orderLinkId": order_link_id,
    }

    if product_id:
        params["productId"] = product_id

    payload = client.get("/v5/earn/order", params)

    rows = _candidate_order_rows(payload)
    return _find_order_by_link_id(
        rows,
        order_link_id=order_link_id,
        product_id=product_id,
    )


def submit_bybit_earn_stake_order(
    client: Any,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Single external Earn POST.

    Official Bybit V5 path:
    POST /v5/earn/place-order
    """
    result = client.post("/v5/earn/place-order", payload)
    data = _result_dict(result)

    # Some Bybit wrappers return order fields directly under result,
    # some may return an empty result with retCode=0.
    if data:
        return data

    return {
        "raw": result,
    }


def require_earn_guard_for_plan(
    db: Session,
    *,
    plan: LiveEarnOrderPlan,
):
    request_id = (
        f"allocation-earn:{plan.allocation_batch_id}:"
        f"{plan.allocation_leg_id}:{plan.earn_order_id}"
    )

    return require_bybit_allocation_earn_order_guard(
        db,
        fund_id=int(plan.fund_id),
        settlement_batch_id=int(plan.settlement_batch_id),
        amount_usdt=plan.stake_amount if plan.coin == "USDT" else plan.amount,
        request_id=request_id,
        metadata={
            "stage25_3_hook": "bybit_allocation_earn_order",
            "allocation_batch_id": int(plan.allocation_batch_id),
            "allocation_leg_id": int(plan.allocation_leg_id),
            "earn_order_id": plan.earn_order_id,
            "order_link_id": plan.order_link_id,
            "product_id": plan.product_id,
            "category": plan.category,
            "coin": plan.coin,
            "stake_amount": str(plan.stake_amount),
            "residual_amount": str(plan.residual_amount),
            "live_external_action": True,
            "whitelist_alone_is_insufficient": True,
        },
    )


def apply_bybit_earn_order_to_leg(
    leg: FundAllocationLeg,
    *,
    order: dict[str, Any],
) -> LiveEarnOrderResult:
    status_raw = _earn_order_status(order)
    status_norm = status_raw.upper()
    external_order_id = _earn_order_external_id(order)
    order_link_id = _earn_order_link_id(order) or leg.order_link_id
    product_id = _earn_order_product_id(order) or leg.earn_product_id
    coin = _earn_order_coin(order) or _normalize_coin(leg.coin) or "USDT"
    amount = _earn_order_amount(order)

    if external_order_id:
        leg.bybit_order_id = external_order_id

    if order_link_id:
        leg.order_link_id = order_link_id
        leg.earn_order_id = leg.earn_order_id or order_link_id

    if product_id:
        leg.earn_product_id = product_id

    if coin:
        leg.coin = coin

    leg.earn_product_status = status_raw

    if status_norm in SUCCESS_EARN_ORDER_STATUSES:
        if leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN:
            leg.status = "residual_earn_completed"
            leg.execution_mode = EXECUTION_MODE_RESIDUAL_EARN
        else:
            leg.status = "filled"
            leg.execution_mode = EXECUTION_MODE_EARN_STAKE

        leg.filled_qty = amount
        leg.filled_usdt = amount if coin == "USDT" else dec(leg.filled_usdt)
        leg.actual_cash_used_usdt = amount if coin == "USDT" else dec(leg.actual_cash_used_usdt)
        leg.residual_usdt = dec(leg.residual_usdt)
        leg.error = None
        leg.confirmed_at = utcnow()

        ok = True
        action = "earn_order_reconciled_success"

    elif status_norm in FAILED_EARN_ORDER_STATUSES:
        leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.error = f"Earn order failed on Bybit: status={status_raw}"
        ok = False
        action = "earn_order_reconciled_failed"

    else:
        # Existing DB constraints do not currently include a dedicated
        # earn_order_pending_reconciliation status. Stage 25.3 therefore
        # fails closed to manual review and never retries blindly.
        leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.error = f"Earn order pending/unknown; manual reconciliation required: status={status_raw}"
        ok = False
        action = "earn_order_pending_or_unknown_requires_review"

    leg.updated_at = utcnow()

    return LiveEarnOrderResult(
        ok=ok,
        action=action,
        allocation_leg_id=int(leg.id),
        allocation_batch_id=int(leg.allocation_batch_id),
        status=str(leg.status),
        execution_mode=leg.execution_mode,
        earn_order_id=leg.earn_order_id,
        order_link_id=leg.order_link_id,
        bybit_order_id=leg.bybit_order_id,
        product_id=leg.earn_product_id,
        coin=leg.coin,
        category=leg.earn_product_category,
        staked_qty=dec(leg.filled_qty),
        staked_usdt=dec(leg.filled_usdt),
        residual_usdt=dec(leg.residual_usdt),
        raw=_json_dict(order),
    )


def reconcile_live_earn_stake_leg_by_link_id(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> LiveEarnOrderResult:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    order_link_id = _normalize_text(leg.order_link_id or leg.earn_order_id)
    if not order_link_id:
        raise LiveEarnOrderError(
            f"Cannot reconcile Earn leg without order_link_id/earn_order_id: {allocation_leg_id}"
        )

    order = fetch_bybit_earn_order_by_link_id(
        client,
        order_link_id=order_link_id,
        product_id=leg.earn_product_id,
    )

    if order is None:
        leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.error = (
            "Earn order not found by deterministic orderLinkId; "
            "manual reconciliation required, no blind retry"
        )
        leg.updated_at = utcnow()

        db.add(leg)
        db.flush()

        return LiveEarnOrderResult(
            ok=False,
            action="earn_order_not_found_requires_review",
            allocation_leg_id=int(leg.id),
            allocation_batch_id=int(leg.allocation_batch_id),
            status=str(leg.status),
            execution_mode=leg.execution_mode,
            earn_order_id=leg.earn_order_id,
            order_link_id=leg.order_link_id,
            bybit_order_id=leg.bybit_order_id,
            product_id=leg.earn_product_id,
            coin=leg.coin,
            category=leg.earn_product_category,
            staked_qty=dec(leg.filled_qty),
            staked_usdt=dec(leg.filled_usdt),
            residual_usdt=dec(leg.residual_usdt),
            raw={},
        )

    result = apply_bybit_earn_order_to_leg(
        leg,
        order=order,
    )

    db.add(leg)
    db.flush()

    return result


def mark_live_earn_order_create_failed(
    db: Session,
    *,
    allocation_leg_id: int,
    error: str,
) -> FundAllocationLeg:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
    leg.error = error
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return leg