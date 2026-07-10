from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

from app.settlement.negative_bybit_flow import (
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
    NegativeBybitFlowError,
    resolve_universal_transfer_context,
    withdrawal_actual_amount,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeBalanceClient:
    def __init__(self, balances: dict[str, Decimal]) -> None:
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self.balances = balances

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

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.post_calls.append((path, payload))
        return {"result": {}}


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def blank_flow() -> SimpleNamespace:
    return SimpleNamespace(
        status="created",
        universal_transfer_id=None,
        universal_transfer_created_at=None,
        universal_transfer_status=None,
        universal_transfer_amount_usdt=None,
        from_account_type=None,
        to_account_type=None,
        from_sub_uid=None,
        to_master_uid=None,
        withdrawal_request_id=None,
        withdrawal_created_at=None,
        withdrawal_status=None,
        withdrawal_id=None,
        withdrawal_amount_usdt=None,
    )


def existing_transfer_flow() -> SimpleNamespace:
    return SimpleNamespace(
        status=BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
        universal_transfer_id="9e11ddc1-53df-5979-b6a9-67b0e8d15b63",
        universal_transfer_created_at=datetime.now(timezone.utc),
        universal_transfer_status="SUCCESS",
        universal_transfer_amount_usdt=Decimal("11.03"),
        from_account_type="FUND",
        to_account_type="FUND",
        from_sub_uid="persisted-fund-sub-uid",
        to_master_uid="persisted-master-uid",
        withdrawal_request_id=None,
        withdrawal_created_at=None,
        withdrawal_status=None,
        withdrawal_id=None,
        withdrawal_amount_usdt=None,
    )


def test_brand_new_flow_selects_balance_route() -> None:
    client = FakeBalanceClient({"FUND": Decimal("20"), "UNIFIED": Decimal("100")})
    flow = blank_flow()

    context = resolve_universal_transfer_context(
        flow=flow,  # type: ignore[arg-type]
        bybit_client=client,  # type: ignore[arg-type]
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
        from_sub_uid="fund-sub-uid",
        to_master_uid="master-uid",
        coin="USDT",
    )

    assert_ok("BRAND_NEW_BALANCE_ROUTE_CALLED", len(client.get_calls) >= 1)
    assert_ok("BRAND_NEW_BALANCE_ROUTE_SELECTED", context["balance_route_selected"] is True)
    assert_ok("BRAND_NEW_TRANSFER_ID_UUID", len(context["transfer_id"]) == 36)
    assert_ok("BRAND_NEW_AMOUNT_FORMATTED", context["universal_transfer_amount_str"] == "11.03")
    assert_ok("BRAND_NEW_ROUTE_FUND", context["from_account_type"] == "FUND" and context["to_account_type"] == "FUND")

    print("STAGE26_3_10B_BRAND_NEW_FLOW_ROUTE_SELECTION_OK")


def test_existing_transfer_retry_skips_balance_route() -> None:
    client = FakeBalanceClient({"FUND": Decimal("0"), "UNIFIED": Decimal("0")})
    flow = existing_transfer_flow()

    context = resolve_universal_transfer_context(
        flow=flow,  # type: ignore[arg-type]
        bybit_client=client,  # type: ignore[arg-type]
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
        from_sub_uid="runtime-fund-sub-uid",
        to_master_uid="runtime-master-uid",
        coin="USDT",
    )

    assert_ok("EXISTING_TRANSFER_BALANCE_ROUTE_NOT_CALLED", len(client.get_calls) == 0)
    assert_ok("EXISTING_TRANSFER_RESUME_FLAG", context["resumed_from_existing_transfer"] is True)
    assert_ok("EXISTING_TRANSFER_BALANCE_ROUTE_FLAG_FALSE", context["balance_route_selected"] is False)

    print("STAGE26_3_10B_EXISTING_TRANSFER_RETRY_SKIPS_BALANCE_ROUTE_OK")


def test_existing_transfer_id_reused() -> None:
    client = FakeBalanceClient({"FUND": Decimal("0"), "UNIFIED": Decimal("0")})
    flow = existing_transfer_flow()

    context = resolve_universal_transfer_context(
        flow=flow,  # type: ignore[arg-type]
        bybit_client=client,  # type: ignore[arg-type]
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
        from_sub_uid="runtime-fund-sub-uid",
        to_master_uid="runtime-master-uid",
        coin="USDT",
    )

    assert_ok("EXISTING_TRANSFER_ID_REUSED", context["transfer_id"] == flow.universal_transfer_id)
    assert_ok("EXISTING_FROM_ACCOUNT_REUSED", context["from_account_type"] == "FUND")
    assert_ok("EXISTING_TO_ACCOUNT_REUSED", context["to_account_type"] == "FUND")
    assert_ok("EXISTING_FROM_UID_REUSED", context["from_sub_uid"] == "persisted-fund-sub-uid")
    assert_ok("EXISTING_TO_UID_REUSED", context["to_master_uid"] == "persisted-master-uid")

    print("STAGE26_3_10B_EXISTING_TRANSFER_ID_REUSED_OK")


def test_withdrawal_pending_no_resend_source_shape() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("WITHDRAWAL_ATTEMPT_HELPER_PRESENT", "_flow_has_withdrawal_attempt" in source)
    assert_ok(
        "WITHDRAWAL_ATTEMPT_MISSING_REQUEST_ID_FAILS",
        "Existing withdrawal attempt is missing withdrawal_request_id" in source,
    )
    assert_ok(
        "WITHDRAWAL_PENDING_NO_RESEND_PRESENT",
        "withdrawal_pending_or_unknown_no_tx_hash" in source
        and '"no_duplicate_resend": True' in source,
    )
    assert_ok("NO_PAYOUT_WHILE_WITHDRAWAL_PENDING", '"no_bsc_payout": True' in source)
    assert_ok("NO_FINALIZATION_WHILE_WITHDRAWAL_PENDING", '"no_accounting_finalization": True' in source)

    print("STAGE26_3_10B_WITHDRAWAL_PENDING_NO_RESEND_OK")


def test_existing_flow_missing_route_fails_closed() -> None:
    client = FakeBalanceClient({"FUND": Decimal("100")})
    flow = existing_transfer_flow()
    flow.from_account_type = None

    try:
        resolve_universal_transfer_context(
            flow=flow,  # type: ignore[arg-type]
            bybit_client=client,  # type: ignore[arg-type]
            settlement_batch_id=80,
            fund_id=9,
            required_master_usdt=Decimal("11.0222489345"),
            from_sub_uid="runtime-fund-sub-uid",
            to_master_uid="runtime-master-uid",
            coin="USDT",
        )
    except NegativeBybitFlowError as exc:
        assert_ok("MISSING_ROUTE_ERROR_MESSAGE", "from_account_type" in str(exc))
    else:
        raise AssertionError("Existing flow missing route must fail closed")

    assert_ok("MISSING_ROUTE_NO_BALANCE_CALL", len(client.get_calls) == 0)
    assert_ok("MISSING_ROUTE_NO_POST_CALL", len(client.post_calls) == 0)

    print("STAGE26_3_10B_EXISTING_FLOW_MISSING_ROUTE_FAILS_CLOSED_OK")


def test_withdrawal_rounding_no_underpay() -> None:
    amount_str, actual = withdrawal_actual_amount(
        withdrawal_request_amount_usdt=Decimal("10.0000000000"),
        precision=6,
    )

    assert_ok("WITHDRAWAL_EXACT_AMOUNT_STR_OK", amount_str == "10")
    assert_ok("WITHDRAWAL_EXACT_AMOUNT_ACTUAL_OK", actual == Decimal("10.0000000000"))

    try:
        withdrawal_actual_amount(
            withdrawal_request_amount_usdt=Decimal("10.123456789"),
            precision=6,
        )
    except NegativeBybitFlowError as exc:
        assert_ok("WITHDRAWAL_ROUNDING_FAILS_CLOSED_MESSAGE", "rounding" in str(exc).lower())
    else:
        raise AssertionError("Withdrawal rounding underpay must fail closed")

    print("STAGE26_3_10B_WITHDRAWAL_ROUNDING_NO_UNDERPAY_OK")


def test_no_random_uuid4() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("NO_UUID4", "uuid4" not in source and "uuid.uuid4" not in source)
    assert_ok("UUID5_STILL_USED", "uuid.uuid5" in source)

    print("STAGE26_3_10B_NO_RANDOM_UUID4_OK")


def test_no_secret_logging() -> None:
    production_sources = "\n".join(
        [
            read("app/settlement/negative_bybit_flow.py"),
        ]
    )

    forbidden = [
        "api_key",
        "api_secret",
        "X-BAPI-API-KEY",
        "X-BAPI-SIGN",
        "signature",
    ]
    leaked = [token for token in forbidden if token.lower() in production_sources.lower()]

    assert_ok("NO_SECRET_TOKENS", not leaked)
    assert_ok("NO_BSC_SEND_RAW_TRANSACTION", "send_raw_transaction" not in production_sources)
    assert_ok("NO_FREEZE_ENDPOINT", "/v5/user/frozen-sub-member" not in production_sources)

    print("STAGE26_3_10B_NO_SECRET_LOGGING_OK")


def main() -> int:
    load_dotenv()

    test_brand_new_flow_selects_balance_route()
    test_existing_transfer_retry_skips_balance_route()
    test_existing_transfer_id_reused()
    test_withdrawal_pending_no_resend_source_shape()
    test_existing_flow_missing_route_fails_closed()
    test_withdrawal_rounding_no_underpay()
    test_no_random_uuid4()
    test_no_secret_logging()

    print("STAGE26_3_10B_RETRY_IDEMPOTENCY_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())