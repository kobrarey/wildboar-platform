from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def index_of(source: str, needle: str) -> int:
    pos = source.find(needle)
    if pos < 0:
        raise AssertionError(f"MISSING_SNIPPET: {needle}")
    return pos


def test_transfer_state_durable_before_withdrawal() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    preflight_pos = index_of(source, "flow.status = BYBIT_FLOW_STATUS_PREFLIGHT_PASSED")
    transfer_post_pos = index_of(source, "created_transfer = create_universal_transfer(")
    transfer_reconciled_pos = index_of(
        source,
        "flow.status = BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED",
    )
    withdrawal_create_pos = index_of(source, "created_withdrawal = create_master_withdrawal(")

    preflight_commit_pos = source.find("db.commit()", preflight_pos, transfer_post_pos)
    transfer_reconciled_commit_pos = source.find(
        "db.commit()",
        transfer_reconciled_pos,
        withdrawal_create_pos,
    )

    assert_ok("PREFLIGHT_COMMIT_BEFORE_UNIVERSAL_TRANSFER_POST", preflight_commit_pos > preflight_pos)
    assert_ok(
        "TRANSFER_RECONCILED_COMMIT_BEFORE_WITHDRAWAL_CREATE",
        transfer_reconciled_commit_pos > transfer_reconciled_pos,
    )
    assert_ok(
        "TRANSFER_ROUTE_AND_IDS_PERSISTED",
        "flow.universal_transfer_id = transfer_id" in source
        and "flow.withdrawal_request_id = request_id" in source
        and "flow.from_account_type = route" in source
        and "flow.to_account_type = route" in source,
    )

    print("STAGE26_3_12F_TRANSFER_STATE_DURABLE_BEFORE_WITHDRAWAL_OK")


def test_withdraw_rate_limit_retryable() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("RATE_LIMIT_HELPER_PRESENT", "_is_withdrawal_rate_limit_error" in source)
    assert_ok("RATE_LIMIT_RETCODE_CHECK", "retCode=131001" in source)
    assert_ok("RATE_LIMIT_MESSAGE_CHECK", "wait at least 10 seconds" in source)
    assert_ok("RATE_LIMIT_RETRY_SECONDS_30", "WITHDRAWAL_RATE_LIMIT_RETRY_SECONDS = 30" in source)
    assert_ok("RATE_LIMIT_RETRY_PERSIST_HELPER", "_persist_withdrawal_rate_limit_retry" in source)
    assert_ok("RATE_LIMIT_DOES_NOT_SET_FAILED", "withdrawal_rate_limit_retry" in source)

    print("STAGE26_3_12F_WITHDRAW_RATE_LIMIT_RETRYABLE_OK")


def test_withdraw_rate_limit_does_not_delete_flow() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("NO_DB_DELETE_FLOW", "delete(flow" not in source and "delete(flow)" not in source)
    assert_ok(
        "RATE_LIMIT_STATUS_REMAINS_TRANSFER_RECONCILED",
        "flow.status = BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED" in source,
    )
    assert_ok(
        "RATE_LIMIT_BATCH_NOT_FAILED",
        "settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING" in source,
    )

    print("STAGE26_3_12F_WITHDRAW_RATE_LIMIT_DOES_NOT_DELETE_FLOW_OK")


def test_retry_skips_duplicate_universal_transfer() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    query_pos = index_of(source, "transfer_record = query_universal_transfer(")
    post_pos = index_of(source, "created_transfer = create_universal_transfer(")

    assert_ok("QUERY_TRANSFER_BEFORE_POST", query_pos < post_pos)
    assert_ok(
        "PREFLIGHT_STATUS_RESUMES_TRANSFER_CONTEXT",
        "BYBIT_FLOW_STATUS_PREFLIGHT_PASSED" in source
        and "UNIVERSAL_TRANSFER_RESUME_STATUSES" in source,
    )
    assert_ok(
        "NO_RESEND_AFTER_PRIOR_TRANSFER_ATTEMPT",
        "Universal Transfer status unknown after prior attempt; no resend" in source,
    )

    print("STAGE26_3_12F_RETRY_SKIPS_DUPLICATE_UNIVERSAL_TRANSFER_OK")


def test_withdraw_request_id_reused_after_rate_limit() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    existing_request_pos = index_of(source, "if flow.withdrawal_request_id:")
    deterministic_request_pos = index_of(source, "request_id = deterministic_withdrawal_request_id(")

    assert_ok("EXISTING_REQUEST_ID_CHECK_BEFORE_DETERMINISTIC", existing_request_pos < deterministic_request_pos)
    assert_ok("RATE_LIMIT_RETURNS_REQUEST_ID", "withdrawal_request_id=request_id" in source)
    assert_ok("RATE_LIMIT_DIAGNOSTIC_REUSES_REQUEST_ID", "request_id_reused_on_retry" in source)

    print("STAGE26_3_12F_WITHDRAW_REQUEST_ID_REUSED_AFTER_RATE_LIMIT_OK")


def test_no_blind_retry_after_external_transfer() -> None:
    source = read("app/settlement/negative_bybit_flow.py")
    worker = read("workers/fund_negative_bybit_flow_worker.py")

    assert_ok("TRANSFER_CREATE_STATE_COMMIT_PRESENT", "created_transfer = create_universal_transfer(" in source)
    assert_ok("TRANSFER_CREATE_COMMIT_PRESENT", "db.commit()" in source)
    assert_ok("WITHDRAW_CREATE_STATE_COMMIT_PRESENT", "created_withdrawal = create_master_withdrawal(" in source)
    assert_ok("BYBIT_API_ERROR_CAUGHT", "BybitApiError" in source)
    assert_ok(
        "WORKER_RESUMES_MASTER_FLOW_PROCESSING",
        "BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING" in worker,
    )
    assert_ok(
        "WORKER_RESUMES_WITHDRAWAL_PENDING",
        "BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING" in worker,
    )

    print("STAGE26_3_12F_NO_BLIND_RETRY_AFTER_EXTERNAL_TRANSFER_OK")


def main() -> int:
    test_transfer_state_durable_before_withdrawal()
    test_withdraw_rate_limit_retryable()
    test_withdraw_rate_limit_does_not_delete_flow()
    test_retry_skips_duplicate_universal_transfer()
    test_withdraw_request_id_reused_after_rate_limit()
    test_no_blind_retry_after_external_transfer()

    print("STAGE26_3_12F_EXTERNAL_STEP_DURABILITY_AND_WITHDRAW_RETRY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())