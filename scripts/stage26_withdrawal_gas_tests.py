from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.settlement import statuses
from workers import bsc_withdrawal_processor as worker


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_config_values() -> None:
    assert_ok("WITHDRAW_GAS_WAIT_RETRY_SEC_PRESENT", int(settings.WITHDRAW_GAS_WAIT_RETRY_SEC) >= 30)
    assert_ok("WITHDRAW_GAS_ALERT_COOLDOWN_SEC_PRESENT", int(settings.WITHDRAW_GAS_ALERT_COOLDOWN_SEC) >= 60)


def test_status_constants() -> None:
    assert_ok(
        "WALLET_WAITING_FOR_GAS_STATUS",
        statuses.WALLET_TRANSFER_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )
    assert_ok(
        "WORKER_IMPORTS_WAITING_STATUS",
        worker.WALLET_TRANSFER_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )


def test_helper_functions() -> None:
    assert_ok(
        "OK_COMPLIANCE_MAPS_TO_INSUFFICIENT_OK_GAS",
        worker.waiting_for_gas_reason("ok") == "insufficient_ok_gas",
    )
    assert_ok(
        "BLOCKED_COMPLIANCE_MAPS_TO_INSUFFICIENT_BLOCKED_GAS",
        worker.waiting_for_gas_reason("blocked") == "insufficient_blocked_gas",
    )
    assert_ok(
        "DEFAULT_COMPLIANCE_MAPS_TO_INSUFFICIENT_OK_GAS",
        worker.waiting_for_gas_reason(None) == "insufficient_ok_gas",
    )


def test_source_no_5sec_gas_retry_spam() -> None:
    src = Path("workers/bsc_withdrawal_processor.py").read_text(encoding="utf-8")

    assert_ok("SOURCE_HAS_MARK_WAITING_FOR_GAS", "def mark_waiting_for_gas" in src)
    assert_ok("SOURCE_SETS_WAITING_FOR_GAS", "tr.status = WALLET_TRANSFER_STATUS_WAITING_FOR_GAS" in src)
    assert_ok("SOURCE_SETS_NEXT_RETRY_AT", "tr.next_retry_at = now + withdraw_gas_retry_delay()" in src)
    assert_ok("SOURCE_RATE_LIMITS_GAS_ALERT", "last_gas_alert_at" in src and "withdraw_gas_alert_cooldown" in src)

    assert_ok(
        "SOURCE_LOADS_WAITING_ONLY_AFTER_RETRY_AT",
        "WalletTransfer.status == WALLET_TRANSFER_STATUS_WAITING_FOR_GAS" in src
        and "WalletTransfer.next_retry_at <= now" in src,
    )

    assert_ok(
        "SOURCE_GAS_TOPUP_PRECHECKS_FEE_WALLET_BALANCE",
        "fee_wallet_balance_wei" in src
        and "required_fee_wallet_wei" in src
        and "fee_wallet_balance_wei < required_fee_wallet_wei" in src,
    )

    assert_ok(
        "SOURCE_GAS_SEND_ERROR_CAN_WAIT_FOR_GAS",
        "gas_tx_send_error" in src
        and "insufficient_fee_wallet_bnb" in src
        and "mark_waiting_for_gas(" in src,
    )

    assert_ok(
        "SOURCE_USER_WALLET_GAS_PRECHECK_PAYOUT",
        "phase=payout" in src
        and "insufficient_user_wallet_gas" in src,
    )

    assert_ok(
        "SOURCE_USER_WALLET_GAS_PRECHECK_FEE",
        "phase=fee" in src
        and "insufficient_user_wallet_gas" in src,
    )


def test_no_final_failed_for_temporary_gas_source() -> None:
    src = Path("workers/bsc_withdrawal_processor.py").read_text(encoding="utf-8")

    insufficient_idx = src.find("insufficient_fee_wallet_bnb")
    waiting_idx = src.find("tr.status = WALLET_TRANSFER_STATUS_WAITING_FOR_GAS")
    assert_ok(
        "TEMP_FEE_WALLET_GAS_USES_WAITING_STATUS",
        insufficient_idx != -1 and waiting_idx != -1,
    )

    gas_send_anchor = "txh = sign_and_send_raw(w3, fee_priv, tx)"
    gas_send_idx = src.find(gas_send_anchor)
    assert_ok("GAS_SEND_BLOCK_FOUND", gas_send_idx != -1)

    gas_send_tail = src[gas_send_idx:]
    gas_send_except_idx = gas_send_tail.find("except Exception as e:")
    step2_idx = gas_send_tail.find("# ---------- Step 2: payout")

    assert_ok(
        "GAS_SEND_EXCEPTION_BLOCK_BOUNDED",
        gas_send_except_idx != -1 and step2_idx != -1 and gas_send_except_idx < step2_idx,
    )

    gas_send_exception_block = gas_send_tail[gas_send_except_idx:step2_idx]

    assert_ok(
        "GAS_SEND_EXCEPTION_DOES_NOT_FINAL_FAIL_TEMP_GAS",
        "mark_waiting_for_gas(" in gas_send_exception_block
        and "insufficient_fee_wallet_bnb" in gas_send_exception_block
        and "db_mark_failed" not in gas_send_exception_block,
    )


def main() -> None:
    test_config_values()
    test_status_constants()
    test_helper_functions()
    test_source_no_5sec_gas_retry_spam()
    test_no_final_failed_for_temporary_gas_source()
    print("STAGE26_WITHDRAWAL_GAS_TESTS_OK")


if __name__ == "__main__":
    main()