from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session
from web3 import Web3

from app.config import settings
from app.models import (
    Fund,
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
    FundWallet,
    UserWallet,
)
from app.operation_guard.hooks import (
    require_bsc_buy_collection_gas_topup_guard,
    require_bsc_buy_collection_usdt_to_settlement_guard,
)
from app.operation_guard.service import OperationGuardBlockedError
from app.settlement.bsc_intent_service import (
    BscIntentError,
    broadcast_persisted_transfer_intent,
    persist_prepared_transfer_intent,
    prepare_native_bnb_transaction,
    prepare_usdt_transfer_transaction,
    prepared_transaction_from_transfer,
)
from app.settlement.buy_reserve_service import (
    release_buy_reserve_if_safe,
)
from app.settlement.gas_service import (
    WEI_PER_BNB,
    get_bnb_balance,
    get_web3,
)
from app.settlement.pricing_lock import unlock_pricing_for_fund
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_STATUS_FAILED,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_GAS_CHECKING,
    BATCH_STATUS_GAS_READY,
    BATCH_STATUS_BUY_USDT_COLLECTED,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_BUY_COLLECTING,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SETTLING,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_FAILED,
    TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
    TRANSFER_STATUS_PENDING,
    TRANSFER_STATUS_PREPARED,
    TRANSFER_STATUS_PROCESSING,
    TRANSFER_STATUS_SENT,
    TRANSFER_STATUS_SKIPPED,
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
    TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
)
from app.telegram import send_telegram_message
from app.wallets import decrypt_private_key


log = logging.getLogger("settlement.transfer_service")

ZERO = Decimal("0")

ERC20_TRANSFER_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }
]


class SettlementTransferError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuyCollectionResult:
    batch_id: int
    fund_id: int
    fund_code: str
    buy_orders_count: int
    collected_orders_count: int
    pending_orders_count: int
    failed_orders_count: int
    batch_status: str
    message: str


@dataclass(frozen=True)
class TxReceiptCheckResult:
    action: str  # missing | pending | confirmed | failed | error
    receipt_status: int | None
    confirmations: int
    block_number: int | None
    current_block: int | None
    error: str | None = None


@dataclass(frozen=True)
class SettlementTransferConfirmationResult:
    transfer_id: int
    batch_id: int | None
    order_id: int | None
    transfer_type: str | None
    tx_hash: str | None
    action: str  # pending | confirmed | failed | already_confirmed | skipped
    receipt_status: int | None
    confirmations: int
    batch_status: str | None
    order_status: str | None
    message: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _checksum(w3: Web3, address: str) -> str:
    if not address:
        raise SettlementTransferError("Address is empty")
    return w3.to_checksum_address(address)


def _normalize_private_key(private_key: str) -> str:
    value = (private_key or "").strip()
    if not value:
        raise SettlementTransferError("Private key is empty")
    if not value.startswith("0x"):
        value = "0x" + value
    return value


def _bnb_required_for_erc20_transfer(w3: Web3) -> Decimal:
    gas_price_wei = Decimal(int(w3.eth.gas_price))
    fallback_gas = Decimal(int(settings.ERC20_TRANSFER_GAS_FALLBACK))
    buffer_mult = Decimal(settings.WITHDRAW_GAS_BUFFER_MULT)

    return (fallback_gas * gas_price_wei * buffer_mult) / WEI_PER_BNB


def _usdt_amount_to_raw(amount_usdt: Decimal) -> int:
    decimals = int(settings.BSC_USDT_DECIMALS)
    return int(amount_usdt * (Decimal(10) ** decimals))


