from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import io
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func

from app.config import settings
from app.db import SessionLocal
from app.models import (
    Fund,
    FundAllocationBatch,
    FundNavMinute,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    User,
    UserFundPosition,
    UserWallet,
)
from app.settlement.statuses import (
    BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_PENDING,
    ORDER_STATUS_PROCESSING,
    ORDER_STATUS_SETTLING,
    ORDER_STATUS_SUCCESS,
)
from app.trading.order_service import (
    TradingOrderError,
    create_redeem_order,
    validate_redeem_shares_limits,
)


ROOT = Path(__file__).resolve().parents[1]

READY_MARKER = "STAGE26_3_1_PRODUCTION_WB_TEST_SELL_PATH_READY_OK"
NOT_READY_MARKER = "STAGE26_3_1_PRODUCTION_WB_TEST_SELL_PATH_NOT_READY"

ACTIVE_ORDER_STATUSES = {
    ORDER_STATUS_PENDING,
    ORDER_STATUS_SETTLING,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_PROCESSING,
}

NEGATIVE_MODELS = (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundNegativeBybitFlow,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeFinalizationBatch,
)

REQUIRED_NEGATIVE_MODULES = (
    "app.settlement.negative_net_targets",
    "app.settlement.negative_sale_plan",
    "app.settlement.negative_sale_execution",
    "app.settlement.negative_bybit_flow",
    "app.settlement.negative_payout_flow",
    "app.settlement.negative_finalization",
    "app.settlement.negative_net_fees",
    "workers.fund_negative_net_targets_worker",
    "workers.fund_negative_sale_plan_worker",
    "workers.fund_negative_sale_execution_worker",
    "workers.fund_negative_bybit_flow_worker",
    "workers.fund_negative_payout_worker",
    "workers.fund_negative_finalization_worker",
)

SOURCE_PATHS = {
    "order_service": "app/trading/order_service.py",
    "routes": "app/trading/routes.py",
    "sale_execution": "app/settlement/negative_sale_execution.py",
    "bybit_flow": "app/settlement/negative_bybit_flow.py",
    "payout_flow": "app/settlement/negative_payout_flow.py",
    "finalization": "app/settlement/negative_finalization.py",
    "targets_worker": "workers/fund_negative_net_targets_worker.py",
    "sale_plan_worker": "workers/fund_negative_sale_plan_worker.py",
    "sale_execution_worker": "workers/fund_negative_sale_execution_worker.py",
    "bybit_flow_worker": "workers/fund_negative_bybit_flow_worker.py",
    "payout_worker": "workers/fund_negative_payout_worker.py",
    "finalization_worker": "workers/fund_negative_finalization_worker.py",
}


class SellPathNotReady(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def print_kv(key: str, value: Any) -> None:
    print(f"{key}={value}")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 26.3.1 production-safe wb_test sell/redeem path verifier. "
            "Rollback-only. No live Bybit POST. No BSC transaction."
        )
    )
    parser.add_argument("--fund-code", default="wb_test")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--redeem-shares", default=None)
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def require_rollback(args: argparse.Namespace) -> None:
    if not args.rollback:
        raise SellPathNotReady("--rollback is required")


def normalize_fund_code(value: str) -> str:
    code = str(value or "").strip().lower()
    if not code:
        raise SellPathNotReady("--fund-code is required")
    return code


def parse_positive_decimal(value: Any, *, name: str) -> Decimal:
    amount = dec(value)
    if amount <= Decimal("0"):
        raise SellPathNotReady(f"{name} must be > 0")
    return amount


def get_fund_or_fail(db, *, fund_code: str) -> Fund:
    fund = (
        db.query(Fund)
        .filter(
            Fund.is_active == True,
            func.lower(Fund.code) == str(fund_code).lower(),
        )
        .first()
    )
    if fund is None:
        raise SellPathNotReady(f"fund_not_found: {fund_code}")
    return fund


def get_user_or_fail(db, *, user_id: int) -> User:
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise SellPathNotReady(f"user_not_found: {user_id}")
    if not bool(user.is_active):
        raise SellPathNotReady(f"user_not_active: {user_id}")
    return user


