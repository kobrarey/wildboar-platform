from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
import uuid

from dotenv import load_dotenv

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    create_master_withdrawal,
    create_universal_transfer,
    format_bybit_asset_amount,
)
from app.settlement.negative_bybit_flow import (
    _is_withdrawal_failed_like,
    _is_withdrawal_pending_like,
    _is_withdrawal_success_like,
    choose_universal_transfer_account_route,
    deterministic_universal_transfer_id,
    deterministic_withdrawal_request_id,
    universal_transfer_actual_amount,
    withdrawal_actual_amount,
)
from workers.bsc_usdt_deposit_listener import is_internal_platform_payout_transfer


ROOT = Path(__file__).resolve().parents[1]


class FakeBalanceClient:
    def __init__(self, balances: dict[str, Decimal]) -> None:
        self.balances = balances
        self.get_calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.get_calls.append((path, params))
        account_type = str(params["accountType"]).upper()
        amount = self.balances.get(account_type, Decimal("0"))
        return {
            "result": {
                "accountType": account_type,
                "coin": params["coin"],
                "memberId": params.get("memberId"),
                "walletBalance": str(amount),
                "transferBalance": str(amount),
                "transferSafeAmount": str(amount),
                "ltvTransferSafeAmount": str(amount),
            }
        }


class FakeUniversalTransferClient:
    def __init__(self) -> None:
        self.post_called = False
        self.last_path: str | None = None
        self.last_payload: dict[str, Any] | None = None

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.post_called = True
        self.last_path = path
        self.last_payload = payload
        return {
            "result": {
                "transferId": payload["transferId"],
                "status": "SUCCESS",
            }
        }


class FakeWithdrawClient:
    def __init__(self) -> None:
        self.post_called = False
        self.last_path: str | None = None
        self.last_payload: dict[str, Any] | None = None

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.post_called = True
        self.last_path = path
        self.last_payload = payload
        return {
            "result": {
                "withdrawalId": "test-withdrawal-id",
                "requestId": payload["requestId"],
                "coin": payload["coin"],
                "chain": payload["chain"],
                "address": payload["address"],
                "amount": payload["amount"],
                "feeType": payload["feeType"],
                "status": "Pending",
            }
        }


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def table_block(schema: str, table_name: str) -> str:
    marker = f"CREATE TABLE public.{table_name} ("
    start = schema.find(marker)
    if start < 0:
        raise AssertionError(f"TABLE_NOT_FOUND_{table_name}")

    end = schema.find("\n);", start)
    if end < 0:
        raise AssertionError(f"TABLE_END_NOT_FOUND_{table_name}")

    return schema[start:end]


def assert_table_statuses(schema: str, table_name: str, statuses: list[str]) -> None:
    block = table_block(schema, table_name)
    missing = [status for status in statuses if f"'{status}'" not in block]
    if missing:
        raise AssertionError(f"{table_name} missing statuses: {missing}")

    print(f"{table_name}_STATUS_COVERAGE_OK")


def test_universal_transfer_amount_format() -> None:
    assert_ok(
        "UT_FORMAT_UP_2",
        format_bybit_asset_amount(
            Decimal("11.0222489345"),
            precision=2,
            rounding="up",
        )
        == "11.03",
    )
    assert_ok(
        "UT_FORMAT_STRIP_ZERO",
        format_bybit_asset_amount(
            Decimal("10.0000000000"),
            precision=2,
            rounding="down",
        )
        == "10",
    )
    assert_ok(
        "UT_FORMAT_SMALL",
        format_bybit_asset_amount(
            Decimal("0.1000000000"),
            precision=2,
            rounding="up",
        )
        == "0.1",
    )

    client = FakeUniversalTransferClient()
    transfer_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.03"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )

    result = create_universal_transfer(
        client,  # type: ignore[arg-type]
        transfer_id=transfer_id,
        coin="USDT",
        amount_usdt=Decimal("11.0222489345"),
        amount_str="11.03",
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )

    assert_ok("UT_POST_AMOUNT_FORMATTED", client.last_payload is not None and client.last_payload.get("amount") == "11.03")
    assert_ok("UT_RESULT_AMOUNT_POSTED", result.amount_usdt == Decimal("11.03"))

    print("STAGE26_3_10_UNIVERSAL_TRANSFER_AMOUNT_FORMAT_OK")


