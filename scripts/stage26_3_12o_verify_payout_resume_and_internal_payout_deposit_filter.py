from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import Base
from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundOrder,
    FundSettlementBatch,
    FundWallet,
    User,
    UserWallet,
    WalletTransfer,
)
from app.settlement import negative_payout_flow as payout_flow
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BYBIT_FLOW_STATUS_COMPLETED,
    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_PROCESSING,
    PAYOUT_BATCH_STATUS_BALANCE_REFRESH_MOCKED,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PAYOUT_BATCH_STATUS_CREATED,
    PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_BATCH_STATUS_GAS_READY,
    PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    PAYOUT_LEG_STATUS_PLANNED,
)
from workers import bsc_usdt_deposit_listener as deposit_listener
from workers.fund_negative_payout_worker import _load_candidates


D = Decimal
NOW = datetime(2042, 1, 1, 12, 0, tzinfo=timezone.utc)
TEST_SCHEMA_PREFIX = "wb_stage26_3_12o_r1_"

TEST_TABLES = [
    User.__table__,
    UserWallet.__table__,
    WalletTransfer.__table__,
    Fund.__table__,
    FundWallet.__table__,
    FundSettlementBatch.__table__,
    FundOrder.__table__,
    FundNegativeSaleBatch.__table__,
    FundNegativeBybitFlow.__table__,
    FundNegativePayoutBatch.__table__,
    FundNegativePayoutLeg.__table__,
]


class CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def capture_logs() -> Iterator[list[str]]:
    root = logging.getLogger()
    old_level = root.level
    handler = CapturingHandler()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler.messages
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


@contextmanager
def patched_attr(obj: Any, name: str, value: Any) -> Iterator[None]:
    old_value = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old_value)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_safe_local_postgres_url(database_url: str) -> None:
    url = make_url(database_url)
    backend = url.get_backend_name().lower()
    host = (url.host or "").strip().lower()
    database = (url.database or "").strip().lower()

    require(backend == "postgresql", "Verifier requires PostgreSQL")
    require(
        host in {"localhost", "127.0.0.1", "::1"},
        f"Refusing non-local database host: {host or '<empty>'}",
    )
    require(database != "", "Database name is required")
    require(
        not any(token in database for token in ("prod", "production", "live")),
        f"Refusing production-like database name: {database}",
    )


def create_test_schema() -> tuple[Any, Any, sessionmaker, str]:
    database_url = str(settings.DATABASE_URL)
    assert_safe_local_postgres_url(database_url)

    schema = TEST_SCHEMA_PREFIX + uuid.uuid4().hex[:12]
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    test_engine = None

    try:
        with admin_engine.begin() as conn:
            stale_schemas = conn.execute(
                text(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name LIKE :pattern
                    """
                ),
                {"pattern": f"{TEST_SCHEMA_PREFIX}%"},
            ).scalars().all()

            for stale_schema in stale_schemas:
                if str(stale_schema).startswith(TEST_SCHEMA_PREFIX):
                    conn.execute(
                        text(f'DROP SCHEMA IF EXISTS "{stale_schema}" CASCADE')
                    )

            conn.execute(text(f'CREATE SCHEMA "{schema}"'))

        test_engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"options": f"-csearch_path={schema}"},
        )

        # Minimal dependency required by
        # fund_negative_payout_batches.operator_action_id.
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE fund_operator_actions (
                        id BIGSERIAL PRIMARY KEY
                    )
                    """
                )
            )

        Base.metadata.create_all(test_engine, tables=TEST_TABLES)

        with test_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE worker_cursors (
                        name VARCHAR(128) PRIMARY KEY,
                        last_block BIGINT NOT NULL,
                        last_log_index INTEGER NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            )

        TestSessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=test_engine,
        )
        return admin_engine, test_engine, TestSessionLocal, schema
    except Exception:
        if test_engine is not None:
            test_engine.dispose()

        with admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))

        admin_engine.dispose()
        raise


def drop_test_schema(admin_engine: Any, test_engine: Any, schema: str) -> None:
    test_engine.dispose()
    with admin_engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    admin_engine.dispose()


def new_fund(db: Session, code: str) -> Fund:
    fund = Fund(
        code=code,
        name_ru=code,
        name_en=code,
        category="test",
        sort_order=0,
        is_active=True,
    )
    db.add(fund)
    db.flush()
    return fund


