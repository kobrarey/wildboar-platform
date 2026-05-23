from __future__ import annotations

import argparse
import getpass
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.bybit.client import BybitApiError, BybitV5Client
from app.bybit.deposit_addresses import (
    BybitChainInfo,
    BybitDepositAddress,
    BybitSubMember,
    query_coin_chains,
    query_sub_members,
    validate_chain_deposit_enabled,
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


@dataclass(frozen=True)
class SyncRow:
    fund_code: str
    fund_id: int
    sub_uid: str
    subaccount_name: str | None
    coin: str
    chain: str | None
    chain_type: str
    deposit_address: str
    deposit_tag: str | None
    status: str


class SyncConfigError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync verified Bybit subaccount deposit addresses for Wild Boar funds."
    )

    parser.add_argument(
        "--list-subaccounts",
        action="store_true",
        help="List Bybit subaccounts and exit without writing to DB.",
    )

    parser.add_argument(
        "--coin",
        default="USDT",
        help="Coin to sync. Default: USDT.",
    )

    parser.add_argument(
        "--chain-type",
        required=False,
        default=None,
        help="Bybit chainType to sync, for example BSC. Required unless --list-subaccounts is used.",
    )

    parser.add_argument(
        "--map",
        dest="mapping_inline",
        default=None,
        help=(
            "Explicit mapping fund_code:sub_uid,... "
            "Example: btc_fund:123,defi_sniper:234,wb10:345,wb_test:456,wb_defi:567,wb_web3:678"
        ),
    )

    parser.add_argument(
        "--mapping-file",
        default=None,
        help=(
            "Path to local JSON mapping file. "
            "Example: {\"btc_fund\":\"123456\", ...}. No secrets should be stored there."
        ),
    )

    parser.add_argument(
        "--base-url",
        default="https://api.bybit.com",
        help="Bybit API base URL. Default: https://api.bybit.com",
    )

    parser.add_argument(
        "--recv-window-ms",
        type=int,
        default=30000,
        help="Bybit recv_window in milliseconds. Default: 30000.",
    )

    parser.add_argument(
        "--api-key-env",
        default="BYBIT_MASTER_API_KEY",
        help="Temporary env var name for master API key.",
    )

    parser.add_argument(
        "--api-secret-env",
        default="BYBIT_MASTER_API_SECRET",
        help="Temporary env var name for master API secret.",
    )

    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Do not ask interactively for API credentials; require temporary env vars.",
    )

    return parser.parse_args()


def _normalize_coin(value: str) -> str:
    coin = (value or "").strip().upper()
    if not coin:
        raise SyncConfigError("coin is empty")
    return coin


def _normalize_chain_type(value: str) -> str:
    chain_type = (value or "").strip()
    if not chain_type:
        raise SyncConfigError("chain_type is empty")
    return chain_type


def parse_mapping_inline(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}

    if not raw or not raw.strip():
        raise SyncConfigError("--map is empty")

    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue

        if ":" not in item:
            raise SyncConfigError(
                f"Invalid --map item={item!r}. Expected fund_code:sub_uid"
            )

        fund_code_raw, sub_uid_raw = item.split(":", 1)
        fund_code = fund_code_raw.strip().lower()
        sub_uid = sub_uid_raw.strip()

        if not fund_code:
            raise SyncConfigError(f"Invalid --map item={item!r}: empty fund_code")
        if not sub_uid:
            raise SyncConfigError(f"Invalid --map item={item!r}: empty sub_uid")

        if fund_code in mapping:
            raise SyncConfigError(f"Duplicate fund_code in --map: {fund_code}")

        mapping[fund_code] = sub_uid

    return mapping


