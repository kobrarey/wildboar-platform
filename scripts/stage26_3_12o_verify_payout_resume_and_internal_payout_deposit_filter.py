from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def block(source: str, start: str, end: str) -> str:
    a = source.index(start)
    b = source.index(end, a)
    return source[a:b]


def test_payout_worker_selects_processing() -> None:
    worker = read("workers/fund_negative_payout_worker.py")

    assert_ok(
        "WORKER_IMPORTS_PAYOUT_PROCESSING",
        "BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING" in worker,
    )
    assert_ok(
        "WORKER_JOINS_PAYOUT_BATCH",
        "FundNegativePayoutBatch" in worker and ".outerjoin(" in worker,
    )
    assert_ok(
        "WORKER_FILTERS_COMPLETED_BYBIT_FLOW",
        "FundNegativeBybitFlow.status == BYBIT_FLOW_STATUS_COMPLETED" in worker,
    )
    assert_ok(
        "WORKER_SELECTS_CASH_READY",
        "BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT" in worker,
    )
    assert_ok(
        "WORKER_SELECTS_PROCESSING_WITH_RESUMABLE_BATCH",
        "FundSettlementBatch.status" in worker
        and "BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING" in worker
        and "FundNegativePayoutBatch.id.isnot(None)" in worker
        and "LIVE_RESUMABLE_PAYOUT_BATCH_STATUSES" in worker,
    )

    flow = read("app/settlement/negative_payout_flow.py")
    assert_ok(
        "LIVE_RESUMABLE_STATUSES_DO_NOT_INCLUDE_COMPLETED",
        "PAYOUT_BATCH_STATUS_COMPLETED" not in block(
            flow,
            "LIVE_RESUMABLE_PAYOUT_BATCH_STATUSES = {",
            "}",
        ),
    )

    print("STAGE26_3_12O_PAYOUT_WORKER_SELECTS_PAYOUT_PROCESSING_OK")


def test_existing_tx_hash_resume_no_duplicate_send() -> None:
    source = read("app/settlement/negative_payout_flow.py")
    fn = block(
        source,
        "def _send_or_confirm_live_payout_leg(",
        "def _refresh_live_balances_after_confirmed_payouts(",
    )

    tx_hash_branch = block(
        fn,
        "if leg.tx_hash:",
        "request_id = deterministic_redeem_payout_request_id(",
    )

    assert_ok("EXISTING_TX_HASH_BRANCH_PRESENT", "if leg.tx_hash:" in fn)
    assert_ok("EXISTING_TX_HASH_CHECKS_CONFIRMATION", "_check_tx_confirmed(w3, leg.tx_hash)" in tx_hash_branch)
    assert_ok("EXISTING_TX_HASH_SETS_CONFIRMED_STATUS", "PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED" in tx_hash_branch)
    assert_ok("EXISTING_TX_HASH_SETS_CONFIRMATIONS", "leg.confirmations =" in tx_hash_branch)
    assert_ok("EXISTING_TX_HASH_SETS_CONFIRMED_AT", "leg.confirmed_at =" in tx_hash_branch)
    assert_ok("EXISTING_TX_HASH_SETS_CONFIRMATION_JSON", "leg.confirmation_json = _json_dict" in tx_hash_branch)
    assert_ok("NO_SEND_IN_EXISTING_TX_HASH_BRANCH", "_send_usdt_transfer(" not in tx_hash_branch)
    assert_ok("SEND_ONLY_AFTER_REQUEST_ID_FOR_NEW_LEG", fn.index("_send_usdt_transfer(") > fn.index("request_id = deterministic_redeem_payout_request_id("))

    print("STAGE26_3_12O_EXISTING_TX_HASH_RESUME_NO_DUPLICATE_SEND_OK")


def test_confirmed_payout_completes_batch() -> None:
    source = read("app/settlement/negative_payout_flow.py")
    fn = block(
        source,
        "def execute_negative_payout_flow_live(",
        "def execute_negative_payout_flow_mock(",
    )

    assert_ok("BATCH_COMPLETED_STATUS_SET", "batch.status = PAYOUT_BATCH_STATUS_COMPLETED" in fn)
    assert_ok(
        "SETTLEMENT_PAYOUTS_CONFIRMED_SET",
        "settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED" in fn,
    )
    assert_ok("CONFIRMED_TOTAL_SET", "batch.confirmed_total_payout_usdt = confirmed_total" in fn or "batch.confirmed_total_payout_usdt = expected_total_payout_usdt" in source)
    assert_ok("CONFIRMED_COUNT_SET", "batch.confirmed_payout_leg_count = confirmed_count" in fn)
    assert_ok("CONFIRMED_TOTAL_MATCH_CHECK", "Confirmed payout total mismatch" in fn)
    assert_ok("CONFIRMED_COUNT_MATCH_CHECK", "Confirmed payout leg count mismatch" in fn)

    print("STAGE26_3_12O_CONFIRMED_PAYOUT_COMPLETES_BATCH_OK")


