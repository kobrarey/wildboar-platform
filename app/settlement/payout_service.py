from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
    FundWallet,
    UserWallet,
)
from app.settlement.gas_service import get_web3
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_POSITIVE_NET_PROCESSING,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SETTLING,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_FAILED,
    TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
    TRANSFER_STATUS_PENDING,
    TRANSFER_STATUS_PENDING_CONFIRMATION,
    TRANSFER_STATUS_SENT,
    TRANSFER_STATUS_SKIPPED,
    TRANSFER_TYPE_REDEEM_PAYOUT_SETTLEMENT_TO_USER_WALLET,
)
from app.settlement.transfer_service import _check_tx_confirmed, _send_usdt_transfer
from app.telegram import send_telegram_message
from app.wallets import decrypt_private_key


log = logging.getLogger("settlement.payout_service")

ZERO = Decimal("0")


class SellerPayoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class SellerPayoutPlanRow:
    order_id: int
    user_id: int
    fund_id: int
    shares: Decimal
    settlement_price_usdt: Decimal
    redeem_usdt: Decimal
    to_address: str
    existing_transfer_status: str | None
    existing_tx_hash: str | None


@dataclass(frozen=True)
class SellerPayoutResult:
    batch_id: int
    fund_id: int
    planned_count: int
    confirmed_count: int
    pending_count: int
    failed_count: int
    total_redeem_usdt: Decimal
    seller_payouts_completed: bool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _send_alert(text: str) -> None:
    try:
        send_telegram_message(text)
    except Exception as exc:
        log.warning("Seller payout Telegram alert failed: %s", exc)


def _get_batch_for_update(db: Session, *, batch_id: int) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise SellerPayoutError(f"Batch not found: {batch_id}")

    return batch


def _get_active_settlement_wallet(db: Session, *, fund_id: int) -> FundWallet:
    wallet = (
        db.query(FundWallet)
        .filter(
            FundWallet.fund_id == fund_id,
            FundWallet.blockchain == "BSC",
            FundWallet.wallet_type == "settlement",
            FundWallet.is_active == True,
        )
        .first()
    )

    if wallet is None:
        raise SellerPayoutError(f"Active settlement wallet not found for fund_id={fund_id}")

    return wallet


def _get_active_user_wallet_for_update(db: Session, *, user_id: int) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == user_id,
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .with_for_update()
        .first()
    )

    if wallet is None:
        raise SellerPayoutError(f"Active user wallet not found for user_id={user_id}")

    return wallet


def _get_redeem_orders_for_update(db: Session, *, batch_id: int) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id == batch_id,
            FundOrder.side == ORDER_SIDE_REDEEM,
            FundOrder.status.in_(
                [
                    ORDER_STATUS_SETTLING,
                    ORDER_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
                    ORDER_STATUS_BUY_COLLECTED,
                ]
            ),
        )
        .order_by(FundOrder.created_at.asc(), FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _find_payout_transfer_for_update(
    db: Session,
    *,
    batch_id: int,
    order_id: int,
) -> FundSettlementTransfer | None:
    return (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.batch_id == batch_id,
            FundSettlementTransfer.order_id == order_id,
            FundSettlementTransfer.transfer_type == TRANSFER_TYPE_REDEEM_PAYOUT_SETTLEMENT_TO_USER_WALLET,
        )
        .with_for_update()
        .first()
    )


def _create_or_update_payout_transfer(
    db: Session,
    *,
    existing: FundSettlementTransfer | None,
    batch: FundSettlementBatch,
    order: FundOrder,
    from_address: str,
    to_address: str,
    amount_usdt: Decimal,
    status: str,
    tx_hash: str | None = None,
    error: str | None = None,
) -> FundSettlementTransfer:
    now = utcnow()

    if existing is None:
        row = FundSettlementTransfer(
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_REDEEM_PAYOUT_SETTLEMENT_TO_USER_WALLET,
            from_address=from_address,
            to_address=to_address,
            amount_usdt=amount_usdt,
            amount_bnb=None,
            gas_tx_hash=None,
            tx_hash=tx_hash,
            status=status,
            attempts=1 if tx_hash or error else 0,
            error=error,
            created_at=now,
            updated_at=now,
            sent_at=now if tx_hash else None,
            confirmed_at=now if status == TRANSFER_STATUS_CONFIRMED else None,
        )
        db.add(row)
        db.flush()
        return row

    existing.from_address = from_address
    existing.to_address = to_address
    existing.amount_usdt = amount_usdt
    existing.tx_hash = tx_hash or existing.tx_hash
    existing.status = status
    existing.error = error
    existing.updated_at = now

    if tx_hash and existing.sent_at is None:
        existing.sent_at = now

    if status == TRANSFER_STATUS_CONFIRMED and existing.confirmed_at is None:
        existing.confirmed_at = now

    if tx_hash or error:
        existing.attempts = int(existing.attempts or 0) + 1

    db.add(existing)
    db.flush()
    return existing


