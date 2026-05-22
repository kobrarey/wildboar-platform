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
from app.settlement.gas_service import (
    WEI_PER_BNB,
    get_bnb_balance,
    get_web3,
    send_native_bnb,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_STATUS_FAILED,
    BATCH_STATUS_GAS_READY,
    BATCH_STATUS_BUY_USDT_COLLECTED,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_BUY_COLLECTING,
    ORDER_STATUS_SETTLING,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_FAILED,
    TRANSFER_STATUS_PENDING,
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


def _check_tx_confirmed(w3: Web3, tx_hash: str | None) -> bool:
    if not tx_hash:
        return False

    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return False

    if receipt is None:
        return False

    return int(receipt.get("status", 0)) == 1


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
    batch.status = BATCH_STATUS_FAILED
    batch.error = error
    batch.updated_at = now


def _mark_order_failed(order: FundOrder, *, error: str) -> None:
    order.error = error


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
    ok_bnb = get_bnb_balance(w3, ok_wallet_address)

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

        raise SettlementTransferError(error)

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

    tx_hash = send_native_bnb(
        w3,
        from_private_key=settings.FEE_WALLET_OK_PRIVATE_KEY,
        from_address=ok_wallet_address,
        to_address=user_address,
        amount_bnb=amount_bnb,
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
        tx_hash=tx_hash,
        gas_tx_hash=tx_hash,
        status=TRANSFER_STATUS_SENT,
        error=None,
    )

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

    private_key = decrypt_private_key(user_wallet.encrypted_private_key)

    tx_hash = _send_usdt_transfer(
        w3,
        from_private_key=private_key,
        from_address=from_address,
        to_address=to_address,
        amount_usdt=amount_usdt,
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
        tx_hash=tx_hash,
        status=TRANSFER_STATUS_SENT,
        error=None,
    )

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
        .with_for_update(skip_locked=True)
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
        .with_for_update(skip_locked=True)
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

            user_wallet = _get_active_user_wallet_for_update(db, user_id=order.user_id)

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
                if dry_run:
                    # In dry-run we simulate successful state-machine path without final balance mutation.
                    collected += 1
                else:
                    collected += 1
            else:
                pending += 1

        if failed > 0:
            raise SettlementTransferError(f"{failed} buy orders failed in batch {batch.id}")

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

        for order in buy_orders:
            if order.status != ORDER_STATUS_BUY_COLLECTED:
                _mark_order_failed(order, error=error)
                db.add(order)

        db.add(batch)
        db.flush()

        _send_alert(
            "❌ Settlement buy-side collection failed\n"
            f"Fund: {fund.code}\n"
            f"Batch ID: {batch.id}\n"
            f"Error: {error}"
        )

        raise