def new_user(db: Session, suffix: str) -> User:
    user = User(
        created_at=NOW,
        email=f"stage26-r1-{suffix}@example.test",
        first_name="Stage26",
        last_name=suffix,
        password_hash="not-a-real-password-hash",
        is_active=True,
        is_email_verified=True,
        two_factor_enabled=True,
        account_type="basic",
    )
    db.add(user)
    db.flush()
    return user


def new_user_wallet(
    db: Session,
    *,
    user: User,
    address: str,
    is_active: bool,
    balance: Decimal = D("0"),
) -> UserWallet:
    wallet = UserWallet(
        user_id=int(user.id),
        blockchain="BSC",
        address=address,
        encrypted_private_key="test-only-encrypted-key",
        usdt_balance=balance,
        usdt_reserved=D("0"),
        is_active=is_active,
    )
    db.add(wallet)
    db.flush()
    return wallet


def new_settlement_batch(
    db: Session,
    *,
    fund: Fund,
    day_offset: int,
    status: str,
    payout_amount: Decimal = D("10"),
) -> FundSettlementBatch:
    ts = NOW + timedelta(days=day_offset)
    batch = FundSettlementBatch(
        fund_id=int(fund.id),
        settlement_date=date(2042, 1, 1) + timedelta(days=day_offset),
        cutoff_ts=ts,
        settlement_ts=ts,
        total_net_user_payout_usdt=payout_amount,
        bybit_withdrawal_fee_usdt=D("1"),
        required_master_usdt=payout_amount + D("1"),
        withdrawal_request_amount_usdt=payout_amount,
        status=status,
    )
    db.add(batch)
    db.flush()
    return batch


def new_sale_batch(
    db: Session,
    *,
    fund: Fund,
    settlement_batch: FundSettlementBatch,
) -> FundNegativeSaleBatch:
    sale_batch = FundNegativeSaleBatch(
        settlement_batch_id=int(settlement_batch.id),
        fund_id=int(fund.id),
    )
    db.add(sale_batch)
    db.flush()
    return sale_batch


def new_bybit_flow(
    db: Session,
    *,
    fund: Fund,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch,
    status: str,
    settlement_wallet: FundWallet | None = None,
    payout_amount: Decimal = D("10"),
) -> FundNegativeBybitFlow:
    flow = FundNegativeBybitFlow(
        settlement_batch_id=int(settlement_batch.id),
        sale_batch_id=int(sale_batch.id),
        fund_id=int(fund.id),
        status=status,
        coin="USDT",
        chain="BSC",
        required_master_usdt=payout_amount + D("1"),
        withdrawal_request_amount_usdt=payout_amount,
        bybit_withdrawal_fee_usdt=D("1"),
        settlement_wallet_id=(
            int(settlement_wallet.id) if settlement_wallet is not None else None
        ),
        settlement_wallet_address=(
            str(settlement_wallet.address) if settlement_wallet is not None else None
        ),
        withdrawal_status="success" if status == BYBIT_FLOW_STATUS_COMPLETED else None,
        withdrawal_amount_usdt=payout_amount,
        withdrawal_fee_usdt=D("1"),
        withdrawal_coin="USDT",
        withdrawal_chain="BSC",
        withdrawal_tx_hash=(
            "0x" + "b" * 64 if status == BYBIT_FLOW_STATUS_COMPLETED else None
        ),
        settlement_wallet_receipt_status=(
            "CONFIRMED" if status == BYBIT_FLOW_STATUS_COMPLETED else None
        ),
        settlement_wallet_received_usdt=(
            payout_amount if status == BYBIT_FLOW_STATUS_COMPLETED else None
        ),
    )
    db.add(flow)
    db.flush()
    return flow


def new_payout_batch(
    db: Session,
    *,
    fund: Fund,
    settlement_batch: FundSettlementBatch,
    bybit_flow: FundNegativeBybitFlow,
    status: str,
    settlement_wallet: FundWallet | None = None,
    payout_amount: Decimal = D("10"),
) -> FundNegativePayoutBatch:
    batch = FundNegativePayoutBatch(
        settlement_batch_id=int(settlement_batch.id),
        bybit_flow_id=int(bybit_flow.id),
        fund_id=int(fund.id),
        status=status,
        coin="USDT",
        chain="BSC",
        settlement_wallet_id=(
            int(settlement_wallet.id) if settlement_wallet is not None else None
        ),
        settlement_wallet_address=(
            str(settlement_wallet.address) if settlement_wallet is not None else None
        ),
        expected_total_payout_usdt=payout_amount,
    )
    db.add(batch)
    db.flush()
    return batch


