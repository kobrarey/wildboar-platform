from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    UserFundPosition,
    UserWallet,
)
from app.settlement.negative_finalization_types import (
    NegativeFinalizationError,
    NegativeFinalizationResult,
    _json_dict,
    utcnow,
)
from app.settlement.negative_sale_snapshot import dec
from app.settlement.share_quantity import (
    ShareQuantityError,
    calculate_successful_buy_share_quantity,
    require_share_quantity_4dp_aligned,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BYBIT_FLOW_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_ACCOUNTING_FINALIZED,
    FINALIZATION_BATCH_STATUS_ACCOUNTING_PROCESSING,
    FINALIZATION_BATCH_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_CREATED,
    FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    FINALIZATION_BATCH_STATUS_PRICING_UNLOCKED,
    FINALIZATION_BATCH_STATUS_VALIDATING,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SUCCESS,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
)


ZERO = Decimal("0")
Q10 = Decimal("0.0000000001")


class NegativeShareQuantityError(
    NegativeFinalizationError
):
    pass


def _q10(value: Any) -> Decimal:
    return dec(value).quantize(Q10)


def _share_4dp(
    value: Any,
    *,
    field_name: str,
    allow_negative: bool = False,
) -> Decimal:
    try:
        return require_share_quantity_4dp_aligned(
            value,
            field_name=field_name,
            allow_negative=allow_negative,
        )
    except ShareQuantityError as exc:
        raise NegativeShareQuantityError(
            str(exc)
        ) from exc


def _same_decimal(left: Any, right: Any) -> bool:
    return _q10(left) == _q10(right)


def _positive(value: Any) -> bool:
    return dec(value) > ZERO


def _now_or_supplied(now):
    return now or utcnow()


def _order_ids_from_leg(leg: FundNegativePayoutLeg) -> list[int]:
    raw = leg.order_ids_json

    if raw is None:
        return []

    if isinstance(raw, dict):
        values = raw.get("order_ids") or []
    elif isinstance(raw, list):
        values = raw
    else:
        return []

    return [int(value) for value in values]


def _position_key(user_id: int, fund_id: int) -> str:
    return f"{int(user_id)}:{int(fund_id)}"


def _wallet_key(user_wallet_id: int) -> str:
    return str(int(user_wallet_id))


def _result_from_completed(
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    status_before: str | None,
    settlement_status_before: str | None,
    idempotent: bool,
) -> NegativeFinalizationResult:
    return NegativeFinalizationResult(
        ok=True,
        finalization_batch_id=int(finalization.id),
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(finalization.payout_batch_id),
        fund_id=int(fund.id),
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=finalization.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        buy_order_count=finalization.buy_order_count,
        redeem_order_count=finalization.redeem_order_count,
        success_order_count=finalization.success_order_count,
        shares_outstanding_before=str(finalization.shares_outstanding_before),
        shares_outstanding_after=(
            str(finalization.shares_outstanding_after)
            if finalization.shares_outstanding_after is not None
            else None
        ),
        total_buy_usdt=(
            str(finalization.total_buy_usdt)
            if finalization.total_buy_usdt is not None
            else None
        ),
        total_buy_shares=(
            str(finalization.total_buy_shares)
            if finalization.total_buy_shares is not None
            else None
        ),
        total_redeem_shares=(
            str(finalization.total_redeem_shares)
            if finalization.total_redeem_shares is not None
            else None
        ),
        planned_net_shares_change=(
            str(finalization.planned_net_shares_change)
            if finalization.planned_net_shares_change is not None
            else None
        ),
        actual_net_shares_change=(
            str(finalization.actual_net_shares_change)
            if finalization.actual_net_shares_change is not None
            else None
        ),
        total_net_user_payout_usdt=(
            str(finalization.total_net_user_payout_usdt)
            if finalization.total_net_user_payout_usdt is not None
            else None
        ),
        total_partial_month_fee_usdt=(
            str(finalization.total_partial_month_fee_usdt)
            if finalization.total_partial_month_fee_usdt is not None
            else None
        ),
        accounting_finalized_at=(
            finalization.accounting_finalized_at.isoformat()
            if finalization.accounting_finalized_at is not None
            else None
        ),
        pricing_unlocked_at=(
            finalization.pricing_unlocked_at.isoformat()
            if finalization.pricing_unlocked_at is not None
            else None
        ),
        idempotent=idempotent,
        diagnostics={"idempotent": idempotent},
    )


def _set_failed(
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund | None,
    status_before: str | None,
    settlement_status_before: str | None,
    error: str,
    now,
    diagnostics: dict[str, Any] | None = None,
) -> NegativeFinalizationResult:
    finalization.status = FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    finalization.error = error
    finalization.updated_at = now
    finalization.reconciliation_json = _json_dict(
        {
            "ok": False,
            "error": error,
            "diagnostics": diagnostics or {},
            "no_real_bybit_calls": True,
            "no_real_bsc_calls": True,
            "no_payout_transfers": True,
            "no_nav_chart_writes": True,
            "no_server_deploy": True,
        }
    )
    finalization.report_json = _json_dict(
        {
            "stage": "23.6",
            "ok": False,
            "error": error,
            "final_state": FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
        }
    )

    settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    settlement_batch.error = error
    settlement_batch.updated_at = now

    return NegativeFinalizationResult(
        ok=False,
        finalization_batch_id=(
            int(finalization.id) if finalization.id is not None else None
        ),
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=(
            int(finalization.payout_batch_id)
            if finalization.payout_batch_id is not None
            else None
        ),
        fund_id=int(finalization.fund_id) if finalization.fund_id is not None else None,
        fund_code=str(fund.code) if fund is not None else None,
        status_before=status_before,
        status_after=finalization.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        buy_order_count=finalization.buy_order_count,
        redeem_order_count=finalization.redeem_order_count,
        success_order_count=finalization.success_order_count,
        shares_outstanding_before=(
            str(finalization.shares_outstanding_before)
            if finalization.shares_outstanding_before is not None
            else None
        ),
        shares_outstanding_after=(
            str(finalization.shares_outstanding_after)
            if finalization.shares_outstanding_after is not None
            else None
        ),
        total_buy_usdt=(
            str(finalization.total_buy_usdt)
            if finalization.total_buy_usdt is not None
            else None
        ),
        total_buy_shares=(
            str(finalization.total_buy_shares)
            if finalization.total_buy_shares is not None
            else None
        ),
        total_redeem_shares=(
            str(finalization.total_redeem_shares)
            if finalization.total_redeem_shares is not None
            else None
        ),
        planned_net_shares_change=(
            str(finalization.planned_net_shares_change)
            if finalization.planned_net_shares_change is not None
            else None
        ),
        actual_net_shares_change=(
            str(finalization.actual_net_shares_change)
            if finalization.actual_net_shares_change is not None
            else None
        ),
        total_net_user_payout_usdt=(
            str(finalization.total_net_user_payout_usdt)
            if finalization.total_net_user_payout_usdt is not None
            else None
        ),
        total_partial_month_fee_usdt=(
            str(finalization.total_partial_month_fee_usdt)
            if finalization.total_partial_month_fee_usdt is not None
            else None
        ),
        accounting_finalized_at=(
            finalization.accounting_finalized_at.isoformat()
            if finalization.accounting_finalized_at is not None
            else None
        ),
        pricing_unlocked_at=(
            finalization.pricing_unlocked_at.isoformat()
            if finalization.pricing_unlocked_at is not None
            else None
        ),
        error=error,
        diagnostics=diagnostics or {},
    )


