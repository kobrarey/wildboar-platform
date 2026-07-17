from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    FundOrder,
    FundSettlementTransfer,
    UserWallet,
)
from app.settlement.statuses import (
    ORDER_SIDE_BUY,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_FAILED,
    TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
    TRANSFER_STATUS_PENDING_CONFIRMATION,
    TRANSFER_STATUS_PREPARED,
    TRANSFER_STATUS_PROCESSING,
    TRANSFER_STATUS_SENT,
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
)


ZERO = Decimal("0")


class BuyReserveReleaseError(RuntimeError):
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
    existing = str(order.error or "").strip()

    if existing:
        order.error = f"{existing}; {message}"
    else:
        order.error = message


def release_buy_reserve_if_safe(
    db: Session,
    *,
    order_id: int,
    reason: str,
    proven_failed_transfer_id: int | None = None,
    proven_receipt_status: int | None = None,
) -> Decimal:
    order = (
        db.query(FundOrder)
        .filter(FundOrder.id == int(order_id))
        .with_for_update()
        .first()
    )

    if order is None:
        raise BuyReserveReleaseError(
            f"Fund order not found: order_id={order_id}"
        )

    if str(order.side) != ORDER_SIDE_BUY:
        raise BuyReserveReleaseError(
            "Reserve release is allowed only for buy orders: "
            f"order_id={order.id} side={order.side}"
        )

    amount_usdt = _dec(order.amount_usdt)

    if amount_usdt <= ZERO:
        raise BuyReserveReleaseError(
            "Buy order amount must be positive: "
            f"order_id={order.id} amount_usdt={amount_usdt}"
        )

    released_before = _dec(
        order.buy_reserve_released_usdt
    )

    if released_before == amount_usdt:
        return ZERO

    if released_before != ZERO:
        raise BuyReserveReleaseError(
            "Inconsistent prior reserve release amount: "
            f"order_id={order.id} "
            f"expected={amount_usdt} "
            f"actual={released_before}"
        )

    if order.collection_confirmed_at is not None:
        raise BuyReserveReleaseError(
            "Buy reserve cannot be released after collection confirmation: "
            f"order_id={order.id}"
        )

    transfers = (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.order_id == int(order.id),
            FundSettlementTransfer.transfer_type
            == TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
        )
        .with_for_update()
        .all()
    )

    ambiguous_statuses = {
        TRANSFER_STATUS_PREPARED,
        TRANSFER_STATUS_PROCESSING,
        TRANSFER_STATUS_SENT,
        TRANSFER_STATUS_CONFIRMED,
        TRANSFER_STATUS_PENDING_CONFIRMATION,
    }

    failed_statuses = {
        TRANSFER_STATUS_FAILED,
        TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
    }

    for transfer in transfers:
        is_proven_failed = (
            proven_failed_transfer_id is not None
            and int(transfer.id)
            == int(proven_failed_transfer_id)
            and proven_receipt_status == 0
            and str(transfer.status) in failed_statuses
        )

        if is_proven_failed:
            continue

        if str(transfer.status) in ambiguous_statuses:
            raise BuyReserveReleaseError(
                "Buy reserve release blocked by ambiguous transfer status: "
                f"order_id={order.id} "
                f"transfer_id={transfer.id} "
                f"status={transfer.status}"
            )

        if (
            transfer.tx_hash
            or transfer.prepared_tx_hash
            or transfer.prepared_raw_tx
            or transfer.broadcast_at is not None
            or transfer.confirmed_at is not None
        ):
            raise BuyReserveReleaseError(
                "Buy reserve release blocked because the USDT transfer "
                "may have been broadcast: "
                f"order_id={order.id} "
                f"transfer_id={transfer.id}"
            )

    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == int(order.user_id),
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .with_for_update()
        .first()
    )

    if wallet is None:
        raise BuyReserveReleaseError(
            "Active BSC user wallet not found: "
            f"order_id={order.id} user_id={order.user_id}"
        )

    reserved_before = _dec(wallet.usdt_reserved)

    if reserved_before < amount_usdt:
        raise BuyReserveReleaseError(
            "Exact buy reserve release is impossible: "
            f"order_id={order.id} "
            f"reserved={reserved_before} "
            f"required={amount_usdt}"
        )

    wallet.usdt_reserved = reserved_before - amount_usdt

    order.buy_reserve_released_usdt = amount_usdt
    order.buy_reserve_released_at = utcnow()

    _append_order_error(
        order,
        message=(
            f"released_reserved_usdt={amount_usdt}; "
            f"reason={reason}"
        ),
    )

    db.add(wallet)
    db.add(order)
    db.flush()

    return amount_usdt