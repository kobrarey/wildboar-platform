from __future__ import annotations

import ast
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models import (
    Fund,
    FundChartDaily,
    FundOrder,
    FundSettlementBatch,
    User,
)
from app.settlement.negative_net_targets import (
    REDEEM_ORDER_TARGET_STATUSES,
    calculate_and_store_negative_net_targets,
    _load_redeem_orders_for_update,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_PENDING,
    ORDER_STATUS_PROCESSING,
    ORDER_STATUS_SUCCESS,
)


ROOT = Path(__file__).resolve().parents[1]


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def ast_call_names(path: str) -> set[str]:
    tree = ast.parse(read(path))
    calls: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)

    return calls


def month_start(ts: datetime) -> datetime:
    ts = ts.astimezone(timezone.utc)
    return datetime(ts.year, ts.month, 1, tzinfo=timezone.utc)


def create_user(db) -> User:
    suffix = uuid.uuid4().hex[:12]
    user = User(
        created_at=utcnow(),
        email=f"stage26_3_3_{suffix}@example.com",
        first_name="Stage",
        last_name="NegativeTargets",
        phone=None,
        password_hash="test-only",
        is_active=True,
        is_email_verified=True,
        two_factor_enabled=False,
        account_type="tester",
        compliance_status="ok",
    )
    db.add(user)
    db.flush()
    return user


def create_fund(db) -> Fund:
    suffix = uuid.uuid4().hex[:12]
    fund = Fund(
        code=f"stage26_3_3_{suffix}",
        name_ru="Stage 26.3.3 Test",
        name_en="Stage 26.3.3 Test",
        category="test",
        sort_order=9999,
        is_active=True,
    )
    db.add(fund)
    db.flush()
    return fund


def create_month_open_chart(db, *, fund: Fund, settlement_ts: datetime) -> FundChartDaily:
    chart = FundChartDaily(
        fund_id=int(fund.id),
        ts_utc=month_start(settlement_ts),
        open=Decimal("500"),
        high=Decimal("610"),
        low=Decimal("490"),
        close=Decimal("600"),
        volume=Decimal("0"),
    )
    db.add(chart)
    db.flush()
    return chart


