from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.config import settings
from app.i18n import t
from app.trading.order_service import (
    TradingOrderError,
    _to_decimal,
    validate_buy_amount_limits,
    validate_redeem_shares_limits,
)


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def assert_trading_error(name: str, fn, expected_key: str) -> None:
    try:
        fn()
    except TradingOrderError as exc:
        assert_ok(name, exc.error_key == expected_key)
        return

    raise AssertionError(f"{name}: expected {expected_key}")


def test_buy_amount_limits() -> None:
    assert_trading_error(
        "BUY_AMOUNT_ZERO_REJECTED",
        lambda: _to_decimal("0", error_key="invalid_amount"),
        "invalid_amount",
    )

    assert_trading_error(
        "BUY_AMOUNT_001_BELOW_MIN",
        lambda: validate_buy_amount_limits(_to_decimal("0.01", error_key="invalid_amount")),
        "buy_amount_below_min",
    )

    assert_trading_error(
        "BUY_AMOUNT_999_BELOW_MIN",
        lambda: validate_buy_amount_limits(_to_decimal("9.99", error_key="invalid_amount")),
        "buy_amount_below_min",
    )

    validate_buy_amount_limits(_to_decimal("10", error_key="invalid_amount"))
    assert_ok("BUY_AMOUNT_10_ALLOWED_BY_LIMIT", True)

    validate_buy_amount_limits(_to_decimal("10000000", error_key="invalid_amount"))
    assert_ok("BUY_AMOUNT_10000000_ALLOWED_BY_LIMIT", True)

    assert_trading_error(
        "BUY_AMOUNT_10000000_01_ABOVE_MAX",
        lambda: validate_buy_amount_limits(_to_decimal("10000000.01", error_key="invalid_amount")),
        "buy_amount_above_max",
    )


def test_redeem_share_limits() -> None:
    assert_trading_error(
        "REDEEM_SHARES_ZERO_REJECTED",
        lambda: _to_decimal("0", error_key="invalid_shares"),
        "invalid_shares",
    )

    validate_redeem_shares_limits(_to_decimal("1000", error_key="invalid_shares"))
    assert_ok("REDEEM_SHARES_1000_ALLOWED_BY_LIMIT", True)

    assert_trading_error(
        "REDEEM_SHARES_1000_0001_ABOVE_MAX",
        lambda: validate_redeem_shares_limits(_to_decimal("1000.0001", error_key="invalid_shares")),
        "redeem_shares_above_max",
    )


def test_config_values() -> None:
    assert_ok("CONFIG_BUY_MIN_10", settings.TRADING_BUY_MIN_USDT == Decimal("10"))
    assert_ok("CONFIG_BUY_MAX_10000000", settings.TRADING_BUY_MAX_USDT == Decimal("10000000"))
    assert_ok("CONFIG_REDEEM_MAX_1000", settings.TRADING_REDEEM_MAX_SHARES == Decimal("1000"))


def test_i18n_messages() -> None:
    assert_ok(
        "I18N_EN_BUY_BELOW_MIN",
        t("en", "buy_amount_below_min") == "Minimum purchase amount is 10 USDT.",
    )
    assert_ok(
        "I18N_RU_BUY_BELOW_MIN",
        t("ru", "buy_amount_below_min") == "Минимальная сумма покупки — 10 USDT.",
    )
    assert_ok(
        "I18N_EN_BUY_ABOVE_MAX",
        t("en", "buy_amount_above_max") == "Maximum purchase amount is 10,000,000 USDT.",
    )
    assert_ok(
        "I18N_RU_BUY_ABOVE_MAX",
        t("ru", "buy_amount_above_max") == "Максимальная сумма покупки — 10,000,000 USDT.",
    )
    assert_ok(
        "I18N_EN_REDEEM_ABOVE_MAX",
        t("en", "redeem_shares_above_max") == "Maximum redemption amount is 1,000 shares.",
    )
    assert_ok(
        "I18N_RU_REDEEM_ABOVE_MAX",
        t("ru", "redeem_shares_above_max") == "Максимальная сумма погашения — 1,000 паёв.",
    )


