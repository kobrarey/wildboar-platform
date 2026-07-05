from __future__ import annotations

import ast
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.allocation.live_execution import refresh_live_allocation_batch_progress
from app.allocation.live_spot_orders import (
    BybitOrderCreateLowerLimitReject,
    LiveSpotOrderError,
    is_bybit_order_create_lower_limit_reject,
    mark_live_spot_order_lower_limit_rejected_as_terminal_skip,
    repair_live_spot_lower_limit_order_not_found_if_safe,
    submit_bybit_spot_market_order,
)
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    EXECUTION_MODE_SKIPPED,
    LEG_GROUP_SPOT,
    LEG_TYPE_BUY_THEN_STAKE,
)
from app.db import SessionLocal
from app.models import Fund, FundAllocationBatch, FundAllocationLeg, FundSettlementBatch


ROOT = Path(__file__).resolve().parents[1]
ZERO = Decimal("0")


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


def dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


class LowerLimitReturnClient:
    def __init__(self):
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.public_get_calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, payload: dict[str, Any] | None = None):
        self.post_calls.append((path, dict(payload or {})))
        return {
            "retCode": 170140,
            "retMsg": "Order value exceeded lower limit",
            "result": {},
        }

    def get(self, path: str, params: dict[str, Any] | None = None):
        self.get_calls.append((path, dict(params or {})))
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"list": []},
        }

    def public_get(self, path: str, params: dict[str, Any] | None = None):
        self.public_get_calls.append((path, dict(params or {})))

        if path == "/v5/market/instruments-info":
            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": str((params or {}).get("symbol") or "LDOUSDT"),
                            "category": str((params or {}).get("category") or "spot"),
                            "status": "Trading",
                            "baseCoin": "LDO",
                            "quoteCoin": "USDT",
                            "priceFilter": {"tickSize": "0.0001"},
                            "lotSizeFilter": {
                                "qtyStep": "0.01",
                                "minOrderQty": "0.01",
                                "maxOrderQty": "1000000",
                                "minOrderAmt": "5",
                                "maxMarketOrderQty": "1000000",
                                "maxLimitOrderQty": "1000000",
                            },
                        }
                    ]
                },
            }

        raise RuntimeError(f"Unexpected public_get path={path}")


class LowerLimitExceptionClient(LowerLimitReturnClient):
    def post(self, path: str, payload: dict[str, Any] | None = None):
        self.post_calls.append((path, dict(payload or {})))
        raise RuntimeError("retCode=170140 retMsg=Order value exceeded lower limit")


class NonLowerLimitErrorClient(LowerLimitReturnClient):
    def post(self, path: str, payload: dict[str, Any] | None = None):
        self.post_calls.append((path, dict(payload or {})))
        return {
            "retCode": 10001,
            "retMsg": "generic parameter error",
            "result": {},
        }