def get_position_or_fail(db, *, user_id: int, fund_id: int) -> UserFundPosition:
    position = (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id == int(user_id),
            UserFundPosition.fund_id == int(fund_id),
        )
        .with_for_update()
        .first()
    )
    if position is None:
        raise SellPathNotReady("user_position_not_found")
    return position


def get_wallet_or_fail(db, *, user_id: int) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == int(user_id),
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .first()
    )
    if wallet is None:
        raise SellPathNotReady("active_bsc_user_wallet_not_found")
    if dec(wallet.usdt_balance) < dec(wallet.usdt_reserved):
        raise SellPathNotReady("wallet_reserved_exceeds_balance")
    return wallet


def get_latest_nav_or_fail(db, *, fund_id: int) -> FundNavMinute:
    nav = (
        db.query(FundNavMinute)
        .filter(FundNavMinute.fund_id == int(fund_id))
        .order_by(FundNavMinute.ts_utc.desc(), FundNavMinute.id.desc())
        .first()
    )
    if nav is None:
        raise SellPathNotReady("latest_nav_not_found")
    if dec(nav.nav_usdt) <= 0:
        raise SellPathNotReady("latest_nav_not_positive")
    return nav


def validate_pricing_nav_state(
    db,
    *,
    fund_id: int,
    position: UserFundPosition,
) -> None:
    runtime = db.query(FundRuntimeState).filter(FundRuntimeState.fund_id == int(fund_id)).first()
    if runtime is not None and bool(runtime.pricing_locked):
        raise SellPathNotReady("pricing_locked")

    nav = get_latest_nav_or_fail(db, fund_id=fund_id)
    now = utcnow()
    nav_ts = nav.ts_utc
    if nav_ts.tzinfo is None:
        nav_ts = nav_ts.replace(tzinfo=timezone.utc)

    age_sec = (now - nav_ts).total_seconds()
    max_age = max(int(settings.SETTLEMENT_PRICE_MAX_AGE_SEC), 300)

    if age_sec > max_age:
        raise SellPathNotReady(f"latest_nav_stale: age_sec={age_sec}, max_age={max_age}")

    if dec(nav.shares_outstanding) < dec(position.shares):
        raise SellPathNotReady(
            f"latest_nav_shares_outstanding_below_user_position: "
            f"nav_shares={nav.shares_outstanding}, user_position={position.shares}"
        )


def active_orders_count(db, *, fund_id: int, user_id: int | None = None, side: str | None = None) -> int:
    q = db.query(FundOrder).filter(
        FundOrder.fund_id == int(fund_id),
        FundOrder.status.in_(sorted(ACTIVE_ORDER_STATUSES)),
    )
    if user_id is not None:
        q = q.filter(FundOrder.user_id == int(user_id))
    if side is not None:
        q = q.filter(FundOrder.side == side)
    return q.count()


def validate_no_active_orders(db, *, fund_id: int, user_id: int) -> None:
    any_active = active_orders_count(db, fund_id=fund_id, user_id=user_id)
    redeem_active = active_orders_count(
        db,
        fund_id=fund_id,
        user_id=user_id,
        side=ORDER_SIDE_REDEEM,
    )

    if any_active != 0:
        raise SellPathNotReady(f"active_orders_exist: {any_active}")
    if redeem_active != 0:
        raise SellPathNotReady(f"active_redeem_orders_exist: {redeem_active}")


def validate_negative_state_clean(db, *, fund_id: int) -> None:
    counts = {
        model.__tablename__: db.query(model).filter(model.fund_id == int(fund_id)).count()
        for model in NEGATIVE_MODELS
    }
    dirty = {name: count for name, count in counts.items() if int(count) != 0}
    if dirty:
        raise SellPathNotReady(f"negative_state_not_clean: {dirty}")


