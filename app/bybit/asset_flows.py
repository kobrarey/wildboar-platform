from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.bybit.client import BybitApiError, BybitV5Client


class BybitAssetFlowError(RuntimeError):
    pass


@dataclass(frozen=True)
class BybitUniversalTransferResult:
    transfer_id: str
    coin: str
    amount_usdt: Decimal
    from_member_id: str
    to_member_id: str
    from_account_type: str
    to_account_type: str
    status: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class BybitWithdrawalResult:
    request_id: str
    withdrawal_id: str | None
    coin: str
    chain: str
    address: str
    amount_usdt: Decimal
    fee_type: int
    status: str | None
    tx_hash: str | None
    raw: dict[str, Any]


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    return {}


def _result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")

    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]

    if isinstance(result, dict):
        for key in ("list", "rows", "data"):
            value = result.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]

    return []


def _first_matching(
    rows: list[dict[str, Any]],
    *,
    field_names: tuple[str, ...],
    expected: str,
) -> dict[str, Any] | None:
    expected_clean = str(expected or "").strip()
    for row in rows:
        for field in field_names:
            value = str(row.get(field) or "").strip()
            if value and value == expected_clean:
                return row
    return None


def _status_from(row: dict[str, Any]) -> str | None:
    for key in ("status", "transferStatus", "withdrawStatus", "state"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _tx_hash_from(row: dict[str, Any]) -> str | None:
    for key in ("txID", "txId", "txid", "txHash", "transactionHash"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _withdrawal_id_from(row: dict[str, Any]) -> str | None:
    for key in ("withdrawalId", "withdrawId", "id"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def create_universal_transfer(
    client: BybitV5Client,
    *,
    transfer_id: str,
    coin: str,
    amount_usdt: Decimal,
    from_member_id: str,
    to_member_id: str,
    from_account_type: str = "UNIFIED",
    to_account_type: str = "UNIFIED",
) -> BybitUniversalTransferResult:
    clean_transfer_id = str(transfer_id or "").strip()
    clean_coin = str(coin or "").strip().upper()
    amount = _dec(amount_usdt)

    if not clean_transfer_id:
        raise BybitAssetFlowError("transfer_id is required")
    if clean_coin != "USDT":
        raise BybitAssetFlowError("Only USDT universal transfer is supported")
    if amount <= 0:
        raise BybitAssetFlowError("Universal transfer amount must be positive")
    if not str(from_member_id or "").strip():
        raise BybitAssetFlowError("from_member_id is required")
    if not str(to_member_id or "").strip():
        raise BybitAssetFlowError("to_member_id is required")

    payload = {
        "transferId": clean_transfer_id,
        "coin": clean_coin,
        "amount": str(amount),
        "fromMemberId": str(from_member_id).strip(),
        "toMemberId": str(to_member_id).strip(),
        "fromAccountType": str(from_account_type).strip(),
        "toAccountType": str(to_account_type).strip(),
    }

    raw = client.post("/v5/asset/transfer/universal-transfer", payload)
    row = _result_dict(raw)

    return BybitUniversalTransferResult(
        transfer_id=clean_transfer_id,
        coin=clean_coin,
        amount_usdt=amount,
        from_member_id=str(from_member_id).strip(),
        to_member_id=str(to_member_id).strip(),
        from_account_type=str(from_account_type).strip(),
        to_account_type=str(to_account_type).strip(),
        status=_status_from(row),
        raw=raw,
    )


def query_universal_transfer(
    client: BybitV5Client,
    *,
    transfer_id: str,
) -> BybitUniversalTransferResult | None:
    clean_transfer_id = str(transfer_id or "").strip()
    if not clean_transfer_id:
        raise BybitAssetFlowError("transfer_id is required")

    raw = client.get(
        "/v5/asset/transfer/query-universal-transfer-list",
        {"transferId": clean_transfer_id},
    )
    rows = _result_list(raw)
    row = _first_matching(
        rows,
        field_names=("transferId", "transfer_id"),
        expected=clean_transfer_id,
    )
    if row is None:
        return None

    return BybitUniversalTransferResult(
        transfer_id=clean_transfer_id,
        coin=str(row.get("coin") or "").strip().upper(),
        amount_usdt=_dec(row.get("amount") or row.get("amount_usdt")),
        from_member_id=str(row.get("fromMemberId") or row.get("from_member_id") or "").strip(),
        to_member_id=str(row.get("toMemberId") or row.get("to_member_id") or "").strip(),
        from_account_type=str(row.get("fromAccountType") or row.get("from_account_type") or "").strip(),
        to_account_type=str(row.get("toAccountType") or row.get("to_account_type") or "").strip(),
        status=_status_from(row),
        raw=row,
    )


def create_master_withdrawal(
    client: BybitV5Client,
    *,
    request_id: str,
    coin: str,
    chain: str,
    address: str,
    amount_usdt: Decimal,
    fee_type: int,
    account_type: str = "UNIFIED",
) -> BybitWithdrawalResult:
    clean_request_id = str(request_id or "").strip()
    clean_coin = str(coin or "").strip().upper()
    clean_chain = str(chain or "").strip().upper()
    clean_address = str(address or "").strip()
    amount = _dec(amount_usdt)

    if not clean_request_id:
        raise BybitAssetFlowError("request_id is required")
    if clean_coin != "USDT":
        raise BybitAssetFlowError("Only USDT withdrawal is supported")
    if clean_chain != "BSC":
        raise BybitAssetFlowError("Only BSC withdrawal is supported")
    if not clean_address:
        raise BybitAssetFlowError("withdrawal address is required")
    if amount <= 0:
        raise BybitAssetFlowError("Withdrawal amount must be positive")

    payload = {
        "requestId": clean_request_id,
        "coin": clean_coin,
        "chain": clean_chain,
        "address": clean_address,
        "amount": str(amount),
        "feeType": int(fee_type),
        "accountType": str(account_type).strip(),
    }

    raw = client.post("/v5/asset/withdraw/create", payload)
    row = _result_dict(raw)

    return BybitWithdrawalResult(
        request_id=clean_request_id,
        withdrawal_id=_withdrawal_id_from(row),
        coin=clean_coin,
        chain=clean_chain,
        address=clean_address,
        amount_usdt=amount,
        fee_type=int(fee_type),
        status=_status_from(row),
        tx_hash=_tx_hash_from(row),
        raw=raw,
    )


def query_master_withdrawal(
    client: BybitV5Client,
    *,
    request_id: str,
) -> BybitWithdrawalResult | None:
    clean_request_id = str(request_id or "").strip()
    if not clean_request_id:
        raise BybitAssetFlowError("request_id is required")

    raw = client.get(
        "/v5/asset/withdraw/query-record",
        {"requestId": clean_request_id},
    )
    rows = _result_list(raw)
    row = _first_matching(
        rows,
        field_names=("requestId", "request_id"),
        expected=clean_request_id,
    )
    if row is None:
        return None

    return BybitWithdrawalResult(
        request_id=clean_request_id,
        withdrawal_id=_withdrawal_id_from(row),
        coin=str(row.get("coin") or "").strip().upper(),
        chain=str(row.get("chain") or "").strip().upper(),
        address=str(row.get("address") or "").strip(),
        amount_usdt=_dec(row.get("amount") or row.get("amount_usdt")),
        fee_type=int(row.get("feeType") or row.get("fee_type") or 0),
        status=_status_from(row),
        tx_hash=_tx_hash_from(row),
        raw=row,
    )