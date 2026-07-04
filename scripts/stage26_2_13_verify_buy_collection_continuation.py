from __future__ import annotations

import ast
import argparse
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models import (
    Fund,
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
    FundWallet,
    User,
    UserWallet,
)
from app.settlement import transfer_service
from app.settlement.buy_collection_continuation import (
    continue_buy_collection_for_active_batches,
    scan_active_collecting_buy_usdt_batch_ids,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_SIDE_BUY,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_BUY_COLLECTING,
    ORDER_STATUS_SETTLING,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_SENT,
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
    TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
)
from workers import fund_buy_collection_continuation_worker as continuation_worker

ROOT = Path(__file__).resolve().parents[1]


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


class FakeEth:
    def __init__(self, *, receipt=None, block_number: int = 200):
        self._receipt = receipt
        self.block_number = block_number
        self.gas_price = 1
        self.chain_id = 56

    def get_transaction_receipt(self, _tx_hash):
        return self._receipt

    def get_transaction_count(self, _address):
        return 1

    def send_raw_transaction(self, *_args, **_kwargs):
        raise AssertionError("send_raw_transaction must not be called by test fake")


class FakeWeb3:
    def __init__(self, *, receipt=None, block_number: int = 200):
        self.eth = FakeEth(receipt=receipt, block_number=block_number)

    def to_checksum_address(self, address: str) -> str:
        return str(address)

    def to_hex(self, value) -> str:
        return str(value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def patch_transfer_service(*, receipt=None):
    original = {
        "get_web3": transfer_service.get_web3,
        "get_bnb_balance": transfer_service.get_bnb_balance,
        "send_native_bnb": transfer_service.send_native_bnb,
        "_send_usdt_transfer": transfer_service._send_usdt_transfer,
        "decrypt_private_key": transfer_service.decrypt_private_key,
        "require_gas_guard": transfer_service.require_bsc_buy_collection_gas_topup_guard,
        "require_usdt_guard": transfer_service.require_bsc_buy_collection_usdt_to_settlement_guard,
    }

    calls = {
        "send_native_bnb": 0,
        "send_usdt": 0,
        "gas_guard": 0,
        "usdt_guard": 0,
    }

    transfer_service.get_web3 = lambda: FakeWeb3(receipt=receipt, block_number=200)
    transfer_service.get_bnb_balance = lambda *_args, **_kwargs: Decimal("1")

    def fake_send_native_bnb(*_args, **_kwargs):
        calls["send_native_bnb"] += 1
        raise AssertionError("BNB gas top-up must not be sent in this test")

    def fake_send_usdt(*_args, **_kwargs):
        calls["send_usdt"] += 1
        return "0xstage26213usdt"

    def fake_gas_guard(*_args, **_kwargs):
        calls["gas_guard"] += 1
        return SimpleNamespace(event_id=1)

    def fake_usdt_guard(*_args, **_kwargs):
        calls["usdt_guard"] += 1
        return SimpleNamespace(event_id=2)

    transfer_service.send_native_bnb = fake_send_native_bnb
    transfer_service._send_usdt_transfer = fake_send_usdt
    transfer_service.decrypt_private_key = lambda _value: "0x" + "11" * 32
    transfer_service.require_bsc_buy_collection_gas_topup_guard = fake_gas_guard
    transfer_service.require_bsc_buy_collection_usdt_to_settlement_guard = fake_usdt_guard

    return original, calls


def restore_transfer_service(original) -> None:
    transfer_service.get_web3 = original["get_web3"]
    transfer_service.get_bnb_balance = original["get_bnb_balance"]
    transfer_service.send_native_bnb = original["send_native_bnb"]
    transfer_service._send_usdt_transfer = original["_send_usdt_transfer"]
    transfer_service.decrypt_private_key = original["decrypt_private_key"]
    transfer_service.require_bsc_buy_collection_gas_topup_guard = original["require_gas_guard"]
    transfer_service.require_bsc_buy_collection_usdt_to_settlement_guard = original["require_usdt_guard"]


def create_fixture(
    db,
    *,
    order_status: str = ORDER_STATUS_SETTLING,
    gas_status: str = TRANSFER_STATUS_CONFIRMED,
    gas_tx_hash: str | None = "0xgas",
    usdt_status: str | None = None,
    usdt_tx_hash: str | None = None,
    batch_status: str = BATCH_STATUS_COLLECTING_BUY_USDT,
):
    suffix = uuid.uuid4().hex[:12]
    now = utcnow()

    fund = Fund(
        code=f"stage26_2_13_{suffix}",
        name_ru="Stage 26.2.13 Test",
        name_en="Stage 26.2.13 Test",
        category="test",
        sort_order=9999,
        is_active=True,
    )
    db.add(fund)
    db.flush()

    user = User(
        created_at=now,
        email=f"stage26_2_13_{suffix}@example.com",
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

    settlement_wallet = FundWallet(
        fund_id=fund.id,
        blockchain="BSC",
        wallet_type="settlement",
        address="0x0000000000000000000000000000000000000022",
        encrypted_private_key="not_used",
        is_active=True,
    )
    db.add(settlement_wallet)
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
        status=batch_status,
        pricing_locked_at=now,
        pricing_unlocked_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()

    order = FundOrder(
        user_id=user.id,
        fund_id=fund.id,
        side=ORDER_SIDE_BUY,
        amount_usdt=Decimal("10"),
        shares=None,
        price_usdt=None,
        status=order_status,
        settlement_batch_id=batch.id,
        reserved_at=now,
        settlement_locked_at=now,
        created_at=now,
        executed_at=None,
    )
    db.add(order)
    db.flush()

    gas_transfer = FundSettlementTransfer(
        batch_id=batch.id,
        order_id=order.id,
        fund_id=fund.id,
        user_id=user.id,
        transfer_type=TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
        from_address="0x00000000000000000000000000000000000000aa",
        to_address=wallet.address,
        amount_bnb=Decimal("0.0001"),
        tx_hash=gas_tx_hash,
        gas_tx_hash=gas_tx_hash,
        status=gas_status,
        attempts=1,
        created_at=now,
        updated_at=now,
        sent_at=now if gas_tx_hash else None,
        confirmed_at=now if gas_status == TRANSFER_STATUS_CONFIRMED else None,
    )
    db.add(gas_transfer)
    db.flush()

    usdt_transfer = None
    if usdt_status is not None:
        usdt_transfer = FundSettlementTransfer(
            batch_id=batch.id,
            order_id=order.id,
            fund_id=fund.id,
            user_id=user.id,
            transfer_type=TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
            from_address=wallet.address,
            to_address=settlement_wallet.address,
            amount_usdt=Decimal("10"),
            tx_hash=usdt_tx_hash,
            status=usdt_status,
            attempts=1 if usdt_tx_hash else 0,
            created_at=now,
            updated_at=now,
            sent_at=now if usdt_tx_hash else None,
            confirmed_at=now if usdt_status == TRANSFER_STATUS_CONFIRMED else None,
        )
        db.add(usdt_transfer)
        db.flush()

    return fund, user, wallet, settlement_wallet, batch, order, gas_transfer, usdt_transfer


def test_confirmed_gas_sends_usdt() -> None:
    db = SessionLocal()
    original, calls = patch_transfer_service(receipt=None)

    try:
        fund, _user, _wallet, _settlement_wallet, batch, order, _gas, _usdt = create_fixture(
            db,
            gas_status=TRANSFER_STATUS_CONFIRMED,
            usdt_status=None,
        )

        result = continue_buy_collection_for_active_batches(
            db,
            fund_codes=[fund.code],
            limit=10,
            dry_run=False,
        )

        usdt_rows = (
            db.query(FundSettlementTransfer)
            .filter(
                FundSettlementTransfer.batch_id == batch.id,
                FundSettlementTransfer.transfer_type == TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
            )
            .all()
        )

        db.refresh(order)
        db.refresh(batch)

        assert_ok("CONFIRMED_GAS_BATCH_PROCESSED", result.processed_count == 1)
        assert_ok("CONFIRMED_GAS_USDT_SENT_ONCE", calls["send_usdt"] == 1)
        assert_ok("CONFIRMED_GAS_NO_NEW_BNB", calls["send_native_bnb"] == 0)
        assert_ok("CONFIRMED_GAS_USDT_ROW_CREATED", len(usdt_rows) == 1)
        assert_ok("CONFIRMED_GAS_USDT_ROW_SENT", usdt_rows[0].status == TRANSFER_STATUS_SENT)
        assert_ok("CONFIRMED_GAS_ORDER_COLLECTING", order.status == ORDER_STATUS_BUY_COLLECTING)
        assert_ok("CONFIRMED_GAS_BATCH_STILL_COLLECTING", batch.status == BATCH_STATUS_COLLECTING_BUY_USDT)
        print("STAGE26_2_13_BUY_COLLECTION_CONTINUATION_CONFIRMED_GAS_SENDS_USDT_OK")

    finally:
        restore_transfer_service(original)
        db.rollback()
        db.close()


def test_pending_gas_no_usdt() -> None:
    db = SessionLocal()
    original, calls = patch_transfer_service(receipt=None)

    try:
        fund, _user, _wallet, _settlement_wallet, batch, _order, _gas, _usdt = create_fixture(
            db,
            gas_status=TRANSFER_STATUS_SENT,
            gas_tx_hash="0xgaspending",
            usdt_status=None,
        )

        continue_buy_collection_for_active_batches(
            db,
            fund_codes=[fund.code],
            limit=10,
            dry_run=False,
        )

        usdt_count = (
            db.query(FundSettlementTransfer)
            .filter(
                FundSettlementTransfer.batch_id == batch.id,
                FundSettlementTransfer.transfer_type == TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
            )
            .count()
        )

        assert_ok("PENDING_GAS_NO_USDT_ROWS", usdt_count == 0)
        assert_ok("PENDING_GAS_NO_USDT_SEND_CALL", calls["send_usdt"] == 0)
        assert_ok("PENDING_GAS_NO_DUPLICATE_BNB", calls["send_native_bnb"] == 0)
        print("STAGE26_2_13_BUY_COLLECTION_CONTINUATION_PENDING_GAS_NO_USDT_OK")

    finally:
        restore_transfer_service(original)
        db.rollback()
        db.close()


def test_existing_sent_usdt_no_duplicate() -> None:
    db = SessionLocal()
    original, calls = patch_transfer_service(receipt=None)

    try:
        fund, _user, _wallet, _settlement_wallet, batch, _order, _gas, _usdt = create_fixture(
            db,
            order_status=ORDER_STATUS_BUY_COLLECTING,
            gas_status=TRANSFER_STATUS_CONFIRMED,
            usdt_status=TRANSFER_STATUS_SENT,
            usdt_tx_hash="0xusdtsent",
        )

        continue_buy_collection_for_active_batches(
            db,
            fund_codes=[fund.code],
            limit=10,
            dry_run=False,
        )

        usdt_count = (
            db.query(FundSettlementTransfer)
            .filter(
                FundSettlementTransfer.batch_id == batch.id,
                FundSettlementTransfer.transfer_type == TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
            )
            .count()
        )

        assert_ok("NO_DUPLICATE_USDT_COUNT_STILL_ONE", usdt_count == 1)
        assert_ok("NO_DUPLICATE_USDT_SEND_NOT_CALLED", calls["send_usdt"] == 0)
        print("STAGE26_2_13_BUY_COLLECTION_CONTINUATION_NO_DUPLICATE_USDT_OK")

    finally:
        restore_transfer_service(original)
        db.rollback()
        db.close()


def test_confirmed_usdt_accounting_idempotent() -> None:
    db = SessionLocal()
    original, calls = patch_transfer_service(receipt={"status": 1, "blockNumber": 180})

    try:
        fund, _user, wallet, _settlement_wallet, batch, order, _gas, _usdt = create_fixture(
            db,
            order_status=ORDER_STATUS_BUY_COLLECTING,
            gas_status=TRANSFER_STATUS_CONFIRMED,
            usdt_status=TRANSFER_STATUS_CONFIRMED,
            usdt_tx_hash="0xusdtconfirmed",
        )

        first = continue_buy_collection_for_active_batches(
            db,
            fund_codes=[fund.code],
            limit=10,
            dry_run=False,
        )
        second = continue_buy_collection_for_active_batches(
            db,
            fund_codes=[fund.code],
            limit=10,
            dry_run=False,
        )

        db.refresh(wallet)
        db.refresh(order)
        db.refresh(batch)

        assert_ok("CONFIRMED_USDT_FIRST_PROCESSED", first.processed_count == 1)
        assert_ok("CONFIRMED_USDT_SECOND_NOT_PROCESSED", second.processed_count == 0)
        assert_ok("CONFIRMED_USDT_BALANCE_DECREASED_ONCE", wallet.usdt_balance == Decimal("2"))
        assert_ok("CONFIRMED_USDT_RESERVED_DECREASED_ONCE", wallet.usdt_reserved == Decimal("0"))
        assert_ok("CONFIRMED_USDT_ORDER_COLLECTED", order.status == ORDER_STATUS_BUY_COLLECTED)
        assert_ok("CONFIRMED_USDT_BATCH_ADVANCED", batch.status == BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION)
        assert_ok("CONFIRMED_USDT_NO_SEND_CALL", calls["send_usdt"] == 0)
        print("STAGE26_2_13_BUY_COLLECTION_CONTINUATION_CONFIRMED_USDT_ACCOUNTING_IDEMPOTENT_OK")

    finally:
        restore_transfer_service(original)
        db.rollback()
        db.close()


def test_worker_safety() -> None:
    worker_source = read("workers/fund_buy_collection_continuation_worker.py")
    service_source = read("app/settlement/buy_collection_continuation.py")
    worker_calls = ast_call_names("workers/fund_buy_collection_continuation_worker.py")

    assert_ok("WORKER_SAFETY_LIVE_GATED", "evaluate_live_gate" in worker_source and "live_bsc" in worker_source)
    assert_ok("WORKER_SAFETY_ROLLBACK_DRY_RUN", "if args.dry_run" in worker_source and "db.rollback()" in worker_source)
    assert_ok("WORKER_SAFETY_NO_RAW_TX", "send_raw_transaction" not in worker_source and "send_raw_transaction" not in worker_calls)
    assert_ok("WORKER_SAFETY_NO_DIRECT_USDT_SEND", "_send_usdt_transfer" not in worker_source and "_send_usdt_transfer" not in worker_calls)
    assert_ok("WORKER_SAFETY_NO_DIRECT_BNB_SEND", "send_native_bnb" not in worker_source and "send_native_bnb" not in worker_calls)
    assert_ok("WORKER_SAFETY_NO_BATCH_CREATE", "run_settlement_batches_once" not in worker_source and "FundSettlementBatch(" not in worker_source)
    assert_ok("WORKER_SAFETY_SERVICE_REUSES_COLLECT", "collect_buy_usdt_for_batch" in service_source)

    blocked_args = argparse.Namespace(
        fund_code="wb_test",
        fund_codes=None,
        limit=1,
        sleep_sec=60,
        run_now=True,
        dry_run=False,
        live_bsc=False,
    )

    original_gate = continuation_worker._validate_buy_collection_continuation_live_gate
    original_session = continuation_worker.SessionLocal

    try:
        continuation_worker._validate_buy_collection_continuation_live_gate = lambda _args: False

        def forbidden_session():
            raise AssertionError("SessionLocal must not be opened when live gate blocks")

        continuation_worker.SessionLocal = forbidden_session
        rc = continuation_worker.run_once(blocked_args)
        assert_ok("WORKER_SAFETY_LIVE_GATE_BLOCKS", rc == 0)

    finally:
        continuation_worker._validate_buy_collection_continuation_live_gate = original_gate
        continuation_worker.SessionLocal = original_session

    print("STAGE26_2_13_BUY_COLLECTION_CONTINUATION_WORKER_SAFETY_OK")


def test_path_marker() -> None:
    from scripts.stage26_2_8_verify_production_wb_test_actual_path import (
        verify_buy_collection_continuation_path,
    )

    result = verify_buy_collection_continuation_path()
    assert_ok("CONTINUATION_PATH_VERIFIER_OK", result["ok"] is True)
    print("STAGE26_2_13_BUY_COLLECTION_CONTINUATION_PATH_OK")


def main() -> int:
    load_dotenv()

    test_confirmed_gas_sends_usdt()
    test_pending_gas_no_usdt()
    test_existing_sent_usdt_no_duplicate()
    test_confirmed_usdt_accounting_idempotent()
    test_worker_safety()
    test_path_marker()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())