def test_order_service_validation_before_mutation_source() -> None:
    src = Path("app/trading/order_service.py").read_text(encoding="utf-8")

    buy_fn = src.split("def create_buy_order", 1)[1].split("def create_redeem_order", 1)[0]
    redeem_fn = src.split("def create_redeem_order", 1)[1]

    assert_ok(
        "BUY_LIMIT_CHECK_BEFORE_WALLET_LOCK",
        buy_fn.find("validate_buy_amount_limits(amount)") != -1
        and buy_fn.find("_lock_active_user_wallet") != -1
        and buy_fn.find("validate_buy_amount_limits(amount)") < buy_fn.find("_lock_active_user_wallet"),
    )
    assert_ok(
        "BUY_LIMIT_CHECK_BEFORE_WALLET_RESERVE",
        buy_fn.find("validate_buy_amount_limits(amount)") != -1
        and buy_fn.find("wallet.usdt_reserved") != -1
        and buy_fn.find("validate_buy_amount_limits(amount)") < buy_fn.find("wallet.usdt_reserved"),
    )
    assert_ok(
        "BUY_LIMIT_CHECK_BEFORE_FUND_ORDER",
        buy_fn.find("validate_buy_amount_limits(amount)") != -1
        and buy_fn.find("FundOrder(") != -1
        and buy_fn.find("validate_buy_amount_limits(amount)") < buy_fn.find("FundOrder("),
    )

    assert_ok(
        "REDEEM_LIMIT_CHECK_BEFORE_POSITION_LOCK",
        redeem_fn.find("validate_redeem_shares_limits(shares_dec)") != -1
        and redeem_fn.find("_lock_user_position") != -1
        and redeem_fn.find("validate_redeem_shares_limits(shares_dec)") < redeem_fn.find("_lock_user_position"),
    )
    assert_ok(
        "REDEEM_LIMIT_CHECK_BEFORE_SHARES_RESERVE",
        redeem_fn.find("validate_redeem_shares_limits(shares_dec)") != -1
        and redeem_fn.find("position.shares_reserved") != -1
        and redeem_fn.find("validate_redeem_shares_limits(shares_dec)") < redeem_fn.find("position.shares_reserved"),
    )
    assert_ok(
        "REDEEM_LIMIT_CHECK_BEFORE_FUND_ORDER",
        redeem_fn.find("validate_redeem_shares_limits(shares_dec)") != -1
        and redeem_fn.find("FundOrder(") != -1
        and redeem_fn.find("validate_redeem_shares_limits(shares_dec)") < redeem_fn.find("FundOrder("),
    )


def test_api_route_error_passthrough_source() -> None:
    routes = Path("app/trading/routes.py").read_text(encoding="utf-8")

    assert_ok("API_BUY_RETURNS_ERROR_KEY", '"error": error_key' in routes)
    assert_ok("API_BUY_RETURNS_LOCALIZED_MESSAGE", '"message": t(lang, error_key)' in routes)
    assert_ok("API_BUY_USES_TRADING_ORDER_ERROR", "except TradingOrderError as exc" in routes)
    assert_ok("API_REDEEM_USES_TRADING_ORDER_ERROR", routes.count("except TradingOrderError as exc") >= 2)


def test_terminal_limits_from_backend_source() -> None:
    routes = Path("app/trading/routes.py").read_text(encoding="utf-8")
    template = Path("templates/terminal.html").read_text(encoding="utf-8")
    js = Path("static/js/terminal.js").read_text(encoding="utf-8")

    assert_ok("ROUTES_PASSES_BUY_MIN", "trading_buy_min_usdt" in routes)
    assert_ok("ROUTES_PASSES_BUY_MAX", "trading_buy_max_usdt" in routes)
    assert_ok("ROUTES_PASSES_REDEEM_MAX", "trading_redeem_max_shares" in routes)

    assert_ok("TEMPLATE_HAS_BUY_MIN_DATA", "data-buy-min-usdt" in template)
    assert_ok("TEMPLATE_HAS_BUY_MAX_DATA", "data-buy-max-usdt" in template)
    assert_ok("TEMPLATE_HAS_REDEEM_MAX_DATA", "data-redeem-max-shares" in template)

    assert_ok("JS_READS_BUY_MIN_FROM_DATASET", "wrap.dataset.buyMinUsdt" in js)
    assert_ok("JS_READS_BUY_MAX_FROM_DATASET", "wrap.dataset.buyMaxUsdt" in js)
    assert_ok("JS_READS_REDEEM_MAX_FROM_DATASET", "wrap.dataset.redeemMaxShares" in js)
    assert_ok("JS_NO_HARDCODED_BUY_MAX_CONST", "const BUY_MAX = 10_000_000" not in js)
    assert_ok("JS_NO_HARDCODED_REDEEM_MAX_CONST", "const REDEEM_MAX = 1000" not in js)
    assert_ok("JS_BUY_BELOW_MIN_ERROR", "Minimum purchase amount is 10 USDT." in js)


def main() -> None:
    test_buy_amount_limits()
    test_redeem_share_limits()
    test_config_values()
    test_i18n_messages()
    test_order_service_validation_before_mutation_source()
    test_api_route_error_passthrough_source()
    test_terminal_limits_from_backend_source()
    print("STAGE25_5_ORDER_LIMITS_TESTS_OK")


if __name__ == "__main__":
    main()