from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    FundOrder,
    FundSettlementBatch,
)
from app.settlement.buy_reserve_service import (
    BuyReserveReleaseError,
    release_buy_reserve_if_safe,
)
from app.settlement.negative_external_state import (
    NegativeExternalState,
    inspect_negative_external_state,
)
from app.settlement.pricing_lock import (
    get_runtime_state_for_update,
    unlock_pricing_for_fund,
)
from app.settlement.redeem_reserve_service import (
    RedeemReserveReleaseError,
    release_redeem_reserve_if_safe,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SUCCESS,
)


ZERO = Decimal("0")


class NegativePreExternalFailureError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativePreExternalFailureResult:
    settlement_batch_id: int
    status: str
    source: str
    error: str
    external_state: NegativeExternalState
    buy_reserve_released_usdt: Decimal
    redeem_reserve_released_shares: Decimal
    reserve_release_blocked: tuple[str, ...]
    pricing_unlocked: bool
    pricing_unlock_blocked: str | None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: object) -> Decimal:
    if value is None:
        return ZERO

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


def _append_error(
    obj: FundOrder | FundSettlementBatch,
    *,
    message: str,
) -> None:
    clean_message = str(message or "").strip()

    if not clean_message:
        return

    existing = str(obj.error or "").strip()

    if clean_message in existing:
        return

    obj.error = (
        f"{existing}; {clean_message}"
        if existing
        else clean_message
    )


def _external_evidence_message(
    item: dict[str, Any],
) -> str:
    return (
        "external_evidence:"
        f"action={item.get('action')}:"
        f"model={item.get('model')}:"
        f"row_id={item.get('row_id')}:"
        f"field={item.get('field')}:"
        f"value={item.get('value')}"
    )


def _load_batch_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(
            FundSettlementBatch.id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )

    if batch is None:
        raise NegativePreExternalFailureError(
            "Settlement batch not found: "
            f"settlement_batch_id={settlement_batch_id}"
        )

    return batch


