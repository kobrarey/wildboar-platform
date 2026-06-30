from __future__ import annotations

import ast
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import settings
from app.allocation.live_execution import preflight_live_allocation_batch
from app.allocation.live_policy import (
    BUY_THEN_STAKE_SPOT_ONLY_REASON,
    DERIVATIVE_OPTION_SKIP_REASON,
    BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED,
    BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY,
    DERIVATIVE_LIVE_POLICY_FAIL_CLOSED,
    DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING,
    classify_live_leg_policy,
)
from app.allocation.plan_service import _build_planned_legs
from app.allocation.snapshot_service import (
    AllocationAccountRisk,
    AllocationSnapshot,
    AllocationSnapshotHolding,
)
from app.allocation.statuses import (
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_LONG_OPTION_INCREASE,
    LEG_TYPE_PERP_INCREASE,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.models import FundAllocationLeg


ROOT = Path(__file__).resolve().parents[1]


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def imported_names(path: str) -> set[str]:
    tree = ast.parse(read(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            for alias in node.names:
                names.add(alias.name)

    return names


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def snapshot_settings() -> dict[str, Any]:
    return {
        "ALLOCATION_DERIVATIVE_LIVE_POLICY": settings.ALLOCATION_DERIVATIVE_LIVE_POLICY,
        "ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY": settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY,
        "ALLOCATION_EARN_ENABLED": settings.ALLOCATION_EARN_ENABLED,
        "ALLOCATION_EARN_ALLOW_LIVE": settings.ALLOCATION_EARN_ALLOW_LIVE,
        "ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST": settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST,
        "ALLOCATION_EARN_ALLOWED_FUND_CODES": settings.ALLOCATION_EARN_ALLOWED_FUND_CODES,
        "ALLOCATION_EARN_ALLOWED_COINS": settings.ALLOCATION_EARN_ALLOWED_COINS,
        "ALLOCATION_EARN_ALLOWED_CATEGORIES": settings.ALLOCATION_EARN_ALLOWED_CATEGORIES,
        "ALLOCATION_EARN_ALLOWED_PRODUCT_IDS": settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS,
    }


def restore_settings(snapshot: dict[str, Any]) -> None:
    for key, value in snapshot.items():
        setattr(settings, key, value)


def configure_default_fail_closed() -> None:
    settings.ALLOCATION_DERIVATIVE_LIVE_POLICY = DERIVATIVE_LIVE_POLICY_FAIL_CLOSED
    settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY = BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED
    settings.ALLOCATION_EARN_ENABLED = True
    settings.ALLOCATION_EARN_ALLOW_LIVE = True
    settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST = False
    settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
    settings.ALLOCATION_EARN_ALLOWED_COINS = ""
    settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
    settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = ""


def configure_controlled_ready_policy() -> None:
    settings.ALLOCATION_DERIVATIVE_LIVE_POLICY = DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING
    settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY = BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY
    settings.ALLOCATION_EARN_ENABLED = True
    settings.ALLOCATION_EARN_ALLOW_LIVE = True
    settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST = False
    settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
    settings.ALLOCATION_EARN_ALLOWED_COINS = ""
    settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
    settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = ""


def build_wb_test_like_snapshot() -> AllocationSnapshot:
    return AllocationSnapshot(
        fund_id=1,
        fund_code="wb_test",
        snapshot_ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        account_type="UNIFIED",
        snapshot_source="stage26_2_7_fixture_wb_test_like",
        risk=AllocationAccountRisk(
            total_equity_usdt=Decimal("1010"),
            total_wallet_balance_usdt=Decimal("1010"),
            total_available_usdt=Decimal("900"),
            total_initial_margin_usdt=Decimal("100"),
            total_maintenance_margin_usdt=Decimal("50"),
            account_im_rate=Decimal("0.10"),
            account_mm_rate=Decimal("0.05"),
        ),
        holdings=[
            AllocationSnapshotHolding(
                leg_group="cash",
                leg_type="stable_cash",
                coin="USDT",
                symbol=None,
                category="wallet",
                location="UNIFIED",
                size=Decimal("110"),
                usd_value=Decimal("110"),
            ),
            AllocationSnapshotHolding(
                leg_group="spot",
                leg_type="spot_holding",
                coin="BTC",
                symbol="BTCUSDT",
                category="spot",
                location="UNIFIED",
                size=Decimal("0.01"),
                usd_value=Decimal("300"),
            ),
            AllocationSnapshotHolding(
                leg_group="earn",
                leg_type="earn_holding",
                coin="USDT",
                symbol=None,
                category="earn",
                location="EARN",
                size=Decimal("200"),
                usd_value=Decimal("200"),
                product="Earn",
                product_category="FlexibleSaving",
            ),
            AllocationSnapshotHolding(
                leg_group="earn",
                leg_type="earn_holding",
                coin="LDO",
                symbol="LDOUSDT",
                category="earn",
                location="EARN",
                size=Decimal("20"),
                usd_value=Decimal("40"),
                product="Earn",
                product_category="FlexibleSaving",
            ),
            AllocationSnapshotHolding(
                leg_group="perp",
                leg_type="perp_position",
                coin="ETH",
                symbol="ETHUSDT",
                category="linear",
                side="Buy",
                location="UNIFIED",
                size=Decimal("0.10"),
                notional_usd=Decimal("250"),
            ),
            AllocationSnapshotHolding(
                leg_group="long_option",
                leg_type="long_option_position",
                coin="BTC",
                symbol="BTC-31DEC26-100000-C",
                category="option",
                side="Buy",
                location="UNIFIED",
                size=Decimal("1"),
                notional_usd=Decimal("100"),
            ),
        ],
        raw_summary_json={"source": "stage26_2_7_fixture"},
    )


def build_fixture_legs() -> list[FundAllocationLeg]:
    snapshot = build_wb_test_like_snapshot()
    positive_net_usdt = Decimal("10")
    base_nav_for_scale_usdt = Decimal("1000")
    scale = positive_net_usdt / base_nav_for_scale_usdt

    planned_legs, _raw_cash_usdt, _adjusted_cash_usdt = _build_planned_legs(
        snapshot=snapshot,
        positive_net_usdt=positive_net_usdt,
        scale=scale,
        base_nav_for_scale_usdt=base_nav_for_scale_usdt,
    )

    out: list[FundAllocationLeg] = []

    for idx, planned in enumerate(planned_legs, start=1):
        leg = FundAllocationLeg(
            id=idx,
            allocation_batch_id=100,
            settlement_batch_id=200,
            fund_id=1,
            leg_index=planned.leg_index,
            leg_key=planned.leg_key,
            leg_group=planned.leg_group,
            leg_type=planned.leg_type,
            coin=planned.coin,
            symbol=planned.symbol,
            category=planned.category,
            side=planned.side,
            location=planned.location,
            current_size=planned.current_size,
            current_usd_value=planned.current_usd_value,
            current_notional_usd=planned.current_notional_usd,
            source_weight=planned.source_weight,
            target_usdt=planned.target_usdt,
            target_qty=planned.target_qty,
            status=planned.status,
            execution_mode="planned",
            error=planned.error,
        )
        out.append(leg)

    return out


def classify_legs(legs: list[FundAllocationLeg]) -> dict[str, Any]:
    supported_live = []
    policy_skipped = []
    fail_closed = []
    required_guards: set[str] = set()

    planned_leg_rows = []

    for leg in legs:
        decision = classify_live_leg_policy(leg)

        planned_leg_rows.append(
            {
                "leg_group": leg.leg_group,
                "leg_type": leg.leg_type,
                "coin": leg.coin,
                "symbol": leg.symbol,
                "category": leg.category,
                "target_usdt": str(leg.target_usdt),
                "target_qty": str(leg.target_qty),
                "policy_action": decision.action,
                "policy_reason": decision.reason,
            }
        )

        required_guards.update(decision.required_guard_actions)

        if decision.fail_closed:
            fail_closed.append((leg, decision))
        elif decision.policy_skipped:
            policy_skipped.append((leg, decision))
        elif decision.supported_live:
            supported_live.append((leg, decision))
        else:
            fail_closed.append((leg, decision))

    return {
        "planned_leg_rows": planned_leg_rows,
        "supported_live": supported_live,
        "policy_skipped": policy_skipped,
        "fail_closed": fail_closed,
        "required_guards": sorted(required_guards),
        "ready": not fail_closed,
    }


def test_fixture_plan_produces_required_leg_types() -> list[FundAllocationLeg]:
    legs = build_fixture_legs()
    leg_types = {str(leg.leg_type) for leg in legs}

    assert_ok("FIXTURE_HAS_STABLE_CASH", LEG_TYPE_STABLE_CASH in leg_types)
    assert_ok("FIXTURE_HAS_SPOT_BUY", LEG_TYPE_SPOT_BUY in leg_types)
    assert_ok("FIXTURE_HAS_USDT_EARN_STAKE", LEG_TYPE_USDT_EARN_STAKE in leg_types)
    assert_ok("FIXTURE_HAS_BUY_THEN_STAKE", LEG_TYPE_BUY_THEN_STAKE in leg_types)
    assert_ok("FIXTURE_HAS_PERP_INCREASE", LEG_TYPE_PERP_INCREASE in leg_types)
    assert_ok("FIXTURE_HAS_LONG_OPTION_INCREASE", LEG_TYPE_LONG_OPTION_INCREASE in leg_types)

    counts = Counter(str(leg.leg_type) for leg in legs)
    print(f"FIXTURE_PLANNED_LEG_TYPES={dict(sorted(counts.items()))}")

    for leg in legs:
        print(
            "FIXTURE_LEG "
            f"group={leg.leg_group} "
            f"type={leg.leg_type} "
            f"coin={leg.coin} "
            f"symbol={leg.symbol} "
            f"category={leg.category} "
            f"target_usdt={leg.target_usdt} "
            f"target_qty={leg.target_qty}"
        )

    return legs


def test_default_policy_not_ready(legs: list[FundAllocationLeg]) -> None:
    configure_default_fail_closed()
    result = classify_legs(legs)

    assert_ok("DEFAULT_POLICY_NOT_READY", result["ready"] is False)
    assert_ok(
        "DEFAULT_POLICY_HAS_FAIL_CLOSED_BUY_THEN_STAKE",
        any(
            decision.reason == "buy_then_stake_live_policy_fail_closed"
            for _leg, decision in result["fail_closed"]
        ),
    )
    assert_ok(
        "DEFAULT_POLICY_HAS_FAIL_CLOSED_DERIVATIVE_OR_OPTION",
        any(
            str(decision.reason or "").startswith("derivative_option_live_policy_fail_closed")
            for _leg, decision in result["fail_closed"]
        ),
    )

    print("STAGE26_2_7_WB_TEST_ACTUAL_ALLOCATION_PATH_NOT_READY")


def test_controlled_policy_ready(legs: list[FundAllocationLeg]) -> None:
    configure_controlled_ready_policy()
    result = classify_legs(legs)

    assert_ok("CONTROLLED_POLICY_READY", result["ready"] is True)
    assert_ok(
        "CONTROLLED_POLICY_SKIPS_DERIVATIVE_OPTION",
        any(
            decision.reason == DERIVATIVE_OPTION_SKIP_REASON
            for _leg, decision in result["policy_skipped"]
        ),
    )
    assert_ok(
        "CONTROLLED_POLICY_BUY_THEN_STAKE_SPOT_ONLY",
        any(
            leg.leg_type == LEG_TYPE_BUY_THEN_STAKE
            and decision.reason == BUY_THEN_STAKE_SPOT_ONLY_REASON
            for leg, decision in result["supported_live"]
        ),
    )
    assert_ok(
        "CONTROLLED_POLICY_REQUIRES_TRADE_GUARD",
        "bybit_allocation_trade_order" in result["required_guards"],
    )
    assert_ok(
        "CONTROLLED_POLICY_REQUIRES_EARN_GUARD",
        "bybit_allocation_earn_order" in result["required_guards"],
    )
    assert_ok(
        "CONTROLLED_POLICY_DOES_NOT_REQUIRE_STRATEGY_GUARD",
        "bybit_allocation_strategy_order" not in result["required_guards"],
    )

    print(f"SUPPORTED_LIVE_LEGS={len(result['supported_live'])}")
    print(f"POLICY_SKIPPED_LEGS={len(result['policy_skipped'])}")
    print(f"FAIL_CLOSED_LEGS={len(result['fail_closed'])}")
    print(f"REQUIRED_OPERATION_GUARD_ACTION_TYPES={result['required_guards']}")


def test_source_contracts() -> None:
    config = read("app/config.py")
    env_example = read(".env.example")
    live_policy = read("app/allocation/live_policy.py")
    live_execution = read("app/allocation/live_execution.py")
    live_spot_orders = read("app/allocation/live_spot_orders.py")
    worker = read("workers/fund_allocation_execution_worker.py")
    derivative_handlers = read("app/allocation/derivative_handlers.py")

    assert_ok(
        "CONFIG_HAS_DERIVATIVE_POLICY_FAIL_CLOSED_DEFAULT",
        'ALLOCATION_DERIVATIVE_LIVE_POLICY: str = "fail_closed"' in config,
    )
    assert_ok(
        "CONFIG_HAS_BUY_THEN_STAKE_POLICY_FAIL_CLOSED_DEFAULT",
        'ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY: str = "fail_closed"' in config,
    )
    assert_ok(
        "ENV_EXAMPLE_HAS_DERIVATIVE_POLICY",
        "ALLOCATION_DERIVATIVE_LIVE_POLICY=fail_closed" in env_example,
    )
    assert_ok(
        "ENV_EXAMPLE_HAS_BUY_THEN_STAKE_POLICY",
        "ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY=fail_closed" in env_example,
    )
    assert_ok(
        "LIVE_POLICY_HAS_DERIVATIVE_SKIP_REASON",
        DERIVATIVE_OPTION_SKIP_REASON in live_policy,
    )
    assert_ok(
        "LIVE_POLICY_HAS_BUY_THEN_STAKE_SPOT_ONLY_REASON",
        BUY_THEN_STAKE_SPOT_ONLY_REASON in live_policy,
    )
    assert_ok(
        "LIVE_POLICY_DOES_NOT_ENABLE_SEQUENTIAL_SPOT_THEN_EARN",
        "sequential_spot_then_earn" not in live_policy,
    )
    assert_ok(
        "LIVE_EXECUTION_USES_POLICY_CLASSIFIER",
        "classify_live_leg_policy" in live_execution,
    )
    assert_ok(
        "LIVE_EXECUTION_HAS_POLICY_SKIP_MARKER",
        "mark_policy_skipped_leg_without_external_call" in live_execution,
    )
    assert_ok(
        "LIVE_SPOT_ALLOWS_BUY_THEN_STAKE_SPOT_ONLY",
        "BUY_THEN_STAKE_SPOT_ONLY_REASON" in live_spot_orders,
    )
    assert_ok(
        "WORKER_USES_POLICY_SKIP_HELPER",
        "_mark_policy_skipped_leg_in_own_session" in worker,
    )
    assert_ok(
        "WORKER_ROUTES_BUY_THEN_STAKE_TO_SPOT_PATH",
        "LEG_TYPE_BUY_THEN_STAKE" in worker and "{LEG_TYPE_SPOT_BUY, LEG_TYPE_BUY_THEN_STAKE}" in worker,
    )
    assert_ok(
        "LIVE_WORKER_MOCK_DERIVATIVE_HANDLER_ONLY_IN_MOCK_PATH",
        "handle_derivative_leg_mock" in worker and "args.live_execution" in worker,
    )
    assert_ok(
        "DERIVATIVE_HANDLER_STILL_MOCK_ONLY",
        "Forbidden:" in derivative_handlers and "real Bybit orders" in derivative_handlers,
    )
    verify_imports = imported_names("scripts/stage26_2_7_verify_wb_test_actual_allocation_path.py")
    verify_script = read("scripts/stage26_2_7_verify_wb_test_actual_allocation_path.py")
    verify_script_body = verify_script.split(
        'verify_script = read("scripts/stage26_2_7_verify_wb_test_actual_allocation_path.py")',
        1,
    )[0]

    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_IMPORT_BYBIT_CLIENT",
        "BybitV5Client" not in verify_imports
        and "build_fund_bybit_client" not in verify_imports
        and "app.bybit.fund_client" not in verify_imports,
    )
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_CALL_CLIENT_POST",
        ".post(" not in verify_script_body,
    )
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_CALL_BSC_SEND",
        ".send_raw_transaction(" not in verify_script_body,
    )
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_CREATE_FUND_ORDER",
        "FundOrder(" not in verify_script_body,
    )
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_RUN_LIFECYCLE",
        "process_" not in verify_script_body.lower(),
    )


def main() -> None:
    original = snapshot_settings()

    try:
        legs = test_fixture_plan_produces_required_leg_types()
        test_default_policy_not_ready(legs)
        test_controlled_policy_ready(legs)
        test_source_contracts()
        print("STAGE26_2_7_FIXTURE_ALLOCATION_PATH_READY_OK")

    finally:
        restore_settings(original)


if __name__ == "__main__":
    main()