def create_candidate_case(
    db: Session,
    *,
    fund: Fund,
    day_offset: int,
    settlement_status: str,
    bybit_status: str,
    payout_status: str | None,
) -> FundSettlementBatch:
    settlement_batch = new_settlement_batch(
        db,
        fund=fund,
        day_offset=day_offset,
        status=settlement_status,
    )
    sale_batch = new_sale_batch(
        db,
        fund=fund,
        settlement_batch=settlement_batch,
    )
    bybit_flow = new_bybit_flow(
        db,
        fund=fund,
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
        status=bybit_status,
    )
    if payout_status is not None:
        new_payout_batch(
            db,
            fund=fund,
            settlement_batch=settlement_batch,
            bybit_flow=bybit_flow,
            status=payout_status,
        )
    return settlement_batch


def test_worker_candidates(db: Session) -> list[dict[str, Any]]:
    fund = new_fund(db, "stage26_candidate")

    cases = [
        (
            "cash_ready_no_payout",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_COMPLETED,
            None,
            True,
        ),
        (
            "cash_ready_resumable",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_CREATED,
            True,
        ),
        (
            "processing_resumable",
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_GAS_READY,
            True,
        ),
        (
            "processing_no_payout",
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BYBIT_FLOW_STATUS_COMPLETED,
            None,
            False,
        ),
        (
            "processing_completed",
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_COMPLETED,
            False,
        ),
        (
            "processing_failed",
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
            False,
        ),
        (
            "cash_ready_completed",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_COMPLETED,
            False,
        ),
        (
            "cash_ready_failed",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
            False,
        ),
        (
            "cash_ready_payouts_confirmed",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED,
            False,
        ),
        (
            "processing_payouts_confirmed",
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED,
            False,
        ),
        (
            "cash_ready_other_terminal",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_BALANCE_REFRESH_MOCKED,
            False,
        ),
        (
            "processing_other_terminal",
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BYBIT_FLOW_STATUS_COMPLETED,
            PAYOUT_BATCH_STATUS_BALANCE_REFRESH_MOCKED,
            False,
        ),
        (
            "terminal_settlement",
            BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
            BYBIT_FLOW_STATUS_COMPLETED,
            None,
            False,
        ),
        (
            "bybit_not_completed",
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
            BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
            None,
            False,
        ),
    ]

    truth_table: list[dict[str, Any]] = []
    case_ids: dict[str, int] = {}

    for index, (name, settlement_status, bybit_status, payout_status, expected) in enumerate(cases):
        batch = create_candidate_case(
            db,
            fund=fund,
            day_offset=index,
            settlement_status=settlement_status,
            bybit_status=bybit_status,
            payout_status=payout_status,
        )
        case_ids[name] = int(batch.id)
        truth_table.append(
            {
                "case": name,
                "settlement_status": settlement_status,
                "bybit_status": bybit_status,
                "payout_status": payout_status,
                "expected_selected": expected,
            }
        )

    db.commit()

    selected_ids = {
        int(batch.id)
        for batch in _load_candidates(db, fund_code=str(fund.code))
    }

    for row in truth_table:
        actual = case_ids[str(row["case"])] in selected_ids
        row["actual_selected"] = actual
        require(
            actual is bool(row["expected_selected"]),
            f"Candidate mismatch: {row}",
        )

    return truth_table


