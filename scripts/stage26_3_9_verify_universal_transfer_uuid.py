from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Any
import uuid

from dotenv import load_dotenv

from app.bybit.asset_flows import BybitAssetFlowError, create_universal_transfer
from app.settlement.negative_bybit_flow import deterministic_universal_transfer_id


ROOT = Path(__file__).resolve().parents[1]


class FakeBybitClient:
    def __init__(self) -> None:
        self.post_called = False
        self.last_path: str | None = None
        self.last_payload: dict[str, Any] | None = None

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.post_called = True
        self.last_path = path
        self.last_payload = payload
        return {
            "retCode": 0,
            "result": {
                "transferId": payload.get("transferId"),
                "status": "SUCCESS",
            },
        }


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def parse_uuid(value: str) -> uuid.UUID:
    return uuid.UUID(str(value))


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


def test_transfer_id_uuid() -> None:
    transfer_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
    )
    parsed = parse_uuid(transfer_id)

    assert_ok("TRANSFER_ID_IS_UUID", str(parsed) == transfer_id)
    assert_ok("TRANSFER_ID_CANONICAL_LEN_36", len(transfer_id) == 36)
    assert_ok("TRANSFER_ID_NOT_OLD_PREFIX", not transfer_id.startswith("neg-net-transfer:"))

    print("STAGE26_3_9_UNIVERSAL_TRANSFER_ID_UUID_OK")


def test_transfer_id_deterministic() -> None:
    base = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
    )
    same = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345000"),
    )
    diff_batch = deterministic_universal_transfer_id(
        settlement_batch_id=81,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
    )
    diff_fund = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=10,
        required_master_usdt=Decimal("11.0222489345"),
    )
    diff_amount = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489346"),
    )

    assert_ok("TRANSFER_ID_SAME_INPUTS_SAME_UUID", base == same)
    assert_ok("TRANSFER_ID_DIFF_BATCH_DIFF_UUID", base != diff_batch)
    assert_ok("TRANSFER_ID_DIFF_FUND_DIFF_UUID", base != diff_fund)
    assert_ok("TRANSFER_ID_DIFF_AMOUNT_DIFF_UUID", base != diff_amount)

    print("STAGE26_3_9_UNIVERSAL_TRANSFER_ID_DETERMINISTIC_OK")


def test_current_batch80_transfer_id_uuid() -> None:
    transfer_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
    )
    parsed = parse_uuid(transfer_id)

    assert_ok("CURRENT_BATCH80_TRANSFER_ID_UUID_PARSE_OK", str(parsed) == transfer_id)
    assert_ok("CURRENT_BATCH80_TRANSFER_ID_UUID_FORMAT_OK", len(transfer_id) == 36)

    print("STAGE26_3_9_CURRENT_BATCH80_TRANSFER_ID_UUID_OK")


def test_create_universal_transfer_rejects_non_uuid() -> None:
    client = FakeBybitClient()

    try:
        create_universal_transfer(
            client,  # type: ignore[arg-type]
            transfer_id="neg-net-transfer:80:9:11.0222489345",
            coin="USDT",
            amount_usdt=Decimal("11.0222489345"),
            from_member_id="fund-sub-uid",
            to_member_id="master-uid",
            from_account_type="UNIFIED",
            to_account_type="UNIFIED",
        )
    except BybitAssetFlowError as exc:
        assert_ok("NON_UUID_TRANSFER_ID_ERROR_MESSAGE_OK", "UUID" in str(exc))
    else:
        raise AssertionError("NON_UUID_TRANSFER_ID_SHOULD_FAIL")

    assert_ok("NON_UUID_TRANSFER_ID_NO_POST", client.post_called is False)

    valid_transfer_id = deterministic_universal_transfer_id(
        settlement_batch_id=80,
        fund_id=9,
        required_master_usdt=Decimal("11.0222489345"),
    )
    valid_client = FakeBybitClient()
    result = create_universal_transfer(
        valid_client,  # type: ignore[arg-type]
        transfer_id=valid_transfer_id.upper(),
        coin="USDT",
        amount_usdt=Decimal("11.0222489345"),
        from_member_id="fund-sub-uid",
        to_member_id="master-uid",
        from_account_type="UNIFIED",
        to_account_type="UNIFIED",
    )

    assert_ok("VALID_TRANSFER_ID_POST_CALLED", valid_client.post_called is True)
    assert_ok("VALID_TRANSFER_ID_CANONICAL_RETURNED", result.transfer_id == valid_transfer_id)
    assert_ok(
        "VALID_TRANSFER_ID_CANONICAL_PAYLOAD",
        valid_client.last_payload is not None
        and valid_client.last_payload.get("transferId") == valid_transfer_id,
    )

    print("STAGE26_3_9_CREATE_UNIVERSAL_TRANSFER_REJECTS_NON_UUID_OK")


def test_no_random_uuid4_in_transfer_id_path() -> None:
    source = read("app/settlement/negative_bybit_flow.py")
    calls = ast_call_names("app/settlement/negative_bybit_flow.py")

    assert_ok("TRANSFER_ID_USES_UUID5", "uuid.uuid5" in source)
    assert_ok("TRANSFER_ID_DOES_NOT_USE_UUID4", "uuid4" not in calls and "uuid.uuid4" not in source)
    assert_ok("OLD_NEG_NET_TRANSFER_PREFIX_REMOVED", "neg-net-transfer:" not in source)

    print("STAGE26_3_9_NO_RANDOM_UUID4_IN_TRANSFER_ID_PATH_OK")


def test_no_secret_logging_or_forbidden_paths() -> None:
    negative_source = read("app/settlement/negative_bybit_flow.py")
    asset_source = read("app/bybit/asset_flows.py")
    verifier_source = read("scripts/stage26_3_9_verify_universal_transfer_uuid.py")

    production_sources = "\n".join(
        [
            negative_source,
            asset_source,
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
    assert_ok("NO_SECRET_TOKENS_IN_CHANGED_SOURCES", not leaked)

    assert_ok("NO_BSC_TX_PATH_ADDED", "send_raw_transaction" not in production_sources)

    # asset_flows.py already has an old freeze_sub_uid() helper.
    # Stage 26.3.9 must not route negative Bybit flow or this verifier through freeze logic.
    freeze_endpoint = "/v5/user/" + "frozen-sub-member"
    assert_ok(
        "NEGATIVE_BYBIT_FLOW_NO_FREEZE_ENDPOINT",
        freeze_endpoint not in negative_source,
    )
    assert_ok(
        "VERIFIER_NO_FREEZE_ENDPOINT",
        freeze_endpoint not in verifier_source,
    )

    print("STAGE26_3_9_NO_SECRET_LOGGING_OK")


def main() -> int:
    load_dotenv()

    test_transfer_id_uuid()
    test_transfer_id_deterministic()
    test_current_batch80_transfer_id_uuid()
    test_create_universal_transfer_rejects_non_uuid()
    test_no_random_uuid4_in_transfer_id_path()
    test_no_secret_logging_or_forbidden_paths()

    print("STAGE26_3_9_UNIVERSAL_TRANSFER_UUID_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())