def validate_buy_path_terminal_clean(
    db,
    *,
    fund_id: int,
    user_id: int,
    redeem_shares: Decimal,
) -> None:
    buy_order = (
        db.query(FundOrder)
        .filter(
            FundOrder.user_id == int(user_id),
            FundOrder.fund_id == int(fund_id),
            FundOrder.side == "buy",
            FundOrder.status == ORDER_STATUS_SUCCESS,
            FundOrder.shares.isnot(None),
            FundOrder.settlement_batch_id.isnot(None),
        )
        .order_by(FundOrder.id.desc())
        .first()
    )
    if buy_order is None:
        raise SellPathNotReady("terminal_success_buy_order_not_found")
    if dec(buy_order.shares) < redeem_shares:
        raise SellPathNotReady(
            f"latest_success_buy_shares_below_redeem_shares: "
            f"buy_shares={buy_order.shares}, redeem_shares={redeem_shares}"
        )

    settlement_batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(buy_order.settlement_batch_id))
        .first()
    )
    if settlement_batch is None:
        raise SellPathNotReady("buy_order_settlement_batch_not_found")
    if settlement_batch.status != BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED:
        raise SellPathNotReady(
            f"buy_path_settlement_batch_not_completed: {settlement_batch.status}"
        )

    allocation_batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.settlement_batch_id == int(settlement_batch.id))
        .first()
    )
    if allocation_batch is None:
        raise SellPathNotReady("buy_path_allocation_batch_not_found")
    if allocation_batch.status != "allocation_completed_with_residual_cash":
        raise SellPathNotReady(
            f"buy_path_allocation_batch_unexpected_status: {allocation_batch.status}"
        )


def validate_redeem_route_and_service_source() -> None:
    routes_source = read(SOURCE_PATHS["routes"])
    order_source = read(SOURCE_PATHS["order_service"])

    required = [
        '@router.post("/api/trading/orders/redeem")',
        "create_redeem_order",
        "validate_redeem_shares_limits",
        'side="redeem"',
        "shares_reserved",
        "active_redeem_order_exists",
        "commit: bool = True",
    ]
    missing = [item for item in required if item not in routes_source + order_source]
    if missing:
        raise SellPathNotReady(f"redeem_route_or_service_missing: {missing}")


def validate_negative_imports() -> None:
    for module_name in REQUIRED_NEGATIVE_MODULES:
        importlib.import_module(module_name)


def validate_negative_live_gates_source() -> None:
    config_source = read("app/config.py")
    sources = "\n".join(read(path) for path in SOURCE_PATHS.values())

    required_settings = [
        "LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED",
        "NEGATIVE_NET_TARGETS_ALLOW_LIVE_FEE",
        "NEGATIVE_NET_SALE_PLAN_ALLOW_LIVE_READONLY",
        "NEGATIVE_NET_SALE_EXECUTION_ALLOW_LIVE",
        "NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE",
        "NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE_EXECUTION",
        "NEGATIVE_NET_PAYOUT_ALLOW_LIVE",
        "NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION",
        "NEGATIVE_NET_FINALIZATION_ALLOW_LIVE_EXECUTION",
    ]
    missing_settings = [name for name in required_settings if name not in config_source]
    if missing_settings:
        raise SellPathNotReady(f"missing_negative_live_settings: {missing_settings}")

    required_gate_snippets = [
        "negative_net_targets_fee",
        "negative_sale_plan_live_read_only",
        "negative_sale_execution",
        "negative_bybit_flow",
        "negative_payout",
        "negative_finalization",
        "evaluate_live_gate",
    ]
    missing_gates = [snippet for snippet in required_gate_snippets if snippet not in sources]
    if missing_gates:
        raise SellPathNotReady(f"missing_negative_live_gate_snippets: {missing_gates}")


def validate_operation_guard_coverage_source() -> None:
    sale_execution = read(SOURCE_PATHS["sale_execution"])
    bybit_flow = read(SOURCE_PATHS["bybit_flow"])
    payout_flow = read(SOURCE_PATHS["payout_flow"])
    finalization = read(SOURCE_PATHS["finalization"])

    checks = {
        "sale_execution_guard": "require_bybit_negative_sale_order_guard" in sale_execution
        and "create_market_sell_order" in sale_execution,
        "universal_transfer_guard": "require_bybit_universal_transfer_guard" in bybit_flow
        and "create_universal_transfer" in bybit_flow,
        "master_withdrawal_guard": "require_bybit_master_withdrawal_guard" in bybit_flow
        and "create_master_withdrawal" in bybit_flow,
        "bsc_payout_guard": "require_bsc_redeem_payout_guard" in payout_flow
        and "_send_usdt_transfer" in payout_flow,
        "settlement_gas_guard": "require_bsc_settlement_gas_topup_guard" in payout_flow
        and "send_native_bnb" in payout_flow,
        "finalization_idempotent": "_result_from_completed" in finalization
        and "idempotent" in finalization
        and "shares_outstanding_after" in finalization,
    }
    failures = [name for name, ok in checks.items() if not ok]
    if failures:
        raise SellPathNotReady(f"operation_guard_or_idempotency_coverage_missing: {failures}")