def test_universal_transfer_rounds_up_and_covers_required() -> None:
    amount_str, actual = universal_transfer_actual_amount(
        required_master_usdt=Decimal("11.0222489345")
    )

    assert_ok("UT_BATCH80_AMOUNT_STR", amount_str == "11.03")
    assert_ok("UT_ACTUAL_COVERS_REQUIRED", actual >= Decimal("11.0222489345"))
    assert_ok("UT_SURPLUS_WITHIN_QUANTUM", actual - Decimal("11.0222489345") <= Decimal("0.01"))

    print("STAGE26_3_10_UNIVERSAL_TRANSFER_AMOUNT_ROUNDS_UP_AND_COVERS_REQUIRED_OK")


def test_universal_transfer_account_type_balance_selection() -> None:
    fund_client = FakeBalanceClient({"FUND": Decimal("20"), "UNIFIED": Decimal("100")})
    fund_route = choose_universal_transfer_account_route(
        fund_client,  # type: ignore[arg-type]
        coin="USDT",
        amount_usdt=Decimal("11.03"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
    )

    assert_ok("UT_PREFERS_FUND_FROM", fund_route["from_account_type"] == "FUND")
    assert_ok("UT_PREFERS_FUND_TO", fund_route["to_account_type"] == "FUND")

    fallback_client = FakeBalanceClient({"FUND": Decimal("1"), "UNIFIED": Decimal("100")})
    fallback_route = choose_universal_transfer_account_route(
        fallback_client,  # type: ignore[arg-type]
        coin="USDT",
        amount_usdt=Decimal("11.03"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
    )

    assert_ok("UT_FALLBACK_UNIFIED_FROM", fallback_route["from_account_type"] == "UNIFIED")
    assert_ok("UT_FALLBACK_TO_FUND", fallback_route["to_account_type"] == "FUND")

    fail_client = FakeBalanceClient({"FUND": Decimal("1"), "UNIFIED": Decimal("2")})
    try:
        choose_universal_transfer_account_route(
            fail_client,  # type: ignore[arg-type]
            coin="USDT",
            amount_usdt=Decimal("11.03"),
            from_member_id="fund-sub-uid",
            to_member_id="master-uid",
        )
    except Exception as exc:
        assert_ok("UT_ROUTE_FAILS_CLOSED", "transferBalance" in str(exc))
    else:
        raise AssertionError("Universal Transfer route must fail closed")

    print("STAGE26_3_10_UNIVERSAL_TRANSFER_ACCOUNT_TYPE_BALANCE_SELECTION_OK")


def test_universal_transfer_uuid_still_deterministic() -> None:
    base = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.03"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )
    same = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.0300000000"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )
    diff_route = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.03"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="UNIFIED",
        to_account_type="UNIFIED",
    )

    assert_ok("UT_UUID_VALID", str(uuid.UUID(base)) == base)
    assert_ok("UT_UUID_SAME_INPUTS", base == same)
    assert_ok("UT_UUID_ROUTE_CHANGE_DIFF", base != diff_route)

    print("STAGE26_3_10_UNIVERSAL_TRANSFER_UUID_STILL_DETERMINISTIC_OK")


def test_withdraw_request_id_format() -> None:
    request_id = deterministic_withdrawal_request_id(
        settlement_batch_id=80,
        fund_id=9,
        settlement_wallet_address="0x1234567890123456789012345678901234567890",
        withdrawal_request_amount_usdt=Decimal("10.000000"),
        coin="USDT",
        chain="BSC",
    )
    same = deterministic_withdrawal_request_id(
        settlement_batch_id=80,
        fund_id=9,
        settlement_wallet_address="0x1234567890123456789012345678901234567890",
        withdrawal_request_amount_usdt=Decimal("10.0000000000"),
        coin="usdt",
        chain="bsc",
    )
    diff = deterministic_withdrawal_request_id(
        settlement_batch_id=80,
        fund_id=9,
        settlement_wallet_address="0x1234567890123456789012345678901234567890",
        withdrawal_request_amount_usdt=Decimal("10.000001"),
        coin="USDT",
        chain="BSC",
    )

    assert_ok("WITHDRAW_REQUEST_ID_32", len(request_id) == 32)
    assert_ok("WITHDRAW_REQUEST_ID_ALNUM", request_id.isalnum())
    assert_ok("WITHDRAW_REQUEST_ID_PREFIX", request_id.startswith("wbng"))
    assert_ok("WITHDRAW_REQUEST_ID_DETERMINISTIC", request_id == same)
    assert_ok("WITHDRAW_REQUEST_ID_DIFF_AMOUNT", request_id != diff)
    assert_ok("WITHDRAW_REQUEST_ID_NO_COLON_DASH_DOT", ":" not in request_id and "-" not in request_id and "." not in request_id)

    print("STAGE26_3_10_WITHDRAW_REQUEST_ID_FORMAT_OK")


def test_withdraw_amount_format() -> None:
    amount_str, actual = withdrawal_actual_amount(
        withdrawal_request_amount_usdt=Decimal("10.0000000000"),
        precision=6,
    )

    assert_ok("WITHDRAW_AMOUNT_STR_EXACT", amount_str == "10")
    assert_ok("WITHDRAW_ACTUAL_EXACT_NO_UNDERPAY", actual == Decimal("10.0000000000"))
    assert_ok(
        "WITHDRAW_FORMAT_STRIP_ZERO",
        format_bybit_asset_amount(Decimal("10.0000000000"), precision=6, rounding="down") == "10",
    )

    try:
        withdrawal_actual_amount(
            withdrawal_request_amount_usdt=Decimal("10.123456789"),
            precision=6,
        )
    except Exception as exc:
        assert_ok("WITHDRAW_ROUNDING_UNDERPAY_FAILS_CLOSED", "rounding" in str(exc).lower())
    else:
        raise AssertionError("Withdrawal rounding underpay must fail closed")

    print("STAGE26_3_10_WITHDRAW_AMOUNT_FORMAT_OK")


def test_withdraw_payload_bybit_docs() -> None:
    client = FakeWithdrawClient()
    request_id = deterministic_withdrawal_request_id(
        settlement_batch_id=80,
        fund_id=9,
        settlement_wallet_address="0x1234567890123456789012345678901234567890",
        withdrawal_request_amount_usdt=Decimal("10"),
        coin="USDT",
        chain="BSC",
    )

    result = create_master_withdrawal(
        client,  # type: ignore[arg-type]
        request_id=request_id,
        coin="USDT",
        chain="BSC",
        address="0x1234567890123456789012345678901234567890",
        amount_usdt=Decimal("10.0000000000"),
        amount_str="10",
        amount_precision=6,
        fee_type=1,
        account_type="FUND",
        timestamp_ms=1710000000000,
        force_chain=1,
    )

    payload = client.last_payload or {}

    required_keys = {
        "requestId",
        "coin",
        "chain",
        "address",
        "amount",
        "timestamp",
        "forceChain",
        "feeType",
        "accountType",
    }

    assert_ok("WITHDRAW_ENDPOINT", client.last_path == "/v5/asset/withdraw/create")
    assert_ok("WITHDRAW_PAYLOAD_KEYS", required_keys.issubset(set(payload.keys())))
    assert_ok("WITHDRAW_PAYLOAD_REQUEST_ID", payload.get("requestId") == request_id)
    assert_ok("WITHDRAW_PAYLOAD_AMOUNT", payload.get("amount") == "10")
    assert_ok("WITHDRAW_PAYLOAD_TIMESTAMP", payload.get("timestamp") == 1710000000000)
    assert_ok("WITHDRAW_PAYLOAD_FORCE_CHAIN", payload.get("forceChain") == 1)
    assert_ok("WITHDRAW_PAYLOAD_ACCOUNT_TYPE", payload.get("accountType") == "FUND")
    assert_ok("WITHDRAW_RESULT_AMOUNT", result.amount_usdt == Decimal("10"))

    try:
        create_master_withdrawal(
            FakeWithdrawClient(),  # type: ignore[arg-type]
            request_id="neg-net-withdraw:bad",
            coin="USDT",
            chain="BSC",
            address="0x1234567890123456789012345678901234567890",
            amount_usdt=Decimal("10"),
            amount_str="10",
            amount_precision=6,
            fee_type=1,
            account_type="FUND",
            timestamp_ms=1710000000000,
            force_chain=1,
        )
    except BybitAssetFlowError:
        pass
    else:
        raise AssertionError("Invalid withdrawal requestId must fail before POST")

    print("STAGE26_3_10_WITHDRAW_PAYLOAD_BYBIT_DOCS_OK")


def test_withdraw_status_mapping() -> None:
    for status in ["success", "SUCCESS", "BlockchainConfirmed", "blockchainconfirmed", "completed", "complete"]:
        assert_ok(f"WITHDRAW_SUCCESS_{status}", _is_withdrawal_success_like(status))

    for status in ["SecurityCheck", "Pending", "MoreInformationRequired", "processing", "PROCESSING", "reviewing", "REVIEWING", "", None]:
        assert_ok(f"WITHDRAW_PENDING_{status}", _is_withdrawal_pending_like(status))

    for status in ["Reject", "Fail", "CancelByUser", "failed", "FAILED"]:
        assert_ok(f"WITHDRAW_FAILED_{status}", _is_withdrawal_failed_like(status))

    print("STAGE26_3_10_WITHDRAW_STATUS_MAPPING_OK")


def test_withdraw_tx_hash_missing_no_resend() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("OLD_TX_HASH_FAIL_REMOVED", "Withdrawal succeeded but tx_hash is missing" not in source)
    assert_ok("TX_HASH_MISSING_PENDING", "withdrawal_success_like_missing_tx_hash" in source)
    assert_ok("TX_HASH_MISSING_NO_RESEND", '"no_duplicate_resend": True' in source)
    assert_ok("TX_HASH_MISSING_NO_PAYOUT", '"no_bsc_payout": True' in source)
    assert_ok("TX_HASH_MISSING_NO_FINALIZATION", '"no_accounting_finalization": True' in source)

    print("STAGE26_3_10_WITHDRAW_TX_HASH_MISSING_NO_RESEND_OK")


def test_bsc_payout_internal_transfer_not_deposit() -> None:
    wallet_map = {
        "0xuser000000000000000000000000000000000000": (1, 1),
    }
    settlement_addresses = {
        "0xsettlement000000000000000000000000000000",
    }

    assert_ok(
        "INTERNAL_PAYOUT_IGNORED",
        is_internal_platform_payout_transfer(
            from_address="0xSettlement000000000000000000000000000000",
            to_address="0xUser000000000000000000000000000000000000",
            wallet_map=wallet_map,
            platform_settlement_wallet_addresses=settlement_addresses,
        ),
    )
    assert_ok(
        "EXTERNAL_DEPOSIT_ALLOWED",
        not is_internal_platform_payout_transfer(
            from_address="0xExternal000000000000000000000000000000",
            to_address="0xUser000000000000000000000000000000000000",
            wallet_map=wallet_map,
            platform_settlement_wallet_addresses=settlement_addresses,
        ),
    )

    print("STAGE26_3_10_BSC_PAYOUT_INTERNAL_TRANSFER_NOT_DEPOSIT_OK")


def test_no_double_credit_risk() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")

    assert_ok("INTERNAL_SKIP_HELPER_PRESENT", "is_internal_platform_payout_transfer" in source)
    assert_ok(
        "INTERNAL_SKIP_LOG_PRESENT",
        "internal_platform_payout_ignored" in source
        and "cached_platform_settlement_wallet_to_registered_user_wallet" in source,
    )

    skip_block = source.split(
        "if is_internal_platform_payout_transfer(",
        1,
    )[1].split(
        "tx_time = block_time_cache.get(block_number)",
        1,
    )[0]

    assert_ok("SKIP_BLOCK_RETURNS", "return" in skip_block)
    assert_ok("SKIP_BLOCK_NO_DB_INSERT", "db_insert_transfer" not in skip_block)
    assert_ok("SKIP_BLOCK_CURSOR_REALTIME", "db_upsert_cursor" in skip_block)

    print("STAGE26_3_10_NO_DOUBLE_CREDIT_RISK_OK")


def test_downstream_schema_status_coverage() -> None:
    schema = read("db/schema.sql")

    assert_table_statuses(
        schema,
        "fund_settlement_batches",
        [
            "negative_net_master_flow_processing",
            "negative_net_withdrawal_pending",
            "negative_net_withdrawal_reconciling",
            "negative_net_cash_ready_for_payout",
            "negative_net_payout_processing",
            "negative_net_payouts_confirmed",
            "negative_net_accounting_finalized",
            "negative_cash_settlement_completed",
        ],
    )

    assert_table_statuses(
        schema,
        "fund_negative_bybit_flows",
        [
            "created",
            "universal_transfer_reconciled",
            "withdrawal_reconciled",
            "settlement_wallet_receipt_confirmed",
            "completed",
            "failed_requires_review",
        ],
    )

    assert_table_statuses(
        schema,
        "fund_negative_payout_batches",
        [
            "created",
            "gas_check_passed",
            "gas_ready",
            "payouts_planned",
            "payouts_confirmed",
            "completed",
            "failed_requires_review",
            "paused_operator_action_required",
        ],
    )

    assert_table_statuses(
        schema,
        "fund_negative_payout_legs",
        [
            "planned",
            "payout_confirmed",
            "balance_refreshed",
            "failed_requires_review",
        ],
    )

    assert_table_statuses(
        schema,
        "fund_negative_finalization_batches",
        [
            "created",
            "validating",
            "accounting_processing",
            "accounting_finalized",
            "pricing_unlocked",
            "completed",
            "failed_requires_review",
        ],
    )

    print("STAGE26_3_10_DOWNSTREAM_SCHEMA_STATUS_COVERAGE_OK")


def test_no_random_uuid4_in_idempotency_paths() -> None:
    negative_source = read("app/settlement/negative_bybit_flow.py")
    asset_source = read("app/bybit/asset_flows.py")

    combined = negative_source + "\n" + asset_source

    assert_ok("NO_UUID4", "uuid4" not in combined and "uuid.uuid4" not in combined)
    assert_ok("USES_UUID5", "uuid.uuid5" in negative_source)
    assert_ok("WITHDRAW_ID_32_ALNUM_SOURCE", '"wbng" + uuid.uuid5' in negative_source)

    print("STAGE26_3_10_NO_RANDOM_UUID4_IN_IDEMPOTENCY_PATHS_OK")


def test_no_secret_logging() -> None:
    production_sources = "\n".join(
        [
            read("app/bybit/asset_flows.py"),
            read("app/settlement/negative_bybit_flow.py"),
            read("workers/bsc_usdt_deposit_listener.py"),
        ]
    )

    forbidden_secret_tokens = [
        "api_key",
        "api_secret",
        "X-BAPI-API-KEY",
        "X-BAPI-SIGN",
        "signature",
    ]
    leaked = [
        token
        for token in forbidden_secret_tokens
        if token.lower() in production_sources.lower()
    ]

    assert_ok("NO_SECRET_TOKENS_IN_PRODUCTION_CHANGES", not leaked)
    assert_ok("NO_BSC_SEND_RAW_TRANSACTION", "send_raw_transaction" not in production_sources)

    # asset_flows.py previously had a removed helper, so check only sell-flow paths.
    negative_source = read("app/settlement/negative_bybit_flow.py")
    listener_source = read("workers/bsc_usdt_deposit_listener.py")
    frozen_member_endpoint = "/v5/user/" + "frozen-" + "sub-member"
    assert_ok("NEGATIVE_FLOW_NO_FREEZE_ENDPOINT", frozen_member_endpoint not in negative_source)
    assert_ok("DEPOSIT_LISTENER_NO_FREEZE_ENDPOINT", frozen_member_endpoint not in listener_source)

    print("STAGE26_3_10_NO_SECRET_LOGGING_OK")


def main() -> int:
    load_dotenv()

    test_universal_transfer_amount_format()
    test_universal_transfer_rounds_up_and_covers_required()
    test_universal_transfer_account_type_balance_selection()
    test_universal_transfer_uuid_still_deterministic()
    test_withdraw_request_id_format()
    test_withdraw_amount_format()
    test_withdraw_payload_bybit_docs()
    test_withdraw_status_mapping()
    test_withdraw_tx_hash_missing_no_resend()
    test_bsc_payout_internal_transfer_not_deposit()
    test_no_double_credit_risk()
    test_downstream_schema_status_coverage()
    test_no_random_uuid4_in_idempotency_paths()
    test_no_secret_logging()

    print("STAGE26_3_10_FULL_DOWNSTREAM_SELL_PATH_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())