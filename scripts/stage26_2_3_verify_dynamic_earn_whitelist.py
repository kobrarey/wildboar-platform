from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import settings
from app.allocation.live_earn_config import require_live_earn_whitelisted


ROOT = Path(__file__).resolve().parents[1]


BASE_SETTING_VALUES = {
    "ALLOCATION_EARN_ENABLED": True,
    "ALLOCATION_EARN_ALLOW_LIVE": True,
    "ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST": True,
    "ALLOCATION_EARN_ALLOWED_FUND_CODES": "wb_test",
    "ALLOCATION_EARN_ALLOWED_COINS": "",
    "ALLOCATION_EARN_ALLOWED_PRODUCT_IDS": "",
    "ALLOCATION_EARN_ALLOWED_CATEGORIES": "FlexibleSaving",
}


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def snapshot_settings() -> dict[str, Any]:
    return {
        key: getattr(settings, key)
        for key in BASE_SETTING_VALUES
    }


def restore_settings(snapshot: dict[str, Any]) -> None:
    for key, value in snapshot.items():
        setattr(settings, key, value)


def configure(**overrides: Any) -> None:
    for key, value in BASE_SETTING_VALUES.items():
        setattr(settings, key, value)

    for key, value in overrides.items():
        setattr(settings, key, value)


def decision(
    *,
    fund_code: str = "wb_test",
    coin: str = "USDT",
    category: str = "FlexibleSaving",
    product_id: str = "bybit-dynamic-product-1",
    amount: str = "10",
):
    return require_live_earn_whitelisted(
        fund_code=fund_code,
        coin=coin,
        category=category,
        product_id=product_id,
        amount=Decimal(amount),
    )


def test_default_strict_mode_blocks_empty_product_whitelist() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=True,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
    )

    result = decision()

    assert_ok("STRICT_EMPTY_PRODUCT_WHITELIST_BLOCKS", result.ok is False)
    assert_ok(
        "STRICT_EMPTY_PRODUCT_WHITELIST_REASON",
        result.reason == "earn_product_id_whitelist_empty",
    )
    assert_ok(
        "STRICT_DIAGNOSTIC_REQUIRE_PRODUCT_ID_WHITELIST",
        result.diagnostics["require_product_id_whitelist"] is True,
    )
    assert_ok(
        "STRICT_DIAGNOSTIC_DYNAMIC_PRODUCT_ID_FALSE",
        result.diagnostics["dynamic_product_id_allowed"] is False,
    )


def test_dynamic_mode_allows_empty_product_whitelist() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
        ALLOCATION_EARN_ALLOWED_FUND_CODES="wb_test",
        ALLOCATION_EARN_ALLOWED_COINS="",
        ALLOCATION_EARN_ALLOWED_CATEGORIES="FlexibleSaving",
    )

    result = decision(product_id="bybit-dynamic-product-1")

    assert_ok("DYNAMIC_EMPTY_PRODUCT_WHITELIST_ALLOWS", result.ok is True)
    assert_ok("DYNAMIC_EMPTY_PRODUCT_WHITELIST_NO_REASON", result.reason is None)
    assert_ok(
        "DYNAMIC_DIAGNOSTIC_REQUIRE_PRODUCT_ID_WHITELIST_FALSE",
        result.diagnostics["require_product_id_whitelist"] is False,
    )
    assert_ok(
        "DYNAMIC_DIAGNOSTIC_DYNAMIC_PRODUCT_ID_TRUE",
        result.diagnostics["dynamic_product_id_allowed"] is True,
    )


def test_dynamic_mode_enforces_non_empty_product_whitelist() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="abc",
    )

    result = decision(product_id="xyz")

    assert_ok("DYNAMIC_NON_EMPTY_PRODUCT_WHITELIST_BLOCKS", result.ok is False)
    assert_ok(
        "DYNAMIC_NON_EMPTY_PRODUCT_WHITELIST_REASON",
        result.reason == "earn_product_id_not_whitelisted",
    )


