from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.config import settings
from app.settlement.gas_service import (
    TOPUP_MODE_MINIMUM_ALREADY_PRESENT,
    TOPUP_MODE_MINIMUM_OPERATIONAL_FALLBACK,
    TOPUP_MODE_TARGET_RESERVE,
    TOPUP_MODE_WAITING_FOR_GAS,
    choose_settlement_gas_topup_amount,
    configured_settlement_gas_topup_fund_codes,
)


ROOT = Path(__file__).resolve().parents[1]


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def decision(
    *,
    wallet_balance: str,
    target: str = "0.100",
    minimum: str = "0.020",
    ok_balance: str,
    fallback: bool = True,
):
    return choose_settlement_gas_topup_amount(
        wallet_balance_bnb=Decimal(wallet_balance),
        target_bnb=Decimal(target),
        min_operational_bnb=Decimal(minimum),
        ok_balance_bnb=Decimal(ok_balance),
        allow_min_operational_fallback=fallback,
    )


def test_target_topup_path() -> None:
    result = decision(wallet_balance="0.010", ok_balance="0.200")

    assert_ok("TARGET_TOPUP_ACTION_SEND", result.action == "send")
    assert_ok("TARGET_TOPUP_MODE", result.topup_mode == TOPUP_MODE_TARGET_RESERVE)
    assert_ok("TARGET_TOPUP_AMOUNT", result.amount_to_send_bnb == Decimal("0.090"))
    assert_ok("TARGET_TOPUP_TARGET_DEFICIT", result.target_deficit_bnb == Decimal("0.090"))


def test_minimum_operational_fallback_path() -> None:
    result = decision(wallet_balance="0.005", ok_balance="0.020")

    assert_ok("MINIMUM_FALLBACK_ACTION_SEND", result.action == "send")
    assert_ok(
        "MINIMUM_FALLBACK_MODE",
        result.topup_mode == TOPUP_MODE_MINIMUM_OPERATIONAL_FALLBACK,
    )
    assert_ok("MINIMUM_FALLBACK_AMOUNT", result.amount_to_send_bnb == Decimal("0.015"))
    assert_ok("MINIMUM_FALLBACK_OPERATIONAL_DEFICIT", result.operational_deficit_bnb == Decimal("0.015"))


def test_minimum_already_present_path() -> None:
    result = decision(wallet_balance="0.030", ok_balance="0.010")

    assert_ok("MINIMUM_ALREADY_PRESENT_ACTION_SKIP", result.action == "skip")
    assert_ok(
        "MINIMUM_ALREADY_PRESENT_MODE",
        result.topup_mode == TOPUP_MODE_MINIMUM_ALREADY_PRESENT,
    )
    assert_ok("MINIMUM_ALREADY_PRESENT_AMOUNT_ZERO", result.amount_to_send_bnb == Decimal("0"))


def test_fail_closed_path() -> None:
    result = decision(wallet_balance="0.005", ok_balance="0.010")

    assert_ok("FAIL_CLOSED_ACTION_WAITING", result.action == "waiting_for_gas")
    assert_ok("FAIL_CLOSED_MODE", result.topup_mode == TOPUP_MODE_WAITING_FOR_GAS)
    assert_ok("FAIL_CLOSED_AMOUNT_OPERATIONAL_DEFICIT", result.amount_to_send_bnb == Decimal("0.015"))
    assert_ok("FAIL_CLOSED_REASON_HAS_TARGET_DEFICIT", "target_deficit_bnb=" in result.message)
    assert_ok("FAIL_CLOSED_REASON_HAS_OPERATIONAL_DEFICIT", "operational_deficit_bnb=" in result.message)


def test_fallback_disabled_path() -> None:
    result = decision(wallet_balance="0.005", ok_balance="0.020", fallback=False)

    assert_ok("FALLBACK_DISABLED_ACTION_WAITING", result.action == "waiting_for_gas")
    assert_ok("FALLBACK_DISABLED_MODE", result.topup_mode == TOPUP_MODE_WAITING_FOR_GAS)
    assert_ok("FALLBACK_DISABLED_USES_TARGET_DEFICIT", result.amount_to_send_bnb == Decimal("0.095"))
    assert_ok("FALLBACK_DISABLED_REASON", "fallback is disabled" in result.message)