def create_fixture(
    db,
    *,
    leg_status: str = ALLOCATION_LEG_STATUS_PLANNED,
    order_link_id: str | None = None,
    bybit_order_id: str | None = None,
    target_usdt: Decimal = Decimal("0.3298197405"),
):
    suffix = uuid.uuid4().hex[:12]
    now = utcnow()

    fund = Fund(
        code=f"stage26_2_14_{suffix}",
        name_ru="Stage 26.2.14 Test",
        name_en="Stage 26.2.14 Test",
        category="test",
        sort_order=9999,
        is_active=True,
    )
    db.add(fund)
    db.flush()

    settlement_batch = FundSettlementBatch(
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
        status="positive_cash_settlement_completed",
        pricing_locked_at=now,
        pricing_unlocked_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(settlement_batch)
    db.flush()

    allocation_batch = FundAllocationBatch(
        settlement_batch_id=settlement_batch.id,
        fund_id=fund.id,
        snapshot_ts=now,
        positive_net_usdt=Decimal("10"),
        settlement_nav_usdt=Decimal("100"),
        snapshot_total_equity_usdt=Decimal("1000"),
        base_nav_for_scale_usdt=Decimal("1000"),
        scale=Decimal("0.01"),
        snapshot_source="bybit_readonly",
        snapshot_json={"fixture": True},
        status=ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
        allocation_started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(allocation_batch)
    db.flush()

    leg = FundAllocationLeg(
        allocation_batch_id=allocation_batch.id,
        settlement_batch_id=settlement_batch.id,
        fund_id=fund.id,
        leg_index=1,
        leg_key=f"stage26_2_14_ldo_{suffix}",
        leg_group=LEG_GROUP_SPOT,
        leg_type=LEG_TYPE_BUY_THEN_STAKE,
        coin="LDO",
        symbol="LDOUSDT",
        category="spot",
        side="buy",
        location="unified",
        target_usdt=target_usdt,
        target_qty=None,
        execution_mode="planned",
        order_link_id=order_link_id,
        bybit_order_id=bybit_order_id,
        status=leg_status,
        sent_at=now if order_link_id else None,
        created_at=now,
        updated_at=now,
    )
    db.add(leg)
    db.flush()

    return fund, settlement_batch, allocation_batch, leg


def test_post_lower_limit_reject_terminal_skip() -> None:
    db = SessionLocal()

    try:
        _fund, _settlement_batch, allocation_batch, leg = create_fixture(db)

        client = LowerLimitReturnClient()
        try:
            submit_bybit_spot_market_order(
                client,
                payload={
                    "category": "spot",
                    "symbol": "LDOUSDT",
                    "side": "Buy",
                    "orderType": "Market",
                    "qty": "0.3298197405",
                    "orderLinkId": "alloc:test:lower-limit",
                },
            )
            raise AssertionError("Expected BybitOrderCreateLowerLimitReject")
        except BybitOrderCreateLowerLimitReject as exc:
            result = mark_live_spot_order_lower_limit_rejected_as_terminal_skip(
                db,
                allocation_leg_id=int(leg.id),
                error=f"spot_order_create_lower_limit_rejected: retCode=170140 lower-limit: {exc}",
                diagnostics={
                    "source": "test_post_lower_limit_reject",
                    "bybit_order_created": False,
                },
            )

        progress = refresh_live_allocation_batch_progress(
            db,
            allocation_batch_id=int(allocation_batch.id),
        )

        db.refresh(leg)

        assert_ok("LOWER_LIMIT_CLASSIFIED_RESPONSE", is_bybit_order_create_lower_limit_reject({"retCode": 170140, "retMsg": "Order value exceeded lower limit"}))
        assert_ok("LOWER_LIMIT_CLASSIFIED_EXCEPTION", is_bybit_order_create_lower_limit_reject(RuntimeError("retCode=170140 retMsg=Order value exceeded lower limit")))
        assert_ok("LOWER_LIMIT_POST_CALLED_ONCE", len(client.post_calls) == 1)
        assert_ok("LOWER_LIMIT_TERMINAL_ACTION", result.action == "lower_limit_reject_terminal_skip")
        assert_ok("LOWER_LIMIT_STATUS_SKIPPED", leg.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER)
        assert_ok("LOWER_LIMIT_MODE_SKIPPED", leg.execution_mode == EXECUTION_MODE_SKIPPED)
        assert_ok("LOWER_LIMIT_RESIDUAL_TARGET", leg.residual_usdt == Decimal("0.3298197405"))
        assert_ok("LOWER_LIMIT_CASH_USED_ZERO", leg.actual_cash_used_usdt == ZERO)
        assert_ok("LOWER_LIMIT_FILLED_QTY_ZERO", leg.filled_qty == ZERO)
        assert_ok("LOWER_LIMIT_FILLED_USDT_ZERO", leg.filled_usdt == ZERO)
        assert_ok("LOWER_LIMIT_BYBIT_ORDER_ID_NULL", leg.bybit_order_id is None)
        assert_ok("LOWER_LIMIT_ORDER_LINK_CLEARED", leg.order_link_id is None)
        assert_ok("LOWER_LIMIT_BATCH_COMPLETED_RESIDUAL", progress["status"] == ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH)
        assert_ok("LOWER_LIMIT_BATCH_ACTIVE_ZERO", progress["active_legs_count"] == 0)
        print("STAGE26_2_14_BYBIT_LOWER_LIMIT_POST_REJECT_TERMINAL_SKIP_OK")

    finally:
        db.rollback()
        db.close()


def test_existing_order_link_lower_limit_repair() -> None:
    db = SessionLocal()

    try:
        fund, _settlement_batch, allocation_batch, leg = create_fixture(
            db,
            leg_status=ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
            order_link_id="alloc:test:leg:repair:mkt",
            bybit_order_id=None,
        )

        client = LowerLimitReturnClient()

        result = repair_live_spot_lower_limit_order_not_found_if_safe(
            db,
            allocation_leg_id=int(leg.id),
            client=client,
            fund_code=fund.code,
            reason="stage26_2_14_verify_repair",
        )

        progress = refresh_live_allocation_batch_progress(
            db,
            allocation_batch_id=int(allocation_batch.id),
        )

        db.refresh(leg)

        assert_ok("REPAIR_NO_POST_CALLS", len(client.post_calls) == 0)
        assert_ok("REPAIR_LOOKUP_CALLED", len(client.get_calls) >= 2)
        assert_ok("REPAIR_INSTRUMENT_READ_CALLED", len(client.public_get_calls) == 1)
        assert_ok("REPAIR_ACTION", result.action == "lower_limit_reject_terminal_skip")
        assert_ok("REPAIR_STATUS_SKIPPED", leg.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER)
        assert_ok("REPAIR_MODE_SKIPPED", leg.execution_mode == EXECUTION_MODE_SKIPPED)
        assert_ok("REPAIR_RESIDUAL_TARGET", leg.residual_usdt == Decimal("0.3298197405"))
        assert_ok("REPAIR_CASH_USED_ZERO", leg.actual_cash_used_usdt == ZERO)
        assert_ok("REPAIR_ORDER_LINK_CLEARED", leg.order_link_id is None)
        assert_ok("REPAIR_BYBIT_ORDER_ID_NULL", leg.bybit_order_id is None)
        assert_ok("REPAIR_BATCH_COMPLETED_RESIDUAL", progress["status"] == ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH)
        assert_ok("REPAIR_BATCH_ACTIVE_ZERO", progress["active_legs_count"] == 0)
        print("STAGE26_2_14_EXISTING_ORDER_LINK_LOWER_LIMIT_REPAIR_OK")

    finally:
        db.rollback()
        db.close()


def test_non_lower_limit_post_error_fail_closed() -> None:
    client = NonLowerLimitErrorClient()

    assert_ok(
        "NON_LOWER_LIMIT_NOT_CLASSIFIED",
        not is_bybit_order_create_lower_limit_reject(
            {"retCode": 10001, "retMsg": "generic parameter error"}
        ),
    )

    try:
        submit_bybit_spot_market_order(
            client,
            payload={
                "category": "spot",
                "symbol": "LDOUSDT",
                "side": "Buy",
                "orderType": "Market",
                "qty": "0.3298197405",
                "orderLinkId": "alloc:test:non-lower-limit",
            },
        )
        raise AssertionError("Expected LiveSpotOrderError")
    except BybitOrderCreateLowerLimitReject:
        raise AssertionError("Non-lower-limit error must not become lower-limit skip")
    except LiveSpotOrderError as exc:
        assert_ok("NON_LOWER_LIMIT_FAIL_CLOSED_ERROR", "generic parameter error" in str(exc))

    assert_ok("NON_LOWER_LIMIT_POST_CALLED_ONCE", len(client.post_calls) == 1)
    print("STAGE26_2_14_NON_LOWER_LIMIT_POST_ERROR_FAIL_CLOSED_OK")


def test_lower_limit_repair_idempotent() -> None:
    db = SessionLocal()

    try:
        fund, _settlement_batch, allocation_batch, leg = create_fixture(
            db,
            leg_status=ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
            order_link_id="alloc:test:leg:idempotent:mkt",
            bybit_order_id=None,
        )

        client = LowerLimitReturnClient()

        first = repair_live_spot_lower_limit_order_not_found_if_safe(
            db,
            allocation_leg_id=int(leg.id),
            client=client,
            fund_code=fund.code,
            reason="stage26_2_14_verify_idempotency_first",
        )
        progress_first = refresh_live_allocation_batch_progress(
            db,
            allocation_batch_id=int(allocation_batch.id),
        )

        db.refresh(leg)
        residual_after_first = leg.residual_usdt
        order_link_after_first = leg.order_link_id

        second = repair_live_spot_lower_limit_order_not_found_if_safe(
            db,
            allocation_leg_id=int(leg.id),
            client=client,
            fund_code=fund.code,
            reason="stage26_2_14_verify_idempotency_second",
        )
        progress_second = refresh_live_allocation_batch_progress(
            db,
            allocation_batch_id=int(allocation_batch.id),
        )

        db.refresh(leg)

        assert_ok("IDEMPOTENT_FIRST_TERMINAL", first.action == "lower_limit_reject_terminal_skip")
        assert_ok("IDEMPOTENT_SECOND_ALREADY_TERMINAL", second.action == "already_terminal_lower_limit_skip")
        assert_ok("IDEMPOTENT_STATUS_STILL_SKIPPED", leg.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER)
        assert_ok("IDEMPOTENT_RESIDUAL_NOT_CHANGED", leg.residual_usdt == residual_after_first)
        assert_ok("IDEMPOTENT_ORDER_LINK_STILL_CLEARED", leg.order_link_id == order_link_after_first == None)
        assert_ok("IDEMPOTENT_BATCH_FIRST_COMPLETE", progress_first["status"] == ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH)
        assert_ok("IDEMPOTENT_BATCH_SECOND_COMPLETE", progress_second["status"] == ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH)
        assert_ok("IDEMPOTENT_BATCH_ACTIVE_ZERO", progress_second["active_legs_count"] == 0)
        print("STAGE26_2_14_LOWER_LIMIT_REPAIR_IDEMPOTENT_OK")

    finally:
        db.rollback()
        db.close()


def test_safety_and_path_marker() -> None:
    live_spot_source = read("app/allocation/live_spot_orders.py")
    worker_source = read("workers/fund_allocation_execution_worker.py")
    repair_source = read("scripts/stage26_2_14_repair_lower_limit_allocation_leg.py")
    repair_calls = ast_call_names("scripts/stage26_2_14_repair_lower_limit_allocation_leg.py")

    assert_ok("SAFETY_REPAIR_NO_POST", ".post(" not in repair_source and "post" not in repair_calls)
    assert_ok("SAFETY_REPAIR_NO_BSC_RAW_TX", "send_raw_transaction" not in repair_source)
    assert_ok("SAFETY_REPAIR_NO_USDT_SEND", "_send_usdt_transfer" not in repair_source)
    assert_ok("SAFETY_REPAIR_NO_BNB_SEND", "send_native_bnb" not in repair_source)
    assert_ok("SAFETY_TERMINAL_SKIP_CLEARS_LINK", "leg.order_link_id = None" in live_spot_source)
    assert_ok("SAFETY_WORKER_LOWER_LIMIT_BRANCH", "except BybitOrderCreateLowerLimitReject" in worker_source)
    assert_ok("SAFETY_NON_LOWER_LIMIT_STILL_FAILS", "spot_order_create_failed_or_uncertain" in worker_source)

    from scripts.stage26_2_8_verify_production_wb_test_actual_path import (
        verify_bybit_lower_limit_reject_handling_path,
    )

    path_result = verify_bybit_lower_limit_reject_handling_path()
    assert_ok("SAFETY_PRODUCTION_VERIFIER_PATH_OK", path_result["ok"] is True)
    print("STAGE26_2_14_BYBIT_LOWER_LIMIT_REJECT_HANDLING_OK")


def main() -> int:
    load_dotenv()

    test_post_lower_limit_reject_terminal_skip()
    test_existing_order_link_lower_limit_repair()
    test_non_lower_limit_post_error_fail_closed()
    test_lower_limit_repair_idempotent()
    test_safety_and_path_marker()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())