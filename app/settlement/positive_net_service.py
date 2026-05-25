from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.models import FundOrder, FundRuntimeState, FundSettlementBatch
from app.settlement.accounting_service import (
    AccountingFinalizationResult,
    SettlementAccountingError,
    finalize_positive_net_accounting,
)
from app.settlement.bybit_deposit_service import (
    BybitDepositSettlementError,
    confirm_bybit_deposit_for_batch,
    send_or_confirm_positive_net_transfer,
)
from app.settlement.payout_service import (
    SellerPayoutError,
    process_seller_payouts_for_batch,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_PENDING_CONFIRMATION,
    BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_POSITIVE_NET_PROCESSING,
    ORDER_SIDE_BUY,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_SUCCESS,
)
from app.telegram import send_telegram_message


log = logging.getLogger("settlement.positive_net_service")

ZERO = Decimal("0")


class PositiveNetSettlementError(RuntimeError):
    pass


@dataclass(frozen=True)
class PositiveNetSettlementResult:
    batch_id: int
    fund_id: int
    status: str
    seller_payouts_completed: bool
    positive_net_transfer_confirmed: bool
    bybit_deposit_confirmed: bool
    internal_transfer_ready: bool
    accounting_finalized: bool
    pricing_unlocked: bool
    message: str
    accounting_result: AccountingFinalizationResult | None = None


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
        log.warning("Positive net Telegram alert failed: %s", exc)


def _get_batch_for_update(db: Session, *, batch_id: int) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise PositiveNetSettlementError(f"Batch not found: {batch_id}")

    return batch


def _get_pricing_state(db: Session, *, fund_id: int) -> FundRuntimeState | None:
    return (
        db.query(FundRuntimeState)
        .filter(FundRuntimeState.fund_id == fund_id)
        .first()
    )


def _validate_positive_net_batch(batch: FundSettlementBatch) -> None:
    if batch.status == BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED:
        return

    if batch.status not in {
        BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
        BATCH_STATUS_POSITIVE_NET_PROCESSING,
        BATCH_STATUS_PENDING_CONFIRMATION,
    }:
        raise PositiveNetSettlementError(
            f"Batch {batch.id} has invalid status for positive net settlement: {batch.status}"
        )

    if _dec(batch.net_cash_usdt) < 0:
        raise PositiveNetSettlementError(
            f"Batch {batch.id} is not positive-net: net_cash_usdt={batch.net_cash_usdt}"
        )

    if _dec(batch.settlement_price_usdt) <= 0:
        raise PositiveNetSettlementError(
            f"Batch {batch.id} has invalid settlement_price_usdt={batch.settlement_price_usdt}"
        )


def _validate_pricing_lock(db: Session, *, batch: FundSettlementBatch) -> None:
    if batch.accounting_finalized_at is not None:
        return

    state = _get_pricing_state(db, fund_id=batch.fund_id)

    if state is None:
        raise PositiveNetSettlementError(
            f"Pricing runtime state not found for fund_id={batch.fund_id}"
        )

    if not state.pricing_locked:
        raise PositiveNetSettlementError(
            f"Pricing lock is not active for fund_id={batch.fund_id}"
        )

    if state.pricing_lock_batch_id != batch.id:
        raise PositiveNetSettlementError(
            f"Pricing lock batch mismatch for fund_id={batch.fund_id}: "
            f"state_batch={state.pricing_lock_batch_id}, batch_id={batch.id}"
        )


def _validate_buy_collection_completed(db: Session, *, batch_id: int) -> None:
    buy_orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id == batch_id,
            FundOrder.side == ORDER_SIDE_BUY,
        )
        .all()
    )

    bad = [
        order.id
        for order in buy_orders
        if order.status not in {ORDER_STATUS_BUY_COLLECTED, ORDER_STATUS_SUCCESS}
    ]

    if bad:
        raise PositiveNetSettlementError(
            f"Buy-side collection is not completed for batch {batch_id}; "
            f"not ready order ids: {bad}"
        )


def _positive_net_transfer_confirmed(db: Session, *, batch: FundSettlementBatch) -> bool:
    if _dec(batch.net_cash_usdt) == 0:
        return True

    if not batch.bybit_deposit_tx_hash:
        return False

    transfer = (
        db.query(FundOrder)
        .filter(FundOrder.settlement_batch_id == batch.id)
        .first()
    )

    # The actual transfer confirmation is stored/checked inside bybit_deposit_service.
    # This function intentionally relies on batch-level fields only for orchestration.
    return batch.bybit_deposit_tx_hash is not None