def _q10(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0000000001"))


def _q18(value: Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000000000000000001"))


def deterministic_buy_collection_gas_topup_request_id(
    *,
    batch_id: int,
    order_id: int,
    user_wallet_id: int,
    amount_bnb: Decimal,
    to_address: str,
) -> str:
    return (
        f"buy-collection-gas-topup:"
        f"{int(batch_id)}:"
        f"{int(order_id)}:"
        f"{int(user_wallet_id)}:"
        f"{_q18(amount_bnb)}:"
        f"{str(to_address).strip()}"
    )


def deterministic_buy_collection_usdt_request_id(
    *,
    batch_id: int,
    order_id: int,
    user_wallet_id: int,
    amount_usdt: Decimal,
    to_address: str,
) -> str:
    return (
        f"buy-collection-usdt:"
        f"{int(batch_id)}:"
        f"{int(order_id)}:"
        f"{int(user_wallet_id)}:"
        f"{_q10(amount_usdt)}:"
        f"{str(to_address).strip()}"
    )


def _get_active_fund_settlement_wallet(db: Session, *, fund_id: int) -> FundWallet:
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
        raise SettlementTransferError(f"Active settlement wallet not found for fund_id={fund_id}")

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
        raise SettlementTransferError(f"Active user wallet not found for user_id={user_id}")

    return wallet


def _find_transfer(
    db: Session,
    *,
    batch_id: int,
    order_id: int,
    transfer_type: str,
) -> FundSettlementTransfer | None:
    return (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.batch_id == batch_id,
            FundSettlementTransfer.order_id == order_id,
            FundSettlementTransfer.transfer_type == transfer_type,
        )
        .with_for_update()
        .first()
    )


def _create_or_update_transfer(
    db: Session,
    *,
    existing: FundSettlementTransfer | None,
    batch_id: int,
    order_id: int,
    fund_id: int,
    user_id: int,
    transfer_type: str,
    from_address: str,
    to_address: str,
    amount_usdt: Decimal | None = None,
    amount_bnb: Decimal | None = None,
    tx_hash: str | None = None,
    gas_tx_hash: str | None = None,
    request_key: str | None = None,
    status: str = TRANSFER_STATUS_PENDING,
    error: str | None = None,
) -> FundSettlementTransfer:
    now = utcnow()

    if existing is None:
        row = FundSettlementTransfer(
            batch_id=batch_id,
            order_id=order_id,
            fund_id=fund_id,
            user_id=user_id,
            transfer_type=transfer_type,
            request_key=request_key,
            from_address=from_address,
            to_address=to_address,
            amount_usdt=amount_usdt,
            amount_bnb=amount_bnb,
            gas_tx_hash=gas_tx_hash,
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

    if (
        existing.request_key
        and request_key
        and str(existing.request_key) != str(request_key)
    ):
        raise SettlementTransferError(
            "Settlement transfer request key mismatch: "
            f"transfer_id={existing.id}"
        )

    if request_key and not existing.request_key:
        existing.request_key = str(request_key)

    existing.from_address = from_address
    existing.to_address = to_address
    existing.amount_usdt = amount_usdt
    existing.amount_bnb = amount_bnb
    existing.tx_hash = tx_hash or existing.tx_hash
    existing.gas_tx_hash = gas_tx_hash or existing.gas_tx_hash
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


def _receipt_get(receipt: Any, key: str, default: Any = None) -> Any:
    if receipt is None:
        return default
    if hasattr(receipt, "get"):
        return receipt.get(key, default)
    return getattr(receipt, key, default)


def _get_tx_receipt_check(
    w3: Web3,
    tx_hash: str | None,
    *,
    min_confirmations: int | None = None,
) -> TxReceiptCheckResult:
    if not tx_hash:
        return TxReceiptCheckResult(
            action="missing",
            receipt_status=None,
            confirmations=0,
            block_number=None,
            current_block=None,
            error="tx_hash_missing",
        )

    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception as exc:
        return TxReceiptCheckResult(
            action="pending",
            receipt_status=None,
            confirmations=0,
            block_number=None,
            current_block=None,
            error=f"receipt_unavailable: {exc}",
        )

    if receipt is None:
        return TxReceiptCheckResult(
            action="pending",
            receipt_status=None,
            confirmations=0,
            block_number=None,
            current_block=None,
            error=None,
        )

    try:
        receipt_status = int(_receipt_get(receipt, "status", 0))
    except Exception:
        receipt_status = None

    try:
        block_number_raw = _receipt_get(receipt, "blockNumber", None)
        block_number = int(block_number_raw) if block_number_raw is not None else None
    except Exception:
        block_number = None

    try:
        current_block = int(w3.eth.block_number)
    except Exception as exc:
        return TxReceiptCheckResult(
            action="pending",
            receipt_status=receipt_status,
            confirmations=0,
            block_number=block_number,
            current_block=None,
            error=f"current_block_unavailable: {exc}",
        )

    confirmations = 0
    if block_number is not None:
        confirmations = max(current_block - block_number + 1, 0)

    required = int(
        min_confirmations
        if min_confirmations is not None
        else settings.SETTLEMENT_TRANSFER_CONFIRMATION_MIN_CONFIRMATIONS
    )

    if confirmations < required:
        return TxReceiptCheckResult(
            action="pending",
            receipt_status=receipt_status,
            confirmations=confirmations,
            block_number=block_number,
            current_block=current_block,
            error=None,
        )

    if receipt_status == 1:
        return TxReceiptCheckResult(
            action="confirmed",
            receipt_status=receipt_status,
            confirmations=confirmations,
            block_number=block_number,
            current_block=current_block,
            error=None,
        )

    if receipt_status == 0:
        return TxReceiptCheckResult(
            action="failed",
            receipt_status=receipt_status,
            confirmations=confirmations,
            block_number=block_number,
            current_block=current_block,
            error=None,
        )

    return TxReceiptCheckResult(
        action="pending",
        receipt_status=receipt_status,
        confirmations=confirmations,
        block_number=block_number,
        current_block=current_block,
        error=f"unexpected_receipt_status={receipt_status}",
    )


def _check_tx_confirmed(w3: Web3, tx_hash: str | None) -> bool:
    result = _get_tx_receipt_check(w3, tx_hash)
    return result.action == "confirmed"


def _send_usdt_transfer(
    w3: Web3,
    *,
    from_private_key: str,
    from_address: str,
    to_address: str,
    amount_usdt: Decimal,
) -> str:
    if amount_usdt <= 0:
        raise SettlementTransferError(f"Invalid USDT amount: {amount_usdt}")

    if not settings.BSC_USDT_CONTRACT:
        raise SettlementTransferError("BSC_USDT_CONTRACT is not configured")

    private_key = _normalize_private_key(from_private_key)
    from_checksum = _checksum(w3, from_address)
    to_checksum = _checksum(w3, to_address)

    contract = w3.eth.contract(
        address=_checksum(w3, settings.BSC_USDT_CONTRACT),
        abi=ERC20_TRANSFER_ABI,
    )

    amount_raw = _usdt_amount_to_raw(amount_usdt)

    nonce = w3.eth.get_transaction_count(from_checksum)
    gas_price = int(w3.eth.gas_price)
    chain_id = int(w3.eth.chain_id)

    tx = contract.functions.transfer(to_checksum, amount_raw).build_transaction(
        {
            "from": from_checksum,
            "nonce": nonce,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
    )

    if "gas" not in tx or not tx["gas"]:
        tx["gas"] = int(settings.ERC20_TRANSFER_GAS_FALLBACK)

    signed = w3.eth.account.sign_transaction(tx, private_key)
    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
    tx_hash = w3.eth.send_raw_transaction(raw_tx)

    return w3.to_hex(tx_hash)


def _send_alert(text: str) -> None:
    try:
        send_telegram_message(text)
    except Exception as exc:
        log.warning("Settlement transfer Telegram alert failed: %s", exc)


def _mark_batch_failed(batch: FundSettlementBatch, *, error: str) -> None:
    now = utcnow()
    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now


def _mark_order_failed(order: FundOrder, *, error: str) -> None:
    order.status = ORDER_STATUS_FAILED_REQUIRES_REVIEW

    existing_error = str(order.error or "").strip()
    normalized_error = str(error or "").strip()

    if not existing_error:
        order.error = normalized_error
    elif normalized_error and normalized_error not in existing_error:
        order.error = f"{existing_error}; {normalized_error}"


def _confirm_buy_collection(
    *,
    db: Session,
    order: FundOrder,
    wallet: UserWallet,
    amount_usdt: Decimal,
) -> None:
    """
    Confirm buy-side collection and reduce user's reserved/accounted balance.

    Does not change:
    - user_fund_positions.shares
    - funds.shares_outstanding_current
    - final order price/shares
    """
    now = utcnow()

    reserved_before = _dec(wallet.usdt_reserved)
    balance_before = _dec(wallet.usdt_balance)

    if reserved_before < amount_usdt:
        raise SettlementTransferError(
            f"Cannot confirm collection: usdt_reserved={reserved_before} < amount={amount_usdt}"
        )

    if balance_before < amount_usdt:
        raise SettlementTransferError(
            f"Cannot confirm collection: usdt_balance={balance_before} < amount={amount_usdt}"
        )

    wallet.usdt_reserved = reserved_before - amount_usdt
    wallet.usdt_balance = balance_before - amount_usdt

    order.status = ORDER_STATUS_BUY_COLLECTED
    order.collection_confirmed_at = now
    order.error = None

    db.add(wallet)
    db.add(order)
    db.flush()


def _get_batch_for_update(db: Session, *, batch_id: int) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )
    if batch is None:
        raise SettlementTransferError(f"Settlement batch not found: {batch_id}")
    return batch


def _get_order_for_update(db: Session, *, order_id: int | None) -> FundOrder | None:
    if order_id is None:
        return None
    return (
        db.query(FundOrder)
        .filter(FundOrder.id == order_id)
        .with_for_update()
        .first()
    )


def _maybe_advance_batch_after_buy_confirmations(
    db: Session,
    *,
    batch: FundSettlementBatch,
) -> str:
    buy_orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id == batch.id,
            FundOrder.side == ORDER_SIDE_BUY,
        )
        .with_for_update()
        .all()
    )

    if not buy_orders:
        return str(batch.status)

    all_collected = all(
        str(order.status) == ORDER_STATUS_BUY_COLLECTED
        for order in buy_orders
    )

    if not all_collected:
        if batch.status not in {
            BATCH_STATUS_GAS_CHECKING,
            BATCH_STATUS_GAS_READY,
            BATCH_STATUS_COLLECTING_BUY_USDT,
        }:
            return str(batch.status)

        batch.status = BATCH_STATUS_COLLECTING_BUY_USDT
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()
        return str(batch.status)

    return _update_terminal_batch_status(db, batch=batch)


