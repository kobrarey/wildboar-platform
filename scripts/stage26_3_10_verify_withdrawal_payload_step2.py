from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    create_master_withdrawal,
    format_bybit_asset_amount,
    query_coin_info,
)
from app.settlement.negative_bybit_flow import (
    deterministic_withdrawal_request_id,
    withdrawal_actual_amount,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeCoinInfoClient:
    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": {
                "rows": [
                    {
                        "coin": "USDT",
                        "chains": [
                            {
                                "chain": "BSC",
                                "withdrawFee": "1",
                                "withdrawMin": "2",
                                "minAccuracy": "6",
                                "chainWithdraw": "1",
                                "withdrawPercentageFee": "0",
                                "withdrawMax": "1000000",
                            }
                        ],
                    }
                ]
            }
        }


class FakeWithdrawPostClient:
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


def test_request_id_format() -> None:
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
    diff_amount = deterministic_withdrawal_request_id(
        settlement_batch_id=80,
        fund_id=9,
        settlement_wallet_address="0x1234567890123456789012345678901234567890",
        withdrawal_request_amount_usdt=Decimal("10.000001"),
        coin="USDT",
        chain="BSC",
    )

    assert_ok("WITHDRAW_REQUEST_ID_LEN_32", len(request_id) == 32)
    assert_ok("WITHDRAW_REQUEST_ID_ALNUM", request_id.isalnum())
    assert_ok("WITHDRAW_REQUEST_ID_PREFIX", request_id.startswith("wbng"))
    assert_ok("WITHDRAW_REQUEST_ID_DETERMINISTIC", request_id == same)
    assert_ok("WITHDRAW_REQUEST_ID_AMOUNT_CHANGES", request_id != diff_amount)
    assert_ok("WITHDRAW_REQUEST_ID_NO_COLON", ":" not in request_id)
    assert_ok("WITHDRAW_REQUEST_ID_NO_DASH", "-" not in request_id)
    assert_ok("WITHDRAW_REQUEST_ID_NO_DOT", "." not in request_id)

    print("STAGE26_3_10_STEP2_WITHDRAW_REQUEST_ID_FORMAT_OK")


def test_withdraw_amount_format() -> None:
    assert_ok(
        "WITHDRAW_FORMAT_DOWN_OK",
        format_bybit_asset_amount(
            Decimal("10.0000000000"),
            precision=6,
            rounding="down",
        )
        == "10",
    )
    assert_ok(
        "WITHDRAW_FORMAT_DOWN_ONLY_FOR_DISPLAY",
        format_bybit_asset_amount(
            Decimal("10.123456789"),
            precision=6,
            rounding="down",
        )
        == "10.123456",
    )

    amount_str, actual = withdrawal_actual_amount(
        withdrawal_request_amount_usdt=Decimal("10.0000000000"),
        precision=6,
    )

    assert_ok("WITHDRAW_ACTUAL_STR_OK", amount_str == "10")
    assert_ok("WITHDRAW_ACTUAL_EXACT_NO_UNDERPAY", actual == Decimal("10.0000000000"))

    try:
        withdrawal_actual_amount(
            withdrawal_request_amount_usdt=Decimal("10.123456789"),
            precision=6,
        )
    except Exception as exc:
        assert_ok("WITHDRAW_ROUNDING_UNDERPAY_FAILS_CLOSED", "rounding" in str(exc).lower())
    else:
        raise AssertionError("Withdrawal rounding underpay must fail closed")

    print("STAGE26_3_10_STEP2_WITHDRAW_AMOUNT_FORMAT_OK")


def test_query_coin_info() -> None:
    info = query_coin_info(
        FakeCoinInfoClient(),  # type: ignore[arg-type]
        coin="USDT",
        chain="BSC",
    )

    assert_ok("COIN_INFO_CHAIN_OK", info.chain == "BSC")
    assert_ok("COIN_INFO_MIN_ACCURACY_OK", info.min_accuracy == 6)
    assert_ok("COIN_INFO_CHAIN_WITHDRAW_OK", info.chain_withdraw == "1")
    assert_ok("COIN_INFO_WITHDRAW_MIN_OK", info.withdraw_min == Decimal("2"))

    print("STAGE26_3_10_STEP2_COIN_INFO_OK")


def test_withdraw_payload_shape() -> None:
    client = FakeWithdrawPostClient()
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

    assert_ok("WITHDRAW_POST_CALLED", client.post_called)
    assert_ok("WITHDRAW_ENDPOINT_OK", client.last_path == "/v5/asset/withdraw/create")
    assert_ok("WITHDRAW_PAYLOAD_REQUEST_ID_OK", payload.get("requestId") == request_id)
    assert_ok("WITHDRAW_PAYLOAD_COIN_OK", payload.get("coin") == "USDT")
    assert_ok("WITHDRAW_PAYLOAD_CHAIN_OK", payload.get("chain") == "BSC")
    assert_ok("WITHDRAW_PAYLOAD_AMOUNT_FORMATTED", payload.get("amount") == "10")
    assert_ok("WITHDRAW_PAYLOAD_TIMESTAMP", payload.get("timestamp") == 1710000000000)
    assert_ok("WITHDRAW_PAYLOAD_FORCE_CHAIN", payload.get("forceChain") == 1)
    assert_ok("WITHDRAW_PAYLOAD_ACCOUNT_TYPE_FUND", payload.get("accountType") == "FUND")
    assert_ok("WITHDRAW_RESULT_AMOUNT_POSTED", result.amount_usdt == Decimal("10"))

    print("STAGE26_3_10_STEP2_WITHDRAW_PAYLOAD_OK")


def test_invalid_request_id_rejected_before_post() -> None:
    client = FakeWithdrawPostClient()

    try:
        create_master_withdrawal(
            client,  # type: ignore[arg-type]
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
        raise AssertionError("Invalid requestId must fail")

    assert_ok("INVALID_REQUEST_ID_NO_POST", client.post_called is False)

    print("STAGE26_3_10_STEP2_WITHDRAW_REQUEST_ID_PREPOST_VALIDATION_OK")


def test_no_forbidden_paths() -> None:
    negative_source = read("app/settlement/negative_bybit_flow.py")
    asset_source = read("app/bybit/asset_flows.py")
    combined = negative_source + "\n" + asset_source

    assert_ok("NO_UUID4", "uuid4" not in combined and "uuid.uuid4" not in combined)
    assert_ok("NO_BSC_TX_PATH", "send_raw_transaction" not in combined)
    assert_ok("WITHDRAWAL_OLD_PREFIX_REMOVED", "neg-net-withdraw:" not in negative_source)
    assert_ok("WITHDRAWAL_PAYLOAD_FORCE_CHAIN_PRESENT", '"forceChain": 1' in asset_source)
    assert_ok("WITHDRAWAL_PAYLOAD_TIMESTAMP_PRESENT", '"timestamp": clean_timestamp_ms' in asset_source)
    assert_ok("WITHDRAWAL_NO_RAW_AMOUNT_STR", '"amount": str(amount)' not in asset_source)

    print("STAGE26_3_10_STEP2_NO_FORBIDDEN_PATHS_OK")


def main() -> int:
    load_dotenv()

    test_request_id_format()
    test_withdraw_amount_format()
    test_query_coin_info()
    test_withdraw_payload_shape()
    test_invalid_request_id_rejected_before_post()
    test_no_forbidden_paths()

    print("STAGE26_3_10_STEP2_WITHDRAW_PAYLOAD_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())