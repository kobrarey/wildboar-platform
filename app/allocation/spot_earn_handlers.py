from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.execution_engine import prepare_execution_for_leg

from app.allocation.earn_orders import build_earn_stake_payload, simulate_earn_stake
from app.allocation.earn_products import (
    EarnProductUnavailableError,
    get_earn_product_info,
    validate_earn_product_for_stake,
)
from app.allocation.execution_config import get_allocation_execution_config
from app.allocation.idempotency import make_earn_order_link_id
from app.allocation.liquidity import get_last_price
from app.config import settings
from app.allocation.snapshot_service import STABLECOINS
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
    EXECUTION_MODE_BUY_THEN_STAKE,
    EXECUTION_MODE_CASH_NOOP,
    EXECUTION_MODE_EARN_STAKE,
    EXECUTION_MODE_RESIDUAL_CASH,
    EXECUTION_MODE_RESIDUAL_EARN,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.models import FundAllocationLeg


ZERO = Decimal("0")


class SpotEarnHandlerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SpotEarnHandlerDecision:
    allocation_leg_id: int
    allocation_batch_id: int
    status: str
    execution_mode: str
    action: str
    reason: str | None
    diagnostics: dict[str, Any]


def utcnow():
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


def _normalize_coin(value: Any) -> str:
    return _normalize_text(value).upper()


def _target_usdt(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_usdt)


def _is_stablecoin(coin: str | None) -> bool:
    return _normalize_coin(coin) in STABLECOINS