def _bybit_deposit_confirmed(batch: FundSettlementBatch) -> bool:
    if _dec(batch.net_cash_usdt) == 0:
        return True
    return batch.bybit_deposit_confirmed_at is not None


def _internal_transfer_ready_or_skipped(
    db: Session,
    *,
    batch: FundSettlementBatch,
    dry_run: bool,
    mock_bybit: bool,
) -> bool:
    """
    Stage 22.1 guard.

    If Bybit deposit lands directly in UNIFIED / mock / zero-net, no internal transfer is needed.
    If deposit lands in FUND, real FUND -> UNIFIED transfer must be implemented before accounting.
    """
    now = utcnow()
    account_type = (batch.bybit_deposit_account_type or "").strip().lower()

    if _dec(batch.net_cash_usdt) == 0:
        if batch.bybit_internal_transfer_completed_at is None:
            batch.bybit_internal_transfer_id = batch.bybit_internal_transfer_id or "skipped_zero_net"
            batch.bybit_internal_transfer_completed_at = now
            batch.updated_at = now
            db.add(batch)
            db.flush()
        return True

    if batch.bybit_internal_transfer_completed_at is not None:
        return True

    if account_type in {"", "unified", "unifiedtrading", "uta", "spot", "mock_confirmed"}:
        batch.bybit_internal_transfer_id = batch.bybit_internal_transfer_id or (
            "skipped_" + (account_type or "account_type_not_returned")
        )
        batch.bybit_internal_transfer_completed_at = now
        batch.updated_at = now
        db.add(batch)
        db.flush()
        return True

    if account_type == "fund":
        if dry_run or mock_bybit:
            batch.bybit_internal_transfer_id = batch.bybit_internal_transfer_id or "mock_fund_to_unified_completed"
            batch.bybit_internal_transfer_completed_at = now
            batch.updated_at = now
            db.add(batch)
            db.flush()
            return True

        return False

    return False


def _mark_failed_requires_review(
    db: Session,
    *,
    batch: FundSettlementBatch,
    error: str,
) -> None:
    now = utcnow()

    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now

    db.add(batch)
    db.flush()