def test_existing_payout_tx_resume(db: Session) -> dict[str, Any]:
    fund = new_fund(db, "stage26_resume")
    settlement_wallet = FundWallet(
        fund_id=int(fund.id),
        blockchain="BSC",
        wallet_type="settlement",
        address="0x" + "1" * 40,
        encrypted_private_key="test-only-encrypted-settlement-key",
        is_active=True,
    )
    db.add(settlement_wallet)
    db.flush()

    user = new_user(db, "resume")
    user_wallet = new_user_wallet(
        db,
        user=user,
        address="0x" + "2" * 40,
        is_active=True,
        balance=D("100"),
    )

    settlement_batch = new_settlement_batch(
        db,
        fund=fund,
        day_offset=40,
        status=BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
        payout_amount=D("10"),
    )
    sale_batch = new_sale_batch(
        db,
        fund=fund,
        settlement_batch=settlement_batch,
    )
    bybit_flow = new_bybit_flow(
        db,
        fund=fund,
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
        status=BYBIT_FLOW_STATUS_COMPLETED,
        settlement_wallet=settlement_wallet,
        payout_amount=D("10"),
    )

    order = FundOrder(
        user_id=int(user.id),
        fund_id=int(fund.id),
        side=ORDER_SIDE_REDEEM,
        shares=D("1"),
        price_usdt=D("10"),
        gross_redeem_usdt=D("10"),
        net_user_payout_usdt=D("10"),
        status=ORDER_STATUS_PROCESSING,
        settlement_batch_id=int(settlement_batch.id),
    )
    db.add(order)
    db.flush()

    payout_batch = new_payout_batch(
        db,
        fund=fund,
        settlement_batch=settlement_batch,
        bybit_flow=bybit_flow,
        status=PAYOUT_BATCH_STATUS_GAS_READY,
        settlement_wallet=settlement_wallet,
        payout_amount=D("10"),
    )
    payout_batch.planned_total_payout_usdt = D("10")
    payout_batch.payout_leg_count = 1

    payout_leg = FundNegativePayoutLeg(
        payout_batch_id=int(payout_batch.id),
        settlement_batch_id=int(settlement_batch.id),
        bybit_flow_id=int(bybit_flow.id),
        fund_id=int(fund.id),
        user_id=int(user.id),
        user_wallet_id=int(user_wallet.id),
        to_user_wallet_id=int(user_wallet.id),
        status=PAYOUT_LEG_STATUS_PLANNED,
        coin="USDT",
        chain="BSC",
        from_settlement_wallet_id=int(settlement_wallet.id),
        from_address=str(settlement_wallet.address),
        to_address=str(user_wallet.address),
        amount_usdt=D("10"),
        tx_hash="0x" + "c" * 64,
        confirmations=0,
    )
    db.add(payout_leg)
    db.commit()

    send_counter = {"count": 0}

    def fake_send(*args: Any, **kwargs: Any) -> str:
        send_counter["count"] += 1
        return "0x" + "d" * 64

    old_allow_live = settings.NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION
    old_mock_only = settings.NEGATIVE_NET_PAYOUT_MOCK_ONLY

    settings.NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION = True
    settings.NEGATIVE_NET_PAYOUT_MOCK_ONLY = False

    try:
        with (
            patched_attr(payout_flow, "get_web3", lambda: object()),
            patched_attr(
                payout_flow,
                "_ensure_live_settlement_wallet_gas",
                lambda *args, **kwargs: True,
            ),
            patched_attr(payout_flow, "_check_tx_confirmed", lambda *args, **kwargs: True),
            patched_attr(payout_flow, "_send_usdt_transfer", fake_send),
        ):
            result_first = payout_flow.execute_negative_payout_flow_live(
                db,
                settlement_batch_id=int(settlement_batch.id),
                now=NOW + timedelta(hours=1),
            )
            db.commit()
            db.expire_all()

            balance_after_first = D(str(db.get(UserWallet, int(user_wallet.id)).usdt_balance))

            result_second = payout_flow.execute_negative_payout_flow_live(
                db,
                settlement_batch_id=int(settlement_batch.id),
                now=NOW + timedelta(hours=2),
            )
            db.commit()
            db.expire_all()

            balance_after_second = D(str(db.get(UserWallet, int(user_wallet.id)).usdt_balance))
    finally:
        settings.NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION = old_allow_live
        settings.NEGATIVE_NET_PAYOUT_MOCK_ONLY = old_mock_only

    refreshed_leg = db.get(FundNegativePayoutLeg, int(payout_leg.id))
    refreshed_payout_batch = db.get(FundNegativePayoutBatch, int(payout_batch.id))
    refreshed_settlement = db.get(FundSettlementBatch, int(settlement_batch.id))

    require(result_first.ok is True, f"First resume failed: {result_first}")
    require(result_second.ok is True, f"Idempotent resume failed: {result_second}")
    require(result_second.idempotent is True, "Second resume was not idempotent")
    require(send_counter["count"] == 0, "_send_usdt_transfer was called on existing tx")
    require(refreshed_leg is not None, "Payout leg missing")
    require(refreshed_leg.status == PAYOUT_LEG_STATUS_BALANCE_REFRESHED, "Leg not balance_refreshed")
    require(
        int(refreshed_leg.confirmations or 0)
        == int(settings.NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED),
        "Leg confirmations mismatch",
    )
    require(refreshed_leg.confirmed_at is not None, "Leg confirmed_at missing")
    require(
        bool((refreshed_leg.confirmation_json or {}).get("confirmed")),
        "Leg confirmation_json not confirmed",
    )
    require(
        bool((refreshed_leg.confirmation_json or {}).get("no_duplicate_payout")),
        "Leg confirmation_json missing no_duplicate_payout",
    )
    require(refreshed_payout_batch is not None, "Payout batch missing")
    require(refreshed_payout_batch.status == PAYOUT_BATCH_STATUS_COMPLETED, "Payout batch not completed")
    require(
        D(str(refreshed_payout_batch.confirmed_total_payout_usdt)) == D("10"),
        "Confirmed total mismatch",
    )
    require(
        int(refreshed_payout_batch.confirmed_payout_leg_count or 0) == 1,
        "Confirmed count mismatch",
    )
    require(refreshed_settlement is not None, "Settlement batch missing")
    require(
        refreshed_settlement.status == BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
        "Settlement batch not negative_net_payouts_confirmed",
    )
    require(balance_after_first == D("110"), "User balance not updated exactly once")
    require(balance_after_second == D("110"), "User balance changed on idempotent resume")

    return {
        "settlement_batch_id": int(settlement_batch.id),
        "payout_batch_id": int(payout_batch.id),
        "payout_leg_id": int(payout_leg.id),
        "send_call_count": send_counter["count"],
        "leg_status": refreshed_leg.status,
        "leg_confirmations": int(refreshed_leg.confirmations or 0),
        "leg_confirmed_at": refreshed_leg.confirmed_at.isoformat(),
        "confirmation_json": refreshed_leg.confirmation_json,
        "payout_batch_status": refreshed_payout_batch.status,
        "settlement_batch_status": refreshed_settlement.status,
        "confirmed_total_payout_usdt": str(refreshed_payout_batch.confirmed_total_payout_usdt),
        "confirmed_payout_leg_count": int(refreshed_payout_batch.confirmed_payout_leg_count or 0),
        "user_balance_after_first": str(balance_after_first),
        "user_balance_after_second": str(balance_after_second),
    }