def _apply_confirmed_sent_transfer(
    db: Session,
    *,
    transfer: FundSettlementTransfer,
    receipt_check: TxReceiptCheckResult,
) -> SettlementTransferConfirmationResult:
    now = utcnow()
    batch = _get_batch_for_update(db, batch_id=int(transfer.batch_id))

    order = _get_order_for_update(db, order_id=transfer.order_id)
    order_status: str | None = str(order.status) if order is not None else None

    transfer.status = TRANSFER_STATUS_CONFIRMED
    transfer.confirmed_at = transfer.confirmed_at or now
    transfer.updated_at = now
    transfer.error = None
    db.add(transfer)

    if transfer.transfer_type == TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT:
        if order is None:
            raise SettlementTransferError(
                f"Cannot confirm buy collection: order_id missing for transfer_id={transfer.id}"
            )

        if order.status != ORDER_STATUS_BUY_COLLECTED:
            wallet = _get_active_user_wallet_for_update(db, user_id=int(order.user_id))
            amount_usdt = _dec(transfer.amount_usdt or order.amount_usdt)

            _confirm_buy_collection(
                db=db,
                order=order,
                wallet=wallet,
                amount_usdt=amount_usdt,
            )

            order_status = ORDER_STATUS_BUY_COLLECTED
        else:
            order_status = ORDER_STATUS_BUY_COLLECTED

        batch_status = _maybe_advance_batch_after_buy_confirmations(db, batch=batch)

    elif transfer.transfer_type == TRANSFER_TYPE_USER_WALLET_GAS_TOPUP:
        batch_status = str(batch.status)

    else:
        batch_status = str(batch.status)

    db.flush()

    return SettlementTransferConfirmationResult(
        transfer_id=int(transfer.id),
        batch_id=int(transfer.batch_id),
        order_id=int(transfer.order_id) if transfer.order_id is not None else None,
        transfer_type=str(transfer.transfer_type),
        tx_hash=transfer.tx_hash,
        action="confirmed",
        receipt_status=receipt_check.receipt_status,
        confirmations=receipt_check.confirmations,
        batch_status=batch_status,
        order_status=order_status,
        message="sent transfer confirmed from on-chain receipt",
    )