def create_negative_batch(
    db,
    *,
    fund: Fund,
    settlement_date: date | None = None,
) -> FundSettlementBatch:
    now = utcnow()
    settlement_date = settlement_date or date.today()

    batch = FundSettlementBatch(
        fund_id=int(fund.id),
        settlement_date=settlement_date,
        cutoff_ts=now,
        settlement_ts=now,
        price_ts=now,
        settlement_price_usdt=Decimal("600"),
        nav_usdt=Decimal("1000"),
        shares_outstanding_before=Decimal("10"),
        total_buy_usdt=Decimal("0"),
        total_redeem_shares=Decimal("0.0160000000"),
        total_redeem_usdt=Decimal("9.6000000000"),
        net_cash_usdt=Decimal("-9.6000000000"),
        planned_shares_to_issue=Decimal("0"),
        planned_shares_to_redeem=Decimal("0.0160000000"),
        planned_net_shares_change=Decimal("-0.0160000000"),
        status=BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
        pricing_locked_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()
    return batch


def create_order(
    db,
    *,
    user: User,
    fund: Fund,
    batch: FundSettlementBatch,
    side: str,
    status: str,
    shares: Decimal = Decimal("0.0160000000"),
    reserved_at: datetime | None = None,
) -> FundOrder:
    order = FundOrder(
        user_id=int(user.id),
        fund_id=int(fund.id),
        side=side,
        amount_usdt=None,
        shares=shares,
        price_usdt=None,
        status=status,
        settlement_batch_id=int(batch.id),
        reserved_at=reserved_at,
        settlement_locked_at=None,
        collection_confirmed_at=None,
        error=None,
        created_at=utcnow(),
        executed_at=None,
    )
    db.add(order)
    db.flush()
    return order


def assert_loaded_order_ids(db, *, batch: FundSettlementBatch, expected_ids: list[int]) -> None:
    loaded = _load_redeem_orders_for_update(db, settlement_batch_id=int(batch.id))
    loaded_ids = [int(order.id) for order in loaded]
    assert_ok(
        f"LOADED_ORDER_IDS_MATCH_{int(batch.id)}",
        loaded_ids == [int(item) for item in expected_ids],
    )


def run_success_case(*, order_status: str, marker: str) -> None:
    db = SessionLocal()

    try:
        user = create_user(db)
        fund = create_fund(db)
        batch = create_negative_batch(db, fund=fund)
        create_month_open_chart(db, fund=fund, settlement_ts=batch.settlement_ts)
        order = create_order(
            db,
            user=user,
            fund=fund,
            batch=batch,
            side=ORDER_SIDE_REDEEM,
            status=order_status,
        )

        assert_loaded_order_ids(db, batch=batch, expected_ids=[int(order.id)])

        result = calculate_and_store_negative_net_targets(
            db,
            settlement_batch_id=int(batch.id),
            bybit_withdrawal_fee_usdt=Decimal("1"),
            use_live_bybit_withdrawal_fee=False,
        )

        assert_ok(f"{marker}_RESULT_OK", result.ok is True)
        assert_ok(
            f"{marker}_BATCH_STATUS_OK",
            result.status_after == BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
        )
        assert_ok(f"{marker}_ORDER_COUNT_OK", result.order_count == 1)
        assert_ok(
            f"{marker}_ORDER_ID_OK",
            [item.order_id for item in result.order_results] == [int(order.id)],
        )

        print(marker)

    finally:
        db.rollback()
        db.close()


def test_awaiting_order_included() -> None:
    run_success_case(
        order_status=ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
        marker="STAGE26_3_3_NEGATIVE_NET_TARGETS_AWAITING_ORDER_INCLUDED_OK",
    )


def test_pending_order_still_included() -> None:
    run_success_case(
        order_status=ORDER_STATUS_PENDING,
        marker="STAGE26_3_3_NEGATIVE_NET_TARGETS_PENDING_ORDER_STILL_INCLUDED_OK",
    )


def test_processing_order_still_included() -> None:
    run_success_case(
        order_status=ORDER_STATUS_PROCESSING,
        marker="STAGE26_3_3_NEGATIVE_NET_TARGETS_PROCESSING_ORDER_STILL_INCLUDED_OK",
    )


def test_terminal_orders_excluded() -> None:
    terminal_statuses = [
        ORDER_STATUS_SUCCESS,
        ORDER_STATUS_CANCELLED,
        ORDER_STATUS_FAILED,
        ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ]

    for terminal_status in terminal_statuses:
        db = SessionLocal()

        try:
            user = create_user(db)
            fund = create_fund(db)
            batch = create_negative_batch(db, fund=fund)
            create_order(
                db,
                user=user,
                fund=fund,
                batch=batch,
                side=ORDER_SIDE_REDEEM,
                status=terminal_status,
            )

            assert_loaded_order_ids(db, batch=batch, expected_ids=[])

        finally:
            db.rollback()
            db.close()

    print("STAGE26_3_3_NEGATIVE_NET_TARGETS_TERMINAL_ORDERS_EXCLUDED_OK")


def test_wrong_batch_excluded() -> None:
    db = SessionLocal()

    try:
        user = create_user(db)
        fund = create_fund(db)
        target_batch = create_negative_batch(
            db,
            fund=fund,
            settlement_date=date.today(),
        )
        wrong_batch = create_negative_batch(
            db,
            fund=fund,
            settlement_date=date.today() + timedelta(days=1),
        )

        create_order(
            db,
            user=user,
            fund=fund,
            batch=wrong_batch,
            side=ORDER_SIDE_REDEEM,
            status=ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
        )

        assert_loaded_order_ids(db, batch=target_batch, expected_ids=[])
        print("STAGE26_3_3_NEGATIVE_NET_TARGETS_WRONG_BATCH_EXCLUDED_OK")

    finally:
        db.rollback()
        db.close()


def test_buy_side_excluded() -> None:
    db = SessionLocal()

    try:
        user = create_user(db)
        fund = create_fund(db)
        batch = create_negative_batch(db, fund=fund)

        create_order(
            db,
            user=user,
            fund=fund,
            batch=batch,
            side=ORDER_SIDE_BUY,
            status=ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
        )

        assert_loaded_order_ids(db, batch=batch, expected_ids=[])
        print("STAGE26_3_3_NEGATIVE_NET_TARGETS_BUY_SIDE_EXCLUDED_OK")

    finally:
        db.rollback()
        db.close()


def test_reserved_at_null_ok() -> None:
    db = SessionLocal()

    try:
        user = create_user(db)
        fund = create_fund(db)
        batch = create_negative_batch(db, fund=fund)
        order = create_order(
            db,
            user=user,
            fund=fund,
            batch=batch,
            side=ORDER_SIDE_REDEEM,
            status=ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
            reserved_at=None,
        )

        assert_ok("RESERVED_AT_IS_NULL_FIXTURE", order.reserved_at is None)
        assert_loaded_order_ids(db, batch=batch, expected_ids=[int(order.id)])

        source = read("app/settlement/negative_net_targets.py")
        assert_ok(
            "NEGATIVE_TARGETS_DOES_NOT_REQUIRE_RESERVED_AT",
            "reserved_at" not in source,
        )

        print("STAGE26_3_3_NEGATIVE_NET_TARGETS_RESERVED_AT_NULL_OK")

    finally:
        db.rollback()
        db.close()


def test_status_set_exact() -> None:
    expected = {
        ORDER_STATUS_PENDING,
        ORDER_STATUS_PROCESSING,
        ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    }
    terminal = {
        ORDER_STATUS_SUCCESS,
        ORDER_STATUS_CANCELLED,
        ORDER_STATUS_FAILED,
        ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    }

    assert_ok("TARGET_STATUS_SET_EXACT", set(REDEEM_ORDER_TARGET_STATUSES) == expected)
    assert_ok(
        "TARGET_STATUS_TERMINALS_EXCLUDED",
        set(REDEEM_ORDER_TARGET_STATUSES).isdisjoint(terminal),
    )


def test_safety_no_external_writes() -> None:
    source = read("app/settlement/negative_net_targets.py")
    calls = ast_call_names("app/settlement/negative_net_targets.py")

    forbidden_text = [
        ".post(",
        "send_raw_transaction",
        "_send_usdt_transfer",
        "send_native_bnb",
        "create_market_sell_order",
        "create_master_withdrawal",
        "create_universal_transfer",
        "/v5/user/" + "frozen-" + "sub-member",
    ]
    present = [item for item in forbidden_text if item in source]
    assert_ok("NEGATIVE_TARGETS_NO_FORBIDDEN_EXTERNAL_WRITE_TEXT", not present)
    assert_ok("NEGATIVE_TARGETS_NO_POST_CALL", "post" not in calls)


def main() -> int:
    load_dotenv()

    test_status_set_exact()
    test_awaiting_order_included()
    test_pending_order_still_included()
    test_processing_order_still_included()
    test_terminal_orders_excluded()
    test_wrong_batch_excluded()
    test_buy_side_excluded()
    test_reserved_at_null_ok()
    test_safety_no_external_writes()

    print("STAGE26_3_3_NEGATIVE_NET_TARGETS_REDEEM_LOOKUP_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())