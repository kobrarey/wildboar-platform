from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from app.allocation.live_earn_config import require_live_earn_whitelisted
from app.allocation.live_earn_orders import (
    apply_bybit_earn_order_to_leg,
    fetch_bybit_earn_order_by_link_id,
    submit_bybit_earn_stake_order,
)
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_RESIDUAL_CASH,
    ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    EXECUTION_MODE_EARN_STAKE,
    EXECUTION_MODE_RESIDUAL_EARN,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.config import settings


class StubEarnClient:
    def __init__(self, *, orders=None, post_result=None, post_error=None):
        self.orders = orders or []
        self.post_result = post_result or {
            "retCode": 0,
            "result": {
                "orderId": "bybit-earn-order-1",
                "orderLinkId": "alloc:1:leg:2:earn",
                "productId": "TEST-USDT-FLEX",
                "status": "SUCCESS",
                "orderValue": "100",
                "coin": "USDT",
            },
        }
        self.post_error = post_error
        self.get_calls = []
        self.post_calls = []

    def get(self, path, params=None):
        self.get_calls.append((path, params or {}))
        if path == "/v5/earn/order":
            return {
                "retCode": 0,
                "result": {
                    "list": self.orders,
                },
            }

        raise AssertionError(f"Unexpected GET path: {path}")

    def post(self, path, payload=None):
        self.post_calls.append((path, payload or {}))
        if self.post_error:
            raise self.post_error

        if path != "/v5/earn/place-order":
            raise AssertionError(f"Unexpected POST path: {path}")

        return self.post_result


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def make_leg(**overrides):
    data = {
        "id": 2,
        "allocation_batch_id": 1,
        "fund_id": 9,
        "settlement_batch_id": 10,
        "leg_type": LEG_TYPE_USDT_EARN_STAKE,
        "status": "market_order_sent",
        "execution_mode": EXECUTION_MODE_EARN_STAKE,
        "coin": "USDT",
        "category": "FlexibleSaving",
        "target_usdt": Decimal("100"),
        "target_qty": Decimal("0"),
        "required_usdt": Decimal("100"),
        "required_qty": Decimal("100"),
        "filled_qty": Decimal("0"),
        "filled_usdt": Decimal("0"),
        "actual_cash_used_usdt": Decimal("0"),
        "residual_usdt": Decimal("0"),
        "order_link_id": "alloc:1:leg:2:earn",
        "earn_order_id": "alloc:1:leg:2:earn",
        "bybit_order_id": None,
        "earn_product_id": "TEST-USDT-FLEX",
        "earn_product_category": "FlexibleSaving",
        "earn_product_status": "Available",
        "earn_min_stake_amount": Decimal("1"),
        "earn_max_stake_amount": Decimal("100000"),
        "earn_precision": Decimal("0.01"),
        "error": None,
        "sent_at": None,
        "confirmed_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_source_ordering() -> None:
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")
    live_earn = Path("app/allocation/live_earn_orders.py").read_text(encoding="utf-8")

    marker = "def _process_live_earn_leg_in_own_session"
    assert_ok("SOURCE_HAS_LIVE_EARN_PROCESSOR", marker in worker)

    body = worker.split(marker, 1)[1]
    next_func = "\ndef _process_live_spot_leg_in_own_session"
    if next_func in body:
        body = body.split(next_func, 1)[0]

    preflight_pos = body.find("_process_live_batch_preflight_in_own_session")
    reconcile_pos = body.find("reconcile_live_earn_stake_leg_by_link_id")
    persist_pos = body.find("earn_order_id/orderLinkId persisted before POST")
    guard_pos = body.find("require_earn_guard_for_plan")
    post_pos = body.find("submit_bybit_earn_stake_order")

    assert_ok(
        "SOURCE_EARN_PREFLIGHT_BEFORE_POST",
        preflight_pos != -1 and post_pos != -1 and preflight_pos < post_pos,
    )
    assert_ok(
        "SOURCE_EARN_RECONCILIATION_BEFORE_POST",
        reconcile_pos != -1 and post_pos != -1 and reconcile_pos < post_pos,
    )
    assert_ok(
        "SOURCE_EARN_ID_PERSISTED_BEFORE_POST",
        persist_pos != -1 and post_pos != -1 and persist_pos < post_pos,
    )
    assert_ok(
        "SOURCE_EARN_GUARD_BEFORE_POST",
        guard_pos != -1 and post_pos != -1 and guard_pos < post_pos,
    )
    assert_ok(
        "SOURCE_EARN_UNCERTAIN_NO_BLIND_RETRY",
        "earn_order_create_failed_or_uncertain" in body
        and "no blind retry" in live_earn.lower(),
    )
    assert_ok(
        "SOURCE_NO_NOT_WIRED",
        "not wired" not in (worker + live_earn).lower(),
    )


def test_fetch_existing_order_no_post() -> None:
    client = StubEarnClient(
        orders=[
            {
                "orderId": "bybit-earn-order-1",
                "orderLinkId": "alloc:1:leg:2:earn",
                "productId": "TEST-USDT-FLEX",
                "status": "SUCCESS",
                "orderValue": "100",
                "coin": "USDT",
            }
        ]
    )

    order = fetch_bybit_earn_order_by_link_id(
        client,
        order_link_id="alloc:1:leg:2:earn",
        product_id="TEST-USDT-FLEX",
    )

    assert_ok("FETCH_EXISTING_EARN_ORDER_FOUND", order is not None)
    assert_ok("FETCH_EXISTING_EARN_ORDER_NO_POST", len(client.post_calls) == 0)
    assert_ok("FETCH_EXISTING_EARN_ORDER_GET_PATH", client.get_calls[0][0] == "/v5/earn/order")


def test_submit_post_once() -> None:
    client = StubEarnClient()

    result = submit_bybit_earn_stake_order(
        client,
        payload={
            "category": "FlexibleSaving",
            "orderType": "Stake",
            "accountType": "UNIFIED",
            "amount": "100",
            "coin": "USDT",
            "productId": "TEST-USDT-FLEX",
            "orderLinkId": "alloc:1:leg:2:earn",
        },
    )

    assert_ok("SUBMIT_EARN_POST_ONCE", len(client.post_calls) == 1)
    assert_ok("SUBMIT_EARN_POST_PATH", client.post_calls[0][0] == "/v5/earn/place-order")
    assert_ok("SUBMIT_EARN_RETURNED_ORDER_ID", result.get("orderId") == "bybit-earn-order-1")


def test_submit_uncertain_raises_no_retry() -> None:
    client = StubEarnClient(post_error=RuntimeError("network timeout after POST uncertain"))

    try:
        submit_bybit_earn_stake_order(
            client,
            payload={
                "category": "FlexibleSaving",
                "orderType": "Stake",
                "accountType": "UNIFIED",
                "amount": "100",
                "coin": "USDT",
                "productId": "TEST-USDT-FLEX",
                "orderLinkId": "alloc:1:leg:2:earn",
            },
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("SUBMIT_EARN_UNCERTAIN_RAISES")

    assert_ok("SUBMIT_EARN_UNCERTAIN_RAISES", True)
    assert_ok("SUBMIT_EARN_UNCERTAIN_POST_ONCE", len(client.post_calls) == 1)


def test_apply_success_order_to_usdt_earn_leg() -> None:
    leg = make_leg()

    result = apply_bybit_earn_order_to_leg(
        leg,
        order={
            "orderId": "bybit-earn-order-1",
            "orderLinkId": "alloc:1:leg:2:earn",
            "productId": "TEST-USDT-FLEX",
            "status": "SUCCESS",
            "orderValue": "100",
            "coin": "USDT",
        },
    )

    assert_ok("APPLY_EARN_SUCCESS_RESULT_OK", result.ok)
    assert_ok("APPLY_EARN_SUCCESS_STATUS_FILLED", leg.status == ALLOCATION_LEG_STATUS_FILLED)
    assert_ok("APPLY_EARN_SUCCESS_MODE", leg.execution_mode == EXECUTION_MODE_EARN_STAKE)
    assert_ok("APPLY_EARN_SUCCESS_ORDER_ID_SET", leg.bybit_order_id == "bybit-earn-order-1")
    assert_ok("APPLY_EARN_SUCCESS_FILLED_USDT", leg.filled_usdt == Decimal("100"))


def test_apply_success_order_to_residual_earn_leg() -> None:
    leg = make_leg(
        leg_type=LEG_TYPE_RESIDUAL_USDT_EARN,
        execution_mode=EXECUTION_MODE_RESIDUAL_EARN,
    )

    result = apply_bybit_earn_order_to_leg(
        leg,
        order={
            "orderId": "bybit-earn-order-2",
            "orderLinkId": "alloc:1:leg:3:earn",
            "productId": "TEST-USDT-FLEX",
            "status": "SUCCESS",
            "orderValue": "50",
            "coin": "USDT",
        },
    )

    assert_ok("APPLY_RESIDUAL_EARN_SUCCESS_RESULT_OK", result.ok)
    assert_ok(
        "APPLY_RESIDUAL_EARN_SUCCESS_STATUS",
        leg.status == ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED,
    )
    assert_ok("APPLY_RESIDUAL_EARN_SUCCESS_MODE", leg.execution_mode == EXECUTION_MODE_RESIDUAL_EARN)


def test_apply_pending_order_fails_closed() -> None:
    leg = make_leg()

    result = apply_bybit_earn_order_to_leg(
        leg,
        order={
            "orderId": "bybit-earn-order-pending",
            "orderLinkId": "alloc:1:leg:2:earn",
            "productId": "TEST-USDT-FLEX",
            "status": "PROCESSING",
            "orderValue": "100",
            "coin": "USDT",
        },
    )

    assert_ok("APPLY_EARN_PENDING_RESULT_NOT_OK", not result.ok)
    assert_ok(
        "APPLY_EARN_PENDING_STATUS_REVIEW",
        leg.status == ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    )
    assert_ok("APPLY_EARN_PENDING_NO_BLIND_RETRY_TEXT", "manual reconciliation" in leg.error)


def test_whitelist_blocks_empty_product_ids() -> None:
    old_enabled = settings.ALLOCATION_EARN_ENABLED
    old_allow_live = settings.ALLOCATION_EARN_ALLOW_LIVE
    old_funds = settings.ALLOCATION_EARN_ALLOWED_FUND_CODES
    old_coins = settings.ALLOCATION_EARN_ALLOWED_COINS
    old_categories = settings.ALLOCATION_EARN_ALLOWED_CATEGORIES
    old_products = settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS

    try:
        settings.ALLOCATION_EARN_ENABLED = True
        settings.ALLOCATION_EARN_ALLOW_LIVE = True
        settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
        settings.ALLOCATION_EARN_ALLOWED_COINS = "USDT"
        settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
        settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = ""

        decision = require_live_earn_whitelisted(
            fund_code="wb_test",
            coin="USDT",
            category="FlexibleSaving",
            product_id="TEST-USDT-FLEX",
            amount=Decimal("100"),
        )

        assert_ok("WHITELIST_EMPTY_PRODUCT_IDS_BLOCKS", not decision.ok)
        assert_ok(
            "WHITELIST_EMPTY_PRODUCT_IDS_REASON",
            decision.reason == "earn_product_id_whitelist_empty",
        )

    finally:
        settings.ALLOCATION_EARN_ENABLED = old_enabled
        settings.ALLOCATION_EARN_ALLOW_LIVE = old_allow_live
        settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = old_funds
        settings.ALLOCATION_EARN_ALLOWED_COINS = old_coins
        settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = old_categories
        settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = old_products


def test_whitelist_allows_explicit_product() -> None:
    old_enabled = settings.ALLOCATION_EARN_ENABLED
    old_allow_live = settings.ALLOCATION_EARN_ALLOW_LIVE
    old_funds = settings.ALLOCATION_EARN_ALLOWED_FUND_CODES
    old_coins = settings.ALLOCATION_EARN_ALLOWED_COINS
    old_categories = settings.ALLOCATION_EARN_ALLOWED_CATEGORIES
    old_products = settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS

    try:
        settings.ALLOCATION_EARN_ENABLED = True
        settings.ALLOCATION_EARN_ALLOW_LIVE = True
        settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
        settings.ALLOCATION_EARN_ALLOWED_COINS = "USDT"
        settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
        settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = "TEST-USDT-FLEX"

        decision = require_live_earn_whitelisted(
            fund_code="wb_test",
            coin="USDT",
            category="FlexibleSaving",
            product_id="TEST-USDT-FLEX",
            amount=Decimal("100"),
        )

        assert_ok("WHITELIST_EXPLICIT_PRODUCT_ALLOWS", decision.ok)

    finally:
        settings.ALLOCATION_EARN_ENABLED = old_enabled
        settings.ALLOCATION_EARN_ALLOW_LIVE = old_allow_live
        settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = old_funds
        settings.ALLOCATION_EARN_ALLOWED_COINS = old_coins
        settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = old_categories
        settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = old_products


def test_residual_cash_status_constant_available() -> None:
    assert_ok("RESIDUAL_CASH_STATUS_AVAILABLE", ALLOCATION_LEG_STATUS_RESIDUAL_CASH == "residual_cash")


def test_product_unavailable_blocks_before_post_source() -> None:
    live_earn = Path("app/allocation/live_earn_orders.py").read_text(encoding="utf-8")

    assert_ok(
        "PRODUCT_UNAVAILABLE_USES_GET_ONLY_BEFORE_POST",
        "get_earn_product_info" in live_earn
        and "EarnProductUnavailableError" in live_earn
        and live_earn.find("get_earn_product_info") < live_earn.find("submit_bybit_earn_stake_order"),
    )


def test_min_max_precision_validation_before_post_source() -> None:
    live_earn = Path("app/allocation/live_earn_orders.py").read_text(encoding="utf-8")

    assert_ok(
        "MIN_MAX_PRECISION_VALIDATION_BEFORE_POST",
        "validate_earn_product_for_stake" in live_earn
        and live_earn.find("validate_earn_product_for_stake") < live_earn.find("submit_bybit_earn_stake_order"),
    )
    assert_ok(
        "VALIDATION_FAILURE_RAISES_BEFORE_POST",
        "earn_product_validation_failed" in live_earn
        and "if not validation.ok" in live_earn,
    )


def test_operation_guard_blocked_no_post_source() -> None:
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")

    marker = "def _process_live_earn_leg_in_own_session"
    body = worker.split(marker, 1)[1]
    next_func = "\ndef _process_live_spot_leg_in_own_session"
    if next_func in body:
        body = body.split(next_func, 1)[0]

    guard_pos = body.find("require_earn_guard_for_plan")
    post_pos = body.find("submit_bybit_earn_stake_order")
    guard_fail_pos = body.find("earn_order_guard_blocked_or_error")
    create_failed_pos = body.find("mark_live_earn_order_create_failed")

    assert_ok(
        "GUARD_BLOCKED_PATH_EXISTS",
        guard_fail_pos != -1 and create_failed_pos != -1,
    )
    assert_ok(
        "GUARD_BEFORE_EARN_POST_SOURCE",
        guard_pos != -1 and post_pos != -1 and guard_pos < post_pos,
    )
    assert_ok(
        "GUARD_BLOCKED_NO_POST_BY_CONTROL_FLOW",
        guard_fail_pos != -1 and post_pos != -1 and guard_fail_pos < post_pos,
    )


def test_operation_guard_allowed_single_post_source() -> None:
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")

    marker = "def _process_live_earn_leg_in_own_session"
    body = worker.split(marker, 1)[1]
    next_func = "\ndef _process_live_spot_leg_in_own_session"
    if next_func in body:
        body = body.split(next_func, 1)[0]

    assert_ok(
        "GUARD_ALLOWED_BEFORE_SINGLE_POST",
        "Allocation live Earn Operation Guard allowed" in body
        and "single external POST" in body
        and body.find("Allocation live Earn Operation Guard allowed")
        < body.find("submit_bybit_earn_stake_order"),
    )
    assert_ok(
        "EXACTLY_ONE_EARN_POST_CALL_IN_WORKER_BODY",
        body.count("submit_bybit_earn_stake_order(") == 1,
    )


def test_residual_earn_live_enabled_real_path_source() -> None:
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")

    assert_ok(
        "RESIDUAL_EARN_LIVE_ENABLED_ROUTES_TO_EARN_ADAPTER",
        "LEG_TYPE_RESIDUAL_USDT_EARN" in worker
        and "_process_live_earn_leg_in_own_session" in worker
        and "residual_earn_kept_as_cash_because_live_earn_disabled" in worker,
    )


def test_mixed_batch_source_coverage() -> None:
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")
    live_execution = Path("app/allocation/live_execution.py").read_text(encoding="utf-8")

    assert_ok(
        "MIXED_BATCH_STABLE_CASH_COVERED",
        "LEG_TYPE_STABLE_CASH" in worker
        and "_mark_stable_cash_leg_in_own_session" in worker,
    )
    assert_ok(
        "MIXED_BATCH_SPOT_BUY_COVERED",
        "LEG_TYPE_SPOT_BUY" in worker
        and "submit_bybit_spot_market_order" in worker,
    )
    assert_ok(
        "MIXED_BATCH_USDT_EARN_COVERED",
        "LEG_TYPE_USDT_EARN_STAKE" in worker
        and "submit_bybit_earn_stake_order" in worker,
    )
    assert_ok(
        "MIXED_BATCH_RESIDUAL_EARN_COVERED",
        "LEG_TYPE_RESIDUAL_USDT_EARN" in worker
        and (
            "residual_earn_kept_as_cash_because_live_earn_disabled" in worker
            or "submit_bybit_earn_stake_order" in worker
        ),
    )
    assert_ok(
        "BUY_THEN_STAKE_SAFE_BLOCKED_FOR_CURRENT_WB_TEST_AUDIT",
        "buy_then_stake_not_used_by_wb_test_current_local_db_and_not_enabled_for_live"
        in live_execution,
    )


def test_stage25_4_preflight_allows_processed_mixed_batch_statuses_source() -> None:
    live_execution = Path("app/allocation/live_execution.py").read_text(encoding="utf-8")

    assert_ok(
        "STAGE25_4_PREFLIGHT_HAS_ACCEPTED_STATUS_POLICY",
        "LIVE_PREFLIGHT_ALREADY_ACCEPTED_STATUSES" in live_execution
        and "LIVE_PREFLIGHT_RECONCILABLE_PENDING_STATUSES" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_ALLOWS_FILLED",
        "ALLOCATION_LEG_STATUS_FILLED" in live_execution
        and "Already processed successfully or safely residualized" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_ALLOWS_RESIDUAL_EARN_COMPLETED",
        "ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_ALLOWS_RESIDUAL_CASH",
        "ALLOCATION_LEG_STATUS_RESIDUAL_CASH" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_ALLOWS_PARTIAL_FILLED_RESIDUALIZED",
        "ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_ALLOWS_MARKET_SENT_WITH_IDEMPOTENCY",
        "ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT" in live_execution
        and "_leg_has_idempotency_reference" in live_execution
        and "pending_leg_missing_idempotency_reference" in live_execution,
    )


def test_stage25_4_preflight_blocks_failed_and_unsupported_processing_source() -> None:
    live_execution = Path("app/allocation/live_execution.py").read_text(encoding="utf-8")

    assert_ok(
        "STAGE25_4_PREFLIGHT_BLOCKS_FAILED_REVIEW",
        "LIVE_PREFLIGHT_BLOCKING_STATUSES" in live_execution
        and "blocking_leg_status" in live_execution
        and "ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_BLOCKS_NATIVE_ICEBERG_PROCESSING",
        "ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING" in live_execution
        and "unsupported_live_processing_status" in live_execution,
    )
    assert_ok(
        "STAGE25_4_PREFLIGHT_BLOCKS_SLICED_IOC_PROCESSING",
        "ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING" in live_execution
        and "unsupported_live_processing_status" in live_execution,
    )


def test_stage25_4_mixed_batch_regression_source_order() -> None:
    live_execution = Path("app/allocation/live_execution.py").read_text(encoding="utf-8")
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")

    assert_ok("STAGE25_4_MIXED_BATCH_REGRESSION_SOURCE_ORDER", True)

    assert_ok(
        "STAGE25_4_STABLE_CASH_FILLED_WILL_NOT_BLOCK_NEXT_PREFLIGHT",
        "ALLOCATION_LEG_STATUS_FILLED" in live_execution
        and "if leg_status in LIVE_PREFLIGHT_ALREADY_ACCEPTED_STATUSES" in live_execution,
    )
    assert_ok(
        "STAGE25_4_SPOT_MARKET_SENT_RECONCILABLE_NO_DUPLICATE",
        "ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT" in live_execution
        and "_leg_has_idempotency_reference" in live_execution
        and "reconcile_live_spot_market_leg_by_link_id" in worker,
    )
    assert_ok(
        "STAGE25_4_EARN_MARKET_SENT_RECONCILABLE_NO_DUPLICATE",
        "ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT" in live_execution
        and "_leg_has_idempotency_reference" in live_execution
        and "reconcile_live_earn_stake_leg_by_link_id" in worker,
    )
    assert_ok(
        "STAGE25_4_AFTER_EARN_SUCCESS_BATCH_CAN_COMPLETE",
        "ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED" in live_execution
        and "refresh_live_allocation_batch_progress" in live_execution,
    )
    assert_ok(
        "STAGE25_4_BUY_THEN_STAKE_STILL_SAFE_BLOCKED",
        "buy_then_stake_not_used_by_wb_test_current_local_db_and_not_enabled_for_live"
        in live_execution,
    )


def main() -> None:
    test_source_ordering()
    test_fetch_existing_order_no_post()
    test_submit_post_once()
    test_submit_uncertain_raises_no_retry()
    test_apply_success_order_to_usdt_earn_leg()
    test_apply_success_order_to_residual_earn_leg()
    test_apply_pending_order_fails_closed()
    test_whitelist_blocks_empty_product_ids()
    test_whitelist_allows_explicit_product()
    test_residual_cash_status_constant_available()
    test_product_unavailable_blocks_before_post_source()
    test_min_max_precision_validation_before_post_source()
    test_operation_guard_blocked_no_post_source()
    test_operation_guard_allowed_single_post_source()
    test_residual_earn_live_enabled_real_path_source()
    test_mixed_batch_source_coverage()
    test_stage25_4_preflight_allows_processed_mixed_batch_statuses_source()
    test_stage25_4_preflight_blocks_failed_and_unsupported_processing_source()
    test_stage25_4_mixed_batch_regression_source_order()
    print("STAGE25_3_LIVE_EARN_ALLOCATION_STUB_TESTS_OK")


if __name__ == "__main__":
    main()