def _apply_failed_sent_transfer(
    db: Session,
    *,
    transfer: FundSettlementTransfer,
    receipt_check: TxReceiptCheckResult,
) -> SettlementTransferConfirmationResult:
    now = utcnow()
    batch = _get_batch_for_update(db, batch_id=int(transfer.batch_id))
    order = _get_order_for_update(db, order_id=transfer.order_id)

    error = (
        "sent settlement transfer failed on-chain "
        f"transfer_id={transfer.id} tx_hash={transfer.tx_hash} "
        f"receipt_status={receipt_check.receipt_status} confirmations={receipt_check.confirmations}"
    )

    transfer.status = TRANSFER_STATUS_FAILED_REQUIRES_REVIEW
    transfer.updated_at = now
    transfer.error = error
    db.add(transfer)

    order_status: str | None = None

    if order is not None:
        order_status = ORDER_STATUS_FAILED_REQUIRES_REVIEW

        if order.status != ORDER_STATUS_BUY_COLLECTED:
            try:
                release_buy_reserve_if_safe(
                    db,
                    order_id=int(order.id),
                    reason=(
                        f"{error}; "
                        "receipt.status=0 proves the failed tx "
                        "did not move USDT"
                    ),
                    proven_failed_transfer_id=int(transfer.id),
                    proven_receipt_status=(
                        receipt_check.receipt_status
                    ),
                )
            except Exception as exc:
                order.error = (
                    f"{error}; reserve_release_failed={exc}"
                )
        else:
            order.error = f"{error}; order already buy_collected, wallet accounting not changed"

        order.status = ORDER_STATUS_FAILED_REQUIRES_REVIEW
        db.add(order)

    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now
    db.add(batch)

    try:
        unlock_pricing_for_fund(
            db,
            fund_id=int(batch.fund_id),
            batch_id=int(batch.id),
        )
        batch.pricing_unlocked_at = now
        db.add(batch)
    except Exception as exc:
        log.warning(
            "Pricing unlock after failed settlement transfer failed "
            "batch_id=%s transfer_id=%s error=%s",
            batch.id,
            transfer.id,
            exc,
        )
        batch.error = f"{error}; pricing_unlock_failed={exc}"
        db.add(batch)

    db.flush()

    _send_alert(
        "❌ Settlement sent transfer failed on-chain\n"
        f"Batch ID: {batch.id}\n"
        f"Order ID: {transfer.order_id}\n"
        f"Transfer ID: {transfer.id}\n"
        f"Tx: {transfer.tx_hash}\n"
        f"Receipt status: {receipt_check.receipt_status}\n"
        f"Confirmations: {receipt_check.confirmations}"
    )

    return SettlementTransferConfirmationResult(
        transfer_id=int(transfer.id),
        batch_id=int(transfer.batch_id),
        order_id=int(transfer.order_id) if transfer.order_id is not None else None,
        transfer_type=str(transfer.transfer_type),
        tx_hash=transfer.tx_hash,
        action="failed",
        receipt_status=receipt_check.receipt_status,
        confirmations=receipt_check.confirmations,
        batch_status=str(batch.status),
        order_status=order_status,
        message=error,
    )


def confirm_sent_settlement_transfer(
    db: Session,
    transfer_id: int,
    *,
    dry_run: bool = False,
    min_confirmations: int | None = None,
) -> SettlementTransferConfirmationResult:
    """
    Confirmation-only path for an already-sent settlement transfer.

    Safety:
    - does not send BSC tx;
    - does not call Operation Guard;
    - does not create fund orders;
    - does not create settlement batches;
    - idempotent: accounting is applied only when transfer is sent and order is not buy_collected.
    """
    transfer = (
        db.query(FundSettlementTransfer)
        .filter(FundSettlementTransfer.id == int(transfer_id))
        .with_for_update()
        .first()
    )

    if transfer is None:
        raise SettlementTransferError(f"Settlement transfer not found: {transfer_id}")

    if transfer.status == TRANSFER_STATUS_CONFIRMED:
        return SettlementTransferConfirmationResult(
            transfer_id=int(transfer.id),
            batch_id=int(transfer.batch_id),
            order_id=int(transfer.order_id) if transfer.order_id is not None else None,
            transfer_type=str(transfer.transfer_type),
            tx_hash=transfer.tx_hash,
            action="already_confirmed",
            receipt_status=None,
            confirmations=0,
            batch_status=None,
            order_status=None,
            message="transfer already confirmed; no mutation",
        )

    if transfer.status != TRANSFER_STATUS_SENT:
        return SettlementTransferConfirmationResult(
            transfer_id=int(transfer.id),
            batch_id=int(transfer.batch_id),
            order_id=int(transfer.order_id) if transfer.order_id is not None else None,
            transfer_type=str(transfer.transfer_type),
            tx_hash=transfer.tx_hash,
            action="skipped",
            receipt_status=None,
            confirmations=0,
            batch_status=None,
            order_status=None,
            message=f"transfer status is not sent: {transfer.status}",
        )

    if not transfer.tx_hash:
        return SettlementTransferConfirmationResult(
            transfer_id=int(transfer.id),
            batch_id=int(transfer.batch_id),
            order_id=int(transfer.order_id) if transfer.order_id is not None else None,
            transfer_type=str(transfer.transfer_type),
            tx_hash=None,
            action="pending",
            receipt_status=None,
            confirmations=0,
            batch_status=None,
            order_status=None,
            message="sent transfer has no tx_hash",
        )

    w3 = get_web3()
    receipt_check = _get_tx_receipt_check(
        w3,
        transfer.tx_hash,
        min_confirmations=min_confirmations,
    )

    if receipt_check.action in {"missing", "pending", "error"}:
        return SettlementTransferConfirmationResult(
            transfer_id=int(transfer.id),
            batch_id=int(transfer.batch_id),
            order_id=int(transfer.order_id) if transfer.order_id is not None else None,
            transfer_type=str(transfer.transfer_type),
            tx_hash=transfer.tx_hash,
            action="pending",
            receipt_status=receipt_check.receipt_status,
            confirmations=receipt_check.confirmations,
            batch_status=None,
            order_status=None,
            message=receipt_check.error or "receipt pending or below confirmation threshold",
        )

    if dry_run:
        dry_run_action = {
            "confirmed": "dry_run_would_confirm",
            "failed": "dry_run_would_fail",
        }.get(receipt_check.action, f"dry_run_would_{receipt_check.action}")

        return SettlementTransferConfirmationResult(
            transfer_id=int(transfer.id),
            batch_id=int(transfer.batch_id),
            order_id=int(transfer.order_id) if transfer.order_id is not None else None,
            transfer_type=str(transfer.transfer_type),
            tx_hash=transfer.tx_hash,
            action=dry_run_action,
            receipt_status=receipt_check.receipt_status,
            confirmations=receipt_check.confirmations,
            batch_status=None,
            order_status=None,
            message="dry-run: no DB mutation",
        )

    if receipt_check.action == "confirmed":
        return _apply_confirmed_sent_transfer(
            db,
            transfer=transfer,
            receipt_check=receipt_check,
        )

    if receipt_check.action == "failed":
        return _apply_failed_sent_transfer(
            db,
            transfer=transfer,
            receipt_check=receipt_check,
        )

    raise SettlementTransferError(
        f"Unsupported receipt action={receipt_check.action} transfer_id={transfer.id}"
    )