def parse_mapping_file(path: str) -> dict[str, str]:
    p = Path(path)

    if not p.exists():
        raise SyncConfigError(f"Mapping file not found: {p}")

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SyncConfigError(f"Invalid mapping JSON: {p}: {exc}") from exc

    if not isinstance(data, dict):
        raise SyncConfigError("Mapping JSON must be an object fund_code -> sub_uid")

    mapping: dict[str, str] = {}
    for key, value in data.items():
        fund_code = str(key).strip().lower()
        sub_uid = str(value).strip()

        if not fund_code:
            raise SyncConfigError("Mapping JSON contains empty fund_code")
        if not sub_uid:
            raise SyncConfigError(f"Mapping JSON contains empty sub_uid for {fund_code}")

        if fund_code in mapping:
            raise SyncConfigError(f"Duplicate fund_code in mapping JSON: {fund_code}")

        mapping[fund_code] = sub_uid

    return mapping


def load_mapping(args: argparse.Namespace) -> dict[str, str]:
    has_inline = bool(args.mapping_inline and args.mapping_inline.strip())
    has_file = bool(args.mapping_file and args.mapping_file.strip())

    if has_inline and has_file:
        raise SyncConfigError("Use either --map or --mapping-file, not both")

    if not has_inline and not has_file:
        raise SyncConfigError("Provide explicit fund mapping via --map or --mapping-file")

    mapping = (
        parse_mapping_inline(args.mapping_inline)
        if has_inline
        else parse_mapping_file(args.mapping_file)
    )

    unknown = sorted(set(mapping) - SUPPORTED_FUNDS)
    missing = sorted(SUPPORTED_FUNDS - set(mapping))

    if unknown:
        raise SyncConfigError(
            "Mapping contains unsupported fund_code(s): " + ", ".join(unknown)
        )

    if missing:
        raise SyncConfigError(
            "Mapping is missing required fund_code(s): " + ", ".join(missing)
        )

    reverse: dict[str, str] = {}
    duplicates: list[str] = []
    for fund_code, sub_uid in mapping.items():
        if sub_uid in reverse:
            duplicates.append(f"{sub_uid} used by {reverse[sub_uid]} and {fund_code}")
        reverse[sub_uid] = fund_code

    if duplicates:
        raise SyncConfigError("Duplicate sub_uid in mapping: " + "; ".join(duplicates))

    return mapping


def get_master_credentials(args: argparse.Namespace) -> tuple[str, str]:
    api_key = (os.getenv(args.api_key_env) or "").strip()
    api_secret = (os.getenv(args.api_secret_env) or "").strip()

    if api_key and api_secret:
        return api_key, api_secret

    if args.no_prompt:
        missing = []
        if not api_key:
            missing.append(args.api_key_env)
        if not api_secret:
            missing.append(args.api_secret_env)
        raise SyncConfigError(
            "Missing temporary env var(s): "
            + ", ".join(missing)
            + ". Do not store master API credentials permanently."
        )

    if not api_key:
        api_key = input("Master API key: ").strip()

    if not api_secret:
        api_secret = getpass.getpass("Master API secret: ").strip()

    if not api_key:
        raise SyncConfigError("Master API key is empty")
    if not api_secret:
        raise SyncConfigError("Master API secret is empty")

    return api_key, api_secret


def _sub_member_name(item: BybitSubMember) -> str | None:
    return item.username or item.remark or None


def validate_mapping_subaccounts(
    *,
    mapping: dict[str, str],
    sub_members: list[BybitSubMember],
) -> dict[str, BybitSubMember]:
    by_uid = {item.uid: item for item in sub_members}

    missing: list[str] = []
    out: dict[str, BybitSubMember] = {}

    for fund_code, sub_uid in mapping.items():
        member = by_uid.get(str(sub_uid))
        if member is None:
            missing.append(f"{fund_code}:{sub_uid}")
            continue
        out[fund_code] = member

    if missing:
        raise SyncConfigError(
            "Mapped sub_uid(s) not found in Bybit subaccounts: " + ", ".join(missing)
        )

    return out


