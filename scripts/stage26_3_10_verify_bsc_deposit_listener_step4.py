from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from workers.bsc_usdt_deposit_listener import is_internal_platform_payout_transfer


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_internal_payout_detection() -> None:
    wallet_map = {
        "0xuser000000000000000000000000000000000000": (1, 1),
    }
    settlement_addresses = {
        "0xsettlement000000000000000000000000000000",
    }

    assert_ok(
        "INTERNAL_PAYOUT_DETECTED",
        is_internal_platform_payout_transfer(
            from_address="0xSettlement000000000000000000000000000000",
            to_address="0xUser000000000000000000000000000000000000",
            wallet_map=wallet_map,
            platform_settlement_wallet_addresses=settlement_addresses,
        ),
    )

    assert_ok(
        "EXTERNAL_DEPOSIT_NOT_INTERNAL",
        not is_internal_platform_payout_transfer(
            from_address="0xExternal000000000000000000000000000000",
            to_address="0xUser000000000000000000000000000000000000",
            wallet_map=wallet_map,
            platform_settlement_wallet_addresses=settlement_addresses,
        ),
    )

    assert_ok(
        "NON_USER_DESTINATION_NOT_INTERNAL",
        not is_internal_platform_payout_transfer(
            from_address="0xSettlement000000000000000000000000000000",
            to_address="0xOther0000000000000000000000000000000000",
            wallet_map=wallet_map,
            platform_settlement_wallet_addresses=settlement_addresses,
        ),
    )

    print("STAGE26_3_10_STEP4_INTERNAL_PAYOUT_DETECTION_OK")


def test_listener_source_has_required_skip() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")

    assert_ok("LOADS_FUND_WALLET_MODEL", "FundWallet" in source)
    assert_ok(
        "LOADS_ACTIVE_SETTLEMENT_WALLETS",
        "load_active_platform_settlement_wallet_addresses" in source,
    )
    assert_ok(
        "HAS_INTERNAL_PAYOUT_SKIP_HELPER",
        "is_internal_platform_payout_transfer" in source,
    )
    assert_ok(
        "SKIPS_BEFORE_INSERT",
        source.index("is_internal_platform_payout_transfer")
        < source.index("db_insert_transfer"),
    )
    assert_ok(
        "SAFE_LOG_PRESENT",
        "Internal payout transfer ignored by deposit listener" in source,
    )

    print("STAGE26_3_10_STEP4_DEPOSIT_LISTENER_INTERNAL_SKIP_OK")


def test_no_double_credit_risk_source_shape() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")

    skip_block = source.split(
        "if is_internal_platform_payout_transfer(",
        1,
    )[1].split(
        "tx_time = block_time_cache.get(block_number)",
        1,
    )[0]

    assert_ok("SKIP_BLOCK_RETURNS", "return" in skip_block)
    assert_ok("SKIP_BLOCK_NO_DB_INSERT", "db_insert_transfer" not in skip_block)
    assert_ok("SKIP_BLOCK_UPSERTS_CURSOR_REALTIME", "db_upsert_cursor" in skip_block)

    print("STAGE26_3_10_STEP4_NO_DOUBLE_CREDIT_RISK_OK")


def test_reload_includes_settlement_addresses() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")

    assert_ok("MAIN_LOADS_SETTLEMENT_ADDRESSES", "last_settlement_addrs" in source)
    assert_ok(
        "SUBSCRIBE_RECEIVES_SETTLEMENT_ADDRESSES",
        "platform_settlement_wallet_addresses" in source,
    )
    assert_ok(
        "RELOAD_DETECTS_SETTLEMENT_ADDRESS_CHANGE",
        "new_settlement_addrs != last_settlement_addrs" in source,
    )

    print("STAGE26_3_10_STEP4_SETTLEMENT_ADDRESS_RELOAD_OK")


def test_no_forbidden_paths() -> None:
    source = read("workers/bsc_usdt_deposit_listener.py")

    assert_ok("NO_SECRET_TOKENS", "api_secret" not in source.lower() and "api_key" not in source.lower())
    assert_ok("NO_BSC_SEND_RAW_TX", "send_raw_transaction" not in source)
    frozen_member_endpoint = "/v5/user/" + "frozen-" + "sub-member"
    assert_ok("NO_FREEZE_ENDPOINT", frozen_member_endpoint not in source)

    print("STAGE26_3_10_STEP4_NO_FORBIDDEN_PATHS_OK")


def main() -> int:
    load_dotenv()

    test_internal_payout_detection()
    test_listener_source_has_required_skip()
    test_no_double_credit_risk_source_shape()
    test_reload_includes_settlement_addresses()
    test_no_forbidden_paths()

    print("STAGE26_3_10_STEP4_BSC_DEPOSIT_LISTENER_DOUBLE_CREDIT_FIX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())