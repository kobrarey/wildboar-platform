from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
import uuid

from dotenv import load_dotenv

from app.bybit.asset_flows import (
    create_universal_transfer,
    format_bybit_asset_amount,
)
from app.config import settings
from app.settlement.negative_bybit_flow import (
    deterministic_universal_transfer_id,
    universal_transfer_actual_amount,
    universal_transfer_amount_precision,
)
from app.settlement.negative_net_targets import (
    resolve_negative_net_bybit_withdrawal_fee,
)


ROOT = Path(__file__).resolve().parents[1]


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


class FakeCoinInfoClient:
    def __init__(
        self,
        *,
        withdraw_fee: str = "0.8",
        withdraw_percentage_fee: str = "0",
        chain_withdraw: str = "1",
        min_accuracy: str = "0.000001",
    ) -> None:
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.withdraw_fee = withdraw_fee
        self.withdraw_percentage_fee = withdraw_percentage_fee
        self.chain_withdraw = chain_withdraw
        self.min_accuracy = min_accuracy

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.get_calls.append((path, params))
        return {
            "result": {
                "rows": [
                    {
                        "coin": "USDT",
                        "chains": [
                            {
                                "chain": "BSC",
                                "withdrawFee": self.withdraw_fee,
                                "withdrawMin": "1",
                                "minAccuracy": self.min_accuracy,
                                "chainWithdraw": self.chain_withdraw,
                                "withdrawPercentageFee": self.withdraw_percentage_fee,
                                "withdrawMax": "1000000",
                            }
                        ],
                    }
                ]
            }
        }


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def decimal_scale(text: str) -> int:
    value = Decimal(str(text))
    exponent = value.as_tuple().exponent
    return abs(exponent) if exponent < 0 else 0


def test_universal_transfer_2dp_round_up() -> None:
    assert_ok(
        "CONFIG_PRECISION_IS_2",
        int(settings.NEGATIVE_NET_UNIVERSAL_TRANSFER_AMOUNT_PRECISION) == 2,
    )
    assert_ok("HELPER_PRECISION_IS_2", universal_transfer_amount_precision() == 2)

    amount_str, actual = universal_transfer_actual_amount(
        required_master_usdt=Decimal("11.0222489345")
    )

    assert_ok("AMOUNT_STR_1103", amount_str == "11.03")
    assert_ok("AMOUNT_SCALE_LE_2", decimal_scale(amount_str) <= 2)
    assert_ok("ACTUAL_COVERS_REQUIRED", actual >= Decimal("11.0222489345"))
    assert_ok("SURPLUS_LE_001", actual - Decimal("11.0222489345") <= Decimal("0.01"))
    assert_ok("NO_SCIENTIFIC_NOTATION", "E" not in amount_str.upper())
    assert_ok("NO_TRAILING_ZERO", not amount_str.endswith("0"))

    print("STAGE26_3_12_UNIVERSAL_TRANSFER_2DP_ROUND_UP_OK")


def test_batch80_transfer_amount_1103() -> None:
    amount_str, actual = universal_transfer_actual_amount(
        required_master_usdt=Decimal("11.0222489345"),
        precision=2,
    )

    assert_ok("BATCH80_AMOUNT_STR_1103", amount_str == "11.03")
    assert_ok("BATCH80_ACTUAL_1103", actual == Decimal("11.03"))

    print("STAGE26_3_12_BATCH80_TRANSFER_AMOUNT_1103_OK")


def test_universal_transfer_id_uses_actual_amount() -> None:
    raw_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.0222489345"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )
    actual_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.03"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )
    old_6dp_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.022249"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )

    assert_ok("ACTUAL_ID_IS_UUID", str(uuid.UUID(actual_id)) == actual_id)
    assert_ok("RAW_AMOUNT_ID_DIFFERS", raw_id != actual_id)
    assert_ok("OLD_6DP_ID_DIFFERS", old_6dp_id != actual_id)

    print("STAGE26_3_12_UNIVERSAL_TRANSFER_ID_USES_ACTUAL_AMOUNT_OK")


def test_universal_transfer_payload_amount_safe() -> None:
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
        amount_precision=2,
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )

    payload = client.last_payload or {}

    assert_ok("POST_CALLED", client.post_called)
    assert_ok("POST_PATH_OK", client.last_path == "/v5/asset/transfer/universal-transfer")
    assert_ok("PAYLOAD_AMOUNT_1103", payload.get("amount") == "11.03")
    assert_ok("PAYLOAD_AMOUNT_SCALE_LE_2", decimal_scale(str(payload.get("amount"))) <= 2)
    assert_ok("RESULT_AMOUNT_1103", result.amount_usdt == Decimal("11.03"))

    print("STAGE26_3_12_UNIVERSAL_TRANSFER_PAYLOAD_AMOUNT_SAFE_OK")


def test_no_raw_decimal_transfer_amount_post() -> None:
    source = read("app/bybit/asset_flows.py")
    negative_source = read("app/settlement/negative_bybit_flow.py")

    create_transfer_body = source.split(
        "def create_universal_transfer(",
        1,
    )[1].split(
        "def query_universal_transfer(",
        1,
    )[0]

    assert_ok("POST_USES_CLEAN_AMOUNT_STR", '"amount": clean_amount_str' in create_transfer_body)
    assert_ok("NO_RAW_AMOUNT_STR_IN_PAYLOAD", '"amount": str(amount)' not in create_transfer_body)
    assert_ok(
        "ID_SEED_USES_ACTUAL_TRANSFER_AMOUNT",
        "universal_transfer_amount_usdt=universal_transfer_amount_actual" in negative_source,
    )
    assert_ok(
        "NO_RAW_REQUIRED_MASTER_AS_TRANSFER_ID_AMOUNT",
        "universal_transfer_amount_usdt=amounts" not in negative_source,
    )
    assert_ok("SAFE_LOG_PRESENT", "Bybit Universal Transfer POST safe payload summary" in source)

    print("STAGE26_3_12_NO_RAW_DECIMAL_TRANSFER_AMOUNT_POST_OK")