def _unique_nonempty(values: list[str | None]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for value in values:
        item = (value or "").strip()
        if not item:
            continue

        key = item.lower()
        if key in seen:
            continue

        seen.add(key)
        out.append(item)

    return out


def _extract_deposit_address_from_result(
    *,
    result: dict,
    sub_uid: str,
    coin: str,
    requested_chain_type: str,
    accepted_chain_values: list[str],
) -> BybitDepositAddress | None:
    candidates: list[dict] = []

    if isinstance(result.get("chains"), list):
        candidates.extend(result.get("chains") or [])
    elif isinstance(result.get("chains"), dict):
        candidates.append(result.get("chains"))

    if isinstance(result.get("rows"), list):
        candidates.extend(result.get("rows") or [])

    if isinstance(result.get("data"), list):
        candidates.extend(result.get("data") or [])

    if any(
        key in result
        for key in ["addressDeposit", "depositAddress", "address"]
    ):
        candidates.append(result)

    requested = requested_chain_type.strip()
    requested_lower = requested.lower()

    accepted = {
        x.strip().lower()
        for x in accepted_chain_values
        if x and x.strip()
    }

    for item in candidates:
        chain = str(item.get("chain") or "").strip()
        raw_chain_type = str(
            item.get("chainType")
            or item.get("chain_type")
            or item.get("chain_type_name")
            or ""
        ).strip()

        chain_lower = chain.lower()
        raw_chain_type_lower = raw_chain_type.lower()

        chain_ok = (
            not accepted
            or chain_lower in accepted
            or raw_chain_type_lower in accepted
            or chain_lower == requested_lower
            or raw_chain_type_lower == requested_lower
            or raw_chain_type_lower.startswith(requested_lower)
            or requested_lower in raw_chain_type_lower
        )

        if not chain_ok:
            continue

        address = str(
            item.get("addressDeposit")
            or item.get("depositAddress")
            or item.get("address")
            or ""
        ).strip()

        if not address:
            continue

        tag = (
            item.get("tagDeposit")
            or item.get("depositTag")
            or item.get("tag")
            or item.get("memo")
            or None
        )

        return BybitDepositAddress(
            sub_uid=str(sub_uid),
            coin=coin.strip().upper(),
            chain=chain or None,
            # Store our canonical chain_type for later DB lookup by chain_type='BSC'.
            chain_type=requested,
            address_deposit=address,
            tag_deposit=tag,
        )

    return None


def query_sub_member_deposit_address_with_chain_fallback(
    client: BybitV5Client,
    *,
    sub_uid: str,
    coin: str,
    requested_chain_type: str,
    chain_info: BybitChainInfo,
) -> BybitDepositAddress:
    coin_norm = coin.strip().upper()
    requested = requested_chain_type.strip()

    params_variants = [
        {
            "subMemberId": str(sub_uid),
            "coin": coin_norm,
        },
        {
            "subMemberId": str(sub_uid),
            "coin": coin_norm,
            "chainType": requested,
        },
    ]

    errors: list[str] = []

    for params in params_variants:
        label = params.get("chainType") or "WITHOUT chainType"
        print(f"Try deposit address sub_uid={sub_uid} chainType={label}...")

        try:
            data = client.get(
                "/v5/asset/deposit/query-sub-member-address",
                params,
            )
        except BybitApiError as exc:
            errors.append(f"{label}: {exc}")
            continue

        result = data.get("result", {}) or {}

        items: list[dict] = []

        chains = result.get("chains")
        if isinstance(chains, list):
            items.extend(chains)
        elif isinstance(chains, dict):
            items.append(chains)

        if isinstance(result.get("rows"), list):
            items.extend(result.get("rows") or [])

        if isinstance(result.get("data"), list):
            items.extend(result.get("data") or [])

        if any(
            key in result
            for key in ["addressDeposit", "depositAddress", "address"]
        ):
            items.append(result)

        for item in items:
            if not isinstance(item, dict):
                continue

            address = str(item.get("addressDeposit") or "").strip()
            if not address:
                address = str(item.get("depositAddress") or "").strip()
            if not address:
                address = str(item.get("address") or "").strip()

            if not address:
                continue

            returned_chain = str(item.get("chain") or "").strip()
            returned_chain_type = str(item.get("chainType") or "").strip()

            # Bybit returns chainType like "BSC (BEP20)", while our canonical local value is "BSC".
            chain_ok = (
                returned_chain == requested
                or returned_chain_type == requested
                or returned_chain_type.startswith(requested)
                or requested in returned_chain_type
                or returned_chain == (chain_info.chain or "")
            )

            if not chain_ok:
                continue

            return BybitDepositAddress(
                sub_uid=str(sub_uid),
                coin=coin_norm,
                chain=returned_chain or chain_info.chain,
                chain_type=requested,
                address_deposit=address,
                tag_deposit=(
                    item.get("tagDeposit")
                    or item.get("depositTag")
                    or item.get("tag")
                    or item.get("memo")
                    or None
                ),
            )

        errors.append(
            f"{label}: no addressDeposit found in result={json.dumps(result, ensure_ascii=False)[:1500]}"
        )

    raise SyncConfigError(
        f"Deposit address query failed for sub_uid={sub_uid}. "
        f"Errors: {' | '.join(errors)}"
    )


def get_funds_by_code(db: Session) -> dict[str, Fund]:
    rows = db.query(Fund).filter(Fund.code.in_(sorted(SUPPORTED_FUNDS))).all()
    out = {row.code: row for row in rows}

    missing = sorted(SUPPORTED_FUNDS - set(out))
    if missing:
        raise SyncConfigError("Funds not found in local DB: " + ", ".join(missing))

    return out


def _find_conflicting_active_row(
    db: Session,
    *,
    fund_id: int,
    sub_uid: str,
    coin: str,
    chain_type: str,
) -> FundBybitAccount | None:
    return (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.bybit_sub_uid == str(sub_uid),
            FundBybitAccount.coin == coin,
            FundBybitAccount.chain_type == chain_type,
            FundBybitAccount.is_active == True,
            FundBybitAccount.fund_id != fund_id,
        )
        .first()
    )