def process_positive_net_batch(
    db: Session,
    *,
    batch_id: int,
    bybit_client: BybitV5Client | None = None,
    dry_run: bool = False,
    mock_chain: bool = False,
    mock_bybit: bool = False,
    finalize_accounting: bool = True,
) -> PositiveNetSettlementResult:
    """
    Orchestrate positive-net settlement for one batch.

    Does not commit.
    Caller controls transaction boundary.

    dry_run=True:
        - no real on-chain transfer;
        - no real Bybit confirmation;
        - normally stops before accounting unless mock flags make prerequisites complete.

    mock_chain=True:
        - seller payouts and positive net transfer can be marked confirmed without on-chain tx.

    mock_bybit=True:
        - Bybit deposit can be marked confirmed without real Bybit API.

    finalize_accounting=True:
        - accounting finalization runs only after all prerequisites are complete.
    """
    batch = _get_batch_for_update(db, batch_id=batch_id)

    if batch.accounting_finalized_at is not None:
        return PositiveNetSettlementResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            status=batch.status,
            seller_payouts_completed=batch.seller_payouts_completed_at is not None,
            positive_net_transfer_confirmed=True,
            bybit_deposit_confirmed=batch.bybit_deposit_confirmed_at is not None,
            internal_transfer_ready=batch.bybit_internal_transfer_completed_at is not None,
            accounting_finalized=True,
            pricing_unlocked=batch.pricing_unlocked_at is not None,
            message="Batch accounting already finalized.",
            accounting_result=None,
        )

    try:
        _validate_positive_net_batch(batch)
        _validate_pricing_lock(db, batch=batch)
        _validate_buy_collection_completed(db, batch_id=batch.id)

        if batch.status == BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION:
            batch.status = BATCH_STATUS_POSITIVE_NET_PROCESSING
            batch.positive_net_started_at = batch.positive_net_started_at or utcnow()
            batch.updated_at = utcnow()
            db.add(batch)
            db.flush()

        payout_result = process_seller_payouts_for_batch(
            db,
            batch_id=batch.id,
            dry_run=dry_run,
            mock_confirm=mock_chain,
        )

        if not payout_result.seller_payouts_completed:
            return PositiveNetSettlementResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                status=batch.status,
                seller_payouts_completed=False,
                positive_net_transfer_confirmed=False,
                bybit_deposit_confirmed=False,
                internal_transfer_ready=False,
                accounting_finalized=False,
                pricing_unlocked=False,
                message="Seller payouts are not fully confirmed yet.",
                accounting_result=None,
            )

        transfer_result = send_or_confirm_positive_net_transfer(
            db,
            batch_id=batch.id,
            dry_run=dry_run,
            mock_confirm=mock_chain,
        )

        positive_transfer_confirmed = (
            _dec(batch.net_cash_usdt) == 0
            or transfer_result.transfer_status == "confirmed"
        )

        if not positive_transfer_confirmed:
            return PositiveNetSettlementResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                status=batch.status,
                seller_payouts_completed=True,
                positive_net_transfer_confirmed=False,
                bybit_deposit_confirmed=False,
                internal_transfer_ready=False,
                accounting_finalized=False,
                pricing_unlocked=False,
                message="Positive net transfer is not confirmed yet.",
                accounting_result=None,
            )

        bybit_confirmed = confirm_bybit_deposit_for_batch(
            db,
            batch_id=batch.id,
            client=bybit_client,
            mock_confirm=mock_bybit or mock_chain,
        )

        if not bybit_confirmed:
            return PositiveNetSettlementResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                status=batch.status,
                seller_payouts_completed=True,
                positive_net_transfer_confirmed=True,
                bybit_deposit_confirmed=False,
                internal_transfer_ready=False,
                accounting_finalized=False,
                pricing_unlocked=False,
                message="Bybit deposit is not confirmed yet.",
                accounting_result=None,
            )

        batch = _get_batch_for_update(db, batch_id=batch.id)

        internal_ready = _internal_transfer_ready_or_skipped(
            db,
            batch=batch,
            dry_run=dry_run,
            mock_bybit=mock_bybit or mock_chain,
        )

        if not internal_ready:
            return PositiveNetSettlementResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                status=batch.status,
                seller_payouts_completed=True,
                positive_net_transfer_confirmed=True,
                bybit_deposit_confirmed=True,
                internal_transfer_ready=False,
                accounting_finalized=False,
                pricing_unlocked=False,
                message="Bybit FUND -> UNIFIED internal transfer is required before accounting.",
                accounting_result=None,
            )

        if not finalize_accounting:
            return PositiveNetSettlementResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                status=batch.status,
                seller_payouts_completed=True,
                positive_net_transfer_confirmed=True,
                bybit_deposit_confirmed=True,
                internal_transfer_ready=True,
                accounting_finalized=False,
                pricing_unlocked=False,
                message="All prerequisites complete; accounting finalization skipped by flag.",
                accounting_result=None,
            )

        accounting_result = finalize_positive_net_accounting(
            db,
            batch_id=batch.id,
            unlock_pricing=True,
        )

        finalized_batch = _get_batch_for_update(db, batch_id=batch.id)

        return PositiveNetSettlementResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            status=finalized_batch.status,
            seller_payouts_completed=finalized_batch.seller_payouts_completed_at is not None,
            positive_net_transfer_confirmed=True,
            bybit_deposit_confirmed=finalized_batch.bybit_deposit_confirmed_at is not None,
            internal_transfer_ready=finalized_batch.bybit_internal_transfer_completed_at is not None,
            accounting_finalized=finalized_batch.accounting_finalized_at is not None,
            pricing_unlocked=finalized_batch.pricing_unlocked_at is not None,
            message="Positive net settlement finalized.",
            accounting_result=accounting_result,
        )

    except (
        PositiveNetSettlementError,
        SellerPayoutError,
        BybitDepositSettlementError,
        SettlementAccountingError,
    ) as exc:
        error = str(exc)
        _mark_failed_requires_review(db, batch=batch, error=error)

        _send_alert(
            "❌ Positive net settlement failed\n"
            f"Batch ID: {batch.id}\n"
            f"Fund ID: {batch.fund_id}\n"
            f"Error: {error}"
        )

        raise