def test_fund_whitelist_still_blocks() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
        ALLOCATION_EARN_ALLOWED_FUND_CODES="wb_test",
    )

    result = decision(fund_code="other_fund")

    assert_ok("FUND_WHITELIST_STILL_BLOCKS", result.ok is False)
    assert_ok(
        "FUND_WHITELIST_REASON",
        result.reason == "fund_code_not_whitelisted",
    )


def test_category_whitelist_still_blocks() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
        ALLOCATION_EARN_ALLOWED_CATEGORIES="FlexibleSaving",
    )

    result = decision(category="FixedSaving")

    assert_ok("CATEGORY_WHITELIST_STILL_BLOCKS", result.ok is False)
    assert_ok(
        "CATEGORY_WHITELIST_REASON",
        result.reason == "earn_category_not_whitelisted",
    )


def test_coin_whitelist_still_blocks_when_non_empty() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
        ALLOCATION_EARN_ALLOWED_COINS="USDT",
    )

    result = decision(coin="BTC")

    assert_ok("COIN_WHITELIST_STILL_BLOCKS", result.ok is False)
    assert_ok(
        "COIN_WHITELIST_REASON",
        result.reason == "coin_not_whitelisted",
    )


def test_disabled_earn_still_blocks() -> None:
    configure(ALLOCATION_EARN_ENABLED=False)

    result = decision()

    assert_ok("EARN_ENABLED_FALSE_BLOCKS", result.ok is False)
    assert_ok(
        "EARN_ENABLED_FALSE_REASON",
        result.reason == "allocation_earn_enabled_false",
    )

    configure(ALLOCATION_EARN_ALLOW_LIVE=False)

    result = decision()

    assert_ok("EARN_ALLOW_LIVE_FALSE_BLOCKS", result.ok is False)
    assert_ok(
        "EARN_ALLOW_LIVE_FALSE_REASON",
        result.reason == "allocation_earn_allow_live_false",
    )


def test_amount_must_be_positive() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
    )

    result = decision(amount="0")

    assert_ok("AMOUNT_ZERO_BLOCKS", result.ok is False)
    assert_ok(
        "AMOUNT_ZERO_REASON",
        result.reason == "earn_amount_must_be_positive",
    )

    result = decision(amount="-1")

    assert_ok("AMOUNT_NEGATIVE_BLOCKS", result.ok is False)
    assert_ok(
        "AMOUNT_NEGATIVE_REASON",
        result.reason == "earn_amount_must_be_positive",
    )


def test_product_id_still_required() -> None:
    configure(
        ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=False,
        ALLOCATION_EARN_ALLOWED_PRODUCT_IDS="",
    )

    result = decision(product_id="")

    assert_ok("PRODUCT_ID_STILL_REQUIRED", result.ok is False)
    assert_ok(
        "PRODUCT_ID_STILL_REQUIRED_REASON",
        result.reason == "earn_product_id_required",
    )