def confirm_sent_settlement_transfers_for_batch(
    db: Session,
    batch_id: int,
    limit: int | None = None,
    *,
    dry_run: bool = False,
    min_confirmations: int | None = None,
) -> list[SettlementTransferConfirmationResult]:
    q = (
        db.query(FundSettlementTransfer.id)
        .filter(
            FundSettlementTransfer.batch_id == int(batch_id),
            FundSettlementTransfer.status == TRANSFER_STATUS_SENT,
            FundSettlementTransfer.tx_hash.isnot(None),
            FundSettlementTransfer.confirmed_at.is_(None),
            FundSettlementTransfer.transfer_type.in_(
                [
                    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
                    TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
                ]
            ),
        )
        .order_by(FundSettlementTransfer.sent_at.asc().nullslast(), FundSettlementTransfer.id.asc())
    )

    if limit is not None:
        q = q.limit(int(limit))

    transfer_ids = [int(row[0]) for row in q.all()]

    return [
        confirm_sent_settlement_transfer(
            db,
            transfer_id,
            dry_run=dry_run,
            min_confirmations=min_confirmations,
        )
        for transfer_id in transfer_ids
    ]


def _ensure_user_wallet_gas(
    db: Session,
    *,
    w3: Web3,
    batch: FundSettlementBatch,
    order: FundOrder,
    user_wallet: UserWallet,
    dry_run: bool,
) -> bool:
    """
    Ensure buyer wallet has BNB for one USDT transfer.

    Returns True when gas is ready/confirmed.
    Returns False when tx was sent and worker should retry later.
    """
    user_address = _checksum(w3, user_wallet.address)
    ok_wallet_address = _checksum(w3, settings.FEE_WALLET_OK_ADDRESS)

    existing = _find_transfer(
        db,
        batch_id=batch.id,
        order_id=order.id,
        transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
    )

    if existing and existing.status == TRANSFER_STATUS_CONFIRMED:
        return True

    if existing and existing.tx_hash:
        if _check_tx_confirmed(w3, existing.tx_hash):
            existing.status = TRANSFER_STATUS_CONFIRMED
            existing.confirmed_at = utcnow()
            existing.updated_at = utcnow()
            db.add(existing)
            db.flush()
            return True

        existing.status = TRANSFER_STATUS_SENT
        existing.updated_at = utcnow()
        db.add(existing)
        db.flush()
        return False

    if existing is not None:
        prepared_existing = (
            prepared_transaction_from_transfer(
                existing
            )
        )

        if prepared_existing is not None:
            if dry_run:
                return False

            try:
                broadcast_persisted_transfer_intent(
                    db,
                    w3=w3,
                    transfer_id=int(existing.id),
                    from_address=ok_wallet_address,
                    copy_to_gas_tx_hash=True,
                )
            except BscIntentError as exc:
                log.warning(
                    "Prepared buy gas top-up remains pending "
                    "reconciliation: transfer_id=%s error=%s",
                    existing.id,
                    exc,
                )
                return False

            return False

    required_bnb = _bnb_required_for_erc20_transfer(w3)
    current_bnb = get_bnb_balance(w3, user_address)

    if current_bnb >= required_bnb:
        _create_or_update_transfer(
            db,
            existing=existing,
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
            from_address=ok_wallet_address,
            to_address=user_address,
            amount_bnb=ZERO,
            status=TRANSFER_STATUS_SKIPPED,
            error=None,
        )
        return True

    amount_bnb = required_bnb - current_bnb

    if dry_run:
        _create_or_update_transfer(
            db,
            existing=existing,
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
            from_address=ok_wallet_address,
            to_address=user_address,
            amount_bnb=amount_bnb,
            status=TRANSFER_STATUS_SKIPPED,
            error="dry_run: would send BNB gas top-up",
        )
        return True

    ok_bnb = get_bnb_balance(
        w3,
        ok_wallet_address,
    )

    if ok_bnb < amount_bnb:
        error = (
            f"OK gas wallet insufficient for user gas top-up. "
            f"order_id={order.id} user_id={order.user_id} "
            f"user_wallet={user_address} ok_wallet={ok_wallet_address} "
            f"needed_bnb={amount_bnb} available_bnb={ok_bnb}"
        )

        _create_or_update_transfer(
            db,
            existing=existing,
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
            from_address=ok_wallet_address,
            to_address=user_address,
            amount_bnb=amount_bnb,
            status=TRANSFER_STATUS_FAILED,
            error=error,
        )

        try:
            release_buy_reserve_if_safe(
                db,
                order_id=int(order.id),
                reason=error,
            )
        except Exception as reserve_exc:
            error = (
                f"{error}; "
                f"reserve_release_failed={reserve_exc}"
            )

        raise SettlementTransferError(error)

    request_id = deterministic_buy_collection_gas_topup_request_id(
        batch_id=int(batch.id),
        order_id=int(order.id),
        user_wallet_id=int(user_wallet.id),
        amount_bnb=amount_bnb,
        to_address=user_address,
    )

    try:
        guard_decision = require_bsc_buy_collection_gas_topup_guard(
            db,
            fund_id=int(batch.fund_id),
            settlement_batch_id=int(batch.id),
            request_id=request_id,
            amount_bnb=amount_bnb,
            metadata={
                "source": "buy_collection",
                "boundary": "ok_gas_wallet_to_user_wallet",
                "order_id": int(order.id),
                "user_id": int(order.user_id),
                "user_wallet_id": int(user_wallet.id),
                "from_address": str(ok_wallet_address),
                "to_address": str(user_address),
            },
        )
    except OperationGuardBlockedError as exc:
        error = (
            "Operation Guard blocked buy-collection BNB gas top-up "
            f"request_id={request_id}: {exc}"
        )

        _create_or_update_transfer(
            db,
            existing=existing,
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
            from_address=ok_wallet_address,
            to_address=user_address,
            amount_bnb=amount_bnb,
            status=TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
            error=error,
        )

        try:
            release_buy_reserve_if_safe(
                db,
                order_id=int(order.id),
                reason=error,
            )
        except Exception as reserve_exc:
            error = (
                f"{error}; "
                f"reserve_release_failed={reserve_exc}"
            )

        raise SettlementTransferError(error) from exc

    log.info(
        "Operation Guard allowed buy-collection BNB gas top-up "
        "batch_id=%s order_id=%s request_id=%s event_id=%s",
        batch.id,
        order.id,
        request_id,
        guard_decision.event_id,
    )

    intent_status = TRANSFER_STATUS_PENDING

    if (
        existing is not None
        and str(existing.status)
        in {
            TRANSFER_STATUS_PREPARED,
            TRANSFER_STATUS_PROCESSING,
        }
    ):
        intent_status = str(existing.status)

    intent_row = _create_or_update_transfer(
        db,
        existing=existing,
        batch_id=batch.id,
        order_id=order.id,
        fund_id=batch.fund_id,
        user_id=order.user_id,
        transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
        from_address=ok_wallet_address,
        to_address=user_address,
        amount_bnb=amount_bnb,
        request_key=request_id,
        status=intent_status,
        error=None,
    )

    prepared = prepared_transaction_from_transfer(
        intent_row
    )

    if prepared is None:
        prepared = prepare_native_bnb_transaction(
            w3,
            from_private_key=(
                settings.FEE_WALLET_OK_PRIVATE_KEY
            ),
            from_address=ok_wallet_address,
            to_address=user_address,
            amount_bnb=amount_bnb,
        )

        intent_row = persist_prepared_transfer_intent(
            db,
            transfer_id=int(intent_row.id),
            request_key=request_id,
            prepared=prepared,
        )

    try:
        broadcast_persisted_transfer_intent(
            db,
            w3=w3,
            transfer_id=int(intent_row.id),
            from_address=ok_wallet_address,
            copy_to_gas_tx_hash=True,
        )
    except BscIntentError as exc:
        log.warning(
            "Buy gas top-up remains pending reconciliation: "
            "transfer_id=%s error=%s",
            intent_row.id,
            exc,
        )
        return False

    return False