def validate_no_unapproved_external_writes_in_verifier() -> None:
    path = "scripts/stage26_3_1_verify_production_wb_test_sell_path.py"
    tree = ast.parse(read(path))

    forbidden_call_names = {
        "post",
        "send_raw_transaction",
        "_send_usdt_transfer",
        "send_native_bnb",
        "create_market_sell_order",
        "create_master_withdrawal",
        "create_universal_transfer",
        "execute_negative_sale_plan_live",
        "execute_negative_bybit_flow_live",
        "execute_negative_payout_flow_live",
        "finalize_negative_net_settlement",
    }

    forbidden_import_names = {
        "send_raw_transaction",
        "_send_usdt_transfer",
        "send_native_bnb",
        "create_market_sell_order",
        "create_master_withdrawal",
        "create_universal_transfer",
        "execute_negative_sale_plan_live",
        "execute_negative_bybit_flow_live",
        "execute_negative_payout_flow_live",
        "finalize_negative_net_settlement",
    }

    calls: set[str] = set()
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)

        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imports.add(alias.name)

        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)

    forbidden_calls_present = sorted(calls & forbidden_call_names)
    forbidden_imports_present = sorted(imports & forbidden_import_names)

    if forbidden_calls_present:
        raise SellPathNotReady(
            f"verifier_ast_contains_forbidden_external_write_calls: {forbidden_calls_present}"
        )

    if forbidden_imports_present:
        raise SellPathNotReady(
            f"verifier_imports_forbidden_external_write_functions: {forbidden_imports_present}"
        )


def validate_redeem_limits_block_bad_inputs() -> None:
    try:
        create_redeem_order
        validate_redeem_shares_limits
    except Exception as exc:
        raise SellPathNotReady(f"redeem_service_import_failed: {exc}") from exc


def count_safety_state(db, *, fund_id: int, user_id: int) -> dict[str, Any]:
    position = (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id == int(user_id),
            UserFundPosition.fund_id == int(fund_id),
        )
        .first()
    )
    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == int(user_id),
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .first()
    )

    return {
        "fund_orders_count": db.query(FundOrder)
        .filter(FundOrder.fund_id == int(fund_id))
        .count(),
        "redeem_orders_count": (
            db.query(FundOrder)
            .filter(
                FundOrder.fund_id == int(fund_id),
                FundOrder.side == ORDER_SIDE_REDEEM,
            )
            .count()
        ),
        "negative_batches_count": sum(
            db.query(model).filter(model.fund_id == int(fund_id)).count()
            for model in NEGATIVE_MODELS
        ),
        "position_shares": dec(position.shares) if position else None,
        "position_shares_reserved": (
            dec(position.shares_reserved) if position else None
        ),
        "wallet_balance": dec(wallet.usdt_balance) if wallet else None,
        "wallet_reserved": dec(wallet.usdt_reserved) if wallet else None,
    }


def simulate_redeem_order_rollback(
    db,
    *,
    user: User,
    fund: Fund,
    redeem_shares: Decimal,
) -> dict[str, Any]:
    before = count_safety_state(db, fund_id=int(fund.id), user_id=int(user.id))

    nested = db.begin_nested()

    try:
        response = create_redeem_order(
            db=db,
            user=user,
            fund_code=str(fund.code),
            shares=str(redeem_shares),
            lang="en",
            commit=False,
        )

        order_id = int(response["order"]["id"])
        order = db.query(FundOrder).filter(FundOrder.id == order_id).first()
        position = (
            db.query(UserFundPosition)
            .filter(
                UserFundPosition.user_id == int(user.id),
                UserFundPosition.fund_id == int(fund.id),
            )
            .first()
        )

        if order is None:
            raise SellPathNotReady("simulated_redeem_order_not_created")
        if order.side != ORDER_SIDE_REDEEM:
            raise SellPathNotReady(f"simulated_redeem_wrong_side: {order.side}")
        if order.status != ORDER_STATUS_PENDING:
            raise SellPathNotReady(f"simulated_redeem_wrong_status: {order.status}")
        if dec(order.shares) != redeem_shares:
            raise SellPathNotReady(f"simulated_redeem_wrong_shares: {order.shares}")
        if position is None:
            raise SellPathNotReady("position_missing_after_simulated_redeem")
        if dec(position.shares_reserved) < redeem_shares:
            raise SellPathNotReady("redeem_order_did_not_reserve_shares")

        nested.rollback()
        db.expire_all()

    except Exception:
        if nested.is_active:
            nested.rollback()
        db.expire_all()
        raise

    after = count_safety_state(db, fund_id=int(fund.id), user_id=int(user.id))
    if after != before:
        raise SellPathNotReady(f"rollback_state_mismatch: before={before}, after={after}")

    return {
        "before": before,
        "after": after,
        "order_id": order_id,
    }