def upsert_fund_bybit_account(
    db: Session,
    *,
    fund: Fund,
    sub_member: BybitSubMember,
    address: BybitDepositAddress,
) -> FundBybitAccount:
    if not address.address_deposit:
        raise SyncConfigError(
            f"Empty deposit address for fund={fund.code} sub_uid={address.sub_uid}"
        )

    now = utcnow()
    coin = address.coin.upper()
    chain_type = address.chain_type

    conflict = _find_conflicting_active_row(
        db,
        fund_id=fund.id,
        sub_uid=address.sub_uid,
        coin=coin,
        chain_type=chain_type,
    )
    if conflict is not None:
        raise SyncConfigError(
            f"Active Bybit account conflict: sub_uid={address.sub_uid} "
            f"coin={coin} chain_type={chain_type} already belongs to fund_id={conflict.fund_id}"
        )

    row = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == fund.id,
            FundBybitAccount.coin == coin,
            FundBybitAccount.chain_type == chain_type,
            FundBybitAccount.is_active == True,
        )
        .with_for_update()
        .first()
    )

    if row is None:
        row = FundBybitAccount(
            fund_id=fund.id,
            bybit_sub_uid=address.sub_uid,
            bybit_subaccount_name=_sub_member_name(sub_member),
            coin=coin,
            chain=address.chain,
            chain_type=chain_type,
            deposit_address=address.address_deposit,
            deposit_tag=address.tag_deposit,
            is_active=True,
            last_verified_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return row

    row.bybit_sub_uid = address.sub_uid
    row.bybit_subaccount_name = _sub_member_name(sub_member)
    row.chain = address.chain
    row.deposit_address = address.address_deposit
    row.deposit_tag = address.tag_deposit
    row.last_verified_at = now
    row.updated_at = now

    db.add(row)
    db.flush()
    return row


def sync_deposit_addresses(
    *,
    client: BybitV5Client,
    mapping: dict[str, str],
    coin: str,
    chain_type: str,
) -> list[SyncRow]:
    coin_norm = _normalize_coin(coin)
    chain_type_norm = _normalize_chain_type(chain_type)

    print("Query Bybit subaccounts...")
    sub_members = query_sub_members(client)
    sub_members_by_fund = validate_mapping_subaccounts(
        mapping=mapping,
        sub_members=sub_members,
    )

    print(f"Query Bybit coin info coin={coin_norm}...")
    chains = query_coin_chains(client, coin=coin_norm)
    chain_info = validate_chain_deposit_enabled(
        chains,
        requested_chain_type=chain_type_norm,
    )

    # Use requested chain_type for the request, but store returned chainType from address endpoint.
    print(
        f"Using coin={coin_norm} chain_type={chain_type_norm} "
        f"coin_info_chain={chain_info.chain} coin_info_chainType={chain_info.chain_type}"
    )

    rows: list[SyncRow] = []

    with SessionLocal() as db:
        funds_by_code = get_funds_by_code(db)

        try:
            for fund_code in sorted(SUPPORTED_FUNDS):
                sub_uid = mapping[fund_code]
                fund = funds_by_code[fund_code]
                sub_member = sub_members_by_fund[fund_code]

                print(f"Query deposit address fund={fund_code} sub_uid={sub_uid}...")

                address = query_sub_member_deposit_address_with_chain_fallback(
                    client,
                    sub_uid=sub_uid,
                    coin=coin_norm,
                    requested_chain_type=chain_type_norm,
                    chain_info=chain_info,
                )

                upsert_fund_bybit_account(
                    db,
                    fund=fund,
                    sub_member=sub_member,
                    address=address,
                )

                rows.append(
                    SyncRow(
                        fund_code=fund_code,
                        fund_id=fund.id,
                        sub_uid=sub_uid,
                        subaccount_name=_sub_member_name(sub_member),
                        coin=address.coin,
                        chain=address.chain,
                        chain_type=address.chain_type,
                        deposit_address=address.address_deposit,
                        deposit_tag=address.tag_deposit,
                        status="upserted",
                    )
                )

            db.commit()

        except Exception:
            db.rollback()
            raise

    return rows


def list_subaccounts(*, client: BybitV5Client) -> list[BybitSubMember]:
    print("Query Bybit subaccounts...")
    return query_sub_members(client)


def print_subaccounts(sub_members: list[BybitSubMember]) -> None:
    print("")
    print("Bybit subaccounts:")
    print("uid | username | remark | status | member_type | account_type | account_mode")

    for item in sub_members:
        print(
            f"{item.uid} | "
            f"{item.username or ''} | "
            f"{item.remark or ''} | "
            f"{item.status if item.status is not None else ''} | "
            f"{item.member_type or ''} | "
            f"{item.account_type or ''} | "
            f"{item.account_mode or ''}"
        )

    print("")
    print("Delete temporary BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET now.")


def print_summary(rows: list[SyncRow]) -> None:
    print("")
    print("Bybit subaccount deposit address sync summary:")
    print("fund_code | sub_uid | chain | chain_type | deposit_address | tag | status")

    for row in rows:
        print(
            f"{row.fund_code} | {row.sub_uid} | {row.chain or ''} | {row.chain_type} | "
            f"{row.deposit_address} | {row.deposit_tag or ''} | {row.status}"
        )

    print("")
    print("Delete temporary BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET now.")


def main() -> int:
    load_dotenv()

    args = parse_args()

    try:
        if not args.list_subaccounts and not args.chain_type:
            raise SyncConfigError("--chain-type is required unless --list-subaccounts is used")

        api_key, api_secret = get_master_credentials(args)

        client = BybitV5Client(
            api_key=api_key,
            api_secret=api_secret,
            base_url=args.base_url,
            recv_window_ms=args.recv_window_ms,
        )

        if args.list_subaccounts:
            sub_members = list_subaccounts(client=client)
            print_subaccounts(sub_members)
            return 0

        mapping = load_mapping(args)

        rows = sync_deposit_addresses(
            client=client,
            mapping=mapping,
            coin=args.coin,
            chain_type=args.chain_type,
        )

    except (BybitApiError, SyncConfigError) as exc:
        print("")
        print(f"Sync failed: {exc}")
        print("")
        print("Delete temporary BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET now.")
        return 1

    print_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())