def _collect_buy_order_usdt(
    db: Session,
    *,
    w3: Web3,
    batch: FundSettlementBatch,
    order: FundOrder,
    user_wallet: UserWallet,
    settlement_wallet: FundWallet,
    dry_run: bool,
) -> bool:
    """
    Send/confirm user buy USDT -> fund settlement wallet.

    Returns True when collection is confirmed or dry-run completed.
    Returns False when tx is sent but not confirmed yet.
    """
    amount_usdt = _dec(order.amount_usdt)
    if amount_usdt <= 0:
        raise SettlementTransferError(f"Invalid buy order amount_usdt for order_id={order.id}")

    from_address = _checksum(w3, user_wallet.address)
    to_address = _checksum(w3, settlement_wallet.address)

    existing = _find_transfer(
        db,
        batch_id=batch.id,
        order_id=order.id,
        transfer_type=TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
    )

    if existing and existing.status == TRANSFER_STATUS_CONFIRMED:
        if order.status != ORDER_STATUS_BUY_COLLECTED:
            _confirm_buy_collection(
                db=db,
                order=order,
                wallet=user_wallet,
                amount_usdt=amount_usdt,
            )
        return True

    if existing and existing.tx_hash:
        if _check_tx_confirmed(w3, existing.tx_hash):
            existing.status = TRANSFER_STATUS_CONFIRMED
            existing.confirmed_at = utcnow()
            existing.updated_at = utcnow()
            db.add(existing)

            _confirm_buy_collection(
                db=db,
                order=order,
                wallet=user_wallet,
                amount_usdt=amount_usdt,
            )
            return True

        existing.status = TRANSFER_STATUS_SENT
        existing.updated_at = utcnow()
        db.add(existing)
        db.flush()

        order.status = ORDER_STATUS_BUY_COLLECTING
        db.add(order)
        db.flush()
        return False

    if existing is not None:
        prepared_existing = (
            prepared_transaction_from_transfer(
                existing
            )
        )

        if prepared_existing is not None:
            if dry_run:
                return False

            try:
                broadcast_persisted_transfer_intent(
                    db,
                    w3=w3,
                    transfer_id=int(existing.id),
                    from_address=from_address,
                )
            except BscIntentError as exc:
                log.warning(
                    "Prepared buy USDT collection remains "
                    "pending reconciliation: "
                    "transfer_id=%s error=%s",
                    existing.id,
                    exc,
                )
                return False

            return False

    order.status = ORDER_STATUS_BUY_COLLECTING
    db.add(order)
    db.flush()

    if dry_run:
        _create_or_update_transfer(
            db,
            existing=existing,
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
            from_address=from_address,
            to_address=to_address,
            amount_usdt=amount_usdt,
            status=TRANSFER_STATUS_SKIPPED,
            error="dry_run: would send user USDT to settlement wallet",
        )
        return True

    request_id = deterministic_buy_collection_usdt_request_id(
        batch_id=int(batch.id),
        order_id=int(order.id),
        user_wallet_id=int(user_wallet.id),
        amount_usdt=amount_usdt,
        to_address=to_address,
    )

    try:
        guard_decision = require_bsc_buy_collection_usdt_to_settlement_guard(
            db,
            fund_id=int(batch.fund_id),
            settlement_batch_id=int(batch.id),
            amount_usdt=amount_usdt,
            request_id=request_id,
            metadata={
                "source": "buy_collection",
                "boundary": "user_wallet_to_fund_settlement_wallet",
                "order_id": int(order.id),
                "user_id": int(order.user_id),
                "user_wallet_id": int(user_wallet.id),
                "from_address": str(from_address),
                "to_address": str(to_address),
            },
        )
    except OperationGuardBlockedError as exc:
        error = (
            "Operation Guard blocked buy-collection USDT transfer "
            f"request_id={request_id}: {exc}"
        )

        _create_or_update_transfer(
            db,
            existing=existing,
            batch_id=batch.id,
            order_id=order.id,
            fund_id=batch.fund_id,
            user_id=order.user_id,
            transfer_type=TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
            from_address=from_address,
            to_address=to_address,
            amount_usdt=amount_usdt,
            status=TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
            error=error,
        )

        _mark_order_failed(
            order,
            error=error,
        )
        db.add(order)
        db.flush()

        try:
            release_buy_reserve_if_safe(
                db,
                order_id=int(order.id),
                reason=error,
            )
        except Exception as reserve_exc:
            _mark_order_failed(
                order,
                error=(
                    f"reserve_release_failed={reserve_exc}"
                ),
            )
            db.add(order)
            db.flush()

        raise SettlementTransferError(
            str(order.error or error)
        ) from exc

    log.info(
        "Operation Guard allowed buy-collection USDT transfer "
        "batch_id=%s order_id=%s request_id=%s event_id=%s",
        batch.id,
        order.id,
        request_id,
        guard_decision.event_id,
    )

    private_key = decrypt_private_key(
        user_wallet.encrypted_private_key
    )

    intent_status = TRANSFER_STATUS_PENDING

    if (
        existing is not None
        and str(existing.status)
        in {
            TRANSFER_STATUS_PREPARED,
            TRANSFER_STATUS_PROCESSING,
        }
    ):
        intent_status = str(existing.status)

    intent_row = _create_or_update_transfer(
        db,
        existing=existing,
        batch_id=batch.id,
        order_id=order.id,
        fund_id=batch.fund_id,
        user_id=order.user_id,
        transfer_type=(
            TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT
        ),
        from_address=from_address,
        to_address=to_address,
        amount_usdt=amount_usdt,
        request_key=request_id,
        status=intent_status,
        error=None,
    )

    prepared = prepared_transaction_from_transfer(
        intent_row
    )

    if prepared is None:
        prepared = prepare_usdt_transfer_transaction(
            w3,
            from_private_key=private_key,
            from_address=from_address,
            to_address=to_address,
            amount_usdt=amount_usdt,
        )

        intent_row = persist_prepared_transfer_intent(
            db,
            transfer_id=int(intent_row.id),
            request_key=request_id,
            prepared=prepared,
        )

    try:
        broadcast_persisted_transfer_intent(
            db,
            w3=w3,
            transfer_id=int(intent_row.id),
            from_address=from_address,
        )
    except BscIntentError as exc:
        log.warning(
            "Buy USDT collection remains pending "
            "reconciliation: transfer_id=%s error=%s",
            intent_row.id,
            exc,
        )
        return False

    return False