def test_source_contracts() -> None:
    config = read("app/config.py")
    env_example = read(".env.example")
    live_earn_config = read("app/allocation/live_earn_config.py")
    live_earn_orders = read("app/allocation/live_earn_orders.py")
    hooks = read("app/operation_guard/hooks.py")
    statuses = read("app/operation_guard/statuses.py")

    assert_ok(
        "CONFIG_HAS_REQUIRE_PRODUCT_ID_WHITELIST_DEFAULT_TRUE",
        "ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST: bool = True" in config,
    )
    assert_ok(
        "ENV_EXAMPLE_HAS_REQUIRE_PRODUCT_ID_WHITELIST_TRUE",
        "ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST=true" in env_example,
    )
    assert_ok(
        "LIVE_EARN_CONFIG_HAS_REQUIRE_DIAGNOSTIC",
        '"require_product_id_whitelist": require_product_id_whitelist' in live_earn_config,
    )
    assert_ok(
        "LIVE_EARN_CONFIG_HAS_DYNAMIC_DIAGNOSTIC",
        '"dynamic_product_id_allowed": dynamic_product_id_allowed' in live_earn_config,
    )
    assert_ok(
        "LIVE_EARN_CONFIG_PRESERVES_STRICT_EMPTY_WHITELIST_BLOCK",
        'reason="earn_product_id_whitelist_empty"' in live_earn_config,
    )
    assert_ok(
        "LIVE_EARN_CONFIG_PRESERVES_NOT_WHITELISTED_BLOCK",
        'reason="earn_product_id_not_whitelisted"' in live_earn_config,
    )
    assert_ok(
        "LIVE_EARN_CONFIG_PRESERVES_PRODUCT_ID_REQUIRED",
        'reason="earn_product_id_required"' in live_earn_config,
    )
    assert_ok(
        "LIVE_EARN_CONFIG_PRESERVES_AMOUNT_POSITIVE",
        'reason="earn_amount_must_be_positive"' in live_earn_config,
    )

    product_lookup_pos = live_earn_orders.find("get_earn_product_info(")
    product_validation_pos = live_earn_orders.find("validate_earn_product_for_stake(")
    whitelist_pos = live_earn_orders.find("require_live_earn_whitelisted(")
    guard_pos = live_earn_orders.find("require_earn_guard_for_plan")

    assert_ok(
        "EARN_PRODUCT_LOOKUP_BEFORE_WHITELIST",
        product_lookup_pos != -1 and product_lookup_pos < whitelist_pos,
    )
    assert_ok(
        "EARN_PRODUCT_VALIDATION_BEFORE_WHITELIST",
        product_validation_pos != -1 and product_validation_pos < whitelist_pos,
    )
    assert_ok(
        "EARN_GUARD_FUNCTION_STILL_PRESENT",
        guard_pos != -1,
    )
    assert_ok(
        "EARN_ORDER_USES_OPERATION_GUARD_HOOK",
        "require_bybit_allocation_earn_order_guard" in live_earn_orders,
    )
    assert_ok(
        "HOOK_USES_BYBIT_ALLOCATION_EARN_ORDER",
        "OP_GUARD_ACTION_BYBIT_ALLOCATION_EARN_ORDER" in hooks,
    )
    assert_ok(
        "STATUS_HAS_BYBIT_ALLOCATION_EARN_ORDER",
        'OP_GUARD_ACTION_BYBIT_ALLOCATION_EARN_ORDER = "bybit_allocation_earn_order"' in statuses,
    )

    forbidden = "\n".join([config, env_example, live_earn_config, live_earn_orders])
    assert_ok("NO_WALLET_ENC_KEY_TOUCH_IN_CHANGED_LOGIC", "WALLET_ENC_KEY" not in live_earn_config)
    assert_ok("NO_BYBIT_API_ENC_KEY_TOUCH_IN_CHANGED_LOGIC", "BYBIT_API_ENC_KEY" not in live_earn_config)
    assert_ok("NO_FREEZE_GUARD_TOUCH_IN_CHANGED_LOGIC", "BYBIT_SUBACCOUNT_FREEZE_GUARD" not in live_earn_config)
    verify_script = read("scripts/stage26_2_3_verify_dynamic_earn_whitelist.py")
    verify_script_body = verify_script.split(
        'verify_script = read("scripts/stage26_2_3_verify_dynamic_earn_whitelist.py")',
        1,
    )[0]
    assert_ok("VERIFY_SCRIPT_DOES_NOT_IMPORT_BYBIT_CLIENT", "BybitV5Client" not in verify_script_body)
    assert_ok("VERIFY_SCRIPT_DOES_NOT_CALL_CLIENT_POST", ".post(" not in verify_script_body)
    assert_ok("NO_DELETE_IN_VERIFY_SCRIPT", "DELETE FROM" not in verify_script_body.upper())


def main() -> None:
    original = snapshot_settings()
    try:
        test_default_strict_mode_blocks_empty_product_whitelist()
        test_dynamic_mode_allows_empty_product_whitelist()
        test_dynamic_mode_enforces_non_empty_product_whitelist()
        test_fund_whitelist_still_blocks()
        test_category_whitelist_still_blocks()
        test_coin_whitelist_still_blocks_when_non_empty()
        test_disabled_earn_still_blocks()
        test_amount_must_be_positive()
        test_product_id_still_required()
        test_source_contracts()
        print("STAGE26_2_3_DYNAMIC_EARN_WHITELIST_VERIFY_OK")

    finally:
        restore_settings(original)


if __name__ == "__main__":
    main()