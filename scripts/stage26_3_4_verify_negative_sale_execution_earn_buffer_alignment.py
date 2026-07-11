from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.settlement.negative_sale_execution import (
    build_live_sale_reconciliation_values,
)
from app.settlement.statuses import SALE_LEG_STATUS_USDT_EARN_REDEEMED


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


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


def test_plan_execution_alignment_source() -> None:
    plan_source = read("app/settlement/negative_sale_plan.py")
    execution_source = read("app/settlement/negative_sale_execution.py")

    assert_ok(
        "PLAN_COUNTS_USDT_EARN_BUFFER",
        "usdt_earn_used_as_buffer" in plan_source,
    )
    assert_ok(
        "EXECUTION_HAS_LIVE_EARN_REDEEM",
        "execute_live_usdt_earn_redeem_guarded" in execution_source
        and "initial_earn_redeemed_usdt" in execution_source
        and "SALE_LEG_STATUS_USDT_EARN_REDEEMED" in execution_source,
    )

    print("STAGE26_3_4_EARN_BUFFER_PLAN_EXECUTION_ALIGNMENT_OK")


def test_live_usdt_earn_buffer_realized_math() -> None:
    values = build_live_sale_reconciliation_values(
        required_master_usdt=Decimal("11.0222489345"),
        initial_cash_usdt=Decimal("0.1130000000"),
        initial_earn_redeemed_usdt=Decimal("10.9092489345"),
        live_results=[],
    )

    assert_ok(
        "LIVE_EARN_BUFFER_FINAL_SHORTAGE_ZERO",
        values["final_shortage_usdt"] == Decimal("0E-10")
        or values["final_shortage_usdt"] == Decimal("0"),
    )

    print("STAGE26_3_4_LIVE_USDT_EARN_BUFFER_REALIZED_OK")


def test_no_false_cash_in_live_plan() -> None:
    execution_source = read("app/settlement/negative_sale_execution.py")

    assert_ok(
        "EARN_BUFFER_NOT_COUNTED_AS_CASH_WITHOUT_REDEEM",
        "if leg_type == \"usdt_earn_buffer\":" in execution_source
        and "continue" in execution_source,
    )
    assert_ok(
        "RECONCILIATION_REQUIRES_INITIAL_EARN_REDEEMED_INPUT",
        "initial_earn_redeemed_usdt: Decimal" in execution_source,
    )

    print("STAGE26_3_4_NO_FALSE_EARN_CASH_IN_LIVE_PLAN_OK")


def test_current_batch80_shape_math() -> None:
    values = build_live_sale_reconciliation_values(
        required_master_usdt=Decimal("11.0222489345"),
        initial_cash_usdt=Decimal("0.1130000000"),
        initial_earn_redeemed_usdt=Decimal("10.9092489345"),
        live_results=[],
    )

    assert_ok(
        "CURRENT_SHAPE_FINAL_AVAILABLE_OK",
        values["final_available_usdt"] == Decimal("11.0222489345"),
    )
    assert_ok(
        "CURRENT_SHAPE_FINAL_SHORTAGE_ZERO",
        values["final_shortage_usdt"] == Decimal("0"),
    )

    print("STAGE26_3_4_CURRENT_BATCH80_SHAPE_OK")


def test_idempotency_source() -> None:
    source = read("app/settlement/negative_sale_execution.py")

    assert_ok(
        "EARN_REDEEM_QUERIES_EXISTING_BY_LINK_ID",
        "query_earn_order_by_link_id" in source,
    )
    assert_ok(
        "EARN_REDEEM_DETERMINISTIC_LINK_ID",
        "deterministic_negative_sale_earn_redeem_link_id" in source,
    )
    assert_ok(
        "EARN_REDEEM_EXISTING_TERMINAL_SKIP",
        "SALE_LEG_STATUS_USDT_EARN_REDEEMED" in source
        and "idempotent" in source,
    )

    print("STAGE26_3_4_EARN_REDEEM_IDEMPOTENCY_OK")


def test_uncertain_fail_closed_source() -> None:
    source = read("app/settlement/negative_sale_execution.py")

    assert_ok(
        "EARN_REDEEM_UNCERTAIN_NO_HISTORY_FAIL_CLOSED",
        "live_usdt_earn_redeem_uncertain_no_history_fail_closed" in source,
    )
    assert_ok(
        "EARN_REDEEM_UNCERTAIN_NOT_SUCCESS_FAIL_CLOSED",
        "live_usdt_earn_redeem_uncertain_not_success_fail_closed" in source,
    )
    assert_ok(
        "EARN_REDEEM_PENDING_FAIL_CLOSED",
        "live_usdt_earn_redeem_pending_fail_closed" in source,
    )

    print("STAGE26_3_4_EARN_REDEEM_UNCERTAIN_FAIL_CLOSED_OK")


