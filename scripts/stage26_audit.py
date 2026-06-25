from __future__ import annotations

from pathlib import Path

ROOT = Path(".")

TARGET_FILES = [
    "app/settlement/gas_service.py",
    "app/settlement/negative_payout_flow.py",
    "app/settlement/operator_gas_retry.py",
    "workers/settlement_operator_action_worker.py",
    "workers/bsc_withdrawal_processor.py",
    "workers/fee_wallet_swap_worker.py",
    "workers/fund_positive_net_worker.py",
    "workers/fund_positive_net_allocation_worker.py",
    "app/settlement/positive_net_service.py",
    "app/settlement/transfer_service.py",
    "app/settlement/statuses.py",
    "app/models.py",
    "db/schema.sql",
]

PATTERNS = [
    "send_native_bnb",
    "sign_and_send_raw",
    "send_raw_transaction",
    "wait_for_transaction_receipt",
    "get_balance",
    "FEE_WALLET",
    "fee wallet",
    "gas wallet",
    "BNB",
    "bnb",
    "gas_tx_send_error",
    "payout_tx_send_error",
    "fee_tx_send_error",
    "TRANSFER_STATUS_FAILED",
    "failed",
    "processing",
    "paused_operator_action_required",
    "insufficient_ok_gas",
    "waiting_for_gas",
    "next_retry",
    "retry_at",
    "attempts",
    "send_telegram",
    "Telegram",
]

def print_match(path: Path, line_no: int, line: str) -> None:
    print(f"{path.as_posix()}:{line_no}: {line.rstrip()}")

def main() -> None:
    print("STAGE26_AUDIT_TARGET_FILES")
    for item in TARGET_FILES:
        path = ROOT / item
        print(f"{item}: {'FOUND' if path.exists() else 'MISSING'}")

    print("\nSTAGE26_AUDIT_MATCHES")
    for item in TARGET_FILES:
        path = ROOT / item
        if not path.exists():
            continue

        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(text, start=1):
            lower = line.lower()
            if any(pattern.lower() in lower for pattern in PATTERNS):
                print_match(path, idx, line)

    print("\nSTAGE26_AUDIT_DB_DEPENDENCY_HINTS")
    schema = (ROOT / "db/schema.sql").read_text(encoding="utf-8", errors="replace") if (ROOT / "db/schema.sql").exists() else ""
    models = (ROOT / "app/models.py").read_text(encoding="utf-8", errors="replace") if (ROOT / "app/models.py").exists() else ""

    checks = {
        "HAS_WAITING_FOR_GAS_STATUS_TEXT": "waiting_for_gas" in schema or "waiting_for_gas" in models,
        "HAS_NEXT_RETRY_FIELD": "next_retry" in schema or "next_retry" in models or "retry_at" in schema or "retry_at" in models,
        "HAS_PLATFORM_EMERGENCY_LOCKS": "platform_emergency_locks" in schema or "PlatformEmergencyLock" in models,
        "HAS_BYBIT_WITHDRAWAL_WATCHDOG_EVENTS": "bybit_withdrawal_watchdog_events" in schema or "BybitWithdrawalWatchdogEvent" in models,
        "HAS_APPROVED_BYBIT_WITHDRAWAL_WINDOWS": "approved_bybit_withdrawal_windows" in schema or "ApprovedBybitWithdrawalWindow" in models,
    }

    for name, ok in checks.items():
        print(f"{name}: {'YES' if ok else 'NO'}")

    print("\nSTAGE26_AUDIT_DONE")

if __name__ == "__main__":
    main()