def _decision(
    leg: FundAllocationLeg,
    *,
    action: str,
    reason: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> SpotEarnHandlerDecision:
    return SpotEarnHandlerDecision(
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
        raise SpotEarnHandlerError(f"Allocation leg not found: {allocation_leg_id}")

    return leg


def handle_stable_cash_leg_mock(
    db: Session,
    *,
    allocation_leg_id: int,
) -> SpotEarnHandlerDecision:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type != LEG_TYPE_STABLE_CASH:
        raise SpotEarnHandlerError(
            f"Leg is not stable_cash: leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status != ALLOCATION_LEG_STATUS_PLANNED:
        return _decision(
            leg,
            action="skip_non_planned_status",
            reason=f"Leg status is not planned: {leg.status}",
        )

    if not _is_stablecoin(leg.coin):
        leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
        leg.residual_usdt = _target_usdt(leg)
        leg.error = f"stable_cash leg coin is not stablecoin: {leg.coin}"
        leg.updated_at = utcnow()

        db.add(leg)
        db.flush()

        return _decision(
            leg,
            action="failed_stable_cash_coin",
            reason=leg.error,
        )

    target_usdt = _target_usdt(leg)

    leg.execution_mode = EXECUTION_MODE_CASH_NOOP
    leg.status = ALLOCATION_LEG_STATUS_FILLED
    leg.filled_qty = target_usdt
    leg.filled_usdt = target_usdt
    leg.actual_cash_used_usdt = ZERO
    leg.residual_usdt = ZERO
    leg.error = None
    leg.confirmed_at = utcnow()
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return _decision(
        leg,
        action="cash_noop",
        diagnostics={
            "coin": leg.coin,
            "filled_usdt": str(target_usdt),
            "actual_cash_used_usdt": "0",
        },
    )


def handle_spot_buy_leg_mock(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> SpotEarnHandlerDecision:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type != LEG_TYPE_SPOT_BUY:
        raise SpotEarnHandlerError(
            f"Leg is not spot_buy: leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status != ALLOCATION_LEG_STATUS_PLANNED:
        return _decision(
            leg,
            action="skip_non_planned_status",
            reason=f"Leg status is not planned: {leg.status}",
        )

    engine_decision = prepare_execution_for_leg(
        db,
        allocation_leg_id=allocation_leg_id,
        client=client,
        mock_mode=True,
    )

    db.refresh(leg)

    return _decision(
        leg,
        action="spot_buy_mock_execution",
        reason=engine_decision.reason,
        diagnostics={
            "engine_action": engine_decision.action,
            "engine_mode": engine_decision.execution_mode,
            "engine_status": engine_decision.status,
            "residual_usdt": str(dec(leg.residual_usdt)),
        },
    )


def _target_qty(leg: FundAllocationLeg) -> Decimal:
    return dec(leg.target_qty)


def _mark_earn_unavailable(
    db: Session,
    leg: FundAllocationLeg,
    *,
    reason: str,
) -> SpotEarnHandlerDecision:
    leg.status = ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE
    leg.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
    leg.residual_usdt = _target_usdt(leg)
    leg.actual_cash_used_usdt = ZERO
    leg.error = reason
    leg.updated_at = utcnow()

    db.add(leg)
    db.flush()

    return _decision(
        leg,
        action="skip_earn_unavailable",
        reason=reason,
        diagnostics={"residual_usdt": str(leg.residual_usdt)},
    )


def _stake_usdt_price_for_coin(
    *,
    coin: str,
    target_usdt: Decimal,
    target_qty: Decimal,
    fallback_price: Decimal | None = None,
) -> Decimal:
    if _normalize_coin(coin) in STABLECOINS:
        return Decimal("1")

    fallback = dec(fallback_price)
    if fallback > ZERO:
        return fallback

    if target_qty > ZERO and target_usdt > ZERO:
        return target_usdt / target_qty

    return Decimal("1")


def handle_usdt_earn_stake_leg_mock(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
    is_residual_leg: bool = False,
) -> SpotEarnHandlerDecision:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type not in {LEG_TYPE_USDT_EARN_STAKE, LEG_TYPE_RESIDUAL_USDT_EARN}:
        raise SpotEarnHandlerError(
            f"Leg is not usdt_earn_stake/residual_usdt_earn: "
            f"leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status != ALLOCATION_LEG_STATUS_PLANNED:
        return _decision(
            leg,
            action="skip_non_planned_status",
            reason=f"Leg status is not planned: {leg.status}",
        )

    amount_usdt = _target_usdt(leg)

    try:
        product = get_earn_product_info(
            client,
            coin="USDT",
            category=settings.ALLOCATION_USDT_EARN_CATEGORY,
        )
        validation = validate_earn_product_for_stake(
            product=product,
            amount=amount_usdt,
        )

        if not validation.ok:
            if is_residual_leg or leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN:
                leg.status = ALLOCATION_LEG_STATUS_RESIDUAL_CASH
                leg.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
                leg.residual_usdt = amount_usdt
                leg.actual_cash_used_usdt = ZERO
                leg.error = validation.error
                leg.confirmed_at = utcnow()
                leg.updated_at = utcnow()
                db.add(leg)
                db.flush()
                return _decision(
                    leg,
                    action="residual_cash",
                    reason=validation.error,
                    diagnostics={"amount_usdt": str(amount_usdt)},
                )

            leg.status = ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE
            leg.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
            leg.residual_usdt = amount_usdt
            leg.actual_cash_used_usdt = ZERO
            leg.error = validation.error
            leg.updated_at = utcnow()
            db.add(leg)
            db.flush()
            return _decision(
                leg,
                action="skip_usdt_earn_unavailable",
                reason=validation.error,
                diagnostics={"amount_usdt": str(amount_usdt)},
            )

        order_link_id = leg.order_link_id or make_earn_order_link_id(
            leg.allocation_batch_id,
            leg.id,
        )

        payload = build_earn_stake_payload(
            category=product.category,
            product_id=product.product_id,
            coin="USDT",
            amount=validation.stake_amount,
            order_link_id=order_link_id,
        )

        mock = simulate_earn_stake(
            payload=payload,
            stake_usdt_price=Decimal("1"),
            requested_amount=amount_usdt,
            residual_usdt_hint=validation.residual_amount,
            final_status=(
                ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED
                if is_residual_leg or leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN
                else ALLOCATION_LEG_STATUS_FILLED
            ),
        )

        leg.order_link_id = order_link_id
        leg.earn_order_id = mock.earn_order_id
        leg.execution_mode = (
            EXECUTION_MODE_RESIDUAL_EARN
            if is_residual_leg or leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN
            else EXECUTION_MODE_EARN_STAKE
        )
        leg.status = (
            ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED
            if is_residual_leg or leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN
            else ALLOCATION_LEG_STATUS_FILLED
        )
        leg.filled_qty = mock.staked_qty
        leg.filled_usdt = mock.staked_usdt
        leg.actual_cash_used_usdt = mock.staked_usdt
        leg.residual_usdt = mock.residual_usdt
        leg.error = "\n".join(validation.warnings) if validation.warnings else None
        leg.sent_at = leg.sent_at or utcnow()
        leg.confirmed_at = utcnow()
        leg.updated_at = utcnow()

        db.add(leg)
        db.flush()

        return _decision(
            leg,
            action="mock_usdt_earn_stake",
            diagnostics={
                "product_id": product.product_id,
                "stake_amount": str(validation.stake_amount),
                "staked_usdt": str(mock.staked_usdt),
                "residual_usdt": str(mock.residual_usdt),
                "earn_order_id": mock.earn_order_id,
            },
        )

    except EarnProductUnavailableError as exc:
        if is_residual_leg or leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN:
            leg.status = ALLOCATION_LEG_STATUS_RESIDUAL_CASH
            leg.execution_mode = EXECUTION_MODE_RESIDUAL_CASH
            leg.residual_usdt = amount_usdt
            leg.actual_cash_used_usdt = ZERO
            leg.error = str(exc)
            leg.confirmed_at = utcnow()
            leg.updated_at = utcnow()
            db.add(leg)
            db.flush()
            return _decision(
                leg,
                action="residual_cash",
                reason=str(exc),
                diagnostics={"amount_usdt": str(amount_usdt)},
            )

        return _mark_earn_unavailable(db, leg, reason=str(exc))


def handle_buy_then_stake_leg_mock(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> SpotEarnHandlerDecision:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type != LEG_TYPE_BUY_THEN_STAKE:
        raise SpotEarnHandlerError(
            f"Leg is not buy_then_stake: leg_id={leg.id}, leg_type={leg.leg_type}"
        )

    if leg.status != ALLOCATION_LEG_STATUS_PLANNED:
        return _decision(
            leg,
            action="skip_non_planned_status",
            reason=f"Leg status is not planned: {leg.status}",
        )

    coin = _normalize_coin(leg.coin)
    if not coin or coin in STABLECOINS:
        return _mark_earn_unavailable(
            db,
            leg,
            reason=f"buy_then_stake requires non-stable coin, got coin={leg.coin}",
        )

    original_group = leg.leg_group
    original_type = leg.leg_type
    original_category = leg.category
    original_side = leg.side

    earn_category = _normalize_text(leg.category)
    if not earn_category or earn_category.lower() in {"earn", "spot"}:
        earn_category = "FlexibleSaving"

    try:
        product = get_earn_product_info(
            client,
            coin=coin,
            category=earn_category,
        )

        last_price = get_last_price(
            client,
            category="spot",
            symbol=leg.symbol,
        )

        target_qty = _target_qty(leg)
        target_usdt = _target_usdt(leg)

        estimated_qty = target_qty
        if estimated_qty <= ZERO:
            if target_usdt <= ZERO or last_price <= ZERO:
                return _mark_earn_unavailable(
                    db,
                    leg,
                    reason=(
                        f"Cannot estimate buy_then_stake quantity: "
                        f"target_usdt={target_usdt}, last_price={last_price}"
                    ),
                )
            estimated_qty = target_usdt / last_price

        config = get_allocation_execution_config()
        conservative_qty = estimated_qty * config.min_fill_ratio

        precheck = validate_earn_product_for_stake(
            product=product,
            amount=conservative_qty,
        )

        if not precheck.ok:
            return _mark_earn_unavailable(
                db,
                leg,
                reason=(
                    "Earn pre-check failed before spot buy. "
                    f"estimated_qty={estimated_qty}, conservative_qty={conservative_qty}, "
                    f"error={precheck.error}"
                ),
            )

        leg.leg_group = "spot"
        leg.leg_type = LEG_TYPE_SPOT_BUY
        leg.category = "spot"
        leg.side = "Buy"
        db.add(leg)
        db.flush()

        engine_decision = prepare_execution_for_leg(
            db,
            allocation_leg_id=allocation_leg_id,
            client=client,
            mock_mode=True,
        )

        db.refresh(leg)

        spot_status = leg.status
        filled_qty = dec(leg.filled_qty)
        filled_usdt = dec(leg.filled_usdt)
        spot_residual_usdt = dec(leg.residual_usdt)

        leg.leg_group = original_group
        leg.leg_type = original_type
        leg.category = earn_category
        leg.side = original_side

        if filled_qty <= ZERO:
            leg.status = ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
            leg.execution_mode = EXECUTION_MODE_BUY_THEN_STAKE
            leg.residual_usdt = target_usdt
            leg.error = (
                f"Spot execution produced zero filled_qty before Earn stake. "
                f"engine_action={engine_decision.action}"
            )
            leg.updated_at = utcnow()
            db.add(leg)
            db.flush()
            return _decision(
                leg,
                action="buy_then_stake_zero_spot_fill",
                reason=leg.error,
            )

        stake_validation = validate_earn_product_for_stake(
            product=product,
            amount=filled_qty,
        )

        if not stake_validation.ok:
            leg.status = ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
            leg.execution_mode = EXECUTION_MODE_BUY_THEN_STAKE
            leg.residual_usdt = spot_residual_usdt
            leg.error = (
                "Spot buy filled, but filled coin amount is below Earn stake requirements. "
                f"Coin remains as spot. {stake_validation.error}"
            )
            leg.updated_at = utcnow()
            db.add(leg)
            db.flush()
            return _decision(
                leg,
                action="spot_filled_but_earn_below_min",
                reason=leg.error,
                diagnostics={
                    "filled_qty": str(filled_qty),
                    "filled_usdt": str(filled_usdt),
                    "spot_residual_usdt": str(spot_residual_usdt),
                },
            )

        order_link_id = make_earn_order_link_id(
            leg.allocation_batch_id,
            leg.id,
        )

        payload = build_earn_stake_payload(
            category=product.category,
            product_id=product.product_id,
            coin=coin,
            amount=stake_validation.stake_amount,
            order_link_id=order_link_id,
        )

        price = _stake_usdt_price_for_coin(
            coin=coin,
            target_usdt=filled_usdt,
            target_qty=filled_qty,
            fallback_price=last_price,
        )

        mock = simulate_earn_stake(
            payload=payload,
            stake_usdt_price=price,
            requested_amount=filled_qty,
            residual_usdt_hint=spot_residual_usdt + stake_validation.residual_amount * price,
            final_status=(
                ALLOCATION_LEG_STATUS_FILLED
                if dec(leg.fill_ratio) >= config.min_fill_ratio
                else ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
            ),
        )

        leg.execution_mode = EXECUTION_MODE_BUY_THEN_STAKE
        leg.status = (
            ALLOCATION_LEG_STATUS_FILLED
            if dec(leg.fill_ratio) >= config.min_fill_ratio
            else ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED
        )
        leg.earn_order_id = mock.earn_order_id
        leg.filled_qty = filled_qty
        leg.filled_usdt = filled_usdt
        leg.actual_cash_used_usdt = filled_usdt
        leg.residual_usdt = mock.residual_usdt
        leg.error = "\n".join(stake_validation.warnings) if stake_validation.warnings else None
        leg.sent_at = leg.sent_at or utcnow()
        leg.confirmed_at = utcnow()
        leg.updated_at = utcnow()

        db.add(leg)
        db.flush()

        return _decision(
            leg,
            action="mock_buy_then_stake",
            diagnostics={
                "engine_action": engine_decision.action,
                "spot_status": spot_status,
                "product_id": product.product_id,
                "earn_order_id": mock.earn_order_id,
                "filled_qty": str(filled_qty),
                "filled_usdt": str(filled_usdt),
                "stake_amount": str(stake_validation.stake_amount),
                "staked_usdt": str(mock.staked_usdt),
                "residual_usdt": str(mock.residual_usdt),
            },
        )

    except EarnProductUnavailableError as exc:
        leg.leg_group = original_group
        leg.leg_type = original_type
        leg.category = original_category
        leg.side = original_side
        db.add(leg)
        db.flush()
        return _mark_earn_unavailable(db, leg, reason=str(exc))

    except Exception as exc:
        leg.leg_group = original_group
        leg.leg_type = original_type
        leg.category = original_category
        leg.side = original_side

        leg.status = ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.execution_mode = EXECUTION_MODE_BUY_THEN_STAKE
        leg.residual_usdt = dec(leg.residual_usdt)
        leg.error = str(exc)
        leg.updated_at = utcnow()

        db.add(leg)
        db.flush()

        return _decision(
            leg,
            action="buy_then_stake_failed_requires_review",
            reason=str(exc),
        )


def handle_spot_earn_leg_mock(
    db: Session,
    *,
    allocation_leg_id: int,
    client: Any,
) -> SpotEarnHandlerDecision:
    leg = _get_leg_for_update(db, allocation_leg_id=allocation_leg_id)

    if leg.leg_type == LEG_TYPE_STABLE_CASH:
        return handle_stable_cash_leg_mock(
            db,
            allocation_leg_id=allocation_leg_id,
        )

    if leg.leg_type == LEG_TYPE_SPOT_BUY:
        return handle_spot_buy_leg_mock(
            db,
            allocation_leg_id=allocation_leg_id,
            client=client,
        )

    if leg.leg_type == LEG_TYPE_USDT_EARN_STAKE:
        return handle_usdt_earn_stake_leg_mock(
            db,
            allocation_leg_id=allocation_leg_id,
            client=client,
            is_residual_leg=False,
        )

    if leg.leg_type == LEG_TYPE_RESIDUAL_USDT_EARN:
        return handle_usdt_earn_stake_leg_mock(
            db,
            allocation_leg_id=allocation_leg_id,
            client=client,
            is_residual_leg=True,
        )

    if leg.leg_type == LEG_TYPE_BUY_THEN_STAKE:
        return handle_buy_then_stake_leg_mock(
            db,
            allocation_leg_id=allocation_leg_id,
            client=client,
        )

    raise SpotEarnHandlerError(
        f"Unsupported spot/earn leg_type for Stage 22.4: "
        f"leg_id={leg.id}, leg_type={leg.leg_type}"
    )

