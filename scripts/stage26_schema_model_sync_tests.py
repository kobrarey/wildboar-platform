from __future__ import annotations

from pathlib import Path

from app.models import (
    ApprovedBybitWithdrawalWindow,
    BybitWithdrawalWatchdogEvent,
    FeeWalletSwap,
    FundSettlementTransfer,
    PlatformEmergencyLock,
    WalletTransfer,
)
from app.settlement import statuses


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def has_attr(model: type, name: str) -> bool:
    return hasattr(model, name)


def test_retry_columns_in_models() -> None:
    assert_ok("WALLET_TRANSFER_HAS_NEXT_RETRY_AT", has_attr(WalletTransfer, "next_retry_at"))
    assert_ok("WALLET_TRANSFER_HAS_LAST_GAS_ALERT_AT", has_attr(WalletTransfer, "last_gas_alert_at"))

    assert_ok("FUND_SETTLEMENT_TRANSFER_HAS_NEXT_RETRY_AT", has_attr(FundSettlementTransfer, "next_retry_at"))
    assert_ok("FUND_SETTLEMENT_TRANSFER_HAS_LAST_GAS_ALERT_AT", has_attr(FundSettlementTransfer, "last_gas_alert_at"))

    assert_ok("FEE_WALLET_SWAP_HAS_NEXT_RETRY_AT", has_attr(FeeWalletSwap, "next_retry_at"))
    assert_ok("FEE_WALLET_SWAP_HAS_LAST_GAS_ALERT_AT", has_attr(FeeWalletSwap, "last_gas_alert_at"))


def test_new_watchdog_models_exist() -> None:
    assert_ok("MODEL_PLATFORM_EMERGENCY_LOCK_EXISTS", PlatformEmergencyLock.__tablename__ == "platform_emergency_locks")
    assert_ok(
        "MODEL_APPROVED_BYBIT_WITHDRAWAL_WINDOW_EXISTS",
        ApprovedBybitWithdrawalWindow.__tablename__ == "approved_bybit_withdrawal_windows",
    )
    assert_ok(
        "MODEL_BYBIT_WITHDRAWAL_WATCHDOG_EVENT_EXISTS",
        BybitWithdrawalWatchdogEvent.__tablename__ == "bybit_withdrawal_watchdog_events",
    )


def test_new_model_columns_exist() -> None:
    assert_ok("PLATFORM_LOCK_HAS_STATUS", has_attr(PlatformEmergencyLock, "status"))
    assert_ok("PLATFORM_LOCK_HAS_REASON", has_attr(PlatformEmergencyLock, "reason"))
    assert_ok("PLATFORM_LOCK_HAS_SOURCE_EVENT_ID", has_attr(PlatformEmergencyLock, "source_event_id"))

    assert_ok("APPROVED_WINDOW_HAS_COIN", has_attr(ApprovedBybitWithdrawalWindow, "coin"))
    assert_ok("APPROVED_WINDOW_HAS_AMOUNT_MIN", has_attr(ApprovedBybitWithdrawalWindow, "amount_min"))
    assert_ok("APPROVED_WINDOW_HAS_AMOUNT_MAX", has_attr(ApprovedBybitWithdrawalWindow, "amount_max"))
    assert_ok("APPROVED_WINDOW_HAS_EXPIRES_AT", has_attr(ApprovedBybitWithdrawalWindow, "expires_at"))

    assert_ok("WATCHDOG_EVENT_HAS_BYBIT_WITHDRAWAL_ID", has_attr(BybitWithdrawalWatchdogEvent, "bybit_withdrawal_id"))
    assert_ok("WATCHDOG_EVENT_HAS_DECISION", has_attr(BybitWithdrawalWatchdogEvent, "decision"))
    assert_ok("WATCHDOG_EVENT_HAS_CANCEL_ATTEMPTED", has_attr(BybitWithdrawalWatchdogEvent, "cancel_attempted"))
    assert_ok("WATCHDOG_EVENT_HAS_RAW_JSON", has_attr(BybitWithdrawalWatchdogEvent, "raw_json"))


def test_status_constants() -> None:
    assert_ok("TRANSFER_WAITING_FOR_GAS_STATUS_CONSTANT", statuses.TRANSFER_STATUS_WAITING_FOR_GAS == "waiting_for_gas")
    assert_ok(
        "WALLET_TRANSFER_WAITING_FOR_GAS_STATUS_CONSTANT",
        statuses.WALLET_TRANSFER_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )
    assert_ok(
        "FEE_WALLET_SWAP_WAITING_FOR_GAS_STATUS_CONSTANT",
        statuses.FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS == "waiting_for_gas",
    )
    assert_ok(
        "PLATFORM_EMERGENCY_LOCK_ACTIVE_CONSTANT",
        statuses.PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE == "active",
    )
    assert_ok(
        "WATCHDOG_UNEXPECTED_DECISION_CONSTANT",
        statuses.BYBIT_WITHDRAWAL_WATCHDOG_DECISION_UNEXPECTED == "unexpected",
    )


def test_schema_contains_stage26_objects() -> None:
    schema = Path("db/schema.sql").read_text(encoding="utf-8")

    for token in [
        "waiting_for_gas",
        "next_retry_at",
        "last_gas_alert_at",
        "platform_emergency_locks",
        "approved_bybit_withdrawal_windows",
        "bybit_withdrawal_watchdog_events",
        "idx_wallet_transfers_withdraw_gas_retry",
        "idx_fund_settlement_transfers_gas_waiting",
        "idx_fee_wallet_swaps_waiting_for_gas",
    ]:
        assert_ok(f"SCHEMA_HAS_{token.upper()}", token in schema)


def main() -> None:
    test_retry_columns_in_models()
    test_new_watchdog_models_exist()
    test_new_model_columns_exist()
    test_status_constants()
    test_schema_contains_stage26_objects()
    print("STAGE26_SCHEMA_MODEL_SYNC_TESTS_OK")


if __name__ == "__main__":
    main()