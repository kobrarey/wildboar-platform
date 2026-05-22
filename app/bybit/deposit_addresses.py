from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.bybit.client import BybitApiError, BybitV5Client


@dataclass(frozen=True)
class BybitSubMember:
    uid: str
    username: str | None = None
    status: int | None = None
    remark: str | None = None


@dataclass(frozen=True)
class BybitChainInfo:
    coin: str
    chain: str | None
    chain_type: str
    chain_deposit: str | None


@dataclass(frozen=True)
class BybitDepositAddress:
    sub_uid: str
    coin: str
    chain: str | None
    chain_type: str
    address_deposit: str
    tag_deposit: str | None


def query_sub_members(client: BybitV5Client) -> list[BybitSubMember]:
    data = client.get("/v5/user/query-sub-members")
    items = data.get("result", {}).get("subMembers", []) or []

    out: list[BybitSubMember] = []
    for item in items:
        uid = str(item.get("uid") or "").strip()
        if not uid:
            continue

        out.append(
            BybitSubMember(
                uid=uid,
                username=(item.get("username") or item.get("remark") or None),
                status=item.get("status"),
                remark=item.get("remark"),
            )
        )

    return out


def query_coin_chains(client: BybitV5Client, *, coin: str) -> list[BybitChainInfo]:
    coin_norm = coin.strip().upper()
    data = client.get("/v5/asset/coin/query-info", {"coin": coin_norm})
    rows = data.get("result", {}).get("rows", []) or []

    out: list[BybitChainInfo] = []

    for row in rows:
        if str(row.get("coin") or "").upper() != coin_norm:
            continue

        for chain in row.get("chains", []) or []:
            chain_type = str(chain.get("chainType") or "").strip()
            if not chain_type:
                continue

            out.append(
                BybitChainInfo(
                    coin=coin_norm,
                    chain=(chain.get("chain") or None),
                    chain_type=chain_type,
                    chain_deposit=(chain.get("chainDeposit") or None),
                )
            )

    return out


def validate_chain_deposit_enabled(
    chains: list[BybitChainInfo],
    *,
    requested_chain_type: str,
) -> BybitChainInfo:
    requested = requested_chain_type.strip()

    for item in chains:
        # Bybit docs are not fully consistent: some deposit endpoints call the input
        # chainType but say to use the coin-info "chain" value. Accept exact match
        # against either field, but store both returned fields.
        if item.chain_type == requested or item.chain == requested:
            if item.chain_deposit != "1":
                raise BybitApiError(
                    f"Deposit is not enabled for coin={item.coin} chain_type={requested} "
                    f"chainDeposit={item.chain_deposit}"
                )
            return item

    available = ", ".join(
        f"{x.chain_type}(chain={x.chain}, deposit={x.chain_deposit})"
        for x in chains
    )
    raise BybitApiError(
        f"Requested chain_type={requested} not found or unsupported. Available: {available}"
    )


def query_sub_member_deposit_address(
    client: BybitV5Client,
    *,
    sub_uid: str,
    coin: str,
    chain_type: str,
) -> BybitDepositAddress:
    coin_norm = coin.strip().upper()
    sub_uid_norm = str(sub_uid).strip()
    chain_type_norm = chain_type.strip()

    data = client.get(
        "/v5/asset/deposit/query-sub-member-address",
        {
            "subMemberId": sub_uid_norm,
            "coin": coin_norm,
            "chainType": chain_type_norm,
        },
    )

    result = data.get("result", {}) or {}

    # Expected shape is usually {coin, chains:[{chainType,addressDeposit,tagDeposit,chain,...}]}
    # Keep a fallback for single-chain object shape to fail less silently if Bybit changes envelope.
    chains: list[dict[str, Any]]
    if isinstance(result.get("chains"), list):
        chains = result.get("chains") or []
    else:
        chains = [result]

    selected: dict[str, Any] | None = None
    for item in chains:
        returned_chain_type = str(item.get("chainType") or "").strip()
        returned_chain = str(item.get("chain") or "").strip()

        if returned_chain_type == chain_type_norm or returned_chain == chain_type_norm:
            selected = item
            break

    if selected is None and len(chains) == 1:
        selected = chains[0]

    if selected is None:
        available = ", ".join(
            f"{x.get('chainType')}(chain={x.get('chain')})" for x in chains
        )
        raise BybitApiError(
            f"Deposit address not found for sub_uid={sub_uid_norm} coin={coin_norm} "
            f"chain_type={chain_type_norm}. Available: {available}"
        )

    address = str(selected.get("addressDeposit") or "").strip()
    if not address:
        raise BybitApiError(
            f"Empty deposit address for sub_uid={sub_uid_norm} coin={coin_norm} "
            f"chain_type={chain_type_norm}"
        )

    returned_chain_type = str(selected.get("chainType") or chain_type_norm).strip()
    returned_chain = selected.get("chain") or None

    if returned_chain_type != chain_type_norm and returned_chain != chain_type_norm:
        raise BybitApiError(
            f"Deposit address chain mismatch for sub_uid={sub_uid_norm}: "
            f"requested={chain_type_norm}, returned_chain_type={returned_chain_type}, "
            f"returned_chain={returned_chain}"
        )

    return BybitDepositAddress(
        sub_uid=sub_uid_norm,
        coin=coin_norm,
        chain=returned_chain,
        chain_type=returned_chain_type,
        address_deposit=address,
        tag_deposit=(selected.get("tagDeposit") or None),
    )