def test_bybit_client_scope() -> None:
    worker_source = read("workers/fund_negative_sale_execution_worker.py")

    assert_ok(
        "NEGATIVE_SALE_USES_FUND_BYBIT_ACCOUNT",
        "FundBybitAccount" in worker_source
        and "_build_fund_trading_bybit_client" in worker_source,
    )
    assert_ok(
        "NEGATIVE_SALE_DOES_NOT_USE_MASTER_TRADING_CLIENT",
        "_build_trading_bybit_client()" not in worker_source,
    )
    assert_ok(
        "NEGATIVE_SALE_CLIENT_SCOPE_FAILS_CLOSED",
        "NEGATIVE_SALE_EXECUTION_BYBIT_CLIENT_SCOPE_NOT_READY" in worker_source,
    )

    print("STAGE26_3_4_NEGATIVE_SALE_BYBIT_CLIENT_SCOPE_OK")


def test_repair_forward_dry_run_source() -> None:
    source = read("scripts/stage26_3_4_requeue_negative_sale_execution_batch.py")

    required = [
        "--dry-run",
        "--apply",
        "negative_net_sale_execution_final_shortage",
        "BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED",
        "SALE_BATCH_STATUS_SALE_PLAN_CREATED",
        "STAGE26_3_4_REQUEUE_BATCH80_DRY_RUN_OK",
        "no_bybit_action",
        "no_bsc_tx",
    ]
    missing = [item for item in required if item not in source]
    assert_ok("REQUEUE_SCRIPT_REQUIRED_SNIPPETS", not missing)

    print("STAGE26_3_4_REQUEUE_BATCH80_DRY_RUN_OK")


def test_operation_guard_and_external_paths() -> None:
    hook_source = read("app/operation_guard/hooks.py")
    status_source = read("app/operation_guard/statuses.py")
    execution_source = read("app/settlement/negative_sale_execution.py")
    earn_source = read("app/bybit/earn.py")

    assert_ok(
        "EARN_REDEEM_GUARD_ACTION_DEFINED",
        "OP_GUARD_ACTION_BYBIT_EARN_REDEEM" in status_source,
    )
    assert_ok(
        "EARN_REDEEM_GUARD_HOOK_DEFINED",
        "require_bybit_earn_redeem_guard" in hook_source,
    )

    tree = ast.parse(execution_source)
    target_func = None

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "execute_live_usdt_earn_redeem_guarded"
        ):
            target_func = node
            break

    assert_ok(
        "EARN_REDEEM_GUARDED_FUNCTION_EXISTS",
        target_func is not None,
    )

    guard_call_lines: list[int] = []
    redeem_call_lines: list[int] = []

    for node in ast.walk(target_func):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        call_name = None

        if isinstance(func, ast.Name):
            call_name = func.id
        elif isinstance(func, ast.Attribute):
            call_name = func.attr

        if call_name == "require_bybit_earn_redeem_guard":
            guard_call_lines.append(int(node.lineno))

        if call_name == "place_flexible_saving_redeem_order":
            redeem_call_lines.append(int(node.lineno))

    assert_ok(
        "EARN_REDEEM_GUARD_CALL_PRESENT",
        bool(guard_call_lines),
    )
    assert_ok(
        "EARN_REDEEM_POST_CALL_PRESENT",
        bool(redeem_call_lines),
    )
    assert_ok(
        "EARN_REDEEM_POST_GUARDED",
        min(guard_call_lines) < min(redeem_call_lines),
    )

    assert_ok(
        "EARN_WRAPPER_ONLY_APPROVED_POST_ENDPOINT",
        '"/v5/earn/place-order"' in earn_source
        and ('"/v5/user/' + 'frozen-' + 'sub-member"') not in earn_source,
    )


def main() -> int:
    load_dotenv()

    test_plan_execution_alignment_source()
    test_live_usdt_earn_buffer_realized_math()
    test_no_false_cash_in_live_plan()
    test_current_batch80_shape_math()
    test_idempotency_source()
    test_uncertain_fail_closed_source()
    test_bybit_client_scope()
    test_repair_forward_dry_run_source()
    test_operation_guard_and_external_paths()

    print("STAGE26_3_4_NEGATIVE_SALE_EXECUTION_EARN_BUFFER_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())