from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from app.settlement.negative_bybit_flow import (
    _is_withdrawal_failed_like,
    _is_withdrawal_pending_like,
    _is_withdrawal_success_like,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_withdrawal_status_mapping() -> None:
    success_like = [
        "success",
        "SUCCESS",
        "BlockchainConfirmed",
        "blockchainconfirmed",
        "completed",
        "complete",
    ]
    pending_like = [
        "SecurityCheck",
        "Pending",
        "MoreInformationRequired",
        "processing",
        "PROCESSING",
        "reviewing",
        "REVIEWING",
        "",
        None,
    ]
    failed_like = [
        "Reject",
        "Fail",
        "CancelByUser",
        "failed",
        "FAILED",
    ]

    for status in success_like:
        assert_ok(
            f"SUCCESS_LIKE_{status}_OK",
            _is_withdrawal_success_like(status),
        )

    for status in pending_like:
        assert_ok(
            f"PENDING_LIKE_{status}_OK",
            _is_withdrawal_pending_like(status),
        )

    for status in failed_like:
        assert_ok(
            f"FAILED_LIKE_{status}_OK",
            _is_withdrawal_failed_like(status),
        )

    print("STAGE26_3_10_STEP3_WITHDRAW_STATUS_MAPPING_OK")


def test_success_like_missing_tx_hash_no_fail() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok(
        "OLD_TX_HASH_MISSING_FAIL_REMOVED",
        "Withdrawal succeeded but tx_hash is missing" not in source,
    )
    assert_ok(
        "SUCCESS_LIKE_MISSING_TX_HASH_PENDING_PRESENT",
        "withdrawal_success_like_missing_tx_hash" in source,
    )
    assert_ok(
        "SUCCESS_LIKE_MISSING_TX_HASH_NO_RESEND_PRESENT",
        '"no_duplicate_resend": True' in source,
    )
    assert_ok(
        "SUCCESS_LIKE_MISSING_TX_HASH_RECONCILING_STATUS",
        "BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING" in source,
    )

    print("STAGE26_3_10_STEP3_WITHDRAW_TX_HASH_MISSING_NO_RESEND_OK")


def test_pending_unknown_no_resend() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok(
        "PENDING_UNKNOWN_NO_TX_HASH_PRESENT",
        "withdrawal_pending_or_unknown_no_tx_hash" in source,
    )
    assert_ok(
        "WITHDRAWAL_FAILED_STATUS_BRANCH_PRESENT",
        "Withdrawal failed status:" in source,
    )
    assert_ok(
        "WITHDRAWAL_UNEXPECTED_STATUS_BRANCH_PRESENT",
        "Withdrawal unexpected status:" in source,
    )

    print("STAGE26_3_10_STEP3_WITHDRAW_PENDING_UNKNOWN_NO_RESEND_OK")


def test_no_bsc_payout_before_tx_hash() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    missing_tx_hash_block = source.split(
        "withdrawal_success_like_missing_tx_hash",
        1,
    )[1].split(
        "flow.withdrawal_id = withdrawal_record.withdrawal_id or flow.withdrawal_id",
        1,
    )[0]

    assert_ok(
        "MISSING_TX_HASH_NO_CASH_READY_STATUS",
        "BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT" not in missing_tx_hash_block,
    )
    assert_ok(
        "MISSING_TX_HASH_NO_BSC_CONFIRM_CHECK",
        "_check_tx_confirmed" not in missing_tx_hash_block,
    )

    print("STAGE26_3_10_STEP3_NO_BSC_PAYOUT_BEFORE_TX_HASH_OK")


def test_actual_amount_used_after_withdrawal() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok(
        "WITHDRAWAL_RECORD_AMOUNT_CHECKS_ACTUAL",
        "withdrawal_amount_actual" in source
        and "Withdrawal amount mismatch" in source,
    )
    assert_ok(
        "SETTLEMENT_RECEIPT_USES_ACTUAL",
        'flow.settlement_wallet_received_usdt = withdrawal_amount_actual' in source,
    )
    assert_ok(
        "RECEIPT_JSON_USES_ACTUAL",
        '"received_usdt": withdrawal_amount_actual' in source,
    )

    print("STAGE26_3_10_STEP3_WITHDRAW_ACTUAL_AMOUNT_RECONCILIATION_OK")


def test_no_forbidden_paths() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("NO_UUID4", "uuid4" not in source and "uuid.uuid4" not in source)
    assert_ok("NO_BSC_TX_SEND", "send_raw_transaction" not in source)
    frozen_member_endpoint = "/v5/user/" + "frozen-" + "sub-member"
    assert_ok("NO_FREEZE_ENDPOINT", frozen_member_endpoint not in source)

    print("STAGE26_3_10_STEP3_NO_FORBIDDEN_PATHS_OK")


def main() -> int:
    load_dotenv()

    test_withdrawal_status_mapping()
    test_success_like_missing_tx_hash_no_fail()
    test_pending_unknown_no_resend()
    test_no_bsc_payout_before_tx_hash()
    test_actual_amount_used_after_withdrawal()
    test_no_forbidden_paths()

    print("STAGE26_3_10_STEP3_WITHDRAW_STATUS_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())