def test_dynamic_withdraw_fee_from_coin_info() -> None:
    client = FakeCoinInfoClient(withdraw_fee="0.8")
    fee = resolve_negative_net_bybit_withdrawal_fee(
        bybit_withdrawal_fee_usdt=None,
        bybit_client=client,  # type: ignore[arg-type]
        use_live_bybit_withdrawal_fee=True,
        coin="USDT",
        chain="BSC",
    )

    assert_ok("COIN_INFO_ENDPOINT_CALLED", client.get_calls[0][0] == "/v5/asset/coin/query-info")
    assert_ok("FEE_AMOUNT_FROM_WITHDRAW_FEE", fee.amount_usdt == Decimal("0.8"))
    assert_ok("FEE_SOURCE_BYBIT_COIN_INFO", fee.source == "bybit_coin_info")
    assert_ok(
        "FEE_DIAGNOSTICS_SOURCE",
        fee.diagnostics["bybit_withdrawal_fee_source"] == "bybit_coin_info",
    )
    assert_ok("FEE_DIAGNOSTICS_WITHDRAW_FEE", Decimal(fee.diagnostics["withdrawFee"]) == Decimal("0.8"))
    assert_ok("FEE_DIAGNOSTICS_PERCENTAGE_ZERO", Decimal(fee.diagnostics["withdrawPercentageFee"]) == Decimal("0"))
    assert_ok("FEE_DIAGNOSTICS_CHAIN_WITHDRAW", fee.diagnostics["chainWithdraw"] == "1")

    print("STAGE26_3_12_DYNAMIC_WITHDRAW_FEE_FROM_COIN_INFO_OK")


def test_fee_type_zero_policy() -> None:
    config_source = read("app/config.py")
    negative_source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("FEE_TYPE_SETTING_ZERO", int(settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE) == 0)
    assert_ok("CONFIG_DEFAULT_FEE_TYPE_ZERO", "NEGATIVE_NET_WITHDRAWAL_FEE_TYPE: int = 0" in config_source)
    assert_ok("WITHDRAW_FLOW_USES_SETTING_FEE_TYPE", "settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE" in negative_source)

    print("STAGE26_3_12_FEE_TYPE_ZERO_POLICY_OK")


def test_existing_batch80_not_recalculated() -> None:
    targets_source = read("app/settlement/negative_net_targets.py")
    verifier_source = read("scripts/stage26_3_12_verify_universal_transfer_precision_and_dynamic_fee.py")

    sessionlocal_token = "Session" + "Local"
    calculate_store_token = "calculate_" + "and_store_negative_net_targets"

    assert_ok("NO_HARDCODED_BATCH80_TARGET_RECALC", "settlement_batch_id=80" not in targets_source)
    assert_ok("NO_HARDCODED_ORDER46_MUTATION", "order46" not in targets_source and "order_id=46" not in targets_source)
    assert_ok("VERIFIER_HAS_NO_DB_SESSION", sessionlocal_token not in verifier_source)
    assert_ok("VERIFIER_HAS_NO_CALCULATE_AND_STORE_CALL", calculate_store_token not in verifier_source)

    print("STAGE26_3_12_EXISTING_BATCH80_NOT_RECALCULATED_OK")


def test_no_secret_logging() -> None:
    production_files = [
        "app/bybit/asset_flows.py",
        "app/settlement/negative_bybit_flow.py",
        "app/settlement/negative_net_targets.py",
    ]

    forbidden_tokens = [
        "api_key",
        "api_secret",
        "X-BAPI-API-KEY",
        "X-BAPI-SIGN",
        "private_key",
    ]
    freeze_endpoint = "/v5/user/" + "frozen-sub-member"

    leaked: list[str] = []
    bsc_raw_tx_hits: list[str] = []
    freeze_hits: list[str] = []

    for path in production_files:
        source = read(path)
        lower_source = source.lower()

        for token in forbidden_tokens:
            if token.lower() in lower_source:
                leaked.append(f"{path}: {token}")

        if "send_raw_transaction" in source:
            bsc_raw_tx_hits.append(path)

        for line_number, line in enumerate(source.splitlines(), start=1):
            if freeze_endpoint in line:
                freeze_hits.append(f"{path}:{line_number}: {line.strip()}")

    assert_ok("NO_SECRET_TOKENS_IN_CHANGED_PRODUCTION_SOURCES", not leaked)
    assert_ok("NO_BSC_SEND_RAW_TRANSACTION", not bsc_raw_tx_hits)
    assert_ok("NO_FREEZE_ENDPOINT", not freeze_hits)

    print("STAGE26_3_12_NO_SECRET_LOGGING_OK")


def main() -> int:
    load_dotenv()

    test_universal_transfer_2dp_round_up()
    test_batch80_transfer_amount_1103()
    test_universal_transfer_id_uses_actual_amount()
    test_universal_transfer_payload_amount_safe()
    test_no_raw_decimal_transfer_amount_post()
    test_dynamic_withdraw_fee_from_coin_info()
    test_fee_type_zero_policy()
    test_existing_batch80_not_recalculated()
    test_no_secret_logging()

    print("STAGE26_3_12_UNIVERSAL_TRANSFER_PRECISION_AND_DYNAMIC_FEE_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())