def _load_orders_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(settlement_batch_id)
        )
        .order_by(FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _reserve_release_is_complete(
    order: FundOrder,
) -> tuple[bool, str | None]:
    side = str(order.side or "").strip()

    if side == ORDER_SIDE_BUY:
        amount_usdt = _dec(order.amount_usdt)
        released_usdt = _dec(
            order.buy_reserve_released_usdt
        )

        if amount_usdt <= ZERO:
            return (
                False,
                "buy_order_invalid_amount:"
                f"order_id={order.id}:"
                f"amount_usdt={amount_usdt}",
            )

        if released_usdt != amount_usdt:
            return (
                False,
                "buy_reserve_not_fully_released:"
                f"order_id={order.id}:"
                f"expected={amount_usdt}:"
                f"actual={released_usdt}",
            )

        return True, None

    if side == ORDER_SIDE_REDEEM:
        shares = _dec(order.shares)
        released_shares = _dec(
            order.redeem_reserve_released_shares
        )

        if shares <= ZERO:
            return (
                False,
                "redeem_order_invalid_shares:"
                f"order_id={order.id}:"
                f"shares={shares}",
            )

        if released_shares != shares:
            return (
                False,
                "redeem_reserve_not_fully_released:"
                f"order_id={order.id}:"
                f"expected={shares}:"
                f"actual={released_shares}",
            )

        return True, None

    return (
        False,
        "unsupported_order_side:"
        f"order_id={order.id}:"
        f"side={side}",
    )


def fail_negative_batch_pre_external(
    db: Session,
    *,
    settlement_batch_id: int,
    error: str,
    source: str,
) -> NegativePreExternalFailureResult:
    clean_error = str(error or "").strip()
    clean_source = str(source or "").strip()

    if not clean_error:
        raise NegativePreExternalFailureError(
            "Pre-external failure error is required"
        )

    if not clean_source:
        raise NegativePreExternalFailureError(
            "Pre-external failure source is required"
        )

    batch = _load_batch_for_update(
        db,
        settlement_batch_id=settlement_batch_id,
    )
    orders = _load_orders_for_update(
        db,
        settlement_batch_id=settlement_batch_id,
    )

    external_state = inspect_negative_external_state(
        db,
        settlement_batch_id=settlement_batch_id,
    )

    now = utcnow()
    root_error = (
        "pre_external_failure:"
        f"source={clean_source}:"
        f"error={clean_error}"
    )

    batch.status = (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )
    batch.updated_at = now
    _append_error(
        batch,
        message=root_error,
    )

    for order in orders:
        if str(order.status or "") != ORDER_STATUS_SUCCESS:
            order.status = (
                ORDER_STATUS_FAILED_REQUIRES_REVIEW
            )
            order.settlement_locked_at = (
                order.settlement_locked_at
                or now
            )
            _append_error(
                order,
                message=root_error,
            )
            db.add(order)

    db.add(batch)
    db.flush()

    released_buy_usdt = ZERO
    released_redeem_shares = ZERO
    blocked: list[str] = []

    if (
        external_state.safe_to_release_reserves
        and not external_state.accounting_finalized
    ):
        for order in orders:
            if str(order.status or "") == ORDER_STATUS_SUCCESS:
                continue

            if str(order.side or "") == ORDER_SIDE_BUY:
                try:
                    released_buy_usdt += (
                        release_buy_reserve_if_safe(
                            db,
                            order_id=int(order.id),
                            reason=(
                                f"{clean_source}:"
                                f"{clean_error}"
                            ),
                        )
                    )
                except BuyReserveReleaseError as exc:
                    blocked.append(
                        "buy_reserve_release_blocked:"
                        f"order_id={order.id}:"
                        f"{exc}"
                    )

            elif str(order.side or "") == ORDER_SIDE_REDEEM:
                try:
                    released_redeem_shares += (
                        release_redeem_reserve_if_safe(
                            db,
                            order_id=int(order.id),
                            reason=(
                                f"{clean_source}:"
                                f"{clean_error}"
                            ),
                        )
                    )
                except RedeemReserveReleaseError as exc:
                    blocked.append(
                        "redeem_reserve_release_blocked:"
                        f"order_id={order.id}:"
                        f"{exc}"
                    )

            else:
                blocked.append(
                    "reserve_release_blocked:"
                    f"order_id={order.id}:"
                    "unsupported_side="
                    f"{order.side}"
                )
    else:
        reasons = (
            ",".join(external_state.reasons)
            or "external_state_not_proven_safe"
        )

        blocked.append(
            "reserve_release_blocked:"
            f"external_state={reasons}"
        )

        for item in external_state.evidence:
            evidence_message = (
                _external_evidence_message(item)
            )

            if evidence_message not in blocked:
                blocked.append(
                    evidence_message
                )

    for order in orders:
        if str(order.status or "") == ORDER_STATUS_SUCCESS:
            continue

        complete, completion_error = (
            _reserve_release_is_complete(order)
        )

        if not complete and completion_error:
            if completion_error not in blocked:
                blocked.append(completion_error)

    for block_message in blocked:
        _append_error(
            batch,
            message=block_message,
        )

        for order in orders:
            if str(order.status or "") == ORDER_STATUS_SUCCESS:
                continue

            _append_error(
                order,
                message=block_message,
            )
            db.add(order)

    pricing_unlocked = False
    pricing_unlock_blocked: str | None = None

    refreshed_external_state = (
        inspect_negative_external_state(
            db,
            settlement_batch_id=settlement_batch_id,
        )
    )

    can_unlock = (
        refreshed_external_state.safe_to_unlock_pricing
        and not refreshed_external_state.accounting_finalized
        and not blocked
    )

    if can_unlock:
        runtime_state = get_runtime_state_for_update(
            db,
            fund_id=int(batch.fund_id),
        )

        if runtime_state is None:
            if (
                batch.pricing_locked_at is not None
                and batch.pricing_unlocked_at is None
            ):
                pricing_unlock_blocked = (
                    "pricing_unlock_blocked:"
                    "runtime_state_missing_for_locked_batch"
                )

        elif bool(runtime_state.pricing_locked):
            lock_batch_id = (
                int(runtime_state.pricing_lock_batch_id)
                if runtime_state.pricing_lock_batch_id
                is not None
                else None
            )

            if lock_batch_id != int(batch.id):
                pricing_unlock_blocked = (
                    "pricing_unlock_blocked:"
                    "lock_identity_mismatch:"
                    f"expected_batch_id={batch.id}:"
                    f"actual_batch_id={lock_batch_id}"
                )
            else:
                unlocked_state = unlock_pricing_for_fund(
                    db,
                    fund_id=int(batch.fund_id),
                    batch_id=int(batch.id),
                )

                batch.pricing_unlocked_at = (
                    (
                        unlocked_state.pricing_unlocked_at
                        if unlocked_state is not None
                        else None
                    )
                    or utcnow()
                )
                batch.updated_at = utcnow()
                pricing_unlocked = True

        elif batch.pricing_unlocked_at is not None:
            pricing_unlocked = True

        elif batch.pricing_locked_at is not None:
            pricing_unlock_blocked = (
                "pricing_unlock_blocked:"
                "runtime_state_is_unlocked_but_batch_"
                "has_no_pricing_unlocked_at"
            )

    else:
        if blocked:
            pricing_unlock_blocked = (
                "pricing_unlock_blocked:"
                "reserve_release_incomplete"
            )
        elif refreshed_external_state.accounting_finalized:
            pricing_unlock_blocked = (
                "pricing_unlock_blocked:"
                "accounting_finalized"
            )
        else:
            reasons = (
                ",".join(
                    refreshed_external_state.reasons
                )
                or "external_state_not_proven_safe"
            )
            pricing_unlock_blocked = (
                "pricing_unlock_blocked:"
                f"external_state={reasons}"
            )

    if pricing_unlock_blocked:
        _append_error(
            batch,
            message=pricing_unlock_blocked,
        )

        for order in orders:
            if str(order.status or "") == ORDER_STATUS_SUCCESS:
                continue

            _append_error(
                order,
                message=pricing_unlock_blocked,
            )
            db.add(order)

    db.add(batch)
    db.flush()

    return NegativePreExternalFailureResult(
        settlement_batch_id=int(batch.id),
        status=str(batch.status),
        source=clean_source,
        error=clean_error,
        external_state=refreshed_external_state,
        buy_reserve_released_usdt=(
            released_buy_usdt
        ),
        redeem_reserve_released_shares=(
            released_redeem_shares
        ),
        reserve_release_blocked=tuple(blocked),
        pricing_unlocked=pricing_unlocked,
        pricing_unlock_blocked=(
            pricing_unlock_blocked
        ),
    )