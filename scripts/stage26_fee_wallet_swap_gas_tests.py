from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.settlement import statuses
from workers import fee_wallet_swap_worker as worker


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_config_values() -> None:
    assert_ok(
        "FEE_WALLET_SWAP_GAS_WAIT_RETRY_SEC_PRESENT",
        int(settings.FEE_WALLET_SWAP_GAS_WAIT_RETRY_SEC) >= 300,
    )
    assert_ok(
        "FEE_WALLET_SWAP_GAS_ALERT_COOLDOWN_SEC_PRESENT",
        int(settings.FEE_WALLET_SWAP_GAS_ALERT_COOLDOWN_SEC) >= 300,
    )


def test_status_constants() -> None:
    assert_ok(
        "FEE_SWAP_WAITING_FOR_GAS_STATUS",
        statuses.FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )
    assert_ok(
        "WORKER_IMPORTS_FEE_SWAP_WAITING_STATUS",
        worker.FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )


def test_source_waiting_for_gas_flow() -> None:
    src = Path("workers/fee_wallet_swap_worker.py").read_text(encoding="utf-8")

    assert_ok("SOURCE_HAS_GAS_RETRY_DELAY", "def fee_wallet_swap_gas_retry_delay" in src)
    assert_ok("SOURCE_HAS_ALERT_COOLDOWN", "def fee_wallet_swap_gas_alert_cooldown" in src)
    assert_ok("SOURCE_HAS_WAITING_LOOKUP", "def get_waiting_gas_swap" in src)
    assert_ok("SOURCE_HAS_SKIP_WAITING_COOLDOWN", "def should_skip_waiting_gas_swap" in src)
    assert_ok("SOURCE_HAS_MARK_WAITING", "def mark_fee_wallet_swap_waiting_for_gas" in src)
    assert_ok("SOURCE_HAS_CLEAR_WAITING", "def clear_fee_wallet_swap_waiting_for_gas" in src)

    assert_ok(
        "SOURCE_SETS_WAITING_FOR_GAS",
        "status=FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS" in src
        or "row.status = FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS" in src,
    )
    assert_ok(
        "SOURCE_SETS_NEXT_RETRY_AT",
        "next_retry_at=now + fee_wallet_swap_gas_retry_delay()" in src
        or "row.next_retry_at = now + fee_wallet_swap_gas_retry_delay()" in src,
    )
    assert_ok(
        "SOURCE_RATE_LIMITS_ALERT",
        "last_gas_alert_at" in src
        and "fee_wallet_swap_gas_alert_cooldown" in src,
    )
    assert_ok(
        "SOURCE_SKIPS_WITHOUT_DUPLICATE_ROW",
        "Skip without creating duplicate row" in src
        and "should_skip_waiting_gas_swap(wallet_type)" in src,
    )


def test_insufficient_bnb_not_failed() -> None:
    src = Path("workers/fee_wallet_swap_worker.py").read_text(encoding="utf-8")

    insufficient_idx = src.find("if bnb_before < MIN_BNB_FOR_GAS:")
    next_idx = src.find("usdt_balance_units", insufficient_idx)

    assert_ok(
        "INSUFFICIENT_BNB_BLOCK_FOUND",
        insufficient_idx != -1 and next_idx != -1 and insufficient_idx < next_idx,
    )

    block = src[insufficient_idx:next_idx]

    assert_ok(
        "INSUFFICIENT_BNB_USES_WAITING_NOT_FAILED",
        "mark_fee_wallet_swap_waiting_for_gas(" in block
        and "insufficient_fee_wallet_swap_gas" in block
        and "RuntimeError" not in block
        and "status=FEE_WALLET_SWAP_STATUS_FAILED" not in block
        and 'status="failed"' not in block,
    )


def test_success_clears_waiting_rows() -> None:
    src = Path("workers/fee_wallet_swap_worker.py").read_text(encoding="utf-8")

    assert_ok(
        "SUCCESS_RECORD_USES_CONSTANT",
        "status=FEE_WALLET_SWAP_STATUS_SUCCESS" in src,
    )
    assert_ok(
        "SUCCESS_CLEARS_WAITING",
        "clear_fee_wallet_swap_waiting_for_gas(wallet_type)" in src,
    )


def test_no_hardcoded_record_swap_statuses() -> None:
    src = Path("workers/fee_wallet_swap_worker.py").read_text(encoding="utf-8")

    assert_ok("NO_HARDCODED_SUCCESS_STATUS", 'status="success"' not in src)
    assert_ok("NO_HARDCODED_FAILED_STATUS", 'status="failed"' not in src)
    assert_ok("NO_HARDCODED_SKIPPED_STATUS", 'status="skipped"' not in src)


def main() -> None:
    test_config_values()
    test_status_constants()
    test_source_waiting_for_gas_flow()
    test_insufficient_bnb_not_failed()
    test_success_clears_waiting_rows()
    test_no_hardcoded_record_swap_statuses()
    print("STAGE26_FEE_WALLET_SWAP_GAS_TESTS_OK")


if __name__ == "__main__":
    main()