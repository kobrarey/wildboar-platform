from __future__ import annotations

import ast
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.allocation.plan_service import (
    build_allocation_plan_for_settlement_batch,
    is_allocation_batch_plan_noop_status,
)
from app.allocation.snapshot_service import (
    AllocationAccountRisk,
    AllocationSnapshot,
    AllocationSnapshotHolding,
)
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED,
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_PLAN_MUTABLE_STATUSES,
    ALLOCATION_PLAN_NOOP_EXISTING_STATUSES,
    EXECUTION_MODE_MARKET,
    EXECUTION_MODE_PLANNED,
    EXECUTION_MODE_SKIPPED,
    LEG_GROUP_SPOT,
    LEG_TYPE_SPOT_BUY,
)
from app.db import SessionLocal
from app.models import Fund, FundAllocationBatch, FundAllocationLeg, FundSettlementBatch
from workers.fund_allocation_plan_worker import _find_candidate_batches


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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_snapshot(*, fund: Fund) -> AllocationSnapshot:
    now = utcnow()

    return AllocationSnapshot(
        fund_id=int(fund.id),
        fund_code=str(fund.code),
        snapshot_ts=now,
        account_type="UNIFIED",
        risk=AllocationAccountRisk(
            total_equity_usdt=Decimal("1000"),
            total_wallet_balance_usdt=Decimal("1000"),
            total_available_usdt=Decimal("900"),
            total_initial_margin_usdt=Decimal("0"),
            total_maintenance_margin_usdt=Decimal("0"),
            account_im_rate=Decimal("0"),
            account_mm_rate=Decimal("0"),
        ),
        holdings=[
            AllocationSnapshotHolding(
                leg_group="cash",
                leg_type="stable_cash",
                coin="USDT",
                symbol=None,
                category=None,
                side=None,
                location="UNIFIED",
                size=None,
                usd_value=Decimal("100"),
                notional_usd=None,
            ),
            AllocationSnapshotHolding(
                leg_group="spot",
                leg_type="spot_buy",
                coin="BTC",
                symbol="BTCUSDT",
                category="spot",
                side="Buy",
                location="UNIFIED",
                size=Decimal("1"),
                usd_value=Decimal("100"),
                notional_usd=None,
            ),
        ],
        raw_summary_json={"fixture": "stage26_2_15"},
        snapshot_source="stage26_2_15_fixture",
    )