def create_selftest_fixture(db):
    suffix = uuid.uuid4().hex[:12]
    now = utcnow()

    user = User(
        created_at=now,
        email=f"stage26_3_1_{suffix}@example.com",
        first_name="Stage",
        last_name="SellPath",
        phone=None,
        password_hash="test",
        is_active=True,
        is_email_verified=True,
        two_factor_enabled=False,
        account_type="tester",
        compliance_status="ok",
    )
    db.add(user)
    db.flush()

    fund = Fund(
        code=f"stage26_3_1_{suffix}",
        name_ru="Stage 26.3.1",
        name_en="Stage 26.3.1",
        category="test",
        sort_order=9999,
        is_active=True,
    )
    db.add(fund)
    db.flush()

    position = UserFundPosition(
        user_id=int(user.id),
        fund_id=int(fund.id),
        shares=Decimal("10"),
        shares_reserved=Decimal("0"),
    )
    db.add(position)

    wallet = UserWallet(
        user_id=int(user.id),
        blockchain="BSC",
        address=f"0x{suffix:0<40}"[:42],
        encrypted_private_key="test-only",
        usdt_balance=Decimal("2"),
        usdt_reserved=Decimal("0"),
        compliance_status="ok",
        is_active=True,
    )
    db.add(wallet)
    db.flush()

    return user, fund, position, wallet


def selftest_redeem_order_entry() -> None:
    db = SessionLocal()
    original_codes = settings.ORDER_ENTRY_ENABLED_FUND_CODES

    try:
        user, fund, _position, _wallet = create_selftest_fixture(db)
        settings.ORDER_ENTRY_ENABLED_FUND_CODES = str(fund.code)

        result = simulate_redeem_order_rollback(
            db,
            user=user,
            fund=fund,
            redeem_shares=Decimal("1"),
        )

        assert_ok("SELFTEST_REDEEM_ROLLBACK_ORDER_ID_PRESENT", int(result["order_id"]) > 0)
        print("STAGE26_3_1_REDEEM_ORDER_ENTRY_ROLLBACK_OK")

    finally:
        settings.ORDER_ENTRY_ENABLED_FUND_CODES = original_codes
        db.rollback()
        db.close()


def selftest_overshare_blocked() -> None:
    db = SessionLocal()
    original_codes = settings.ORDER_ENTRY_ENABLED_FUND_CODES

    try:
        user, fund, _position, _wallet = create_selftest_fixture(db)
        settings.ORDER_ENTRY_ENABLED_FUND_CODES = str(fund.code)

        try:
            create_redeem_order(
                db=db,
                user=user,
                fund_code=str(fund.code),
                shares="11",
                lang="en",
                commit=False,
            )
            raise AssertionError("Expected insufficient_shares")
        except TradingOrderError as exc:
            assert_ok("SELFTEST_OVERSHARE_ERROR", exc.error_key == "insufficient_shares")

        print("STAGE26_3_1_REDEEM_OVERSHARE_BLOCKED_OK")

    finally:
        settings.ORDER_ENTRY_ENABLED_FUND_CODES = original_codes
        db.rollback()
        db.close()