def make_transfer_log(
    *,
    tx_hash: str,
    from_address: str,
    to_address: str,
    amount: Decimal,
    block_number: int,
    log_index: int,
) -> dict[str, Any]:
    multiplier = Decimal(10) ** Decimal(int(settings.BSC_USDT_DECIMALS))
    raw_amount = int(amount * multiplier)
    return {
        "transactionHash": tx_hash,
        "topics": [
            deposit_listener.TRANSFER_TOPIC,
            deposit_listener._encode_topic_address(from_address),
            deposit_listener._encode_topic_address(to_address),
        ],
        "data": hex(raw_amount),
        "blockNumber": hex(block_number),
        "logIndex": hex(log_index),
    }


def cursor_value(TestSessionLocal: sessionmaker) -> tuple[int, int] | None:
    db = TestSessionLocal()
    try:
        return deposit_listener.get_cursor(db, deposit_listener.CURSOR_NAME)
    finally:
        db.close()


def transfer_count(TestSessionLocal: sessionmaker, tx_hash: str) -> int:
    db = TestSessionLocal()
    try:
        return int(
            db.query(WalletTransfer)
            .filter(WalletTransfer.tx_hash == tx_hash)
            .count()
        )
    finally:
        db.close()


async def test_deposit_filtering_async(
    TestSessionLocal: sessionmaker,
) -> dict[str, Any]:
    db = TestSessionLocal()
    try:
        fund = new_fund(db, "stage26_deposit")
        settlement_wallet = FundWallet(
            fund_id=int(fund.id),
            blockchain="BSC",
            wallet_type="settlement",
            address="0x" + "3" * 40,
            encrypted_private_key="test-only-encrypted-settlement-key",
            is_active=True,
        )
        db.add(settlement_wallet)
        db.flush()

        active_user = new_user(db, "deposit-active")
        active_wallet = new_user_wallet(
            db,
            user=active_user,
            address="0x" + "4" * 40,
            is_active=True,
        )

        inactive_user = new_user(db, "deposit-inactive")
        inactive_wallet = new_user_wallet(
            db,
            user=inactive_user,
            address="0x" + "5" * 40,
            is_active=False,
        )

        known_settlement = new_settlement_batch(
            db,
            fund=fund,
            day_offset=70,
            status=BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            payout_amount=D("3"),
        )
        known_sale = new_sale_batch(
            db,
            fund=fund,
            settlement_batch=known_settlement,
        )
        known_flow = new_bybit_flow(
            db,
            fund=fund,
            settlement_batch=known_settlement,
            sale_batch=known_sale,
            status=BYBIT_FLOW_STATUS_COMPLETED,
            settlement_wallet=settlement_wallet,
            payout_amount=D("3"),
        )
        known_batch = new_payout_batch(
            db,
            fund=fund,
            settlement_batch=known_settlement,
            bybit_flow=known_flow,
            status=PAYOUT_BATCH_STATUS_GAS_READY,
            settlement_wallet=settlement_wallet,
            payout_amount=D("3"),
        )

        known_tx = "0x" + "e" * 64
        known_from = "0x" + "6" * 40
        known_leg = FundNegativePayoutLeg(
            payout_batch_id=int(known_batch.id),
            settlement_batch_id=int(known_settlement.id),
            bybit_flow_id=int(known_flow.id),
            fund_id=int(fund.id),
            user_id=int(active_user.id),
            user_wallet_id=int(active_wallet.id),
            to_user_wallet_id=int(active_wallet.id),
            status=PAYOUT_LEG_STATUS_PLANNED,
            coin="USDT",
            chain="BSC",
            from_settlement_wallet_id=int(settlement_wallet.id),
            from_address=known_from,
            to_address=str(active_wallet.address),
            amount_usdt=D("3"),
            tx_hash=known_tx,
        )
        db.add(known_leg)
        db.commit()
    finally:
        db.close()

    wallet_map = deposit_listener.load_wallet_map()
    settlement_addresses = (
        deposit_listener.load_active_platform_settlement_wallet_addresses()
    )

    require(str(active_wallet.address).lower() in wallet_map, "Active wallet missing from map")
    require(str(inactive_wallet.address).lower() in wallet_map, "Inactive wallet missing from map")
    require(
        str(settlement_wallet.address).lower() in settlement_addresses,
        "Active settlement wallet missing from map",
    )

    external_tx = "0x" + "7" * 64
    settlement_active_tx = "0x" + "8" * 64
    settlement_inactive_tx = "0x" + "9" * 64

    scenarios = [
        {
            "name": "external_to_active_user_wallet",
            "tx_hash": external_tx,
            "from_address": "0x" + "a" * 40,
            "to_address": str(active_wallet.address),
            "amount": D("1"),
            "block": 100,
            "expected_rows": 1,
            "expected_deposit_log": True,
        },
        {
            "name": "settlement_to_active_user_wallet",
            "tx_hash": settlement_active_tx,
            "from_address": str(settlement_wallet.address),
            "to_address": str(active_wallet.address),
            "amount": D("2"),
            "block": 101,
            "expected_rows": 0,
            "expected_deposit_log": False,
        },
        {
            "name": "settlement_to_inactive_user_wallet",
            "tx_hash": settlement_inactive_tx,
            "from_address": str(settlement_wallet.address),
            "to_address": str(inactive_wallet.address),
            "amount": D("2.5"),
            "block": 102,
            "expected_rows": 0,
            "expected_deposit_log": False,
        },
        {
            "name": "known_payout_leg_fallback",
            "tx_hash": known_tx,
            "from_address": known_from,
            "to_address": str(active_wallet.address),
            "amount": D("3"),
            "block": 103,
            "expected_rows": 0,
            "expected_deposit_log": False,
        },
    ]

    output_rows: list[dict[str, Any]] = []

    for scenario in scenarios:
        log = make_transfer_log(
            tx_hash=str(scenario["tx_hash"]),
            from_address=str(scenario["from_address"]),
            to_address=str(scenario["to_address"]),
            amount=D(str(scenario["amount"])),
            block_number=int(scenario["block"]),
            log_index=0,
        )

        with capture_logs() as messages:
            await deposit_listener.handle_log(
                log,
                wallet_map,
                settlement_addresses,
                object(),
                {int(scenario["block"]): NOW},
                update_cursor_flag=True,
            )

        rows = transfer_count(TestSessionLocal, str(scenario["tx_hash"]))
        cursor = cursor_value(TestSessionLocal)
        deposit_detected = any("Deposit detected:" in message for message in messages)

        require(
            rows == int(scenario["expected_rows"]),
            f"WalletTransfer count mismatch for {scenario['name']}: {rows}",
        )
        require(
            cursor == (int(scenario["block"]), 0),
            f"Cursor mismatch for {scenario['name']}: {cursor}",
        )
        require(
            deposit_detected is bool(scenario["expected_deposit_log"]),
            f"Deposit log mismatch for {scenario['name']}: {messages}",
        )

        if not bool(scenario["expected_deposit_log"]):
            require(
                not any("Deposit detected:" in message for message in messages),
                f"Skipped scenario logged Deposit detected: {scenario['name']}",
            )

        if scenario["name"] == "known_payout_leg_fallback":
            require(
                any("internal_platform_payout_ignored" in message for message in messages),
                f"Known payout fallback skip log missing: {messages}",
            )
            require(
                any("deposit_not_inserted" in message for message in messages),
                f"Known payout fallback insert-result log missing: {messages}",
            )

        if str(scenario["name"]).startswith("settlement_to_"):
            require(
                any("internal_platform_payout_ignored" in message for message in messages),
                f"Cached settlement payout skip log missing: {messages}",
            )

        output_rows.append(
            {
                "scenario": scenario["name"],
                "wallet_transfer_rows": rows,
                "cursor": cursor,
                "deposit_detected_logged": deposit_detected,
                "logs": messages,
            }
        )

    return {
        "scenarios": output_rows,
        "inserted_row_counts": {
            row["scenario"]: row["wallet_transfer_rows"] for row in output_rows
        },
    }


