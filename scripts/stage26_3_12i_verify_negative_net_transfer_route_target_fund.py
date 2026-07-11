from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import app.settlement.negative_bybit_flow as flow_mod


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def fake_balance(value: str):
    amount = Decimal(value)
    return SimpleNamespace(
        wallet_balance=amount,
        transfer_balance=amount,
        transfer_safe_amount=amount,
        ltv_transfer_safe_amount=amount,
    )


def run_route_case(*, fund_balance: str, unified_balance: str):
    calls: list[tuple[str, str | None]] = []
    balances = {
        "FUND": fake_balance(fund_balance),
        "UNIFIED": fake_balance(unified_balance),
    }

    original = flow_mod.query_account_coin_balance

    def fake_query_account_coin_balance(
        client,
        *,
        account_type,
        coin,
        member_id=None,
        to_member_id=None,
        to_account_type=None,
        with_transfer_safe_amount=True,
        with_ltv_transfer_safe_amount=True,
    ):
        calls.append((str(account_type).strip().upper(), str(to_account_type).strip().upper()))
        return balances[str(account_type).strip().upper()]

    try:
        flow_mod.query_account_coin_balance = fake_query_account_coin_balance
        route = flow_mod.choose_universal_transfer_account_route(
            object(),
            coin="USDT",
            amount_usdt=Decimal("11.03"),
            from_member_id="fund-sub-uid",
            to_member_id="master-uid",
        )
    finally:
        flow_mod.query_account_coin_balance = original

    return route, calls


def test_fund_sufficient_route_fund_to_fund() -> None:
    route, calls = run_route_case(fund_balance="12", unified_balance="100")

    assert_ok("FUND_SUFFICIENT_FROM_FUND", route["from_account_type"] == "FUND")
    assert_ok("FUND_SUFFICIENT_TO_FUND", route["to_account_type"] == "FUND")
    assert_ok("FUND_SUFFICIENT_DOES_NOT_QUERY_UNIFIED", calls == [("FUND", "FUND")])

    print("STAGE26_3_12I_FUND_SUFFICIENT_ROUTE_FUND_TO_FUND_OK")


def test_unified_source_target_fund() -> None:
    route, calls = run_route_case(fund_balance="1", unified_balance="12")

    assert_ok("UNIFIED_FALLBACK_FROM_UNIFIED", route["from_account_type"] == "UNIFIED")
    assert_ok("UNIFIED_FALLBACK_TO_FUND", route["to_account_type"] == "FUND")
    assert_ok(
        "UNIFIED_FALLBACK_QUERIES_FUND_TARGET",
        calls == [("FUND", "FUND"), ("UNIFIED", "FUND")],
    )

    print("STAGE26_3_12I_UNIFIED_SOURCE_TARGET_FUND_OK")


def test_unified_to_unified_forbidden() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("NO_UNIFIED_TO_UNIFIED_CANDIDATE", '("UNIFIED", "UNIFIED")' not in source)
    assert_ok(
        "UNIFIED_TO_UNIFIED_EXPLICITLY_FORBIDDEN",
        "UNIFIED -> UNIFIED is forbidden for negative-net withdrawal flow" in source,
    )

    try:
        flow_mod._validate_negative_net_universal_transfer_route(
            from_account_type="UNIFIED",
            to_account_type="UNIFIED",
        )
        raise AssertionError("UNIFIED_TO_UNIFIED_NOT_REJECTED")
    except flow_mod.NegativeBybitFlowError as exc:
        assert_ok("UNIFIED_TO_UNIFIED_REJECTED", "UNIFIED -> UNIFIED" in str(exc))

    print("STAGE26_3_12I_UNIFIED_TO_UNIFIED_FORBIDDEN_OK")


def test_withdrawal_account_type_remains_fund() -> None:
    source = read("app/settlement/negative_bybit_flow.py")

    assert_ok("WITHDRAWAL_PREFLIGHT_ACCOUNT_TYPE_FUND", '"withdrawal_account_type": "FUND"' in source)
    assert_ok("WITHDRAWAL_GUARD_ACCOUNT_TYPE_FUND", '"account_type": "FUND"' in source)
    assert_ok("WITHDRAWAL_CREATE_ACCOUNT_TYPE_FUND", 'account_type="FUND"' in source)

    print("STAGE26_3_12I_WITHDRAWAL_ACCOUNT_TYPE_REMAINS_FUND_OK")


def test_transfer_id_seed_includes_route_and_amount() -> None:
    source = read("app/settlement/negative_bybit_flow.py")
    start = source.index("def deterministic_universal_transfer_id(")
    end = source.index("def deterministic_withdrawal_request_id(")
    body = source[start:end]

    assert_ok("TRANSFER_ID_SEED_INCLUDES_AMOUNT", "universal_transfer_amount_usdt=" in body)
    assert_ok("TRANSFER_ID_SEED_INCLUDES_FROM_ACCOUNT_TYPE", "from_account_type=" in body)
    assert_ok("TRANSFER_ID_SEED_INCLUDES_TO_ACCOUNT_TYPE", "to_account_type=" in body)

    print("STAGE26_3_12I_TRANSFER_ID_SEED_ROUTE_AND_AMOUNT_OK")


def test_withdraw_request_id_logic_unchanged() -> None:
    source = read("app/settlement/negative_bybit_flow.py")
    start = source.index("def deterministic_withdrawal_request_id(")
    end = source.index("def universal_transfer_actual_amount(")
    body = source[start:end]

    assert_ok("WITHDRAW_ID_PREFIX_UNCHANGED", 'return "wbng" +' in body)
    assert_ok("WITHDRAW_ID_SEED_STILL_WITHDRAWAL", "wildboar:negative-net-withdrawal:" in body)
    assert_ok("WITHDRAW_ID_NO_FROM_ACCOUNT_TYPE", "from_account_type" not in body)
    assert_ok("WITHDRAW_ID_NO_TO_ACCOUNT_TYPE", "to_account_type" not in body)

    print("STAGE26_3_12I_WITHDRAW_REQUEST_ID_UNCHANGED_OK")


def test_worker_rate_limit_no_tight_loop() -> None:
    worker = read("workers/fund_negative_bybit_flow_worker.py")

    assert_ok("WORKER_RATE_LIMIT_HELPER_PRESENT", "_is_rate_limit_retry_pending" in worker)
    assert_ok("WORKER_RATE_LIMIT_PENDING_STATE", "withdrawal_rate_limit_retry" in worker)
    assert_ok("WORKER_RETRY_DELAY_PENDING_STATE", "withdrawal_rate_limit_retry_delay_not_elapsed" in worker)
    assert_ok("WORKER_RETURNS_FALSE_ON_RATE_LIMIT", "if _is_rate_limit_retry_pending(result):" in worker)
    assert_ok("WORKER_LOOP_SLEEP_ON_FALSE", "if not processed:" in worker and "time.sleep(sleep_seconds)" in worker)

    print("STAGE26_3_12I_WORKER_RATE_LIMIT_NO_TIGHT_LOOP_OK")


def main() -> int:
    test_fund_sufficient_route_fund_to_fund()
    test_unified_source_target_fund()
    test_unified_to_unified_forbidden()
    test_withdrawal_account_type_remains_fund()
    test_transfer_id_seed_includes_route_and_amount()
    test_withdraw_request_id_logic_unchanged()
    test_worker_rate_limit_no_tight_loop()

    print("STAGE26_3_12I_NEGATIVE_NET_TRANSFER_ROUTE_TARGET_FUND_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())