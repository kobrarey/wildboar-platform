from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any
import uuid

from dotenv import load_dotenv

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    create_universal_transfer,
    format_bybit_asset_amount,
    query_account_coin_balance,
)
from app.settlement.negative_bybit_flow import (
    choose_universal_transfer_account_route,
    deterministic_universal_transfer_id,
    universal_transfer_actual_amount,
)


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


class FakePostClient:
    def __init__(self) -> None:
        self.post_called = False
        self.last_payload: dict[str, Any] | None = None

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.post_called = True
        self.last_payload = payload
        return {
            "result": {
                "transferId": payload["transferId"],
                "status": "SUCCESS",
            }
        }


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_amount_formatting() -> None:
    assert_ok(
        "FORMAT_UP_6_OK",
        format_bybit_asset_amount(
            Decimal("11.0222489345"),
            precision=6,
            rounding="up",
        )
        == "11.022249",
    )
    assert_ok(
        "FORMAT_DOWN_STRIPS_ZERO_OK",
        format_bybit_asset_amount(
            Decimal("10.0000000000"),
            precision=6,
            rounding="down",
        )
        == "10",
    )
    assert_ok(
        "FORMAT_SMALL_STRIPS_ZERO_OK",
        format_bybit_asset_amount(
            Decimal("0.1000000000"),
            precision=6,
            rounding="up",
        )
        == "0.1",
    )

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_AMOUNT_FORMAT_OK")


def test_amount_rounds_up_and_covers_required() -> None:
    amount_str, actual = universal_transfer_actual_amount(
        required_master_usdt=Decimal("11.0222489345")
    )

    assert_ok("AMOUNT_STR_BATCH80_OK", amount_str == "11.022249")
    assert_ok("ACTUAL_COVERS_REQUIRED", actual >= Decimal("11.0222489345"))
    assert_ok(
        "SURPLUS_WITHIN_ONE_QUANTUM",
        actual - Decimal("11.0222489345") <= Decimal("0.000001"),
    )

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_ROUND_UP_OK")


def test_account_route_selection_prefers_fund() -> None:
    client = FakeBalanceClient(
        {
            "FUND": Decimal("20"),
            "UNIFIED": Decimal("100"),
        }
    )
    route = choose_universal_transfer_account_route(
        client,  # type: ignore[arg-type]
        coin="USDT",
        amount_usdt=Decimal("11.022249"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
    )

    assert_ok("ROUTE_PREFERS_FUND_FROM", route["from_account_type"] == "FUND")
    assert_ok("ROUTE_PREFERS_FUND_TO", route["to_account_type"] == "FUND")

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_FUND_ROUTE_OK")


def test_account_route_selection_fallback_unified() -> None:
    client = FakeBalanceClient(
        {
            "FUND": Decimal("1"),
            "UNIFIED": Decimal("100"),
        }
    )
    route = choose_universal_transfer_account_route(
        client,  # type: ignore[arg-type]
        coin="USDT",
        amount_usdt=Decimal("11.022249"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
    )

    assert_ok("ROUTE_FALLBACK_UNIFIED_FROM", route["from_account_type"] == "UNIFIED")
    assert_ok("ROUTE_FALLBACK_UNIFIED_TO", route["to_account_type"] == "UNIFIED")

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_UNIFIED_FALLBACK_OK")


def test_account_route_selection_fails_closed() -> None:
    client = FakeBalanceClient(
        {
            "FUND": Decimal("1"),
            "UNIFIED": Decimal("2"),
        }
    )

    try:
        choose_universal_transfer_account_route(
            client,  # type: ignore[arg-type]
            coin="USDT",
            amount_usdt=Decimal("11.022249"),
            from_member_id="fund-sub-uid",
            to_member_id="master-uid",
        )
    except Exception as exc:
        assert_ok("ROUTE_FAILS_CLOSED_MESSAGE", "transferBalance" in str(exc))
    else:
        raise AssertionError("Route selection must fail closed")

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_ROUTE_FAIL_CLOSED_OK")


def test_uuid_seed_includes_route_and_amount() -> None:
    base = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.022249"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )
    same = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.0222490000"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )
    diff_route = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.022249"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="UNIFIED",
        to_account_type="UNIFIED",
    )
    diff_amount = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.022250"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )

    assert_ok("UUID_VALID", str(uuid.UUID(base)) == base)
    assert_ok("UUID_SAME_INPUTS_SAME", base == same)
    assert_ok("UUID_ROUTE_CHANGE_DIFF", base != diff_route)
    assert_ok("UUID_AMOUNT_CHANGE_DIFF", base != diff_amount)

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_UUID_ROUTE_AMOUNT_OK")


def test_create_transfer_uses_formatted_amount() -> None:
    client = FakePostClient()
    transfer_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        universal_transfer_amount_usdt=Decimal("11.022249"),
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
        amount_str="11.022249",
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="FUND",
        to_account_type="FUND",
    )

    assert_ok("POST_CALLED", client.post_called)
    assert_ok(
        "POST_AMOUNT_FORMATTED",
        client.last_payload is not None
        and client.last_payload.get("amount") == "11.022249",
    )
    assert_ok("RESULT_AMOUNT_IS_POSTED_AMOUNT", result.amount_usdt == Decimal("11.022249"))

    print("STAGE26_3_10_STEP1_CREATE_TRANSFER_FORMATTED_AMOUNT_OK")


def test_no_forbidden_paths() -> None:
    negative_source = read("app/settlement/negative_bybit_flow.py")
    asset_source = read("app/bybit/asset_flows.py")

    universal_transfer_source = asset_source.split(
        "def create_master_withdrawal",
        1,
    )[0]

    combined = negative_source + "\n" + universal_transfer_source

    assert_ok("NO_UUID4", "uuid4" not in combined and "uuid.uuid4" not in combined)
    assert_ok("NO_BSC_TX_PATH", "send_raw_transaction" not in combined)
    assert_ok(
        "UNIVERSAL_TRANSFER_USES_FORMATTED_AMOUNT",
        '"amount": clean_amount_str' in universal_transfer_source,
    )
    assert_ok(
        "UNIVERSAL_TRANSFER_NO_RAW_AMOUNT_STR",
        '"amount": str(amount)' not in universal_transfer_source,
    )

    print("STAGE26_3_10_STEP1_NO_FORBIDDEN_PATHS_OK")


def main() -> int:
    load_dotenv()

    test_amount_formatting()
    test_amount_rounds_up_and_covers_required()
    test_account_route_selection_prefers_fund()
    test_account_route_selection_fallback_unified()
    test_account_route_selection_fails_closed()
    test_uuid_seed_includes_route_and_amount()
    test_create_transfer_uses_formatted_amount()
    test_no_forbidden_paths()

    print("STAGE26_3_10_STEP1_UNIVERSAL_TRANSFER_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())