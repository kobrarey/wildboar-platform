from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Literal
import logging
import uuid

from app.bybit.client import BybitApiError, BybitV5Client

log = logging.getLogger("app.bybit.asset_flows")


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
class BybitAccountCoinBalance:
    account_type: str
    coin: str
    member_id: str | None
    wallet_balance: Decimal
    transfer_balance: Decimal
    transfer_safe_amount: Decimal | None
    ltv_transfer_safe_amount: Decimal | None
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


@dataclass(frozen=True)
class BybitCoinChainInfo:
    coin: str
    chain: str
    withdraw_fee: Decimal
    withdraw_min: Decimal
    min_accuracy: int
    chain_withdraw: str | None
    withdraw_percentage_fee: Decimal | None
    withdraw_max: Decimal | None
    raw: dict[str, Any]


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _canonical_uuid(value: str, *, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise BybitAssetFlowError(f"{field_name} is required")

    try:
        parsed = uuid.UUID(clean)
    except ValueError as exc:
        raise BybitAssetFlowError(f"{field_name} must be UUID") from exc

    return str(parsed)


AssetAmountRounding = Literal["up", "down", "exact_or_down"]


def format_bybit_asset_amount(
    amount: Decimal,
    precision: int,
    rounding: AssetAmountRounding,
) -> str:
    if precision is None:
        raise BybitAssetFlowError("Asset amount precision is required")

    precision = int(precision)
    if precision < 0:
        raise BybitAssetFlowError(f"Asset amount precision is invalid: {precision}")

    value = _dec(amount)
    if value <= 0:
        raise BybitAssetFlowError(f"Asset amount must be positive: {value}")

    rounding_mode = {
        "up": ROUND_UP,
        "down": ROUND_DOWN,
        "exact_or_down": ROUND_DOWN,
    }.get(rounding)

    if rounding_mode is None:
        raise BybitAssetFlowError(f"Unsupported asset amount rounding: {rounding}")

    quantum = Decimal("1").scaleb(-precision)
    rounded = value.quantize(quantum, rounding=rounding_mode)

    if rounded <= 0:
        raise BybitAssetFlowError(
            f"Asset amount rounded to non-positive value: amount={value}, "
            f"precision={precision}, rounding={rounding}"
        )

    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")

    if not text or "E" in text.upper():
        raise BybitAssetFlowError(f"Invalid formatted asset amount: {text}")

    return text


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


def _balance_row(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")

    if isinstance(result, dict):
        for key in ("list", "rows", "data", "balance"):
            value = result.get(key)
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict):
                    return first
            if isinstance(value, dict):
                return value

        return result

    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return first

    return {}


def _optional_decimal(row: dict[str, Any], key: str) -> Decimal | None:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return None
    return _dec(value)


def query_account_coin_balance(
    client: BybitV5Client,
    *,
    account_type: str,
    coin: str,
    member_id: str | None = None,
    to_member_id: str | None = None,
    to_account_type: str | None = None,
    with_transfer_safe_amount: bool = True,
    with_ltv_transfer_safe_amount: bool = True,
) -> BybitAccountCoinBalance:
    clean_account_type = str(account_type or "").strip().upper()
    clean_coin = str(coin or "").strip().upper()

    if not clean_account_type:
        raise BybitAssetFlowError("account_type is required")
    if clean_coin != "USDT":
        raise BybitAssetFlowError("Only USDT account coin balance is supported")

    params: dict[str, Any] = {
        "accountType": clean_account_type,
        "coin": clean_coin,
        "withTransferSafeAmount": 1 if with_transfer_safe_amount else 0,
        "withLtvTransferSafeAmount": 1 if with_ltv_transfer_safe_amount else 0,
    }

    if member_id:
        params["memberId"] = str(member_id).strip()
    if to_member_id:
        params["toMemberId"] = str(to_member_id).strip()
    if to_account_type:
        params["toAccountType"] = str(to_account_type).strip().upper()

    raw = client.get("/v5/asset/transfer/query-account-coin-balance", params)
    row = _balance_row(raw)

    return BybitAccountCoinBalance(
        account_type=str(row.get("accountType") or clean_account_type).strip().upper(),
        coin=str(row.get("coin") or clean_coin).strip().upper(),
        member_id=(
            str(row.get("memberId") or member_id).strip()
            if (row.get("memberId") or member_id)
            else None
        ),
        wallet_balance=_dec(row.get("walletBalance")),
        transfer_balance=_dec(row.get("transferBalance")),
        transfer_safe_amount=_optional_decimal(row, "transferSafeAmount"),
        ltv_transfer_safe_amount=_optional_decimal(row, "ltvTransferSafeAmount"),
        raw=row,
    )


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


def _request_id_from(row: dict[str, Any]) -> str | None:
    for key in ("requestId", "request_id", "withdrawalRequestId"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _find_coin_chain_info_row(
    rows: list[dict[str, Any]],
    *,
    coin: str,
    chain: str,
) -> dict[str, Any] | None:
    clean_coin = str(coin or "").strip().upper()
    clean_chain = str(chain or "").strip().upper()

    for coin_row in rows:
        row_coin = str(coin_row.get("coin") or "").strip().upper()
        if row_coin and row_coin != clean_coin:
            continue

        chains = coin_row.get("chains") or coin_row.get("chain")
        if isinstance(chains, list):
            for chain_row in chains:
                if not isinstance(chain_row, dict):
                    continue
                row_chain = str(chain_row.get("chain") or "").strip().upper()
                if row_chain == clean_chain:
                    merged = dict(chain_row)
                    merged["coin"] = row_coin or clean_coin
                    return merged

        row_chain = str(coin_row.get("chain") or "").strip().upper()
        if row_chain == clean_chain:
            return coin_row

    return None


def _parse_min_accuracy(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None

    text = str(value).strip()

    try:
        decimal_value = Decimal(text)
    except Exception:
        return None

    if decimal_value < 0:
        return None

    if decimal_value >= 1 and decimal_value == decimal_value.to_integral_value():
        return int(decimal_value)

    exponent = decimal_value.normalize().as_tuple().exponent
    if exponent < 0:
        return abs(int(exponent))

    return 0


def query_coin_info(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
    chain: str = "BSC",
) -> BybitCoinChainInfo:
    clean_coin = str(coin or "").strip().upper()
    clean_chain = str(chain or "").strip().upper()

    if clean_coin != "USDT":
        raise BybitAssetFlowError("Only USDT coin info is supported")
    if clean_chain != "BSC":
        raise BybitAssetFlowError("Only BSC chain info is supported")

    raw = client.get(
        "/v5/asset/coin/query-info",
        {
            "coin": clean_coin,
        },
    )
    rows = _result_list(raw)
    row = _find_coin_chain_info_row(
        rows,
        coin=clean_coin,
        chain=clean_chain,
    )

    if row is None:
        raise BybitAssetFlowError(f"Coin chain info not found: {clean_coin}/{clean_chain}")

    min_accuracy = _parse_min_accuracy(row.get("minAccuracy"))
    if min_accuracy is None:
        raise BybitAssetFlowError(
            f"Coin chain minAccuracy is missing or invalid: {clean_coin}/{clean_chain}"
        )

    return BybitCoinChainInfo(
        coin=clean_coin,
        chain=clean_chain,
        withdraw_fee=_dec(row.get("withdrawFee")),
        withdraw_min=_dec(row.get("withdrawMin")),
        min_accuracy=int(min_accuracy),
        chain_withdraw=(
            str(row.get("chainWithdraw"))
            if row.get("chainWithdraw") is not None
            else None
        ),
        withdraw_percentage_fee=_optional_decimal(row, "withdrawPercentageFee"),
        withdraw_max=_optional_decimal(row, "withdrawMax"),
        raw=row,
    )


def _validate_withdrawal_request_id(request_id: str) -> str:
    clean_request_id = str(request_id or "").strip()
    if not clean_request_id:
        raise BybitAssetFlowError("request_id is required")
    if len(clean_request_id) > 32:
        raise BybitAssetFlowError("request_id must be <= 32 chars")
    if not clean_request_id.isalnum():
        raise BybitAssetFlowError("request_id must be alphanumeric")

    return clean_request_id


def _withdrawal_result_from_row(
    row: dict[str, Any],
    *,
    fallback_request_id: str | None = None,
) -> BybitWithdrawalResult:
    request_id = (
        _request_id_from(row)
        or str(fallback_request_id or "").strip()
        or str(_withdrawal_id_from(row) or "").strip()
    )

    return BybitWithdrawalResult(
        request_id=request_id,
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
    amount_str: str | None = None,
    amount_precision: int | None = None,
    amount_rounding: AssetAmountRounding = "up",
) -> BybitUniversalTransferResult:
    clean_transfer_id = _canonical_uuid(transfer_id, field_name="transfer_id")
    clean_coin = str(coin or "").strip().upper()
    raw_amount = _dec(amount_usdt)

    if clean_coin != "USDT":
        raise BybitAssetFlowError("Only USDT universal transfer is supported")
    if raw_amount <= 0:
        raise BybitAssetFlowError("Universal transfer amount must be positive")

    if amount_str is not None:
        clean_amount_str = str(amount_str).strip()
        if not clean_amount_str:
            raise BybitAssetFlowError("Universal transfer amount_str is empty")
        if "E" in clean_amount_str.upper():
            raise BybitAssetFlowError(
                f"Universal transfer amount_str uses scientific notation: {clean_amount_str}"
            )
    else:
        if amount_precision is None:
            raise BybitAssetFlowError(
                "Universal transfer amount_precision is required when amount_str is not provided"
            )
        clean_amount_str = format_bybit_asset_amount(
            raw_amount,
            precision=int(amount_precision),
            rounding=amount_rounding,
        )

    amount = _dec(clean_amount_str)
    if amount <= 0:
        raise BybitAssetFlowError("Universal transfer formatted amount must be positive")
    if not str(from_member_id or "").strip():
        raise BybitAssetFlowError("from_member_id is required")
    if not str(to_member_id or "").strip():
        raise BybitAssetFlowError("to_member_id is required")

    payload = {
        "transferId": clean_transfer_id,
        "coin": clean_coin,
        "amount": clean_amount_str,
        "fromMemberId": str(from_member_id).strip(),
        "toMemberId": str(to_member_id).strip(),
        "fromAccountType": str(from_account_type).strip(),
        "toAccountType": str(to_account_type).strip(),
    }

    log.info(
        (
            "Bybit Universal Transfer POST safe payload summary: "
            "path=%s coin=%s amount=%s amount_precision=%s "
            "from_account_type=%s to_account_type=%s transfer_id=%s"
        ),
        "/v5/asset/transfer/universal-transfer",
        clean_coin,
        clean_amount_str,
        amount_precision if amount_precision is not None else "provided_amount_str",
        str(from_account_type).strip().upper(),
        str(to_account_type).strip().upper(),
        clean_transfer_id,
    )

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
    account_type: str = "FUND",
    amount_str: str | None = None,
    amount_precision: int | None = None,
    timestamp_ms: int | None = None,
    force_chain: int = 1,
) -> BybitWithdrawalResult:
    clean_request_id = _validate_withdrawal_request_id(request_id)
    clean_coin = str(coin or "").strip().upper()
    clean_chain = str(chain or "").strip().upper()
    clean_address = str(address or "").strip()
    raw_amount = _dec(amount_usdt)

    if clean_coin != "USDT":
        raise BybitAssetFlowError("Only USDT withdrawal is supported")
    if clean_chain != "BSC":
        raise BybitAssetFlowError("Only BSC withdrawal is supported")
    if not clean_address:
        raise BybitAssetFlowError("withdrawal address is required")
    if raw_amount <= 0:
        raise BybitAssetFlowError("Withdrawal amount must be positive")

    if amount_str is not None:
        clean_amount_str = str(amount_str).strip()
        if not clean_amount_str:
            raise BybitAssetFlowError("Withdrawal amount_str is empty")
        if "E" in clean_amount_str.upper():
            raise BybitAssetFlowError(
                f"Withdrawal amount_str uses scientific notation: {clean_amount_str}"
            )
    else:
        if amount_precision is None:
            raise BybitAssetFlowError(
                "Withdrawal amount_precision is required when amount_str is not provided"
            )
        clean_amount_str = format_bybit_asset_amount(
            raw_amount,
            precision=int(amount_precision),
            rounding="down",
        )

    amount = _dec(clean_amount_str)
    if amount <= 0:
        raise BybitAssetFlowError("Withdrawal formatted amount must be positive")

    clean_timestamp_ms = int(timestamp_ms) if timestamp_ms is not None else None
    if clean_timestamp_ms is None or clean_timestamp_ms <= 0:
        raise BybitAssetFlowError("timestamp_ms is required for withdrawal")

    clean_force_chain = int(force_chain)
    if clean_force_chain != 1:
        raise BybitAssetFlowError("forceChain must be 1 for external BSC withdrawal")

    payload = {
        "requestId": clean_request_id,
        "coin": clean_coin,
        "chain": clean_chain,
        "address": clean_address,
        "amount": clean_amount_str,
        "timestamp": clean_timestamp_ms,
        "forceChain": 1,
        "feeType": int(fee_type),
        "accountType": str(account_type).strip().upper(),
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

    return _withdrawal_result_from_row(row, fallback_request_id=clean_request_id)


def list_master_withdrawals(
    client: BybitV5Client,
    *,
    coin: str | None = None,
    start_time_ms: int | None = None,
    end_time_ms: int | None = None,
    limit: int = 50,
) -> list[BybitWithdrawalResult]:
    clean_params: dict[str, Any] = {
        "limit": int(limit),
    }

    if coin:
        clean_params["coin"] = str(coin).strip().upper()

    if start_time_ms is not None:
        clean_params["startTime"] = int(start_time_ms)

    if end_time_ms is not None:
        clean_params["endTime"] = int(end_time_ms)

    raw = client.get("/v5/asset/withdraw/query-record", clean_params)
    rows = _result_list(raw)

    return [
        _withdrawal_result_from_row(row)
        for row in rows
        if _withdrawal_id_from(row) or _request_id_from(row)
    ]


def cancel_master_withdrawal(
    client: BybitV5Client,
    *,
    withdrawal_id: str,
) -> dict[str, Any]:
    clean_withdrawal_id = str(withdrawal_id or "").strip()
    if not clean_withdrawal_id:
        raise BybitAssetFlowError("withdrawal_id is required")

    return client.post(
        "/v5/asset/withdraw/cancel",
        {
            "id": clean_withdrawal_id,
        },
    )