def test_config_fund_codes_parser() -> None:
    original = settings.SETTLEMENT_GAS_TOPUP_FUND_CODES
    try:
        settings.SETTLEMENT_GAS_TOPUP_FUND_CODES = "wb_test, WB10, , defi_sniper"
        result = configured_settlement_gas_topup_fund_codes()
        assert_ok("CONFIG_FUND_CODES_NORMALIZED", result == {"wb_test", "wb10", "defi_sniper"})

        settings.SETTLEMENT_GAS_TOPUP_FUND_CODES = ""
        result = configured_settlement_gas_topup_fund_codes()
        assert_ok("CONFIG_FUND_CODES_EMPTY_MEANS_ALL", result == set())

    finally:
        settings.SETTLEMENT_GAS_TOPUP_FUND_CODES = original


def test_source_contracts() -> None:
    config = read("app/config.py")
    env_example = read(".env.example")
    gas_service = read("app/settlement/gas_service.py")
    worker = read("workers/fund_settlement_gas_topup_worker.py")

    assert_ok(
        "CONFIG_HAS_MIN_OPERATIONAL_FALLBACK_DEFAULT_TRUE",
        "SETTLEMENT_GAS_ALLOW_MIN_OPERATIONAL_FALLBACK: bool = True" in config,
    )
    assert_ok(
        "CONFIG_HAS_TOPUP_FUND_CODES_DEFAULT_EMPTY",
        'SETTLEMENT_GAS_TOPUP_FUND_CODES: str = ""' in config,
    )
    assert_ok(
        "ENV_EXAMPLE_HAS_MIN_OPERATIONAL_FALLBACK_TRUE",
        "SETTLEMENT_GAS_ALLOW_MIN_OPERATIONAL_FALLBACK=true" in env_example,
    )
    assert_ok(
        "ENV_EXAMPLE_HAS_TOPUP_FUND_CODES",
        "SETTLEMENT_GAS_TOPUP_FUND_CODES=" in env_example,
    )
    assert_ok(
        "SERVICE_ACCEPTS_FUND_CODES",
        "fund_codes: set[str] | None = None" in gas_service,
    )
    assert_ok(
        "SERVICE_FILTERS_BY_LOWER_FUND_CODE",
        "func.lower(Fund.code).in_(sorted(normalized_codes))" in gas_service,
    )
    assert_ok(
        "SERVICE_FAILS_ON_MISSING_FUND_CODE",
        "Active settlement wallet not found for fund_codes=" in gas_service,
    )
    assert_ok(
        "SERVICE_HAS_TARGET_DEFICIT",
        "target_deficit_bnb" in gas_service,
    )
    assert_ok(
        "SERVICE_HAS_OPERATIONAL_DEFICIT",
        "operational_deficit_bnb" in gas_service,
    )
    assert_ok(
        "SERVICE_REUSES_WAITING_FOR_GAS_ROW",
        "Reuse the existing row instead of creating" in gas_service,
    )
    assert_ok(
        "SERVICE_CLEARS_NEXT_RETRY_ON_SENT",
        "row.next_retry_at = None" in gas_service,
    )
    assert_ok(
        "SERVICE_NO_FINAL_FAILED_ON_INSUFFICIENT_OK_GAS",
        "status=TRANSFER_STATUS_FAILED" not in gas_service,
    )
    assert_ok(
        "WORKER_HAS_FUND_CODE_ARG",
        '"--fund-code"' in worker,
    )
    assert_ok(
        "WORKER_HAS_FUND_CODES_ARG",
        '"--fund-codes"' in worker,
    )
    assert_ok(
        "WORKER_PASSES_FUND_CODES_TO_SERVICE",
        "fund_codes=fund_codes" in worker,
    )
    assert_ok(
        "WORKER_LOGS_TOPUP_MODE",
        "topup_mode=%s" in worker,
    )
    verify_script = read("scripts/stage26_2_6_verify_settlement_gas_policy.py")
    verify_script_body = verify_script.split(
        'verify_script = read("scripts/stage26_2_6_verify_settlement_gas_policy.py")',
        1,
    )[0]
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_IMPORT_WEB3",
        "import Web3" not in verify_script_body and "from web3 import Web3" not in verify_script_body,
    )
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_CALL_BSC_SEND",
        ".send_raw_transaction(" not in verify_script_body,
    )
    assert_ok(
        "VERIFY_SCRIPT_DOES_NOT_IMPORT_BYBIT_CLIENT",
        "BybitV5Client" not in verify_script_body,
    )


def main() -> None:
    test_target_topup_path()
    test_minimum_operational_fallback_path()
    test_minimum_already_present_path()
    test_fail_closed_path()
    test_fallback_disabled_path()
    test_config_fund_codes_parser()
    test_source_contracts()
    print("STAGE26_2_6_SETTLEMENT_GAS_POLICY_VERIFY_OK")


if __name__ == "__main__":
    main()