def print_required_markers() -> None:
    markers = [
        "STAGE26_3_12O_PAYOUT_WORKER_SELECTS_PAYOUT_PROCESSING_OK",
        "STAGE26_3_12O_EXISTING_TX_HASH_RESUME_NO_DUPLICATE_SEND_OK",
        "STAGE26_3_12O_CONFIRMED_PAYOUT_COMPLETES_BATCH_OK",
        "STAGE26_3_12O_INTERNAL_SETTLEMENT_PAYOUT_NOT_DEPOSIT_OK",
        "STAGE26_3_12O_KNOWN_PAYOUT_LEG_TX_NOT_DEPOSIT_OK",
        "STAGE26_3_12O_EXTERNAL_USER_DEPOSIT_STILL_INSERTS_OK",
        "STAGE26_3_12O_PAYOUT_RESUME_AND_DEPOSIT_FILTER_OK",
        "STAGE26_3_12O_R1_BEHAVIORAL_VERIFICATION_OK",
    ]
    for marker in markers:
        print(marker)


def main() -> int:
    admin_engine = None
    test_engine = None
    schema = None
    candidate_truth_table = None
    resume_result = None
    deposit_result = None

    try:
        admin_engine, test_engine, TestSessionLocal, schema = create_test_schema()
        deposit_listener.SessionLocal = TestSessionLocal

        db = TestSessionLocal()
        try:
            candidate_truth_table = test_worker_candidates(db)
            resume_result = test_existing_payout_tx_resume(db)
        finally:
            db.close()

        deposit_result = asyncio.run(test_deposit_filtering_async(TestSessionLocal))
    finally:
        if admin_engine is not None and test_engine is not None and schema is not None:
            drop_test_schema(admin_engine, test_engine, schema)

    print({"candidate_truth_table": candidate_truth_table})
    print({"existing_payout_tx_resume": resume_result})
    print({"deposit_filtering": deposit_result})
    print(
        {
            "isolated_test_schema": schema,
            "isolated_test_schema_dropped": True,
            "production_rows_touched": False,
        }
    )

    print_required_markers()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())