def _mark_batch_failed_requires_review(
    db: Session,
    *,
    batch: FundSettlementBatch,
    error: str,
    orders: list[FundOrder],
) -> None:
    now = utcnow()

    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now

    for order in orders:
        if order.status != "success":
            order.status = ORDER_STATUS_FAILED_REQUIRES_REVIEW
            order.error = error
            db.add(order)

    db.add(batch)
    db.flush()


def _redeem_usdt_for_order(
    *,
    order: FundOrder,
    settlement_price: Decimal,
) -> Decimal:
    shares = _dec(order.shares)
    if shares <= 0:
        raise SellerPayoutError(f"Redeem order {order.id} has invalid shares={order.shares}")

    if settlement_price <= 0:
        raise SellerPayoutError(f"Invalid settlement price: {settlement_price}")

    return shares * settlement_price


def plan_seller_payouts_for_batch(
    db: Session,
    *,
    batch_id: int,
) -> list[SellerPayoutPlanRow]:
    batch = _get_batch_for_update(db, batch_id=batch_id)

    settlement_price = _dec(batch.settlement_price_usdt)
    if settlement_price <= 0:
        raise SellerPayoutError(
            f"Batch {batch.id} has invalid settlement_price_usdt={batch.settlement_price_usdt}"
        )

    orders = _get_redeem_orders_for_update(db, batch_id=batch.id)
    rows: list[SellerPayoutPlanRow] = []

    for order in orders:
        user_wallet = _get_active_user_wallet_for_update(db, user_id=order.user_id)
        existing = _find_payout_transfer_for_update(
            db,
            batch_id=batch.id,
            order_id=order.id,
        )

        redeem_usdt = _redeem_usdt_for_order(
            order=order,
            settlement_price=settlement_price,
        )

        rows.append(
            SellerPayoutPlanRow(
                order_id=order.id,
                user_id=order.user_id,
                fund_id=batch.fund_id,
                shares=_dec(order.shares),
                settlement_price_usdt=settlement_price,
                redeem_usdt=redeem_usdt,
                to_address=user_wallet.address,
                existing_transfer_status=existing.status if existing else None,
                existing_tx_hash=existing.tx_hash if existing else None,
            )
        )

    return rows


