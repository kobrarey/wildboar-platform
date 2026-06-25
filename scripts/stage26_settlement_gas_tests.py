from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.settlement import gas_service
from app.settlement import statuses


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_config_values() -> None:
    assert_ok(
        "SETTLEMENT_GAS_WAIT_RETRY_SEC_PRESENT",
        int(settings.SETTLEMENT_GAS_WAIT_RETRY_SEC) >= 30,
    )
    assert_ok(
        "SETTLEMENT_GAS_ALERT_COOLDOWN_SEC_PRESENT",
        int(settings.SETTLEMENT_GAS_ALERT_COOLDOWN_SEC) >= 60,
    )


def test_status_constants() -> None:
    assert_ok(
        "TRANSFER_WAITING_FOR_GAS_STATUS",
        statuses.TRANSFER_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )
    assert_ok(
        "GAS_SERVICE_IMPORTS_WAITING_STATUS",
        gas_service.TRANSFER_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )


def test_source_waiting_for_gas_flow() -> None:
    src = Path("app/settlement/gas_service.py").read_text(encoding="utf-8")

    assert_ok("SOURCE_HAS_SETTLEMENT_RETRY_DELAY", "def settlement_gas_retry_delay" in src)
    assert_ok("SOURCE_HAS_SETTLEMENT_ALERT_COOLDOWN", "def settlement_gas_alert_cooldown" in src)
    assert_ok("SOURCE_HAS_MARK_WAITING_FOR_GAS", "def mark_topup_waiting_for_gas" in src)
    assert_ok("SOURCE_HAS_MARK_TOPUP_SENT", "def mark_topup_sent" in src)

    assert_ok(
        "SOURCE_SETS_WAITING_FOR_GAS_STATUS",
        "row.status = TRANSFER_STATUS_WAITING_FOR_GAS" in src,
    )
    assert_ok(
        "SOURCE_SETS_NEXT_RETRY_AT",
        "row.next_retry_at = now + settlement_gas_retry_delay()" in src,
    )
    assert_ok(
        "SOURCE_RATE_LIMITS_ALERTS",
        "last_gas_alert_at" in src and "settlement_gas_alert_cooldown" in src,
    )
    assert_ok(
        "SOURCE_FIND_EXISTING_INCLUDES_WAITING",
        "TRANSFER_STATUS_WAITING_FOR_GAS" in src
        and "_find_existing_topup_transfer" in src,
    )
    assert_ok(
        "SOURCE_WAITING_SKIPS_UNTIL_RETRY",
        "existing.next_retry_at is not None" in src
        and "skipped until retry time" in src,
    )
    assert_ok(
        "SOURCE_REUSES_DUE_WAITING_ROW",
        "Reuse the existing row instead of creating" in src
        and 'existing.status = "processing"' in src,
    )
    assert_ok(
        "SOURCE_INSUFFICIENT_OK_GAS_NOT_FAILED",
        "insufficient_ok_gas:" in src
        and "mark_topup_waiting_for_gas(" in src,
    )


def test_source_no_failed_for_insufficient_ok_gas() -> None:
    src = Path("app/settlement/gas_service.py").read_text(encoding="utf-8")

    insufficient_idx = src.find("if ok_balance < desired_amount:")
    send_idx = src.find("if dry_run:", insufficient_idx)
    assert_ok(
        "INSUFFICIENT_OK_GAS_BLOCK_FOUND",
        insufficient_idx != -1 and send_idx != -1 and insufficient_idx < send_idx,
    )

    insufficient_block = src[insufficient_idx:send_idx]

    assert_ok(
        "INSUFFICIENT_OK_GAS_USES_WAITING_NOT_FAILED",
        "mark_topup_waiting_for_gas(" in insufficient_block
        and "TRANSFER_STATUS_WAITING_FOR_GAS" in insufficient_block
        and "TRANSFER_STATUS_FAILED" not in insufficient_block,
    )


def test_source_clears_retry_after_sent() -> None:
    src = Path("app/settlement/gas_service.py").read_text(encoding="utf-8")

    assert_ok(
        "MARK_TOPUP_SENT_CLEARS_NEXT_RETRY",
        "def mark_topup_sent" in src
        and "row.next_retry_at = None" in src
        and "row.status = TRANSFER_STATUS_SENT" in src,
    )
    assert_ok(
        "SENT_PATH_REUSES_EXISTING_WAITING_ROW",
        "mark_topup_sent(" in src
        and "existing is not None" in src
        and 'existing.status == "processing"' in src,
    )


def main() -> None:
    test_config_values()
    test_status_constants()
    test_source_waiting_for_gas_flow()
    test_source_no_failed_for_insufficient_ok_gas()
    test_source_clears_retry_after_sent()
    print("STAGE26_SETTLEMENT_GAS_TESTS_OK")


if __name__ == "__main__":
    main()