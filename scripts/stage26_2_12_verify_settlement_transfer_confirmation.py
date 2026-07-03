from __future__ import annotations

import ast
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models import (
    Fund,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    FundSettlementTransfer,
    User,
    UserWallet,
)
from app.settlement import transfer_service
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_SIDE_BUY,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_BUY_COLLECTING,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_FAILED_REQUIRES_REVIEW,
    TRANSFER_STATUS_SENT,
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeEth:
    def __init__(self, *, receipt: dict[str, Any] | None, block_number: int = 200):
        self._receipt = receipt
        self.block_number = block_number
        self.send_raw_transaction_called = False

    def get_transaction_receipt(self, tx_hash: str):
        return self._receipt

    def send_raw_transaction(self, *_args, **_kwargs):
        self.send_raw_transaction_called = True
        raise AssertionError("send_raw_transaction must not be called")


class FakeWeb3:
    def __init__(self, *, receipt: dict[str, Any] | None, block_number: int = 200):
        self.eth = FakeEth(receipt=receipt, block_number=block_number)


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def ast_call_names(path: str) -> set[str]:
    tree = ast.parse(read(path))
    out: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                out.add(func.id)
            elif isinstance(func, ast.Attribute):
                out.add(func.attr)

    return out


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def patch_web3(fake: FakeWeb3):
    original = transfer_service.get_web3
    transfer_service.get_web3 = lambda: fake
    return original


def restore_web3(original) -> None:
    transfer_service.get_web3 = original


