from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    FundOrder,
    UserFundPosition,
)
from app.settlement.negative_external_state import (
    NegativeExternalStateError,
    inspect_negative_external_state,
)
from app.settlement.share_quantity import (
    ShareQuantityError,
    require_share_quantity_4dp_aligned,
)
from app.settlement.statuses import (
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_SUCCESS,
)


ZERO = Decimal("0")


class RedeemReserveReleaseError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: object) -> Decimal:
    if value is None:
        return ZERO

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


def _append_order_error(
    order: FundOrder,
    *,
    message: str,
) -> None:
    clean_message = str(message or "").strip()

    if not clean_message:
        return

    existing = str(order.error or "").strip()

    if clean_message in existing:
        return

    order.error = (
        f"{existing}; {clean_message}"
        if existing
        else clean_message
    )


def release_redeem_reserve_if_safe(
    db: Session,
    *,
    order_id: int,
    reason: str,
) -> Decimal:
    clean_reason = str(reason or "").strip()

    if not clean_reason:
        raise RedeemReserveReleaseError(
            "Redeem reserve release reason is required"
        )

    order = (
        db.query(FundOrder)
        .filter(
            FundOrder.id == int(order_id)
        )
        .with_for_update()
        .first()
    )

    if order is None:
        raise RedeemReserveReleaseError(
            "Fund order not found: "
            f"order_id={order_id}"
        )

    if str(order.side or "") != ORDER_SIDE_REDEEM:
        raise RedeemReserveReleaseError(
            "Reserve release is allowed only for redeem orders: "
            f"order_id={order.id} side={order.side}"
        )

    try:
        redeem_shares = (
            require_share_quantity_4dp_aligned(
                order.shares,
                field_name=(
                    f"redeem_order_{order.id}_shares"
                ),
            )
        )
    except ShareQuantityError as exc:
        raise RedeemReserveReleaseError(
            str(exc)
        ) from exc

    if redeem_shares <= ZERO:
        raise RedeemReserveReleaseError(
            "Redeem order shares must be positive: "
            f"order_id={order.id} "
            f"shares={redeem_shares}"
        )

    released_before = _dec(
        order.redeem_reserve_released_shares
    )

    if released_before == redeem_shares:
        return ZERO

    if released_before != ZERO:
        raise RedeemReserveReleaseError(
            "Inconsistent prior redeem reserve release: "
            f"order_id={order.id} "
            f"expected={redeem_shares} "
            f"actual={released_before}"
        )

    if str(order.status or "") == ORDER_STATUS_SUCCESS:
        raise RedeemReserveReleaseError(
            "Redeem reserve cannot be released for success order: "
            f"order_id={order.id}"
        )

    if order.executed_at is not None:
        raise RedeemReserveReleaseError(
            "Redeem reserve cannot be released after order execution: "
            f"order_id={order.id} "
            f"executed_at={order.executed_at}"
        )

    if order.settlement_batch_id is None:
        raise RedeemReserveReleaseError(
            "Redeem reserve release requires settlement batch: "
            f"order_id={order.id}"
        )

    try:
        external_state = (
            inspect_negative_external_state(
                db,
                settlement_batch_id=int(
                    order.settlement_batch_id
                ),
            )
        )
    except NegativeExternalStateError as exc:
        raise RedeemReserveReleaseError(
            str(exc)
        ) from exc

    if external_state.accounting_finalized:
        block_message = (
            "redeem_reserve_release_blocked:"
            "accounting_finalized"
        )
        _append_order_error(
            order,
            message=block_message,
        )
        db.add(order)
        db.flush()

        raise RedeemReserveReleaseError(
            block_message
        )

    if not external_state.safe_to_release_reserves:
        reasons = (
            ",".join(external_state.reasons)
            or "external_state_not_proven_safe"
        )

        block_message = (
            "redeem_reserve_release_blocked:"
            f"{reasons}"
        )

        _append_order_error(
            order,
            message=block_message,
        )
        db.add(order)
        db.flush()

        raise RedeemReserveReleaseError(
            block_message
        )

    position = (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id
            == int(order.user_id),
            UserFundPosition.fund_id
            == int(order.fund_id),
        )
        .with_for_update()
        .first()
    )

    if position is None:
        raise RedeemReserveReleaseError(
            "User fund position not found: "
            f"order_id={order.id} "
            f"user_id={order.user_id} "
            f"fund_id={order.fund_id}"
        )

    try:
        position_shares = (
            require_share_quantity_4dp_aligned(
                position.shares,
                field_name=(
                    f"position_{position.user_id}_"
                    f"{position.fund_id}_shares"
                ),
            )
        )
        reserved_before = (
            require_share_quantity_4dp_aligned(
                position.shares_reserved,
                field_name=(
                    f"position_{position.user_id}_"
                    f"{position.fund_id}_shares_reserved"
                ),
            )
        )
    except ShareQuantityError as exc:
        raise RedeemReserveReleaseError(
            str(exc)
        ) from exc

    if position_shares < redeem_shares:
        raise RedeemReserveReleaseError(
            "Position shares are below redeem order shares: "
            f"order_id={order.id} "
            f"position_shares={position_shares} "
            f"redeem_shares={redeem_shares}"
        )

    if reserved_before < redeem_shares:
        raise RedeemReserveReleaseError(
            "Exact redeem reserve release is impossible: "
            f"order_id={order.id} "
            f"shares_reserved={reserved_before} "
            f"required={redeem_shares}"
        )

    released_at = utcnow()

    position.shares_reserved = (
        reserved_before
        - redeem_shares
    )

    order.redeem_reserve_released_shares = (
        redeem_shares
    )
    order.redeem_reserve_released_at = (
        released_at
    )
    order.redeem_reserve_release_reason = (
        clean_reason
    )

    _append_order_error(
        order,
        message=(
            "redeem_reserve_released:"
            f"shares={redeem_shares}; "
            f"reason={clean_reason}"
        ),
    )

    db.add(position)
    db.add(order)
    db.flush()

    return redeem_shares