def _get_buy_orders_for_batch(db: Session, *, batch_id: int) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id == batch_id,
            FundOrder.side == ORDER_SIDE_BUY,
            FundOrder.status.in_(
                [
                    ORDER_STATUS_SETTLING,
                    ORDER_STATUS_BUY_COLLECTING,
                    ORDER_STATUS_BUY_COLLECTED,
                ]
            ),
        )
        .order_by(FundOrder.created_at.asc(), FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _get_redeem_orders_for_batch(db: Session, *, batch_id: int) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id == batch_id,
            FundOrder.side == ORDER_SIDE_REDEEM,
            FundOrder.status == ORDER_STATUS_SETTLING,
        )
        .order_by(FundOrder.created_at.asc(), FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _update_terminal_batch_status(
    db: Session,
    *,
    batch: FundSettlementBatch,
) -> str:
    now = utcnow()

    if _dec(batch.net_cash_usdt) >= ZERO:
        final_status = BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION
        order_status = ORDER_STATUS_AWAITING_POSITIVE_NET_EXECUTION
    else:
        final_status = BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION
        order_status = ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION

    batch.status = BATCH_STATUS_BUY_USDT_COLLECTED
    batch.updated_at = now
    db.add(batch)
    db.flush()

    # Immediately move Stage 21 batch terminal state after buy collection.
    batch.status = final_status
    batch.updated_at = now
    db.add(batch)

    redeem_orders = _get_redeem_orders_for_batch(db, batch_id=batch.id)
    for order in redeem_orders:
        order.status = order_status
        db.add(order)

    db.flush()
    return final_status


def collect_buy_usdt_for_batch(
    db: Session,
    *,
    batch_id: int,
    dry_run: bool = False,
) -> BuyCollectionResult:
    """
    Collect buy-side USDT for a settlement batch.

    Stage 21 behavior:
    - can top up user wallet gas from OK gas wallet;
    - can send user buy USDT to fund settlement wallet;
    - records all actions in fund_settlement_transfers;
    - updates buy orders to buy_collected only after confirmed transfer;
    - decreases user wallet usdt_reserved/usdt_balance only after confirmed transfer.

    Does NOT:
    - call Bybit;
    - finalize redeem orders;
    - credit sellers with USDT;
    - change funds.shares_outstanding_current;
    - change user_fund_positions.shares.
    """
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise SettlementTransferError(f"Batch not found: {batch_id}")

    fund = db.query(Fund).filter(Fund.id == batch.fund_id).first()
    if fund is None:
        raise SettlementTransferError(f"Fund not found for batch_id={batch_id}")

    settlement_wallet = _get_active_fund_settlement_wallet(db, fund_id=batch.fund_id)
    buy_orders = _get_buy_orders_for_batch(db, batch_id=batch.id)

    now = utcnow()
    batch.status = BATCH_STATUS_COLLECTING_BUY_USDT
    batch.updated_at = now
    db.add(batch)
    db.flush()

    if not buy_orders:
        final_status = _update_terminal_batch_status(db, batch=batch)
        return BuyCollectionResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            fund_code=fund.code,
            buy_orders_count=0,
            collected_orders_count=0,
            pending_orders_count=0,
            failed_orders_count=0,
            batch_status=final_status,
            message="No buy orders in batch; moved to net execution waiting status.",
        )

    w3 = get_web3()

    collected = 0
    pending = 0
    failed = 0

    try:
        for order in buy_orders:
            if order.status == ORDER_STATUS_BUY_COLLECTED:
                collected += 1
                continue

            try:
                user_wallet = (
                    _get_active_user_wallet_for_update(
                        db,
                        user_id=order.user_id,
                    )
                )

                gas_ready = _ensure_user_wallet_gas(
                    db,
                    w3=w3,
                    batch=batch,
                    order=order,
                    user_wallet=user_wallet,
                    dry_run=dry_run,
                )

                if not gas_ready:
                    pending += 1
                    continue

                confirmed = _collect_buy_order_usdt(
                    db,
                    w3=w3,
                    batch=batch,
                    order=order,
                    user_wallet=user_wallet,
                    settlement_wallet=settlement_wallet,
                    dry_run=dry_run,
                )

                if confirmed:
                    collected += 1
                else:
                    pending += 1

            except Exception as exc:
                error = str(exc)

                _mark_order_failed(
                    order,
                    error=error,
                )

                try:
                    release_buy_reserve_if_safe(
                        db,
                        order_id=int(order.id),
                        reason=error,
                    )
                except Exception as reserve_exc:
                    _mark_order_failed(
                        order,
                        error=(
                            "reserve_release_failed="
                            f"{reserve_exc}"
                        ),
                    )

                db.add(order)
                db.flush()

                failed += 1

        if failed > 0:
            error = (
                f"{failed} buy orders failed "
                f"in batch {batch.id}"
            )

            _mark_batch_failed(
                batch,
                error=error,
            )
            db.add(batch)
            db.flush()

            return BuyCollectionResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                fund_code=fund.code,
                buy_orders_count=len(buy_orders),
                collected_orders_count=collected,
                pending_orders_count=pending,
                failed_orders_count=failed,
                batch_status=batch.status,
                message=error,
            )

        if pending > 0:
            batch.status = BATCH_STATUS_COLLECTING_BUY_USDT
            batch.updated_at = utcnow()
            db.add(batch)
            db.flush()

            return BuyCollectionResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                fund_code=fund.code,
                buy_orders_count=len(buy_orders),
                collected_orders_count=collected,
                pending_orders_count=pending,
                failed_orders_count=failed,
                batch_status=batch.status,
                message="Some buy transfers are still pending confirmation.",
            )

        final_status = _update_terminal_batch_status(db, batch=batch)

        return BuyCollectionResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            fund_code=fund.code,
            buy_orders_count=len(buy_orders),
            collected_orders_count=collected,
            pending_orders_count=0,
            failed_orders_count=0,
            batch_status=final_status,
            message="Buy-side USDT collection completed for Stage 21.",
        )

    except Exception as exc:
        error = str(exc)
        _mark_batch_failed(batch, error=error)

        db.add(batch)
        db.flush()

        _send_alert(
            "❌ Settlement buy-side collection failed\n"
            f"Fund: {fund.code}\n"
            f"Batch ID: {batch.id}\n"
            f"Error: {error}"
        )

        raise