def create_fixture(db, *, tx_hash: str, locked_pricing: bool = False):
    suffix = uuid.uuid4().hex[:12]
    now = utcnow()

    fund = Fund(
        code=f"stage26_2_12_{suffix}",
        name_ru="Stage 26.2.12 Test",
        name_en="Stage 26.2.12 Test",
        category="test",
        sort_order=9999,
        is_active=True,
    )
    db.add(fund)
    db.flush()

    user = User(
        created_at=now,
        email=f"stage26_2_12_{suffix}@example.com",
        first_name="Stage",
        last_name="Test",
        phone=None,
        password_hash="not_used",
        is_active=True,
        is_email_verified=True,
        two_factor_enabled=True,
        account_type="tester",
        compliance_status="ok",
    )
    db.add(user)
    db.flush()

    wallet = UserWallet(
        user_id=user.id,
        blockchain="BSC",
        address="0x0000000000000000000000000000000000000011",
        encrypted_private_key="not_used",
        usdt_balance=Decimal("12"),
        usdt_reserved=Decimal("10"),
        compliance_status="ok",
        is_active=True,
    )
    db.add(wallet)
    db.flush()

    batch = FundSettlementBatch(
        fund_id=fund.id,
        settlement_date=date.today(),
        cutoff_ts=now,
        settlement_ts=now,
        settlement_price_usdt=Decimal("1"),
        total_buy_usdt=Decimal("10"),
        total_redeem_shares=Decimal("0"),
        total_redeem_usdt=Decimal("0"),
        net_cash_usdt=Decimal("10"),
        planned_shares_to_issue=Decimal("10"),
        planned_shares_to_redeem=Decimal("0"),
        planned_net_shares_change=Decimal("10"),
        status=BATCH_STATUS_COLLECTING_BUY_USDT,
        pricing_locked_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()

    if locked_pricing:
        db.add(
            FundRuntimeState(
                fund_id=fund.id,
                pricing_locked=True,
                pricing_lock_reason="settlement",
                pricing_lock_batch_id=batch.id,
                pricing_locked_at=now,
                pricing_unlocked_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()

    order = FundOrder(
        user_id=user.id,
        fund_id=fund.id,
        side=ORDER_SIDE_BUY,
        amount_usdt=Decimal("10"),
        shares=None,
        price_usdt=None,
        status=ORDER_STATUS_BUY_COLLECTING,
        settlement_batch_id=batch.id,
        reserved_at=now,
        settlement_locked_at=now,
        created_at=now,
        executed_at=None,
    )
    db.add(order)
    db.flush()

    transfer = FundSettlementTransfer(
        batch_id=batch.id,
        order_id=order.id,
        fund_id=fund.id,
        user_id=user.id,
        transfer_type=TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
        from_address=wallet.address,
        to_address="0x0000000000000000000000000000000000000022",
        amount_usdt=Decimal("10"),
        amount_bnb=None,
        tx_hash=tx_hash,
        status=TRANSFER_STATUS_SENT,
        attempts=1,
        created_at=now,
        updated_at=now,
        sent_at=now,
        confirmed_at=None,
    )
    db.add(transfer)
    db.flush()

    return fund, user, wallet, batch, order, transfer


def test_success_confirmation() -> None:
    db = SessionLocal()
    fake = FakeWeb3(receipt={"status": 1, "blockNumber": 100}, block_number=130)
    original = patch_web3(fake)

    try:
        fund, user, wallet, batch, order, transfer = create_fixture(db, tx_hash="0x" + "a" * 64)

        result = transfer_service.confirm_sent_settlement_transfer(
            db,
            int(transfer.id),
            min_confirmations=20,
        )

        db.flush()
        db.refresh(transfer)
        db.refresh(order)
        db.refresh(wallet)
        db.refresh(batch)

        assert_ok("SUCCESS_ACTION_CONFIRMED", result.action == "confirmed")
        assert_ok("SUCCESS_TRANSFER_CONFIRMED", transfer.status == TRANSFER_STATUS_CONFIRMED and transfer.confirmed_at is not None)
        assert_ok("SUCCESS_ORDER_BUY_COLLECTED", order.status == ORDER_STATUS_BUY_COLLECTED and order.collection_confirmed_at is not None)
        assert_ok("SUCCESS_WALLET_BALANCE_DECREASED_ONCE", Decimal(wallet.usdt_balance) == Decimal("2"))
        assert_ok("SUCCESS_WALLET_RESERVED_DECREASED_ONCE", Decimal(wallet.usdt_reserved) == Decimal("0"))
        assert_ok("SUCCESS_BATCH_ADVANCED", batch.status == BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION)

        print("STAGE26_2_12_SETTLEMENT_TRANSFER_CONFIRMATION_SUCCESS_OK")

    finally:
        db.rollback()
        db.close()
        restore_web3(original)


def test_idempotency() -> None:
    db = SessionLocal()
    fake = FakeWeb3(receipt={"status": 1, "blockNumber": 100}, block_number=130)
    original = patch_web3(fake)

    try:
        fund, user, wallet, batch, order, transfer = create_fixture(db, tx_hash="0x" + "b" * 64)

        transfer_service.confirm_sent_settlement_transfer(db, int(transfer.id), min_confirmations=20)
        db.flush()
        db.refresh(wallet)

        balance_after_first = Decimal(wallet.usdt_balance)
        reserved_after_first = Decimal(wallet.usdt_reserved)

        second = transfer_service.confirm_sent_settlement_transfer(db, int(transfer.id), min_confirmations=20)
        db.flush()
        db.refresh(wallet)
        db.refresh(order)

        assert_ok("IDEMPOTENCY_SECOND_ALREADY_CONFIRMED", second.action == "already_confirmed")
        assert_ok("IDEMPOTENCY_BALANCE_NOT_DECREMENTED_TWICE", Decimal(wallet.usdt_balance) == balance_after_first == Decimal("2"))
        assert_ok("IDEMPOTENCY_RESERVED_NOT_DECREMENTED_TWICE", Decimal(wallet.usdt_reserved) == reserved_after_first == Decimal("0"))
        assert_ok("IDEMPOTENCY_ORDER_STILL_COLLECTED", order.status == ORDER_STATUS_BUY_COLLECTED)

        print("STAGE26_2_12_SETTLEMENT_TRANSFER_CONFIRMATION_IDEMPOTENCY_OK")

    finally:
        db.rollback()
        db.close()
        restore_web3(original)


def test_pending_confirmation() -> None:
    db = SessionLocal()
    fake = FakeWeb3(receipt={"status": 1, "blockNumber": 120}, block_number=125)
    original = patch_web3(fake)

    try:
        fund, user, wallet, batch, order, transfer = create_fixture(db, tx_hash="0x" + "c" * 64)

        result = transfer_service.confirm_sent_settlement_transfer(
            db,
            int(transfer.id),
            min_confirmations=20,
        )

        db.flush()
        db.refresh(transfer)
        db.refresh(order)
        db.refresh(wallet)

        assert_ok("PENDING_ACTION_PENDING", result.action == "pending")
        assert_ok("PENDING_TRANSFER_STILL_SENT", transfer.status == TRANSFER_STATUS_SENT and transfer.confirmed_at is None)
        assert_ok("PENDING_ORDER_STILL_COLLECTING", order.status == ORDER_STATUS_BUY_COLLECTING)
        assert_ok("PENDING_WALLET_BALANCE_UNCHANGED", Decimal(wallet.usdt_balance) == Decimal("12"))
        assert_ok("PENDING_WALLET_RESERVED_UNCHANGED", Decimal(wallet.usdt_reserved) == Decimal("10"))

        print("STAGE26_2_12_SETTLEMENT_TRANSFER_CONFIRMATION_PENDING_OK")

    finally:
        db.rollback()
        db.close()
        restore_web3(original)


def test_failed_confirmation() -> None:
    db = SessionLocal()
    fake = FakeWeb3(receipt={"status": 0, "blockNumber": 100}, block_number=130)
    original = patch_web3(fake)

    try:
        fund, user, wallet, batch, order, transfer = create_fixture(
            db,
            tx_hash="0x" + "d" * 64,
            locked_pricing=True,
        )

        result = transfer_service.confirm_sent_settlement_transfer(
            db,
            int(transfer.id),
            min_confirmations=20,
        )

        db.flush()
        db.refresh(transfer)
        db.refresh(order)
        db.refresh(wallet)
        db.refresh(batch)

        state = db.query(FundRuntimeState).filter(FundRuntimeState.fund_id == fund.id).first()

        assert_ok("FAILURE_ACTION_FAILED", result.action == "failed")
        assert_ok("FAILURE_TRANSFER_FAILED_REVIEW", transfer.status == TRANSFER_STATUS_FAILED_REQUIRES_REVIEW)
        assert_ok("FAILURE_ORDER_FAILED_REVIEW", order.status == ORDER_STATUS_FAILED_REQUIRES_REVIEW)
        assert_ok("FAILURE_BATCH_FAILED_REVIEW", batch.status == BATCH_STATUS_FAILED_REQUIRES_REVIEW)
        assert_ok("FAILURE_RESERVED_RELEASED", Decimal(wallet.usdt_reserved) == Decimal("0"))
        assert_ok("FAILURE_BALANCE_NOT_DECREASED", Decimal(wallet.usdt_balance) == Decimal("12"))
        assert_ok("FAILURE_PRICING_UNLOCKED", state is None or state.pricing_locked is False)

        print("STAGE26_2_12_SETTLEMENT_TRANSFER_CONFIRMATION_FAILURE_OK")

    finally:
        db.rollback()
        db.close()
        restore_web3(original)


def test_worker_safety() -> None:
    worker_source = read("workers/fund_settlement_transfer_confirmation_worker.py")
    worker_calls = ast_call_names("workers/fund_settlement_transfer_confirmation_worker.py")

    forbidden = {
        "_send_usdt_transfer",
        "send_native_bnb",
        "send_raw_transaction",
    }

    assert_ok(
        "WORKER_NO_FORBIDDEN_CALLS",
        all(name not in worker_calls and name not in worker_source for name in forbidden),
    )
    assert_ok(
        "WORKER_IMPORTS_CONFIRMATION_SERVICE",
        "confirm_sent_settlement_transfer" in worker_source,
    )

    db = SessionLocal()
    fake = FakeWeb3(receipt={"status": 1, "blockNumber": 100}, block_number=130)
    original = patch_web3(fake)

    try:
        fund, user, wallet, batch, order, transfer = create_fixture(db, tx_hash="0x" + "e" * 64)

        result = transfer_service.confirm_sent_settlement_transfer(
            db,
            int(transfer.id),
            dry_run=True,
            min_confirmations=20,
        )

        db.flush()
        db.refresh(transfer)
        db.refresh(order)
        db.refresh(wallet)

        assert_ok("DRY_RUN_WOULD_CONFIRM", result.action == "dry_run_would_confirm")
        assert_ok("DRY_RUN_TRANSFER_UNCHANGED", transfer.status == TRANSFER_STATUS_SENT and transfer.confirmed_at is None)
        assert_ok("DRY_RUN_ORDER_UNCHANGED", order.status == ORDER_STATUS_BUY_COLLECTING)
        assert_ok("DRY_RUN_WALLET_UNCHANGED", Decimal(wallet.usdt_balance) == Decimal("12") and Decimal(wallet.usdt_reserved) == Decimal("10"))
        assert_ok("FAKE_WEB3_NO_RAW_TX", fake.eth.send_raw_transaction_called is False)

        print("STAGE26_2_12_SETTLEMENT_TRANSFER_CONFIRMATION_WORKER_SAFETY_OK")

    finally:
        db.rollback()
        db.close()
        restore_web3(original)


def main() -> int:
    load_dotenv()

    test_success_confirmation()
    test_idempotency()
    test_pending_confirmation()
    test_failed_confirmation()
    test_worker_safety()

    print("STAGE26_2_12_SETTLEMENT_TRANSFER_CONFIRMATION_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())