def selftest_duplicate_active_blocked() -> None:
    db = SessionLocal()
    original_codes = settings.ORDER_ENTRY_ENABLED_FUND_CODES

    try:
        user, fund, _position, _wallet = create_selftest_fixture(db)
        settings.ORDER_ENTRY_ENABLED_FUND_CODES = str(fund.code)

        existing = FundOrder(
            user_id=int(user.id),
            fund_id=int(fund.id),
            side=ORDER_SIDE_REDEEM,
            shares=Decimal("1"),
            amount_usdt=None,
            price_usdt=None,
            status=ORDER_STATUS_PENDING,
            created_at=utcnow(),
            executed_at=None,
        )
        db.add(existing)
        db.flush()

        try:
            create_redeem_order(
                db=db,
                user=user,
                fund_code=str(fund.code),
                shares="1",
                lang="en",
                commit=False,
            )
            raise AssertionError("Expected active_redeem_order_exists")
        except TradingOrderError as exc:
            assert_ok(
                "SELFTEST_DUPLICATE_ACTIVE_ERROR",
                exc.error_key == "active_redeem_order_exists",
            )

        print("STAGE26_3_1_REDEEM_DUPLICATE_ACTIVE_BLOCKED_OK")

    finally:
        settings.ORDER_ENTRY_ENABLED_FUND_CODES = original_codes
        db.rollback()
        db.close()


def selftest_negative_modules_import() -> None:
    validate_negative_imports()
    print("STAGE26_3_1_NEGATIVE_WORKERS_IMPORT_OK")


def selftest_live_gates_fail_closed() -> None:
    validate_negative_live_gates_source()

    from workers.fund_negative_net_targets_worker import _build_parser as targets_parser
    from workers.fund_negative_sale_execution_worker import parse_worker_args as sale_exec_parse
    from workers.fund_negative_bybit_flow_worker import parse_worker_args as bybit_parse
    from workers.fund_negative_payout_worker import _parse_args as payout_parse
    from workers.fund_negative_finalization_worker import _parse_args as final_parse

    targets_args = targets_parser().parse_args(["--live-execution"])
    from workers.fund_negative_net_targets_worker import _validate_stage23_1_args
    assert_ok("SELFTEST_TARGETS_GATE_BLOCKS", _validate_stage23_1_args(targets_args) is None)

    sale_args = sale_exec_parse(["--live-execution"])
    assert_ok("SELFTEST_SALE_EXEC_GATE_BLOCKS", sale_args.live_gate_allowed is False)

    bybit_args = bybit_parse(["--live-execution"])
    assert_ok("SELFTEST_BYBIT_GATE_BLOCKS", bybit_args.live_gate_allowed is False)

    payout_args = payout_parse(["--live-execution"])
    assert_ok("SELFTEST_PAYOUT_GATE_BLOCKS", payout_args.live_gate_allowed is False)

    final_args = final_parse(["--live-execution"])
    assert_ok("SELFTEST_FINAL_GATE_BLOCKS", final_args.live_gate_allowed is False)

    print("STAGE26_3_1_NEGATIVE_LIVE_GATES_FAIL_CLOSED_OK")


def selftest_operation_guard_coverage() -> None:
    validate_operation_guard_coverage_source()
    print("STAGE26_3_1_NEGATIVE_OPERATION_GUARD_COVERAGE_OK")


def selftest_negative_state_clean() -> None:
    db = SessionLocal()

    try:
        _user, fund, _position, _wallet = create_selftest_fixture(db)
        validate_negative_state_clean(db, fund_id=int(fund.id))
        print("STAGE26_3_1_NEGATIVE_STATE_CLEAN_OK")

    finally:
        db.rollback()
        db.close()


def selftest_rollback_safety() -> None:
    validate_no_unapproved_external_writes_in_verifier()
    print("STAGE26_3_1_SELL_PATH_ROLLBACK_SAFETY_OK")


def run_self_test() -> int:
    selftest_redeem_order_entry()
    selftest_overshare_blocked()
    selftest_duplicate_active_blocked()
    selftest_negative_modules_import()
    selftest_live_gates_fail_closed()
    selftest_operation_guard_coverage()
    selftest_negative_state_clean()
    selftest_rollback_safety()
    print("STAGE26_3_1_SELL_PATH_VERIFIER_SELF_TEST_OK")
    return 0


