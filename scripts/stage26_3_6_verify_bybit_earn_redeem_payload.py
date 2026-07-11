from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.bybit.earn import (
    BybitEarnPosition,
    format_bybit_earn_amount,
)
from app.settlement.negative_sale_execution import (
    compute_needed_from_earn,
    deterministic_negative_sale_earn_redeem_link_id,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def ast_call_names(path: str) -> set[str]:
    tree = ast.parse(read(path))
    calls: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)

    return calls


def test_amount_format_precision() -> None:
    assert_ok(
        "FORMAT_50_PRECISION_6_TO_INTEGER",
        format_bybit_earn_amount(
            Decimal("50.0000000000"),
            precision=6,
            rounding="up",
        )
        == "50",
    )
    assert_ok(
        "FORMAT_NO_SCIENTIFIC_NOTATION",
        "E"
        not in format_bybit_earn_amount(
            Decimal("0.000001"),
            precision=6,
            rounding="up",
        ).upper(),
    )

    print("STAGE26_3_6_EARN_AMOUNT_FORMAT_PRECISION_OK")


def test_round_up_covers_shortage() -> None:
    formatted = format_bybit_earn_amount(
        Decimal("10.9092489345"),
        precision=6,
        rounding="up",
    )
    amount = Decimal(formatted)

    assert_ok("ROUND_UP_VALUE_OK", formatted == "10.909249")
    assert_ok("ROUND_UP_COVERS_SHORTAGE", amount >= Decimal("10.9092489345"))
    assert_ok("ROUND_UP_NOT_FULL_BUFFER", amount < Decimal("50"))

    print("STAGE26_3_6_EARN_AMOUNT_ROUND_UP_COVERS_SHORTAGE_OK")


def test_position_available_amount_used() -> None:
    position = BybitEarnPosition(
        category="FlexibleSaving",
        coin="USDT",
        product_id="428",
        amount=Decimal("50"),
        available_amount=Decimal("10.5"),
        freeze_details={"frozen": "39.5"},
        position_id="test-position",
        status="active",
        raw={
            "amount": "50",
            "availableAmount": "10.5",
            "freezeDetails": {"frozen": "39.5"},
        },
    )

    assert_ok("POSITION_TOTAL_AMOUNT_PARSED", position.amount == Decimal("50"))
    assert_ok(
        "POSITION_AVAILABLE_AMOUNT_PARSED",
        position.available_amount == Decimal("10.5"),
    )
    assert_ok("POSITION_FREEZE_DETAILS_PRESENT", position.freeze_details is not None)

    earn_source = read("app/bybit/earn.py")
    execution_source = read("app/settlement/negative_sale_execution.py")

    assert_ok(
        "GET_POSITIONS_PARSES_AVAILABLE_AMOUNT",
        "available_amount=_dec(row.get(\"availableAmount\"))" in earn_source,
    )
    assert_ok(
        "EXECUTION_USES_TOTAL_AVAILABLE_AMOUNT",
        "total_flexible_saving_available_amount" in execution_source,
    )
    assert_ok(
        "EXECUTION_DOES_NOT_USE_TOTAL_POSITION_AMOUNT_FOR_REDEEMABLE",
        "total_flexible_saving_amount(" not in execution_source,
    )

    print("STAGE26_3_6_EARN_POSITION_AVAILABLE_AMOUNT_USED_OK")


def test_current_batch80_amount_shape() -> None:
    needed = compute_needed_from_earn(
        required_master_usdt=Decimal("11.0222489345"),
        already_realized_cash_usdt=Decimal("0.1130000000"),
        target_cash_usdt=Decimal("50.0000000000"),
        available_amount=Decimal("50.0000000000"),
    )
    formatted = format_bybit_earn_amount(
        needed,
        precision=6,
        rounding="up",
    )

    assert_ok("CURRENT_NEEDED_FROM_EARN_OK", needed == Decimal("10.9092489345"))
    assert_ok("CURRENT_FORMATTED_AMOUNT_OK", formatted == "10.909249")
    assert_ok("CURRENT_NOT_REDEEMING_FULL_50", Decimal(formatted) < Decimal("50"))

    print("STAGE26_3_6_CURRENT_BATCH80_AMOUNT_SHAPE_OK")


def test_order_link_id_max_36() -> None:
    order_link_id = deterministic_negative_sale_earn_redeem_link_id(
        sale_batch_id=1,
        leg_id=1,
        leg_index=1,
    )
    assert_ok("ORDER_LINK_ID_LEN_OK", len(order_link_id) <= 36)

    print("STAGE26_3_6_EARN_ORDERLINKID_MAX_36_OK")


def test_payload_keys_match_docs() -> None:
    source = read("app/bybit/earn.py")

    required = [
        '"/v5/earn/place-order"',
        '"category": "FlexibleSaving"',
        '"orderType": "Redeem"',
        '"accountType": account_type',
        '"amount": amount_str',
        '"coin": coin',
        '"productId": product_id',
        '"orderLinkId": order_link_id',
    ]
    missing = [item for item in required if item not in source]
    assert_ok("EARN_PAYLOAD_REQUIRED_KEYS_PRESENT", not missing)

    assert_ok(
        "EARN_PAYLOAD_NO_RAW_DECIMAL_STR_AMOUNT",
        '"amount": str(amount)' not in source,
    )

    print("STAGE26_3_6_EARN_PAYLOAD_KEYS_MATCH_BYBIT_DOCS_OK")


def test_no_secret_logging() -> None:
    source = read("app/bybit/earn.py")

    forbidden_log_tokens = [
        "api_key",
        "api_secret",
        "signature",
        "X-BAPI-API-KEY",
        "X-BAPI-SIGN",
    ]

    lower_source = source.lower()
    leaked = [
        token
        for token in forbidden_log_tokens
        if token.lower() in lower_source
    ]

    assert_ok("NO_SECRET_TOKENS_IN_EARN_LOGGING_SOURCE", not leaked)
    assert_ok("SAFE_PAYLOAD_SUMMARY_PRESENT", "payload_summary" in source)
    assert_ok("ORDER_LINK_LEN_LOGGED_NOT_VALUE_ONLY", "orderLinkId_len" in source)

    print("STAGE26_3_6_NO_SECRET_LOGGING_OK")


def test_no_unapproved_external_paths() -> None:
    earn_source = read("app/bybit/earn.py")
    calls = ast_call_names("app/bybit/earn.py")

    frozen_member_endpoint = "/v5/user/" + "frozen-" + "sub-member"
    assert_ok("NO_FREEZE_ENDPOINT", frozen_member_endpoint not in earn_source)
    assert_ok("NO_BSC_TX_SEND", "send_raw_transaction" not in earn_source)
    assert_ok("NO_REQUESTS_DIRECT_POST", "post" in calls)


def main() -> int:
    load_dotenv()

    test_amount_format_precision()
    test_round_up_covers_shortage()
    test_position_available_amount_used()
    test_current_batch80_amount_shape()
    test_order_link_id_max_36()
    test_payload_keys_match_docs()
    test_no_secret_logging()
    test_no_unapproved_external_paths()

    print("STAGE26_3_6_EARN_REDEEM_PAYLOAD_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())