def create_settlement_fixture(db):
    suffix = uuid.uuid4().hex[:12]
    now = utcnow()

    fund = Fund(
        code=f"stage26_2_15_{suffix}",
        name_ru="Stage 26.2.15 Test",
        name_en="Stage 26.2.15 Test",
        category="test",
        sort_order=9999,
        is_active=True,
    )
    db.add(fund)
    db.flush()

    settlement_batch = FundSettlementBatch(
        fund_id=int(fund.id),
        settlement_date=date.today(),
        cutoff_ts=now,
        settlement_ts=now,
        nav_usdt=Decimal("1000"),
        settlement_price_usdt=Decimal("1"),
        total_buy_usdt=Decimal("10"),
        total_redeem_shares=Decimal("0"),
        total_redeem_usdt=Decimal("0"),
        net_cash_usdt=Decimal("10"),
        planned_shares_to_issue=Decimal("10"),
        planned_shares_to_redeem=Decimal("0"),
        planned_net_shares_change=Decimal("10"),
        status="positive_cash_settlement_completed",
        pricing_locked_at=now,
        pricing_unlocked_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(settlement_batch)
    db.flush()

    return fund, settlement_batch


def create_allocation_batch(
    db,
    *,
    fund: Fund,
    settlement_batch: FundSettlementBatch,
    status: str,
) -> FundAllocationBatch:
    now = utcnow()

    allocation_batch = FundAllocationBatch(
        settlement_batch_id=int(settlement_batch.id),
        fund_id=int(fund.id),
        snapshot_ts=now,
        positive_net_usdt=Decimal("10"),
        settlement_nav_usdt=Decimal("1000"),
        snapshot_total_equity_usdt=Decimal("1000"),
        base_nav_for_scale_usdt=Decimal("1000"),
        scale=Decimal("0.01"),
        snapshot_source="stage26_2_15_fixture",
        snapshot_json={"fixture": True},
        status=status,
        allocation_started_at=now if status == ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING else None,
        completed_at=now if status in {
            ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED,
            ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
        } else None,
        created_at=now,
        updated_at=now,
    )
    db.add(allocation_batch)
    db.flush()

    return allocation_batch


def create_leg(
    db,
    *,
    fund: Fund,
    settlement_batch: FundSettlementBatch,
    allocation_batch: FundAllocationBatch,
    status: str,
    execution_mode: str,
) -> FundAllocationLeg:
    now = utcnow()

    leg = FundAllocationLeg(
        allocation_batch_id=int(allocation_batch.id),
        settlement_batch_id=int(settlement_batch.id),
        fund_id=int(fund.id),
        leg_index=1,
        leg_key=f"stage26_2_15_leg_{uuid.uuid4().hex[:8]}",
        leg_group=LEG_GROUP_SPOT,
        leg_type=LEG_TYPE_SPOT_BUY,
        coin="BTC",
        symbol="BTCUSDT",
        category="spot",
        side="Buy",
        location="UNIFIED",
        current_size=Decimal("1"),
        current_usd_value=Decimal("100"),
        source_weight=Decimal("0.1"),
        target_usdt=Decimal("1"),
        target_qty=Decimal("0.01"),
        execution_mode=execution_mode,
        status=status,
        residual_usdt=Decimal("1") if status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER else Decimal("0"),
        filled_usdt=Decimal("1") if status == ALLOCATION_LEG_STATUS_FILLED else Decimal("0"),
        created_at=now,
        updated_at=now,
    )
    db.add(leg)
    db.flush()

    return leg


def candidate_ids(db, *, fund_code: str) -> list[int]:
    return [
        int(batch.id)
        for batch in _find_candidate_batches(db, fund_code=fund_code)
    ]


def test_creates_when_missing() -> None:
    db = SessionLocal()

    try:
        fund, settlement_batch = create_settlement_fixture(db)
        snapshot = create_snapshot(fund=fund)

        candidates = candidate_ids(db, fund_code=fund.code)
        assert_ok("CREATES_MISSING_CANDIDATE_SELECTED", int(settlement_batch.id) in candidates)

        summary = build_allocation_plan_for_settlement_batch(
            db,
            settlement_batch_id=int(settlement_batch.id),
            snapshot=snapshot,
        )

        allocations_count = (
            db.query(FundAllocationBatch)
            .filter(FundAllocationBatch.settlement_batch_id == int(settlement_batch.id))
            .count()
        )
        legs_count = (
            db.query(FundAllocationLeg)
            .filter(FundAllocationLeg.allocation_batch_id == int(summary.allocation_batch_id))
            .count()
        )

        assert_ok("CREATES_MISSING_ONE_ALLOCATION_BATCH", allocations_count == 1)
        assert_ok("CREATES_MISSING_PLAN_CREATED", summary.status == ALLOCATION_BATCH_STATUS_PLAN_CREATED)
        assert_ok("CREATES_MISSING_LEGS_CREATED", legs_count > 0)
        print("STAGE26_2_15_ALLOCATION_PLAN_CREATES_WHEN_MISSING_OK")

    finally:
        db.rollback()
        db.close()


def assert_noop_for_status(
    *,
    status: str,
    marker: str,
    leg_status: str,
    execution_mode: str,
) -> None:
    db = SessionLocal()

    try:
        fund, settlement_batch = create_settlement_fixture(db)
        allocation_batch = create_allocation_batch(
            db,
            fund=fund,
            settlement_batch=settlement_batch,
            status=status,
        )
        create_leg(
            db,
            fund=fund,
            settlement_batch=settlement_batch,
            allocation_batch=allocation_batch,
            status=leg_status,
            execution_mode=execution_mode,
        )
        snapshot = create_snapshot(fund=fund)

        before_allocations_count = (
            db.query(FundAllocationBatch)
            .filter(FundAllocationBatch.settlement_batch_id == int(settlement_batch.id))
            .count()
        )
        before_legs_count = (
            db.query(FundAllocationLeg)
            .filter(FundAllocationLeg.allocation_batch_id == int(allocation_batch.id))
            .count()
        )

        candidates = candidate_ids(db, fund_code=fund.code)
        assert_ok(f"{marker}_CANDIDATE_EXCLUDED", int(settlement_batch.id) not in candidates)
        assert_ok(f"{marker}_STATUS_IS_NOOP", is_allocation_batch_plan_noop_status(status))

        summary = build_allocation_plan_for_settlement_batch(
            db,
            settlement_batch_id=int(settlement_batch.id),
            snapshot=snapshot,
        )

        after_allocations_count = (
            db.query(FundAllocationBatch)
            .filter(FundAllocationBatch.settlement_batch_id == int(settlement_batch.id))
            .count()
        )
        after_legs_count = (
            db.query(FundAllocationLeg)
            .filter(FundAllocationLeg.allocation_batch_id == int(allocation_batch.id))
            .count()
        )

        db.refresh(allocation_batch)

        assert_ok(f"{marker}_SUMMARY_EXISTING_BATCH", summary.allocation_batch_id == int(allocation_batch.id))
        assert_ok(f"{marker}_SUMMARY_STATUS_UNCHANGED", summary.status == status)
        assert_ok(f"{marker}_NO_DUPLICATE_BATCH", after_allocations_count == before_allocations_count == 1)
        assert_ok(f"{marker}_NO_LEG_MUTATION", after_legs_count == before_legs_count == 1)
        assert_ok(f"{marker}_DB_STATUS_UNCHANGED", allocation_batch.status == status)
        print(marker)

    finally:
        db.rollback()
        db.close()


def test_completed_residual_noop() -> None:
    assert_noop_for_status(
        status=ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
        marker="STAGE26_2_15_ALLOCATION_PLAN_COMPLETED_RESIDUAL_NOOP_OK",
        leg_status=ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
        execution_mode=EXECUTION_MODE_SKIPPED,
    )


def test_completed_noop() -> None:
    assert_noop_for_status(
        status=ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED,
        marker="STAGE26_2_15_ALLOCATION_PLAN_COMPLETED_NOOP_OK",
        leg_status=ALLOCATION_LEG_STATUS_FILLED,
        execution_mode=EXECUTION_MODE_MARKET,
    )


def test_processing_noop() -> None:
    assert_noop_for_status(
        status=ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
        marker="STAGE26_2_15_ALLOCATION_PLAN_PROCESSING_NOOP_OK",
        leg_status=ALLOCATION_LEG_STATUS_PLANNED,
        execution_mode=EXECUTION_MODE_PLANNED,
    )


def test_mutable_still_works() -> None:
    db = SessionLocal()

    try:
        fund, settlement_batch = create_settlement_fixture(db)
        allocation_batch = create_allocation_batch(
            db,
            fund=fund,
            settlement_batch=settlement_batch,
            status=ALLOCATION_BATCH_STATUS_PLAN_CREATED,
        )
        snapshot = create_snapshot(fund=fund)

        assert_ok("MUTABLE_STATUS_IN_SET", ALLOCATION_BATCH_STATUS_PLAN_CREATED in ALLOCATION_PLAN_MUTABLE_STATUSES)

        candidates = candidate_ids(db, fund_code=fund.code)
        assert_ok("MUTABLE_CANDIDATE_SELECTED", int(settlement_batch.id) in candidates)

        summary = build_allocation_plan_for_settlement_batch(
            db,
            settlement_batch_id=int(settlement_batch.id),
            snapshot=snapshot,
        )

        db.refresh(allocation_batch)

        legs_count = (
            db.query(FundAllocationLeg)
            .filter(FundAllocationLeg.allocation_batch_id == int(allocation_batch.id))
            .count()
        )

        assert_ok("MUTABLE_SAME_BATCH_REUSED", summary.allocation_batch_id == int(allocation_batch.id))
        assert_ok("MUTABLE_PLAN_CREATED", allocation_batch.status == ALLOCATION_BATCH_STATUS_PLAN_CREATED)
        assert_ok("MUTABLE_LEGS_PRESENT", legs_count > 0)
        print("STAGE26_2_15_ALLOCATION_PLAN_MUTABLE_STILL_WORKS_OK")

    finally:
        db.rollback()
        db.close()


def test_worker_safety() -> None:
    worker_source = read("workers/fund_allocation_plan_worker.py")
    service_source = read("app/allocation/plan_service.py")
    statuses_source = read("app/allocation/statuses.py")
    worker_calls = ast_call_names("workers/fund_allocation_plan_worker.py")
    service_calls = ast_call_names("app/allocation/plan_service.py")

    assert_ok("SAFETY_WORKER_USES_ALLOCATION_BATCH_FILTER", "FundAllocationBatch" in worker_source)
    assert_ok("SAFETY_WORKER_USES_MUTABLE_STATUS_SET", "ALLOCATION_PLAN_MUTABLE_STATUSES" in worker_source)
    assert_ok("SAFETY_WORKER_EXCLUDES_NON_MUTABLE", "notin_" in worker_source)
    assert_ok("SAFETY_SERVICE_HAS_NOOP_CLASSIFIER", "def is_allocation_batch_plan_noop_status" in service_source)
    assert_ok("SAFETY_SERVICE_HAS_NOOP_SUMMARY", "allocation_plan_noop_existing_non_mutable_status" in service_source)
    assert_ok("SAFETY_STATUSES_DEFINED", "ALLOCATION_PLAN_NOOP_EXISTING_STATUSES" in statuses_source)
    assert_ok("SAFETY_NO_BYBIT_POST", ".post(" not in worker_source and ".post(" not in service_source and "post" not in worker_calls)
    assert_ok("SAFETY_NO_BSC_RAW_TX", "send_raw_transaction" not in worker_source and "send_raw_transaction" not in service_source)
    assert_ok("SAFETY_NO_USDT_SEND", "_send_usdt_transfer" not in worker_source and "_send_usdt_transfer" not in service_source)
    assert_ok("SAFETY_NO_NATIVE_BNB_SEND", "send_native_bnb" not in worker_source and "send_native_bnb" not in service_source)
    assert_ok("SAFETY_NO_OPERATION_GUARD_CHANGE", "operation_guard" not in worker_source.lower() and "operation_guard" not in service_source.lower())

    print("STAGE26_2_15_ALLOCATION_PLAN_WORKER_IDEMPOTENCY_SAFETY_OK")


def test_production_verifier_path() -> None:
    from scripts.stage26_2_8_verify_production_wb_test_actual_path import (
        verify_allocation_plan_idempotency_path,
    )

    result = verify_allocation_plan_idempotency_path()
    assert_ok("PRODUCTION_VERIFIER_ALLOCATION_PLAN_IDEMPOTENCY_PATH_OK", result["ok"] is True)
    print("STAGE26_2_15_ALLOCATION_PLAN_IDEMPOTENCY_PATH_OK")


def main() -> int:
    load_dotenv()

    test_creates_when_missing()
    test_completed_residual_noop()
    test_completed_noop()
    test_processing_noop()
    test_mutable_still_works()
    test_worker_safety()
    test_production_verifier_path()

    print("STAGE26_2_15_ALLOCATION_PLAN_IDEMPOTENCY_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())