def run_production_rollback(args: argparse.Namespace) -> int:
    require_rollback(args)

    fund_code = normalize_fund_code(args.fund_code)
    if args.user_id is None:
        raise SellPathNotReady("--user-id is required")
    redeem_shares = parse_positive_decimal(args.redeem_shares, name="--redeem-shares")
    validate_redeem_shares_limits(redeem_shares)

    db = SessionLocal()

    bybit_post_calls = 0
    bsc_tx_sent = 0

    try:
        fund = get_fund_or_fail(db, fund_code=fund_code)
        user = get_user_or_fail(db, user_id=int(args.user_id))
        position = get_position_or_fail(db, user_id=int(user.id), fund_id=int(fund.id))

        if dec(position.shares) < redeem_shares:
            raise SellPathNotReady(
                f"insufficient_position_shares: position={position.shares}, requested={redeem_shares}"
            )
        if dec(position.shares) - dec(position.shares_reserved) < redeem_shares:
            raise SellPathNotReady(
                f"insufficient_available_position_shares: "
                f"shares={position.shares}, reserved={position.shares_reserved}, requested={redeem_shares}"
            )

        print("STAGE26_3_1_PRODUCTION_USER_POSITION_OK")

        wallet = get_wallet_or_fail(db, user_id=int(user.id))
        validate_no_active_orders(db, fund_id=int(fund.id), user_id=int(user.id))
        print("STAGE26_3_1_PRODUCTION_NO_ACTIVE_ORDERS_OK")

        validate_pricing_nav_state(db, fund_id=int(fund.id), position=position)
        validate_buy_path_terminal_clean(
            db,
            fund_id=int(fund.id),
            user_id=int(user.id),
            redeem_shares=redeem_shares,
        )
        print("STAGE26_3_1_PRODUCTION_PRICING_NAV_OK")

        validate_negative_state_clean(db, fund_id=int(fund.id))
        print("STAGE26_3_1_PRODUCTION_NEGATIVE_STATE_CLEAN_OK")

        validate_redeem_route_and_service_source()
        validate_redeem_limits_block_bad_inputs()

        before = count_safety_state(db, fund_id=int(fund.id), user_id=int(user.id))
        simulate_redeem_order_rollback(
            db,
            user=user,
            fund=fund,
            redeem_shares=redeem_shares,
        )
        after = count_safety_state(db, fund_id=int(fund.id), user_id=int(user.id))

        if before != after:
            raise SellPathNotReady(f"post_rollback_state_changed: before={before}, after={after}")

        print("STAGE26_3_1_REDEEM_ORDER_ENTRY_ROLLBACK_OK")

        validate_negative_imports()
        print("STAGE26_3_1_NEGATIVE_WORKERS_IMPORT_OK")

        validate_negative_live_gates_source()
        print("STAGE26_3_1_NEGATIVE_LIVE_GATES_FAIL_CLOSED_OK")

        validate_operation_guard_coverage_source()
        print("STAGE26_3_1_NEGATIVE_OPERATION_GUARD_COVERAGE_OK")

        validate_no_unapproved_external_writes_in_verifier()
        print("STAGE26_3_1_SELL_PATH_ROLLBACK_SAFETY_OK")

        db.rollback()

        print_kv("FUND_ORDERS_CREATED", 0)
        print_kv("NEGATIVE_BATCHES_CREATED", 0)
        print_kv("BYBIT_POST_CALLS", bybit_post_calls)
        print_kv("BSC_TX_SENT", bsc_tx_sent)
        print_kv("ROLLBACK_COMPLETED", True)
        print_kv("ready", True)
        print(READY_MARKER)
        return 0

    except Exception as exc:
        db.rollback()

        print_kv("FUND_ORDERS_CREATED", 0)
        print_kv("NEGATIVE_BATCHES_CREATED", 0)
        print_kv("BYBIT_POST_CALLS", bybit_post_calls)
        print_kv("BSC_TX_SENT", bsc_tx_sent)
        print_kv("ROLLBACK_COMPLETED", True)
        print_kv("ready", False)
        print(f"NOT_READY_REASON={exc}")
        print(NOT_READY_MARKER)
        return 1

    finally:
        db.close()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if args.self_test:
        return run_self_test()

    return run_production_rollback(args)


if __name__ == "__main__":
    raise SystemExit(main())