def test_internal_settlement_payout_not_deposit() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")
    insert_fn = block(source, "def db_insert_transfer(", "async def handle_log(")

    assert_ok("DB_FAILSAFE_HELPER_PRESENT", "internal_platform_payout_skip_reason_db" in source)
    assert_ok("DB_FAILSAFE_BEFORE_INSERT", insert_fn.index("internal_platform_payout_skip_reason_db(") < insert_fn.index("insert(WalletTransfer)"))
    assert_ok("DB_FAILSAFE_QUERIES_FUND_WALLET", "db.query(FundWallet.id)" in source)
    assert_ok("DB_FAILSAFE_ACTIVE_BSC_SETTLEMENT", 'FundWallet.blockchain == "BSC"' in source and 'FundWallet.wallet_type == "settlement"' in source and "FundWallet.is_active.is_(True)" in source)
    assert_ok("DB_FAILSAFE_QUERIES_USER_WALLET", "db.query(UserWallet.id)" in source and 'UserWallet.blockchain == "BSC"' in source)
    assert_ok("DB_FAILSAFE_RETURNS_FALSE", "return False" in insert_fn)
    assert_ok("INTERNAL_PAYOUT_LOG_PRESENT", "internal_platform_payout_ignored" in source)

    print("STAGE26_3_12O_INTERNAL_SETTLEMENT_PAYOUT_NOT_DEPOSIT_OK")


def test_known_payout_leg_tx_not_deposit() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")

    assert_ok("PAYOUT_LEG_MODEL_IMPORTED", "FundNegativePayoutLeg" in source)
    assert_ok("PAYOUT_LEG_TX_HASH_CHECK", "FundNegativePayoutLeg.tx_hash" in source)
    assert_ok("PAYOUT_LEG_FROM_CHECK", "FundNegativePayoutLeg.from_address" in source)
    assert_ok("PAYOUT_LEG_TO_CHECK", "FundNegativePayoutLeg.to_address" in source)
    assert_ok("PAYOUT_LEG_AMOUNT_CHECK", "FundNegativePayoutLeg.amount_usdt == amount" in source)
    assert_ok("PAYOUT_LEG_SKIP_REASON", "known_negative_payout_leg_tx_hash" in source)

    print("STAGE26_3_12O_KNOWN_PAYOUT_LEG_TX_NOT_DEPOSIT_OK")


def test_external_deposit_still_inserts_and_cursor_ok() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")
    insert_fn = block(source, "def db_insert_transfer(", "async def handle_log(")
    handle_fn = block(source, "async def handle_log(", "async def subscribe_chunk(")

    assert_ok("WALLET_TRANSFER_INSERT_STILL_PRESENT", "insert(WalletTransfer)" in insert_fn)
    assert_ok("WALLET_TRANSFER_TYPE_DEPOSIT_STILL_PRESENT", 'type="deposit"' in insert_fn)
    assert_ok("WALLET_TRANSFER_PENDING_STILL_PRESENT", 'status="pending"' in insert_fn)
    assert_ok("INSERT_COMMITS_AND_RETURNS_TRUE", "db.commit()" in insert_fn and "return True" in insert_fn)
    assert_ok("HANDLE_STILL_CALLS_DB_INSERT_TRANSFER", "db_insert_transfer" in handle_fn)
    assert_ok("CURSOR_UPDATE_AFTER_INSERT_STILL_PRESENT", "db_upsert_cursor" in handle_fn)
    assert_ok("CACHED_INTERNAL_SKIP_STILL_UPDATES_CURSOR", "is_internal_platform_payout_transfer(" in handle_fn and "db_upsert_cursor" in handle_fn)

    print("STAGE26_3_12O_EXTERNAL_USER_DEPOSIT_STILL_INSERTS_OK")


def main() -> int:
    test_payout_worker_selects_processing()
    test_existing_tx_hash_resume_no_duplicate_send()
    test_confirmed_payout_completes_batch()
    test_internal_settlement_payout_not_deposit()
    test_known_payout_leg_tx_not_deposit()
    test_external_deposit_still_inserts_and_cursor_ok()

    print("STAGE26_3_12O_PAYOUT_RESUME_AND_DEPOSIT_FILTER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())