def process_seller_payouts_for_batch(
    db: Session,
    *,
    batch_id: int,
    dry_run: bool = False,
    mock_confirm: bool = False,
) -> SellerPayoutResult:
    """
    Process seller payouts for positive-net settlement.

    Does not commit.
    Caller controls transaction boundary.

    dry_run=True:
        - does not send USDT;
        - creates skipped transfer rows if called inside a rollback check.

    mock_confirm=True:
        - marks payout rows confirmed without sending USDT;
        - use only for local mocked checks.
    """
    batch = _get_batch_for_update(db, batch_id=batch_id)

    if batch.status not in {
        BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
        BATCH_STATUS_POSITIVE_NET_PROCESSING,
    }:
        raise SellerPayoutError(
            f"Batch {batch.id} has invalid status for seller payouts: {batch.status}"
        )

    settlement_price = _dec(batch.settlement_price_usdt)
    if settlement_price <= 0:
        raise SellerPayoutError(
            f"Batch {batch.id} has invalid settlement_price_usdt={batch.settlement_price_usdt}"
        )

    settlement_wallet = _get_active_settlement_wallet(db, fund_id=batch.fund_id)
    orders = _get_redeem_orders_for_update(db, batch_id=batch.id)

    now = utcnow()
    if batch.positive_net_started_at is None:
        batch.positive_net_started_at = now
    batch.status = BATCH_STATUS_POSITIVE_NET_PROCESSING
    batch.updated_at = now
    db.add(batch)
    db.flush()

    if not orders:
        batch.seller_payouts_completed_at = now
        batch.updated_at = now
        db.add(batch)
        db.flush()

        return SellerPayoutResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            planned_count=0,
            confirmed_count=0,
            pending_count=0,
            failed_count=0,
            total_redeem_usdt=ZERO,
            seller_payouts_completed=True,
        )

    w3 = None if dry_run or mock_confirm else get_web3()
    settlement_private_key = None if dry_run or mock_confirm else decrypt_private_key(
        settlement_wallet.encrypted_private_key
    )

    confirmed_count = 0
    pending_count = 0
    failed_count = 0
    total_redeem_usdt = ZERO

    try:
        for order in orders:
            user_wallet = _get_active_user_wallet_for_update(db, user_id=order.user_id)
            existing = _find_payout_transfer_for_update(
                db,
                batch_id=batch.id,
                order_id=order.id,
            )

            redeem_usdt = _redeem_usdt_for_order(
                order=order,
                settlement_price=settlement_price,
            )
            total_redeem_usdt += redeem_usdt

            if existing is not None and existing.status == TRANSFER_STATUS_CONFIRMED:
                confirmed_count += 1
                continue

            if existing is not None and existing.tx_hash:
                if w3 is not None and _check_tx_confirmed(w3, existing.tx_hash):
                    _create_or_update_payout_transfer(
                        db,
                        existing=existing,
                        batch=batch,
                        order=order,
                        from_address=settlement_wallet.address,
                        to_address=user_wallet.address,
                        amount_usdt=redeem_usdt,
                        status=TRANSFER_STATUS_CONFIRMED,
                        tx_hash=existing.tx_hash,
                        error=None,
                    )
                    confirmed_count += 1
                    continue

                _create_or_update_payout_transfer(
                    db,
                    existing=existing,
                    batch=batch,
                    order=order,
                    from_address=settlement_wallet.address,
                    to_address=user_wallet.address,
                    amount_usdt=redeem_usdt,
                    status=TRANSFER_STATUS_PENDING_CONFIRMATION,
                    tx_hash=existing.tx_hash,
                    error=None,
                )
                pending_count += 1
                continue

            if dry_run:
                _create_or_update_payout_transfer(
                    db,
                    existing=existing,
                    batch=batch,
                    order=order,
                    from_address=settlement_wallet.address,
                    to_address=user_wallet.address,
                    amount_usdt=redeem_usdt,
                    status=TRANSFER_STATUS_SKIPPED,
                    tx_hash=None,
                    error="dry_run: seller payout would be sent",
                )
                pending_count += 1
                continue

            if mock_confirm:
                mock_tx_hash = f"mock_seller_payout_tx_{batch.id}_{order.id}"

                _create_or_update_payout_transfer(
                    db,
                    existing=existing,
                    batch=batch,
                    order=order,
                    from_address=settlement_wallet.address,
                    to_address=user_wallet.address,
                    amount_usdt=redeem_usdt,
                    status=TRANSFER_STATUS_CONFIRMED,
                    tx_hash=mock_tx_hash,
                    error=None,
                )
                confirmed_count += 1
                continue

            tx_hash = _send_usdt_transfer(
                w3,
                from_private_key=settlement_private_key,
                from_address=settlement_wallet.address,
                to_address=user_wallet.address,
                amount_usdt=redeem_usdt,
            )

            _create_or_update_payout_transfer(
                db,
                existing=existing,
                batch=batch,
                order=order,
                from_address=settlement_wallet.address,
                to_address=user_wallet.address,
                amount_usdt=redeem_usdt,
                status=TRANSFER_STATUS_SENT,
                tx_hash=tx_hash,
                error=None,
            )
            pending_count += 1

        seller_payouts_completed = pending_count == 0 and failed_count == 0

        if seller_payouts_completed:
            batch.seller_payouts_completed_at = utcnow()
            batch.updated_at = utcnow()
            db.add(batch)
            db.flush()

        return SellerPayoutResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            planned_count=len(orders),
            confirmed_count=confirmed_count,
            pending_count=pending_count,
            failed_count=failed_count,
            total_redeem_usdt=total_redeem_usdt,
            seller_payouts_completed=seller_payouts_completed,
        )

    except Exception as exc:
        error = str(exc)
        failed_count += 1

        _mark_batch_failed_requires_review(
            db,
            batch=batch,
            error=error,
            orders=orders,
        )

        _send_alert(
            "❌ Seller payout failed\n"
            f"Batch ID: {batch.id}\n"
            f"Fund ID: {batch.fund_id}\n"
            f"Error: {error}"
        )

        raise