def _mark_share_failed_orders(
    db: Session,
    *,
    settlement_batch_id: int,
    error: str,
) -> None:
    orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(settlement_batch_id)
        )
        .filter(
            FundOrder.side.in_(
                [ORDER_SIDE_BUY, ORDER_SIDE_REDEEM]
            )
        )
        .with_for_update()
        .all()
    )

    for order in orders:
        if order.status in {
            ORDER_STATUS_SUCCESS,
            ORDER_STATUS_CANCELLED,
        }:
            continue

        order.status = (
            ORDER_STATUS_FAILED_REQUIRES_REVIEW
        )
        order.error = error
        db.add(order)

    db.flush()


def _lock_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    settlement_batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if settlement_batch is None:
        raise NegativeFinalizationError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return settlement_batch


def _lock_fund(db: Session, *, fund_id: int) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .with_for_update()
        .first()
    )
    if fund is None:
        raise NegativeFinalizationError(f"Fund not found: {fund_id}")

    return fund


def _lock_runtime_state(db: Session, *, fund_id: int) -> FundRuntimeState | None:
    return (
        db.query(FundRuntimeState)
        .filter(FundRuntimeState.fund_id == int(fund_id))
        .with_for_update()
        .first()
    )


def _lock_sale_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeSaleBatch:
    sale_batch = (
        db.query(FundNegativeSaleBatch)
        .filter(FundNegativeSaleBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if sale_batch is None:
        raise NegativeFinalizationError("Negative sale batch not found")

    return sale_batch


def _lock_bybit_flow(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeBybitFlow:
    bybit_flow = (
        db.query(FundNegativeBybitFlow)
        .filter(FundNegativeBybitFlow.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if bybit_flow is None:
        raise NegativeFinalizationError("Negative Bybit flow not found")

    return bybit_flow


def _lock_payout_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativePayoutBatch:
    payout_batch = (
        db.query(FundNegativePayoutBatch)
        .filter(FundNegativePayoutBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if payout_batch is None:
        raise NegativeFinalizationError("Negative payout batch not found")

    return payout_batch


def _lock_payout_legs(
    db: Session,
    *,
    payout_batch_id: int,
) -> list[FundNegativePayoutLeg]:
    legs = (
        db.query(FundNegativePayoutLeg)
        .filter(FundNegativePayoutLeg.payout_batch_id == int(payout_batch_id))
        .order_by(FundNegativePayoutLeg.id.asc())
        .with_for_update()
        .all()
    )
    if not legs:
        raise NegativeFinalizationError("Negative payout legs not found")

    return legs


def _lock_existing_finalization(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeFinalizationBatch | None:
    return (
        db.query(FundNegativeFinalizationBatch)
        .filter(
            FundNegativeFinalizationBatch.settlement_batch_id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )


def _validate_input_state(
    *,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch,
    bybit_flow: FundNegativeBybitFlow,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
    existing_finalization: FundNegativeFinalizationBatch | None,
) -> None:
    idempotent_completed = (
        existing_finalization is not None
        and existing_finalization.status == FINALIZATION_BATCH_STATUS_COMPLETED
        and settlement_batch.status == BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
    )

    if not idempotent_completed:
        if settlement_batch.status != BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED:
            raise NegativeFinalizationError(
                "Settlement batch status must be negative_net_payouts_confirmed"
            )

    allowed_sale_statuses = {
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
    }
    if sale_batch.status not in allowed_sale_statuses:
        raise NegativeFinalizationError("Sale batch must be completed")

    if bybit_flow.status != BYBIT_FLOW_STATUS_COMPLETED:
        raise NegativeFinalizationError("Bybit flow must be completed")

    if settings.NEGATIVE_NET_FINALIZATION_REQUIRE_PAYOUTS_CONFIRMED:
        if payout_batch.status != PAYOUT_BATCH_STATUS_COMPLETED:
            raise NegativeFinalizationError("Payout batch must be completed")

        bad_legs = [
            int(leg.id)
            for leg in payout_legs
            if leg.status != PAYOUT_LEG_STATUS_BALANCE_REFRESHED
        ]
        if bad_legs:
            raise NegativeFinalizationError(
                f"All payout legs must be balance_refreshed: {bad_legs}"
            )

        if not payout_batch.balance_refresh_json:
            raise NegativeFinalizationError(
                "Payout batch balance_refresh_json is required"
            )

        missing_leg_refresh = [
            int(leg.id) for leg in payout_legs if not leg.balance_refresh_json
        ]
        if missing_leg_refresh:
            raise NegativeFinalizationError(
                f"Payout leg balance_refresh_json is required: {missing_leg_refresh}"
            )

        if payout_batch.confirmed_total_payout_usdt is None:
            raise NegativeFinalizationError(
                "Payout confirmed_total_payout_usdt is required"
            )

        if payout_batch.confirmed_payout_leg_count != payout_batch.payout_leg_count:
            raise NegativeFinalizationError("Payout confirmed count mismatch")

    if settlement_batch.settlement_price_usdt is None:
        raise NegativeFinalizationError("Settlement price is required")

    if dec(settlement_batch.settlement_price_usdt) <= ZERO:
        raise NegativeFinalizationError("Settlement price must be positive")

    if settlement_batch.shares_outstanding_before is None:
        raise NegativeFinalizationError("Shares outstanding before is required")

    if settlement_batch.pricing_locked_at is None:
        raise NegativeFinalizationError("Pricing lock must exist before finalization")

    if not idempotent_completed and settlement_batch.pricing_unlocked_at is not None:
        raise NegativeFinalizationError("Pricing must not be unlocked before finalization")


def _new_or_existing_finalization(
    db: Session,
    *,
    existing: FundNegativeFinalizationBatch | None,
    settlement_batch: FundSettlementBatch,
    payout_batch: FundNegativePayoutBatch,
    bybit_flow: FundNegativeBybitFlow,
    sale_batch: FundNegativeSaleBatch,
    fund: Fund,
    now,
) -> FundNegativeFinalizationBatch:
    if existing is not None:
        return existing

    finalization = FundNegativeFinalizationBatch(
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(payout_batch.id),
        bybit_flow_id=int(bybit_flow.id),
        sale_batch_id=int(sale_batch.id),
        fund_id=int(fund.id),
        status=FINALIZATION_BATCH_STATUS_CREATED,
        settlement_price_usdt=dec(settlement_batch.settlement_price_usdt),
        shares_outstanding_before=dec(settlement_batch.shares_outstanding_before),
        created_at=now,
        updated_at=now,
    )
    db.add(finalization)
    db.flush()
    return finalization


def _load_relevant_orders(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    excluded_statuses = [
        ORDER_STATUS_FAILED,
        ORDER_STATUS_FAILED_REQUIRES_REVIEW,
        ORDER_STATUS_CANCELLED,
    ]

    orders = (
        db.query(FundOrder)
        .filter(FundOrder.settlement_batch_id == int(settlement_batch_id))
        .filter(FundOrder.side.in_([ORDER_SIDE_BUY, ORDER_SIDE_REDEEM]))
        .filter(~FundOrder.status.in_(excluded_statuses))
        .order_by(FundOrder.side.asc(), FundOrder.user_id.asc(), FundOrder.id.asc())
        .with_for_update()
        .all()
    )
    if not orders:
        raise NegativeFinalizationError("No relevant settlement orders found")

    return orders


def _detect_partial_finalization(
    *,
    orders: list[FundOrder],
    existing_finalization: FundNegativeFinalizationBatch | None,
) -> bool:
    success_count = sum(1 for order in orders if order.status == ORDER_STATUS_SUCCESS)
    if success_count == 0:
        return False

    if success_count == len(orders):
        return False

    return True


def _all_orders_success(orders: list[FundOrder]) -> bool:
    return all(order.status == ORDER_STATUS_SUCCESS for order in orders)


def _split_orders(orders: list[FundOrder]) -> tuple[list[FundOrder], list[FundOrder]]:
    buy_orders = [order for order in orders if order.side == ORDER_SIDE_BUY]
    redeem_orders = [order for order in orders if order.side == ORDER_SIDE_REDEEM]
    return buy_orders, redeem_orders


def _covered_redeem_order_ids(
    *,
    payout_legs: list[FundNegativePayoutLeg],
) -> set[int]:
    covered: set[int] = set()
    for leg in payout_legs:
        covered.update(_order_ids_from_leg(leg))
    return covered


def _validate_redeem_orders(
    *,
    redeem_orders: list[FundOrder],
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
) -> dict[str, Any]:
    redeem_ids = {
        int(order.id)
        for order in redeem_orders
    }
    covered_ids = _covered_redeem_order_ids(
        payout_legs=payout_legs,
    )

    if redeem_ids != covered_ids:
        missing = sorted(redeem_ids - covered_ids)
        extra = sorted(covered_ids - redeem_ids)
        raise NegativeFinalizationError(
            "Payout legs must cover all redeem orders. "
            f"missing={missing}, extra={extra}"
        )

    total_net_payout = ZERO
    total_redeem_shares = ZERO
    total_partial_month_fee = ZERO

    for order in redeem_orders:
        redeem_shares = _share_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )

        if redeem_shares <= ZERO:
            raise NegativeShareQuantityError(
                f"Redeem order {order.id} "
                "shares must be positive"
            )

        if (
            order.net_user_payout_usdt is None
            or dec(order.net_user_payout_usdt) <= ZERO
        ):
            raise NegativeFinalizationError(
                f"Redeem order {order.id} "
                "net_user_payout_usdt must be positive"
            )

        if (
            order.net_price_usdt is None
            or dec(order.net_price_usdt) <= ZERO
        ):
            raise NegativeFinalizationError(
                f"Redeem order {order.id} "
                "net_price_usdt must be positive"
            )

        if (
            order.partial_month_fee_usdt is not None
            and dec(order.partial_month_fee_usdt) < ZERO
        ):
            raise NegativeFinalizationError(
                f"Redeem order {order.id} "
                "partial_month_fee_usdt must be >= 0"
            )

        total_net_payout += dec(
            order.net_user_payout_usdt
        )
        total_redeem_shares += redeem_shares
        total_partial_month_fee += dec(
            order.partial_month_fee_usdt or ZERO
        )

    total_redeem_shares = _share_4dp(
        total_redeem_shares,
        field_name="total_redeem_shares",
    )

    if not _same_decimal(
        total_net_payout,
        payout_batch.confirmed_total_payout_usdt,
    ):
        raise NegativeFinalizationError(
            "Payout total must match redeem orders"
        )

    return {
        "redeem_order_ids": sorted(redeem_ids),
        "payout_leg_order_ids": sorted(covered_ids),
        "total_net_user_payout_usdt": (
            _q10(total_net_payout)
        ),
        "total_redeem_shares": (
            total_redeem_shares
        ),
        "total_partial_month_fee_usdt": (
            _q10(total_partial_month_fee)
        ),
    }


def _validate_buy_orders(
    *,
    buy_orders: list[FundOrder],
    settlement_price_usdt: Decimal,
) -> dict[str, Any]:
    total_buy_usdt = ZERO
    total_buy_shares = ZERO
    computed_shares_by_order_id: dict[
        int,
        Decimal,
    ] = {}

    for order in buy_orders:
        try:
            quantity = (
                calculate_successful_buy_share_quantity(
                    amount_usdt=order.amount_usdt,
                    settlement_price_usdt=(
                        settlement_price_usdt
                    ),
                )
            )
        except ShareQuantityError as exc:
            raise NegativeShareQuantityError(
                f"buy_order_{order.id}:{exc}"
            ) from exc

        buy_shares = quantity.issued_shares

        if order.shares is not None:
            stored_shares = _share_4dp(
                order.shares,
                field_name=(
                    f"buy_order_{order.id}_shares"
                ),
            )

            if stored_shares != buy_shares:
                raise NegativeShareQuantityError(
                    f"Buy order {order.id} "
                    "shares mismatch with canonical "
                    "4dp settlement calculation"
                )

        computed_shares_by_order_id[
            int(order.id)
        ] = buy_shares
        total_buy_usdt += (
            quantity.full_investment_usdt
        )
        total_buy_shares += buy_shares

    total_buy_shares = _share_4dp(
        total_buy_shares,
        field_name="total_buy_shares",
    )

    return {
        "total_buy_usdt": _q10(total_buy_usdt),
        "total_buy_shares": total_buy_shares,
        "computed_shares_by_order_id": (
            computed_shares_by_order_id
        ),
    }


def _lock_position(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPosition | None:
    return (
        db.query(UserFundPosition)
        .filter(UserFundPosition.user_id == int(user_id))
        .filter(UserFundPosition.fund_id == int(fund_id))
        .with_for_update()
        .first()
    )


def _lock_active_user_wallet(
    db: Session,
    *,
    user_id: int,
) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == int(user_id))
        .filter(UserWallet.blockchain == "BSC")
        .filter(UserWallet.is_active.is_(True))
        .order_by(UserWallet.id.asc())
        .with_for_update()
        .first()
    )
    if wallet is None:
        raise NegativeFinalizationError(
            f"Active BSC user wallet not found for user_id={user_id}"
        )

    return wallet


def _validate_positions_and_wallets(
    db: Session,
    *,
    fund_id: int,
    buy_orders: list[FundOrder],
    redeem_orders: list[FundOrder],
) -> dict[str, Any]:
    redeem_positions: dict[int, UserFundPosition] = {}
    buy_positions: dict[
        int,
        UserFundPosition | None,
    ] = {}
    buy_wallets: dict[int, UserWallet] = {}

    positions_before: dict[str, Any] = {}
    wallets_before: dict[str, Any] = {}

    for order in redeem_orders:
        position = _lock_position(db, user_id=int(order.user_id), fund_id=int(fund_id))
        if position is None:
            raise NegativeFinalizationError(
                f"Missing user fund position for redeem order {order.id}"
            )

        redeem_shares = _share_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )
        position_shares = _share_4dp(
            position.shares,
            field_name=(
                f"position_{order.user_id}_{fund_id}"
                "_shares"
            ),
        )
        position_reserved = _share_4dp(
            position.shares_reserved or ZERO,
            field_name=(
                f"position_{order.user_id}_{fund_id}"
                "_shares_reserved"
            ),
        )

        if position_shares < redeem_shares:
            raise NegativeFinalizationError(
                f"Insufficient position shares for redeem order {order.id}"
            )

        if position_reserved < redeem_shares:
            raise NegativeFinalizationError(
                f"Insufficient shares_reserved for redeem order {order.id}"
            )

        redeem_positions[int(order.id)] = position
        positions_before[_position_key(int(order.user_id), int(fund_id))] = {
            "user_id": int(order.user_id),
            "fund_id": int(fund_id),
            "shares": position.shares,
            "shares_reserved": position.shares_reserved,
        }

    for order in buy_orders:
        wallet = _lock_active_user_wallet(db, user_id=int(order.user_id))
        if dec(wallet.usdt_reserved or ZERO) < dec(order.amount_usdt):
            raise NegativeFinalizationError(
                f"Insufficient usdt_reserved for buy order {order.id}"
            )

        position = _lock_position(
            db,
            user_id=int(order.user_id),
            fund_id=int(fund_id),
        )

        if position is not None:
            _share_4dp(
                position.shares,
                field_name=(
                    f"position_{order.user_id}_{fund_id}"
                    "_shares"
                ),
            )
            _share_4dp(
                position.shares_reserved or ZERO,
                field_name=(
                    f"position_{order.user_id}_{fund_id}"
                    "_shares_reserved"
                ),
            )

        buy_wallets[int(order.id)] = wallet
        buy_positions[int(order.id)] = position

        positions_before[
            _position_key(
                int(order.user_id),
                int(fund_id),
            )
        ] = {
            "user_id": int(order.user_id),
            "fund_id": int(fund_id),
            "shares": (
                position.shares
                if position is not None
                else ZERO
            ),
            "shares_reserved": (
                position.shares_reserved
                if position is not None
                else ZERO
            ),
            "position_existed_before": (
                position is not None
            ),
        }
        wallets_before[_wallet_key(int(wallet.id))] = {
            "user_id": int(order.user_id),
            "wallet_id": int(wallet.id),
            "address": wallet.address,
            "usdt_balance": wallet.usdt_balance,
            "usdt_reserved": wallet.usdt_reserved,
        }

    return {
        "redeem_positions": redeem_positions,
        "buy_positions": buy_positions,
        "buy_wallets": buy_wallets,
        "positions_before": positions_before,
        "user_wallet_reserves_before": wallets_before,
    }


def _validate_share_totals(
    *,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    total_buy_shares: Decimal,
    total_redeem_shares: Decimal,
) -> dict[str, Decimal]:
    total_buy_shares = _share_4dp(
        total_buy_shares,
        field_name="total_buy_shares",
    )
    total_redeem_shares = _share_4dp(
        total_redeem_shares,
        field_name="total_redeem_shares",
    )
    shares_outstanding_before = _share_4dp(
        settlement_batch.shares_outstanding_before,
        field_name="shares_outstanding_before",
    )

    planned_issue = _share_4dp(
        settlement_batch.planned_shares_to_issue
        or ZERO,
        field_name="planned_shares_to_issue",
    )
    planned_redeem = _share_4dp(
        settlement_batch.planned_shares_to_redeem
        or ZERO,
        field_name="planned_shares_to_redeem",
    )
    planned_net_change = _share_4dp(
        settlement_batch.planned_net_shares_change
        or ZERO,
        field_name="planned_net_shares_change",
        allow_negative=True,
    )

    actual_net_change = _share_4dp(
        total_buy_shares - total_redeem_shares,
        field_name="actual_net_shares_change",
        allow_negative=True,
    )
    shares_outstanding_after = _share_4dp(
        shares_outstanding_before
        + actual_net_change,
        field_name="shares_outstanding_after",
    )
    current_fund_shares = _share_4dp(
        fund.shares_outstanding_current,
        field_name="fund_shares_outstanding_current",
    )

    if planned_issue != total_buy_shares:
        raise NegativeShareQuantityError(
            "Planned shares to issue mismatch"
        )

    if planned_redeem != total_redeem_shares:
        raise NegativeShareQuantityError(
            "Planned shares to redeem mismatch"
        )

    if planned_net_change != actual_net_change:
        raise NegativeShareQuantityError(
            "Planned net shares change mismatch"
        )

    if current_fund_shares != shares_outstanding_before:
        raise NegativeShareQuantityError(
            "Fund shares_outstanding_current mismatch"
        )

    return {
        "shares_outstanding_before": (
            shares_outstanding_before
        ),
        "shares_outstanding_after": (
            shares_outstanding_after
        ),
        "actual_net_shares_change": (
            actual_net_change
        ),
        "planned_net_shares_change": (
            planned_net_change
        ),
    }


def _prepare_accounting_context(
    db: Session,
    *,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
) -> dict[str, Any]:
    orders = _load_relevant_orders(db, settlement_batch_id=int(settlement_batch.id))

    if _detect_partial_finalization(orders=orders, existing_finalization=None):
        raise NegativeFinalizationError(
            "Partial finalization detected: some orders are success and some are not"
        )

    buy_orders, redeem_orders = _split_orders(orders)

    redeem_validation = _validate_redeem_orders(
        redeem_orders=redeem_orders,
        payout_batch=payout_batch,
        payout_legs=payout_legs,
    )
    buy_validation = _validate_buy_orders(
        buy_orders=buy_orders,
        settlement_price_usdt=dec(settlement_batch.settlement_price_usdt),
    )

    position_wallet_validation = _validate_positions_and_wallets(
        db,
        fund_id=int(fund.id),
        buy_orders=buy_orders,
        redeem_orders=redeem_orders,
    )

    share_validation = _validate_share_totals(
        settlement_batch=settlement_batch,
        fund=fund,
        total_buy_shares=buy_validation["total_buy_shares"],
        total_redeem_shares=redeem_validation["total_redeem_shares"],
    )

    return {
        "orders": orders,
        "buy_orders": buy_orders,
        "redeem_orders": redeem_orders,
        "redeem_validation": redeem_validation,
        "buy_validation": buy_validation,
        "position_wallet_validation": position_wallet_validation,
        "share_validation": share_validation,
    }


def _positions_after_json(
    *,
    positions_before: dict[str, Any],
    redeem_positions: dict[int, UserFundPosition],
    buy_positions: dict[int, UserFundPosition],
    orders: list[FundOrder],
    fund_id: int,
) -> dict[str, Any]:
    result = dict(positions_before)

    for order in orders:
        position = None
        if int(order.id) in redeem_positions:
            position = redeem_positions[int(order.id)]
        if int(order.id) in buy_positions:
            position = buy_positions[int(order.id)]

        if position is None:
            continue

        result[_position_key(int(order.user_id), int(fund_id))] = {
            "user_id": int(order.user_id),
            "fund_id": int(fund_id),
            "shares": position.shares,
            "shares_reserved": position.shares_reserved,
        }

    return result


def _wallet_reserves_after_json(
    *,
    wallets_before: dict[str, Any],
    buy_wallets: dict[int, UserWallet],
) -> dict[str, Any]:
    result = dict(wallets_before)

    for wallet in buy_wallets.values():
        result[_wallet_key(int(wallet.id))] = {
            "user_id": int(wallet.user_id),
            "wallet_id": int(wallet.id),
            "address": wallet.address,
            "usdt_balance": wallet.usdt_balance,
            "usdt_reserved": wallet.usdt_reserved,
        }

    return result


def _apply_redeem_accounting(
    *,
    redeem_orders: list[FundOrder],
    redeem_positions: dict[int, UserFundPosition],
    executed_at,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []

    for order in redeem_orders:
        position = redeem_positions.get(int(order.id))
        if position is None:
            raise NegativeFinalizationError(
                f"Redeem position not locked for order {order.id}"
            )

        position_shares_before = dec(position.shares)
        position_reserved_before = dec(position.shares_reserved or ZERO)

        redeem_shares = _share_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )

        position.shares = _share_4dp(
            position_shares_before - redeem_shares,
            field_name=(
                f"position_{order.user_id}_shares_after"
            ),
        )
        position.shares_reserved = _share_4dp(
            position_reserved_before
            - redeem_shares,
            field_name=(
                f"position_{order.user_id}"
                "_shares_reserved_after"
            ),
        )

        order_amount_before = order.amount_usdt
        order_price_before = order.price_usdt
        order_status_before = order.status
        order_executed_before = order.executed_at

        order.amount_usdt = dec(order.net_user_payout_usdt)
        order.price_usdt = dec(order.net_price_usdt)
        order.status = ORDER_STATUS_SUCCESS
        order.executed_at = executed_at

        updates.append(
            {
                "order_id": int(order.id),
                "side": ORDER_SIDE_REDEEM,
                "user_id": int(order.user_id),
                "shares": order.shares,
                "amount_usdt_before": order_amount_before,
                "amount_usdt_after": order.amount_usdt,
                "price_usdt_before": order_price_before,
                "price_usdt_after": order.price_usdt,
                "status_before": order_status_before,
                "status_after": order.status,
                "executed_at_before": order_executed_before,
                "executed_at_after": order.executed_at,
                "gross_redeem_usdt": order.gross_redeem_usdt,
                "success_fee_usdt": order.success_fee_usdt,
                "management_fee_usdt": order.management_fee_usdt,
                "partial_month_fee_usdt": order.partial_month_fee_usdt,
                "net_user_payout_usdt": order.net_user_payout_usdt,
                "net_price_usdt": order.net_price_usdt,
                "position_shares_before": position_shares_before,
                "position_shares_after": position.shares,
                "position_shares_reserved_before": position_reserved_before,
                "position_shares_reserved_after": position.shares_reserved,
            }
        )

    return updates


def _apply_buy_accounting(
    db: Session,
    *,
    fund_id: int,
    buy_orders: list[FundOrder],
    buy_positions: dict[
        int,
        UserFundPosition | None,
    ],
    buy_wallets: dict[int, UserWallet],
    computed_shares_by_order_id: dict[
        int,
        Decimal,
    ],
    settlement_price_usdt: Decimal,
    executed_at,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    created_positions: dict[
        tuple[int, int],
        UserFundPosition,
    ] = {}

    for order in buy_orders:
        position = buy_positions.get(int(order.id))
        wallet = buy_wallets.get(int(order.id))
        buy_shares = computed_shares_by_order_id.get(
            int(order.id)
        )

        if wallet is None:
            raise NegativeFinalizationError(
                f"Buy wallet not locked for order {order.id}"
            )

        if buy_shares is None:
            raise NegativeFinalizationError(
                f"Buy shares not calculated for order {order.id}"
            )

        buy_shares = _share_4dp(
            buy_shares,
            field_name=f"buy_order_{order.id}_shares",
        )

        if position is None:
            position_key = (
                int(order.user_id),
                int(fund_id),
            )
            position = created_positions.get(
                position_key
            )

            if position is None:
                position = UserFundPosition(
                    user_id=int(order.user_id),
                    fund_id=int(fund_id),
                    shares=ZERO,
                    shares_reserved=ZERO,
                )
                db.add(position)
                db.flush()

                created_positions[
                    position_key
                ] = position

        buy_positions[int(order.id)] = position

        wallet_balance_before = dec(wallet.usdt_balance or ZERO)
        wallet_reserved_before = dec(wallet.usdt_reserved or ZERO)
        position_shares_before = dec(position.shares or ZERO)
        position_reserved_before = dec(position.shares_reserved or ZERO)

        wallet.usdt_reserved = _q10(wallet_reserved_before - dec(order.amount_usdt))
        position.shares = _share_4dp(
            position_shares_before + buy_shares,
            field_name=(
                f"position_{order.user_id}_shares_after"
            ),
        )

        order_shares_before = order.shares
        order_price_before = order.price_usdt
        order_status_before = order.status
        order_executed_before = order.executed_at

        order.shares = buy_shares
        order.price_usdt = settlement_price_usdt
        order.status = ORDER_STATUS_SUCCESS
        order.executed_at = executed_at

        updates.append(
            {
                "order_id": int(order.id),
                "side": ORDER_SIDE_BUY,
                "user_id": int(order.user_id),
                "amount_usdt": order.amount_usdt,
                "shares_before": order_shares_before,
                "shares_after": order.shares,
                "price_usdt_before": order_price_before,
                "price_usdt_after": order.price_usdt,
                "status_before": order_status_before,
                "status_after": order.status,
                "executed_at_before": order_executed_before,
                "executed_at_after": order.executed_at,
                "wallet_id": int(wallet.id),
                "wallet_usdt_balance_before": wallet_balance_before,
                "wallet_usdt_balance_after": wallet.usdt_balance,
                "wallet_usdt_reserved_before": wallet_reserved_before,
                "wallet_usdt_reserved_after": wallet.usdt_reserved,
                "position_shares_before": position_shares_before,
                "position_shares_after": position.shares,
                "position_shares_reserved_before": position_reserved_before,
                "position_shares_reserved_after": position.shares_reserved,
                "note": "user_wallet.usdt_balance is not double-debited in Stage 23.6",
            }
        )

    return updates


def _release_pricing_lock(
    *,
    runtime_state: FundRuntimeState | None,
    settlement_batch: FundSettlementBatch,
    unlock_ts,
) -> dict[str, Any]:
    before = {
        "runtime_found": runtime_state is not None,
        "settlement_pricing_locked_at": settlement_batch.pricing_locked_at,
        "settlement_pricing_unlocked_at": settlement_batch.pricing_unlocked_at,
    }

    if runtime_state is not None:
        before.update(
            {
                "runtime_pricing_locked": getattr(runtime_state, "pricing_locked", None),
                "runtime_pricing_lock_reason": getattr(runtime_state, "pricing_lock_reason", None),
                "runtime_pricing_lock_batch_id": getattr(runtime_state, "pricing_lock_batch_id", None),
                "runtime_pricing_locked_at": getattr(runtime_state, "pricing_locked_at", None),
                "runtime_pricing_unlocked_at": getattr(runtime_state, "pricing_unlocked_at", None),
            }
        )

        if hasattr(runtime_state, "pricing_locked"):
            runtime_state.pricing_locked = False
        if hasattr(runtime_state, "pricing_lock_reason"):
            runtime_state.pricing_lock_reason = None
        if hasattr(runtime_state, "pricing_lock_batch_id"):
            runtime_state.pricing_lock_batch_id = None
        if hasattr(runtime_state, "pricing_unlocked_at"):
            runtime_state.pricing_unlocked_at = unlock_ts
        if hasattr(runtime_state, "updated_at"):
            runtime_state.updated_at = unlock_ts

    settlement_batch.pricing_unlocked_at = unlock_ts

    after = {
        "runtime_found": runtime_state is not None,
        "settlement_pricing_locked_at": settlement_batch.pricing_locked_at,
        "settlement_pricing_unlocked_at": settlement_batch.pricing_unlocked_at,
    }

    if runtime_state is not None:
        after.update(
            {
                "runtime_pricing_locked": getattr(runtime_state, "pricing_locked", None),
                "runtime_pricing_lock_reason": getattr(runtime_state, "pricing_lock_reason", None),
                "runtime_pricing_lock_batch_id": getattr(runtime_state, "pricing_lock_batch_id", None),
                "runtime_pricing_locked_at": getattr(runtime_state, "pricing_locked_at", None),
                "runtime_pricing_unlocked_at": getattr(runtime_state, "pricing_unlocked_at", None),
            }
        )

    return {
        "before": before,
        "after": after,
        "unlock_ts": unlock_ts,
    }


def _apply_accounting_finalization(
    db: Session,
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    runtime_state: FundRuntimeState | None,
    context: dict[str, Any],
    now,
) -> None:
    if not settings.NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING:
        raise NegativeFinalizationError(
            "NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING must be true for Stage 23.6"
        )

    buy_orders = context["buy_orders"]
    redeem_orders = context["redeem_orders"]
    buy_validation = context["buy_validation"]
    redeem_validation = context["redeem_validation"]
    position_wallet_validation = context["position_wallet_validation"]
    share_validation = context["share_validation"]

    finalization.status = FINALIZATION_BATCH_STATUS_ACCOUNTING_PROCESSING
    finalization.updated_at = now

    redeem_updates = _apply_redeem_accounting(
        redeem_orders=redeem_orders,
        redeem_positions=position_wallet_validation["redeem_positions"],
        executed_at=now,
    )
    buy_updates = _apply_buy_accounting(
        db,
        fund_id=int(fund.id),
        buy_orders=buy_orders,
        buy_positions=(
            position_wallet_validation[
                "buy_positions"
            ]
        ),
        buy_wallets=(
            position_wallet_validation[
                "buy_wallets"
            ]
        ),
        computed_shares_by_order_id=(
            buy_validation[
                "computed_shares_by_order_id"
            ]
        ),
        settlement_price_usdt=dec(
            settlement_batch.settlement_price_usdt
        ),
        executed_at=now,
    )

    fund_shares_before = dec(fund.shares_outstanding_current)
    fund.shares_outstanding_current = share_validation["shares_outstanding_after"]

    finalization.order_updates_json = _json_dict(
        {
            "redeem_updates": redeem_updates,
            "buy_updates": buy_updates,
            "executed_at": now,
        }
    )
    finalization.positions_after_json = _json_dict(
        _positions_after_json(
            positions_before=position_wallet_validation["positions_before"],
            redeem_positions=position_wallet_validation["redeem_positions"],
            buy_positions=position_wallet_validation["buy_positions"],
            orders=context["orders"],
            fund_id=int(fund.id),
        )
    )
    finalization.user_wallet_reserves_after_json = _json_dict(
        _wallet_reserves_after_json(
            wallets_before=position_wallet_validation["user_wallet_reserves_before"],
            buy_wallets=position_wallet_validation["buy_wallets"],
        )
    )
    finalization.fund_update_json = _json_dict(
        {
            "fund_id": int(fund.id),
            "fund_code": fund.code,
            "shares_outstanding_current_before": fund_shares_before,
            "shares_outstanding_current_after": fund.shares_outstanding_current,
            "shares_outstanding_before_from_settlement": share_validation[
                "shares_outstanding_before"
            ],
            "shares_outstanding_after": share_validation["shares_outstanding_after"],
            "actual_net_shares_change": share_validation["actual_net_shares_change"],
        }
    )

    finalization.accounting_finalized_at = now
    finalization.status = FINALIZATION_BATCH_STATUS_ACCOUNTING_FINALIZED

    settlement_batch.accounting_finalized_at = now
    settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED
    settlement_batch.updated_at = now

    pricing_lock_json = _release_pricing_lock(
        runtime_state=runtime_state,
        settlement_batch=settlement_batch,
        unlock_ts=now,
    )

    finalization.pricing_lock_json = _json_dict(pricing_lock_json)
    finalization.pricing_unlocked_at = now
    finalization.status = FINALIZATION_BATCH_STATUS_PRICING_UNLOCKED

    settlement_batch.pricing_unlocked_at = now

    finalization.success_order_count = len(context["orders"])
    finalization.status = FINALIZATION_BATCH_STATUS_COMPLETED
    finalization.completed_at = now
    finalization.updated_at = now

    settlement_batch.status = BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
    settlement_batch.updated_at = now

    finalization.accounting_json = _json_dict(
        {
            "buy_order_count": len(buy_orders),
            "redeem_order_count": len(redeem_orders),
            "success_order_count": len(context["orders"]),
            "total_buy_usdt": buy_validation["total_buy_usdt"],
            "total_buy_shares": buy_validation["total_buy_shares"],
            "total_redeem_shares": redeem_validation["total_redeem_shares"],
            "actual_net_shares_change": share_validation["actual_net_shares_change"],
            "shares_outstanding_before": share_validation["shares_outstanding_before"],
            "shares_outstanding_after": share_validation["shares_outstanding_after"],
            "total_net_user_payout_usdt": redeem_validation[
                "total_net_user_payout_usdt"
            ],
            "total_partial_month_fee_usdt": redeem_validation[
                "total_partial_month_fee_usdt"
            ],
            "accounting_finalized_at": now,
            "pricing_unlocked_at": now,
            "orders_executed_at_equals_pricing_unlocked_at": True,
            "buy_user_wallet_usdt_balance_not_double_debited": True,
        }
    )

    finalization.reconciliation_json = _json_dict(
        {
            "ok": True,
            "payout_total_matches_redeem_orders": True,
            "payout_legs_cover_all_redeem_orders": True,
            "planned_net_shares_change_matches_actual": True,
            "fund_shares_outstanding_current_updated": True,
            "settlement_accounting_finalized_at_set": True,
            "settlement_pricing_unlocked_at_set": True,
            "order_executed_at_equals_pricing_unlocked_at": True,
            "settlement_status": settlement_batch.status,
            "no_real_bybit_calls": True,
            "no_real_bsc_calls": True,
            "no_payout_transfers": True,
            "no_nav_chart_writes": True,
            "no_server_deploy": True,
        }
    )

    finalization.report_json = _json_dict(
        {
            "stage": "23.6",
            "ok": True,
            "fund_id": int(fund.id),
            "fund_code": fund.code,
            "settlement_batch_id": int(settlement_batch.id),
            "finalization_batch_id": int(finalization.id),
            "buy_order_count": len(buy_orders),
            "redeem_order_count": len(redeem_orders),
            "success_order_count": len(context["orders"]),
            "total_buy_usdt": buy_validation["total_buy_usdt"],
            "total_buy_shares": buy_validation["total_buy_shares"],
            "total_redeem_shares": redeem_validation["total_redeem_shares"],
            "net_shares_change": share_validation["actual_net_shares_change"],
            "shares_outstanding_before": share_validation["shares_outstanding_before"],
            "shares_outstanding_after": share_validation["shares_outstanding_after"],
            "total_payout_usdt": redeem_validation["total_net_user_payout_usdt"],
            "total_partial_month_fee_usdt": redeem_validation[
                "total_partial_month_fee_usdt"
            ],
            "pricing_unlock_timestamp": now,
            "final_settlement_status": settlement_batch.status,
        }
    )

    db.flush()


def finalize_negative_net_settlement(
    db: Session,
    *,
    settlement_batch_id: int,
    now=None,
) -> NegativeFinalizationResult:
    if not settings.NEGATIVE_NET_FINALIZATION_ENABLED:
        raise NegativeFinalizationError("Negative-net finalization is disabled")

    now = _now_or_supplied(now)

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    fund = _lock_fund(db, fund_id=int(settlement_batch.fund_id))
    sale_batch = _lock_sale_batch(db, settlement_batch_id=int(settlement_batch.id))
    bybit_flow = _lock_bybit_flow(db, settlement_batch_id=int(settlement_batch.id))
    payout_batch = _lock_payout_batch(db, settlement_batch_id=int(settlement_batch.id))
    payout_legs = _lock_payout_legs(db, payout_batch_id=int(payout_batch.id))
    existing_finalization = _lock_existing_finalization(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    status_before = (
        str(existing_finalization.status) if existing_finalization is not None else None
    )

    try:
        _validate_input_state(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
            bybit_flow=bybit_flow,
            payout_batch=payout_batch,
            payout_legs=payout_legs,
            existing_finalization=existing_finalization,
        )

        if (
            existing_finalization is not None
            and existing_finalization.status == FINALIZATION_BATCH_STATUS_COMPLETED
            and settlement_batch.status == BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
        ):
            return _result_from_completed(
                finalization=existing_finalization,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                idempotent=True,
            )

        finalization = _new_or_existing_finalization(
            db,
            existing=existing_finalization,
            settlement_batch=settlement_batch,
            payout_batch=payout_batch,
            bybit_flow=bybit_flow,
            sale_batch=sale_batch,
            fund=fund,
            now=now,
        )
        status_before = str(finalization.status)

        finalization.status = FINALIZATION_BATCH_STATUS_VALIDATING
        finalization.settlement_price_usdt = dec(settlement_batch.settlement_price_usdt)
        finalization.shares_outstanding_before = dec(
            settlement_batch.shares_outstanding_before
        )
        finalization.payout_batch_id = int(payout_batch.id)
        finalization.bybit_flow_id = int(bybit_flow.id)
        finalization.sale_batch_id = int(sale_batch.id)
        finalization.fund_id = int(fund.id)
        finalization.finalization_started_at = now
        finalization.updated_at = now
        finalization.validation_json = _json_dict(
            {
                "stage": "23.6",
                "settlement_status": settlement_batch.status,
                "sale_batch_status": sale_batch.status,
                "bybit_flow_status": bybit_flow.status,
                "payout_batch_status": payout_batch.status,
                "payout_leg_count": len(payout_legs),
                "pricing_locked_at": settlement_batch.pricing_locked_at,
                "pricing_unlocked_at": settlement_batch.pricing_unlocked_at,
                "no_real_bybit_calls": True,
                "no_real_bsc_calls": True,
                "no_payout_transfers": True,
                "no_nav_chart_writes": True,
            }
        )

        context = _prepare_accounting_context(
            db,
            settlement_batch=settlement_batch,
            fund=fund,
            payout_batch=payout_batch,
            payout_legs=payout_legs,
        )

        finalization.buy_order_count = len(context["buy_orders"])
        finalization.redeem_order_count = len(context["redeem_orders"])
        finalization.success_order_count = 0
        finalization.total_buy_usdt = context["buy_validation"]["total_buy_usdt"]
        finalization.total_buy_shares = context["buy_validation"]["total_buy_shares"]
        finalization.total_redeem_shares = context["redeem_validation"]["total_redeem_shares"]
        finalization.planned_net_shares_change = context["share_validation"][
            "planned_net_shares_change"
        ]
        finalization.actual_net_shares_change = context["share_validation"][
            "actual_net_shares_change"
        ]
        finalization.shares_outstanding_after = context["share_validation"][
            "shares_outstanding_after"
        ]
        finalization.total_net_user_payout_usdt = context["redeem_validation"][
            "total_net_user_payout_usdt"
        ]
        finalization.total_partial_month_fee_usdt = context["redeem_validation"][
            "total_partial_month_fee_usdt"
        ]
        finalization.positions_before_json = _json_dict(
            context["position_wallet_validation"]["positions_before"]
        )
        finalization.user_wallet_reserves_before_json = _json_dict(
            context["position_wallet_validation"]["user_wallet_reserves_before"]
        )
        finalization.validation_json = _json_dict(
            {
                **(finalization.validation_json or {}),
                "orders_loaded": len(context["orders"]),
                "buy_order_count": len(context["buy_orders"]),
                "redeem_order_count": len(context["redeem_orders"]),
                "redeem_validation": context["redeem_validation"],
                "buy_validation": {
                    "total_buy_usdt": context["buy_validation"]["total_buy_usdt"],
                    "total_buy_shares": context["buy_validation"]["total_buy_shares"],
                    "computed_shares_by_order_id": context["buy_validation"][
                        "computed_shares_by_order_id"
                    ],
                },
                "share_validation": context["share_validation"],
            }
        )
        finalization.updated_at = now

        runtime_state = _lock_runtime_state(db, fund_id=int(fund.id))

        _apply_accounting_finalization(
            db,
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            runtime_state=runtime_state,
            context=context,
            now=now,
        )

        return NegativeFinalizationResult(
            ok=True,
            finalization_batch_id=int(finalization.id),
            settlement_batch_id=int(settlement_batch.id),
            payout_batch_id=int(payout_batch.id),
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=finalization.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            buy_order_count=finalization.buy_order_count,
            redeem_order_count=finalization.redeem_order_count,
            success_order_count=finalization.success_order_count,
            shares_outstanding_before=str(finalization.shares_outstanding_before),
            shares_outstanding_after=str(finalization.shares_outstanding_after),
            total_buy_usdt=str(finalization.total_buy_usdt),
            total_buy_shares=str(finalization.total_buy_shares),
            total_redeem_shares=str(finalization.total_redeem_shares),
            planned_net_shares_change=str(finalization.planned_net_shares_change),
            actual_net_shares_change=str(finalization.actual_net_shares_change),
            total_net_user_payout_usdt=str(finalization.total_net_user_payout_usdt),
            total_partial_month_fee_usdt=str(finalization.total_partial_month_fee_usdt),
            accounting_finalized_at=finalization.accounting_finalized_at.isoformat(),
            pricing_unlocked_at=finalization.pricing_unlocked_at.isoformat(),
            diagnostics={
                "finalized": True,
                "no_real_bybit_calls": True,
                "no_real_bsc_calls": True,
                "no_payout_transfers": True,
                "no_nav_chart_writes": True,
                "no_server_deploy": True,
            },
        )

    except NegativeShareQuantityError as exc:
        if (
            "finalization" not in locals()
            or finalization is None
        ):
            raise

        _mark_share_failed_orders(
            db,
            settlement_batch_id=int(
                settlement_batch.id
            ),
            error=str(exc),
        )

        return _set_failed(
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            error=str(exc),
            now=now,
            diagnostics={
                "share_quantity_failure": True,
                "share_quantum": "0.0001",
                "rounding_mode": "ROUND_DOWN",
            },
        )

    except NegativeFinalizationError as exc:
        if "finalization" not in locals() or finalization is None:
            raise

        return _set_failed(
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )