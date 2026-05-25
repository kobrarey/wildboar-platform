from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.bybit.client import BybitApiError, BybitV5Client
from app.config import settings
from app.models import FundSettlementBatch, FundSettlementTransfer, FundWallet
from app.settlement.bybit_destination import get_active_bybit_deposit_destination
from app.settlement.gas_service import get_web3
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_PENDING_CONFIRMATION,
    BATCH_STATUS_POSITIVE_NET_PROCESSING,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
    TRANSFER_STATUS_PENDING_CONFIRMATION,
    TRANSFER_STATUS_SENT,
    TRANSFER_STATUS_SKIPPED,
    TRANSFER_TYPE_POSITIVE_NET_SETTLEMENT_TO_BYBIT_SUBACCOUNT,
)
from app.settlement.transfer_service import _check_tx_confirmed, _send_usdt_transfer
from app.telegram import send_telegram_message
from app.wallets import decrypt_private_key


log = logging.getLogger("settlement.bybit_deposit_service")

ZERO = Decimal("0")


class BybitDepositSettlementError(RuntimeError):
    pass


@dataclass(frozen=True)
class PositiveNetTransferPlan:
    batch_id: int
    fund_id: int
    amount_usdt: Decimal
    from_address: str
    to_address: str
    bybit_sub_uid: str
    chain_type: str
    existing_transfer_status: str | None
    existing_tx_hash: str | None


@dataclass(frozen=True)
class PositiveNetTransferResult:
    batch_id: int
    fund_id: int
    amount_usdt: Decimal
    transfer_status: str
    tx_hash: str | None
    bybit_deposit_confirmed: bool
    message: str


@dataclass(frozen=True)
class BybitDepositRecord:
    tx_hash: str
    coin: str
    chain: str | None
    chain_type: str | None
    amount: Decimal
    status: str | None
    success_at: str | None
    account_type: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class BybitInternalTransferResult:
    transfer_id: str
    status: str
    completed: bool
    raw: dict[str, Any]


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
        log.warning("Bybit deposit settlement Telegram alert failed: %s", exc)


def _get_batch_for_update(db: Session, *, batch_id: int) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        raise BybitDepositSettlementError(f"Batch not found: {batch_id}")

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
        raise BybitDepositSettlementError(
            f"Active settlement wallet not found for fund_id={fund_id}"
        )

    return wallet


def _find_positive_net_transfer_for_update(
    db: Session,
    *,
    batch_id: int,
) -> FundSettlementTransfer | None:
    return (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.batch_id == batch_id,
            FundSettlementTransfer.transfer_type == TRANSFER_TYPE_POSITIVE_NET_SETTLEMENT_TO_BYBIT_SUBACCOUNT,
        )
        .with_for_update()
        .first()
    )


def _create_or_update_positive_net_transfer(
    db: Session,
    *,
    existing: FundSettlementTransfer | None,
    batch: FundSettlementBatch,
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
            order_id=None,
            fund_id=batch.fund_id,
            user_id=None,
            transfer_type=TRANSFER_TYPE_POSITIVE_NET_SETTLEMENT_TO_BYBIT_SUBACCOUNT,
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
) -> None:
    now = utcnow()

    batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now

    transfer = _find_positive_net_transfer_for_update(db, batch_id=batch.id)
    if transfer is not None:
        transfer.status = TRANSFER_STATUS_FAILED_REQUIRES_REVIEW
        transfer.error = error
        transfer.updated_at = now
        db.add(transfer)

    db.add(batch)
    db.flush()


def plan_positive_net_transfer_for_batch(
    db: Session,
    *,
    batch_id: int,
) -> PositiveNetTransferPlan | None:
    batch = _get_batch_for_update(db, batch_id=batch_id)

    amount_usdt = _dec(batch.net_cash_usdt)
    if amount_usdt < 0:
        raise BybitDepositSettlementError(
            f"Batch {batch.id} is not positive-net: net_cash_usdt={amount_usdt}"
        )

    if amount_usdt == 0:
        return None

    settlement_wallet = _get_active_settlement_wallet(db, fund_id=batch.fund_id)

    destination = get_active_bybit_deposit_destination(
        db,
        fund_id=batch.fund_id,
        coin="USDT",
        chain_type="BSC",
    )

    existing = _find_positive_net_transfer_for_update(db, batch_id=batch.id)

    return PositiveNetTransferPlan(
        batch_id=batch.id,
        fund_id=batch.fund_id,
        amount_usdt=amount_usdt,
        from_address=settlement_wallet.address,
        to_address=destination.deposit_address,
        bybit_sub_uid=destination.bybit_sub_uid,
        chain_type=destination.chain_type,
        existing_transfer_status=existing.status if existing else None,
        existing_tx_hash=existing.tx_hash if existing else None,
    )


def send_or_confirm_positive_net_transfer(
    db: Session,
    *,
    batch_id: int,
    dry_run: bool = False,
    mock_confirm: bool = False,
) -> PositiveNetTransferResult:
    """
    Send/confirm positive net USDT transfer:
        fund settlement wallet -> Bybit subaccount deposit address.

    Does not commit.
    Caller controls transaction boundary.

    dry_run=True:
        - does not send USDT;
        - writes skipped transfer row if caller later commits.
        Usually use inside rollback check.

    mock_confirm=True:
        - marks transfer confirmed without sending USDT.
        Use only for local mocked checks.
    """
    batch = _get_batch_for_update(db, batch_id=batch_id)

    if batch.status not in {
        BATCH_STATUS_POSITIVE_NET_PROCESSING,
        BATCH_STATUS_PENDING_CONFIRMATION,
    }:
        raise BybitDepositSettlementError(
            f"Batch {batch.id} has invalid status for positive net transfer: {batch.status}"
        )

    amount_usdt = _dec(batch.net_cash_usdt)
    if amount_usdt < 0:
        raise BybitDepositSettlementError(
            f"Batch {batch.id} is not positive-net: net_cash_usdt={amount_usdt}"
        )

    if amount_usdt == 0:
        batch.bybit_deposit_confirmed_at = utcnow()
        batch.bybit_deposit_account_type = "skipped_zero_net"
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()

        return PositiveNetTransferResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            amount_usdt=ZERO,
            transfer_status=TRANSFER_STATUS_SKIPPED,
            tx_hash=None,
            bybit_deposit_confirmed=True,
            message="Positive net is zero; Bybit transfer skipped.",
        )

    settlement_wallet = _get_active_settlement_wallet(db, fund_id=batch.fund_id)

    destination = get_active_bybit_deposit_destination(
        db,
        fund_id=batch.fund_id,
        coin="USDT",
        chain_type="BSC",
    )

    existing = _find_positive_net_transfer_for_update(db, batch_id=batch.id)

    try:
        if existing is not None and existing.status == TRANSFER_STATUS_CONFIRMED:
            batch.bybit_deposit_tx_hash = existing.tx_hash
            db.add(batch)
            db.flush()

            return PositiveNetTransferResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                amount_usdt=amount_usdt,
                transfer_status=TRANSFER_STATUS_CONFIRMED,
                tx_hash=existing.tx_hash,
                bybit_deposit_confirmed=batch.bybit_deposit_confirmed_at is not None,
                message="Existing positive net transfer already confirmed.",
            )

        if existing is not None and existing.tx_hash:
            w3 = None if dry_run or mock_confirm else get_web3()

            if w3 is not None and _check_tx_confirmed(w3, existing.tx_hash):
                _create_or_update_positive_net_transfer(
                    db,
                    existing=existing,
                    batch=batch,
                    from_address=settlement_wallet.address,
                    to_address=destination.deposit_address,
                    amount_usdt=amount_usdt,
                    status=TRANSFER_STATUS_CONFIRMED,
                    tx_hash=existing.tx_hash,
                    error=None,
                )
                batch.bybit_deposit_tx_hash = existing.tx_hash
                batch.updated_at = utcnow()
                db.add(batch)
                db.flush()

                return PositiveNetTransferResult(
                    batch_id=batch.id,
                    fund_id=batch.fund_id,
                    amount_usdt=amount_usdt,
                    transfer_status=TRANSFER_STATUS_CONFIRMED,
                    tx_hash=existing.tx_hash,
                    bybit_deposit_confirmed=False,
                    message="BSC transfer confirmed; waiting for Bybit deposit confirmation.",
                )

            _create_or_update_positive_net_transfer(
                db,
                existing=existing,
                batch=batch,
                from_address=settlement_wallet.address,
                to_address=destination.deposit_address,
                amount_usdt=amount_usdt,
                status=TRANSFER_STATUS_PENDING_CONFIRMATION,
                tx_hash=existing.tx_hash,
                error=None,
            )
            batch.status = BATCH_STATUS_PENDING_CONFIRMATION
            batch.updated_at = utcnow()
            db.add(batch)
            db.flush()

            return PositiveNetTransferResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                amount_usdt=amount_usdt,
                transfer_status=TRANSFER_STATUS_PENDING_CONFIRMATION,
                tx_hash=existing.tx_hash,
                bybit_deposit_confirmed=False,
                message="Positive net transfer pending BSC confirmation.",
            )

        if dry_run:
            row = _create_or_update_positive_net_transfer(
                db,
                existing=existing,
                batch=batch,
                from_address=settlement_wallet.address,
                to_address=destination.deposit_address,
                amount_usdt=amount_usdt,
                status=TRANSFER_STATUS_SKIPPED,
                tx_hash=None,
                error="dry_run: positive net transfer would be sent",
            )

            return PositiveNetTransferResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                amount_usdt=amount_usdt,
                transfer_status=row.status,
                tx_hash=None,
                bybit_deposit_confirmed=False,
                message="Dry-run: positive net transfer skipped.",
            )

        if mock_confirm:
            mock_tx_hash = f"mock_positive_net_tx_{batch.id}"

            row = _create_or_update_positive_net_transfer(
                db,
                existing=existing,
                batch=batch,
                from_address=settlement_wallet.address,
                to_address=destination.deposit_address,
                amount_usdt=amount_usdt,
                status=TRANSFER_STATUS_CONFIRMED,
                tx_hash=mock_tx_hash,
                error=None,
            )

            batch.bybit_deposit_tx_hash = row.tx_hash
            batch.updated_at = utcnow()
            db.add(batch)
            db.flush()

            return PositiveNetTransferResult(
                batch_id=batch.id,
                fund_id=batch.fund_id,
                amount_usdt=amount_usdt,
                transfer_status=row.status,
                tx_hash=row.tx_hash,
                bybit_deposit_confirmed=False,
                message="Mock-confirm: positive net transfer marked confirmed.",
            )

        w3 = get_web3()
        private_key = decrypt_private_key(settlement_wallet.encrypted_private_key)

        tx_hash = _send_usdt_transfer(
            w3,
            from_private_key=private_key,
            from_address=settlement_wallet.address,
            to_address=destination.deposit_address,
            amount_usdt=amount_usdt,
        )

        row = _create_or_update_positive_net_transfer(
            db,
            existing=existing,
            batch=batch,
            from_address=settlement_wallet.address,
            to_address=destination.deposit_address,
            amount_usdt=amount_usdt,
            status=TRANSFER_STATUS_SENT,
            tx_hash=tx_hash,
            error=None,
        )

        batch.bybit_deposit_tx_hash = tx_hash
        batch.status = BATCH_STATUS_PENDING_CONFIRMATION
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()

        return PositiveNetTransferResult(
            batch_id=batch.id,
            fund_id=batch.fund_id,
            amount_usdt=amount_usdt,
            transfer_status=row.status,
            tx_hash=tx_hash,
            bybit_deposit_confirmed=False,
            message="Positive net transfer sent; waiting confirmation.",
        )

    except Exception as exc:
        error = str(exc)
        _mark_batch_failed_requires_review(db, batch=batch, error=error)

        _send_alert(
            "❌ Positive net transfer failed\n"
            f"Batch ID: {batch.id}\n"
            f"Fund ID: {batch.fund_id}\n"
            f"Error: {error}"
        )

        raise


def _extract_deposit_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result", {}) or {}

    for key in ["rows", "list", "data"]:
        value = result.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]

    return []


def query_bybit_sub_member_deposit_records(
    client: BybitV5Client,
    *,
    sub_member_id: str,
    coin: str = "USDT",
    tx_hash: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "subMemberId": str(sub_member_id),
        "coin": coin,
        "limit": limit,
    }

    if tx_hash:
        params["txID"] = tx_hash

    try:
        payload = client.get("/v5/asset/deposit/query-sub-member-record", params)
        return _extract_deposit_records(payload)
    except BybitApiError:
        if not tx_hash:
            raise

    # Fallback without txID: some Bybit endpoints ignore/reject exact txID filter.
    payload = client.get(
        "/v5/asset/deposit/query-sub-member-record",
        {
            "subMemberId": str(sub_member_id),
            "coin": coin,
            "limit": limit,
        },
    )
    return _extract_deposit_records(payload)


def query_bybit_deposit_records(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
    tx_hash: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Master-account fallback only. Positive-net fund settlement must use
    query_bybit_sub_member_deposit_records(...).
    """
    params: dict[str, Any] = {
        "coin": coin,
        "limit": limit,
    }

    if tx_hash:
        params["txID"] = tx_hash

    try:
        payload = client.get("/v5/asset/deposit/query-record", params)
        return _extract_deposit_records(payload)
    except BybitApiError:
        if not tx_hash:
            raise

    payload = client.get(
        "/v5/asset/deposit/query-record",
        {
            "coin": coin,
            "limit": limit,
        },
    )
    return _extract_deposit_records(payload)


def _record_tx_hash(record: dict[str, Any]) -> str:
    return str(
        record.get("txID")
        or record.get("txId")
        or record.get("txid")
        or record.get("txHash")
        or record.get("transactionHash")
        or ""
    ).strip()


def _record_amount(record: dict[str, Any]) -> Decimal:
    raw = (
        record.get("amount")
        or record.get("coinAmount")
        or record.get("depositAmount")
        or "0"
    )
    return _dec(raw)


def _record_coin(record: dict[str, Any]) -> str:
    return str(record.get("coin") or "").strip().upper()


def _record_chain_values(record: dict[str, Any]) -> set[str]:
    out: set[str] = set()

    for key in ["chain", "chainType", "chain_type"]:
        value = str(record.get(key) or "").strip()
        if value:
            out.add(value)
            out.add(value.lower())

    return out


def _record_status(record: dict[str, Any]) -> str | None:
    value = record.get("status")
    if value is None:
        value = record.get("depositStatus")
    if value is None:
        value = record.get("state")
    return str(value) if value is not None else None


def _record_success_at(record: dict[str, Any]) -> str | None:
    value = (
        record.get("successAt")
        or record.get("successTime")
        or record.get("updatedTime")
        or record.get("createTime")
    )
    return str(value) if value is not None else None


def _record_account_type(record: dict[str, Any]) -> str | None:
    value = (
        record.get("accountType")
        or record.get("toAccountType")
        or record.get("walletType")
    )
    return str(value) if value is not None else None


def _record_to_address(record: dict[str, Any]) -> str | None:
    value = (
        record.get("toAddress")
        or record.get("address")
        or record.get("depositAddress")
        or record.get("addressDeposit")
    )
    return str(value).strip() if value else None


def find_matching_deposit_record(
    *,
    records: list[dict[str, Any]],
    tx_hash: str,
    expected_amount: Decimal,
    dust_tolerance: Decimal,
    coin: str = "USDT",
    chain_type: str = "BSC",
    expected_to_address: str | None = None,
) -> BybitDepositRecord | None:
    target_tx = tx_hash.lower()
    coin_norm = coin.strip().upper()
    chain_norm = chain_type.strip().lower()

    for record in records:
        record_tx = _record_tx_hash(record).lower()
        if record_tx != target_tx:
            continue

        if _record_coin(record) != coin_norm:
            continue

        chain_values = _record_chain_values(record)
        if chain_values and chain_norm not in chain_values and not any(
            chain_norm in value.lower() for value in chain_values
        ):
            continue

        record_to_address = _record_to_address(record)
        if expected_to_address and record_to_address:
            if record_to_address.lower() != expected_to_address.lower():
                continue

        amount = _record_amount(record)
        if amount + dust_tolerance < expected_amount:
            raise BybitDepositSettlementError(
                f"Bybit deposit amount mismatch: tx={tx_hash} "
                f"expected={expected_amount}, actual={amount}, tolerance={dust_tolerance}"
            )

        return BybitDepositRecord(
            tx_hash=tx_hash,
            coin=coin_norm,
            chain=record.get("chain"),
            chain_type=record.get("chainType") or record.get("chain_type"),
            amount=amount,
            status=_record_status(record),
            success_at=_record_success_at(record),
            account_type=_record_account_type(record),
            raw=record,
        )

    return None


def confirm_bybit_deposit_for_batch(
    db: Session,
    *,
    batch_id: int,
    client: BybitV5Client | None = None,
    mock_confirm: bool = False,
) -> bool:
    """
    Confirm Bybit deposit record for positive-net settlement.

    Does not commit.
    Caller controls transaction boundary.

    mock_confirm=True:
        - marks Bybit deposit as confirmed without API call.
        Use only for local mocked checks.
    """
    batch = _get_batch_for_update(db, batch_id=batch_id)

    amount_usdt = _dec(batch.net_cash_usdt)
    if amount_usdt < 0:
        raise BybitDepositSettlementError(
            f"Batch {batch.id} is not positive-net: net_cash_usdt={amount_usdt}"
        )

    if amount_usdt == 0:
        batch.bybit_deposit_confirmed_at = batch.bybit_deposit_confirmed_at or utcnow()
        batch.bybit_deposit_account_type = batch.bybit_deposit_account_type or "skipped_zero_net"
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()
        return True

    transfer = _find_positive_net_transfer_for_update(db, batch_id=batch.id)
    if transfer is None:
        raise BybitDepositSettlementError(
            f"Positive net transfer row not found for batch {batch.id}"
        )

    if transfer.status != TRANSFER_STATUS_CONFIRMED:
        raise BybitDepositSettlementError(
            f"Positive net transfer is not BSC-confirmed for batch {batch.id}: "
            f"status={transfer.status}"
        )

    tx_hash = transfer.tx_hash or batch.bybit_deposit_tx_hash
    if not tx_hash:
        raise BybitDepositSettlementError(
            f"Positive net transfer tx_hash missing for batch {batch.id}"
        )

    if batch.bybit_deposit_confirmed_at is not None:
        return True

    if mock_confirm:
        batch.bybit_deposit_tx_hash = tx_hash
        batch.bybit_deposit_confirmed_at = utcnow()
        batch.bybit_deposit_account_type = "mock_confirmed"
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()
        return True

    if client is None:
        raise BybitDepositSettlementError(
            "Bybit client is required unless mock_confirm=True"
        )

    destination = get_active_bybit_deposit_destination(
        db,
        fund_id=batch.fund_id,
        coin="USDT",
        chain_type="BSC",
    )

    records = query_bybit_sub_member_deposit_records(
        client,
        sub_member_id=destination.bybit_sub_uid,
        coin="USDT",
        tx_hash=tx_hash,
    )

    record = find_matching_deposit_record(
        records=records,
        tx_hash=tx_hash,
        expected_amount=amount_usdt,
        dust_tolerance=Decimal(settings.POSITIVE_NET_DUST_TOLERANCE_USDT),
        coin="USDT",
        chain_type=destination.chain_type,
        expected_to_address=destination.deposit_address,
    )

    if record is None:
        batch.status = BATCH_STATUS_PENDING_CONFIRMATION
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()
        return False

    batch.bybit_deposit_tx_hash = tx_hash
    batch.bybit_deposit_confirmed_at = utcnow()
    batch.bybit_deposit_account_type = record.account_type
    batch.updated_at = utcnow()

    db.add(batch)
    db.flush()
    return True


def deterministic_internal_transfer_id(
    *,
    batch_id: int,
    fund_id: int,
) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"positive-net-fund-to-unified:{batch_id}:{fund_id}",
        )
    )


def _parse_internal_transfer_status(payload: dict[str, Any]) -> str:
    result = payload.get("result", {}) or {}

    raw = (
        result.get("status")
        or result.get("transferStatus")
        or payload.get("status")
        or "SUCCESS"
    )

    return str(raw).strip().upper()


def execute_fund_to_unified_internal_transfer(
    client: BybitV5Client,
    *,
    transfer_id: str,
    amount_usdt: Decimal,
) -> BybitInternalTransferResult:
    if amount_usdt <= 0:
        return BybitInternalTransferResult(
            transfer_id=transfer_id,
            status="SKIPPED_ZERO_AMOUNT",
            completed=True,
            raw={},
        )

    payload = {
        "transferId": transfer_id,
        "coin": "USDT",
        "amount": str(amount_usdt),
        "fromAccountType": "FUND",
        "toAccountType": "UNIFIED",
    }

    response = client.post("/v5/asset/transfer/inter-transfer", payload)
    status = _parse_internal_transfer_status(response)

    if status in {"SUCCESS", "SUCCEEDED"}:
        return BybitInternalTransferResult(
            transfer_id=transfer_id,
            status=status,
            completed=True,
            raw=response,
        )

    if status in {"PENDING", "STATUS_UNKNOWN", "UNKNOWN"}:
        return BybitInternalTransferResult(
            transfer_id=transfer_id,
            status=status,
            completed=False,
            raw=response,
        )

    raise BybitDepositSettlementError(
        f"Bybit FUND -> UNIFIED internal transfer failed: "
        f"transferId={transfer_id} status={status} response={response}"
    )


def ensure_fund_to_unified_internal_transfer(
    db: Session,
    *,
    batch_id: int,
    client: BybitV5Client | None,
    dry_run: bool = False,
    mock_confirm: bool = False,
) -> bool:
    batch = _get_batch_for_update(db, batch_id=batch_id)

    amount_usdt = _dec(batch.net_cash_usdt)
    now = utcnow()

    if amount_usdt == 0:
        batch.bybit_internal_transfer_id = batch.bybit_internal_transfer_id or "skipped_zero_net"
        batch.bybit_internal_transfer_completed_at = batch.bybit_internal_transfer_completed_at or now
        batch.updated_at = now
        db.add(batch)
        db.flush()
        return True

    if batch.bybit_internal_transfer_completed_at is not None:
        return True

    account_type = (batch.bybit_deposit_account_type or "").strip().lower()

    if account_type in {"unified", "unifiedtrading", "uta"}:
        batch.bybit_internal_transfer_id = batch.bybit_internal_transfer_id or f"skipped_{account_type}"
        batch.bybit_internal_transfer_completed_at = now
        batch.updated_at = now
        db.add(batch)
        db.flush()
        return True

    # Real-mode rule:
    # FUND or unknown account type requires FUND -> UNIFIED transfer before accounting.
    transfer_id = batch.bybit_internal_transfer_id or deterministic_internal_transfer_id(
        batch_id=batch.id,
        fund_id=batch.fund_id,
    )
    batch.bybit_internal_transfer_id = transfer_id
    db.add(batch)
    db.flush()

    if dry_run:
        batch.status = BATCH_STATUS_PENDING_CONFIRMATION
        batch.updated_at = now
        db.add(batch)
        db.flush()
        return False

    if mock_confirm:
        batch.bybit_internal_transfer_completed_at = now
        batch.updated_at = now
        db.add(batch)
        db.flush()
        return True

    if client is None:
        raise BybitDepositSettlementError(
            "Bybit client is required for real FUND -> UNIFIED internal transfer"
        )

    result = execute_fund_to_unified_internal_transfer(
        client,
        transfer_id=transfer_id,
        amount_usdt=amount_usdt,
    )

    if result.completed:
        batch.bybit_internal_transfer_completed_at = now
        batch.updated_at = now
        db.add(batch)
        db.flush()
        return True

    batch.status = BATCH_STATUS_PENDING_CONFIRMATION
    batch.updated_at = now
    db.add(batch)
    db.flush()
    return False