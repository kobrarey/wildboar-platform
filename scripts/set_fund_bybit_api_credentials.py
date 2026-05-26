from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.bybit.credentials import (
    BybitCredentialsError,
    get_active_fund_bybit_client,
    set_fund_bybit_api_credentials,
)
from app.db import SessionLocal
from app.models import Fund, FundBybitAccount


SUPPORTED_FUNDS = {
    "btc_fund",
    "defi_sniper",
    "wb10",
    "wb_test",
    "wb_defi",
    "wb_web3",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Store encrypted per-fund Bybit subaccount API credentials "
            "in fund_bybit_accounts."
        )
    )

    parser.add_argument(
        "--fund-code",
        required=True,
        help="Fund code. Example: wb10.",
    )

    parser.add_argument(
        "--coin",
        default="USDT",
        help="Coin for fund_bybit_accounts lookup. Default: USDT.",
    )

    parser.add_argument(
        "--chain-type",
        default="BSC",
        help="Chain type for fund_bybit_accounts lookup. Default: BSC.",
    )

    parser.add_argument(
        "--verify-readonly",
        action="store_true",
        help=(
            "Optional Stage 24 check: build client from encrypted credentials "
            "and call a read-only Bybit endpoint. Do not use in Stage 22.1.2."
        ),
    )

    return parser.parse_args()


def normalize_fund_code(value: str) -> str:
    code = (value or "").strip().lower()

    if code not in SUPPORTED_FUNDS:
        allowed = ", ".join(sorted(SUPPORTED_FUNDS))
        raise BybitCredentialsError(
            f"Unsupported fund_code={code!r}. Allowed: {allowed}"
        )

    return code


def prompt_required(label: str) -> str:
    value = input(label).strip()

    if not value:
        raise BybitCredentialsError(f"{label.strip(': ')} is required")

    return value


def prompt_optional(label: str) -> str | None:
    value = input(label).strip()
    return value or None


def get_fund_and_account(db, *, fund_code: str, coin: str, chain_type: str):
    fund = db.query(Fund).filter(Fund.code == fund_code).first()
    if fund is None:
        raise BybitCredentialsError(f"Fund not found: {fund_code}")

    account = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == fund.id,
            FundBybitAccount.coin == coin,
            FundBybitAccount.chain_type == chain_type,
            FundBybitAccount.is_active == True,
        )
        .first()
    )

    if account is None:
        raise BybitCredentialsError(
            f"Active fund_bybit_accounts row not found for "
            f"fund_code={fund_code}, coin={coin}, chain_type={chain_type}. "
            f"Run Bybit subaccount deposit address sync first."
        )

    return fund, account


def verify_readonly_if_requested(db, *, fund_id: int, enabled: bool) -> None:
    if not enabled:
        return

    client = get_active_fund_bybit_client(db, fund_id=fund_id)

    # Read-only API key information endpoint.
    # Do not print raw response because it may contain metadata about the key.
    client.get("/v5/user/query-api", {})

    account = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == fund_id,
            FundBybitAccount.coin == "USDT",
            FundBybitAccount.chain_type == "BSC",
            FundBybitAccount.is_active == True,
        )
        .first()
    )

    if account is not None:
        account.api_key_last_verified_at = utcnow()
        db.add(account)
        db.flush()


def main() -> int:
    load_dotenv()

    args = parse_args()
    fund_code = normalize_fund_code(args.fund_code)
    coin = args.coin.strip().upper()
    chain_type = args.chain_type.strip().upper()

    db = SessionLocal()

    try:
        fund, account = get_fund_and_account(
            db,
            fund_code=fund_code,
            coin=coin,
            chain_type=chain_type,
        )

        print("")
        print("Per-fund Bybit subaccount API credentials setup")
        print("------------------------------------------------")
        print(f"fund_code: {fund.code}")
        print(f"fund_id: {fund.id}")
        print(f"bybit_sub_uid: {account.bybit_sub_uid}")
        print(f"coin: {account.coin}")
        print(f"chain_type: {account.chain_type}")
        print("")
        print("Plain API key/secret will not be printed.")
        print("Values will be encrypted with BYBIT_API_ENC_KEY before DB write.")
        print("")

        api_key = prompt_required("API key: ")
        api_secret = getpass.getpass("API secret: ").strip()

        if not api_secret:
            raise BybitCredentialsError("API secret is required")

        api_key_label = prompt_optional("Key label / name, optional: ")
        api_permissions = prompt_optional("Permissions text, optional: ")
        api_ip_whitelist = prompt_optional("IP whitelist text, optional: ")

        result = set_fund_bybit_api_credentials(
            db,
            fund_code=fund.code,
            api_key=api_key,
            api_secret=api_secret,
            api_key_label=api_key_label,
            api_permissions=api_permissions,
            api_ip_whitelist=api_ip_whitelist,
            coin=coin,
            chain_type=chain_type,
        )

        if args.verify_readonly:
            verify_readonly_if_requested(
                db,
                fund_id=result.fund_id,
                enabled=True,
            )

        db.commit()

        print("")
        print("Bybit subaccount API credentials saved.")
        print("---------------------------------------")
        print(f"fund_code: {result.fund_code}")
        print(f"fund_id: {result.fund_id}")
        print(f"bybit_sub_uid: {result.bybit_sub_uid}")
        print(f"api_key_label: {result.api_key_label or ''}")
        print(f"api_permissions: {result.api_permissions or ''}")
        print(f"api_ip_whitelist: {result.api_ip_whitelist or ''}")
        print(f"api_key_is_active: {result.api_key_is_active}")
        print("api_key: encrypted, not shown")
        print("api_secret: encrypted, not shown")

        if args.verify_readonly:
            print("verify_readonly: OK")
        else:
            print("verify_readonly: skipped")

        return 0

    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())