from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from app.bybit.client import BybitV5Client


ZERO = Decimal("0")


class BybitTransferableBalanceError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class BybitTransferableBalance:
    account_type: str
    destination_account_type: str
    coin: str

    wallet_balance: Decimal | None
    transfer_balance: Decimal
    transfer_safe_amount: Decimal | None
    ltv_transfer_safe_amount: Decimal | None

    confirmed_transferable_amount: Decimal
    source_endpoint: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)

        for key in (
            "wallet_balance",
            "transfer_balance",
            "transfer_safe_amount",
            "ltv_transfer_safe_amount",
            "confirmed_transferable_amount",
        ):
            value = result[key]

            result[key] = (
                str(value)
                if value is not None
                else None
            )

        return result


def _decimal(
    value: Any,
    *,
    field_name: str,
    required: bool,
) -> Decimal | None:
    if value is None or value == "":
        if required:
            raise BybitTransferableBalanceError(
                f"{field_name} is required"
            )

        return None

    if isinstance(value, bool):
        raise BybitTransferableBalanceError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise BybitTransferableBalanceError(
            f"{field_name} must not be float"
        )

    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise BybitTransferableBalanceError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise BybitTransferableBalanceError(
            f"{field_name} must be finite"
        )

    if result < ZERO:
        raise BybitTransferableBalanceError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _required_dict(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BybitTransferableBalanceError(
            f"{field_name} must be a dict"
        )

    return dict(value)


def parse_unified_transferable_balance(
    payload: dict[str, Any],
    *,
    expected_coin: str = "USDT",
    destination_account_type: str = "FUND",
) -> BybitTransferableBalance:
    if not isinstance(payload, dict):
        raise BybitTransferableBalanceError(
            "Bybit response must be a dict"
        )

    ret_code = payload.get("retCode")

    if ret_code not in (None, 0, "0"):
        raise BybitTransferableBalanceError(
            "Bybit transferable balance "
            f"request failed: retCode={ret_code}, "
            f"retMsg={payload.get('retMsg')}"
        )

    result = _required_dict(
        payload.get("result"),
        field_name="result",
    )
    balance = _required_dict(
        result.get("balance"),
        field_name="result.balance",
    )

    normalized_coin = str(
        expected_coin
    ).strip().upper()

    response_coin = str(
        balance.get("coin")
        or ""
    ).strip().upper()

    if response_coin != normalized_coin:
        raise BybitTransferableBalanceError(
            "Bybit balance coin mismatch: "
            f"expected={normalized_coin}, "
            f"actual={response_coin or None}"
        )

    account_type = str(
        result.get("accountType")
        or ""
    ).strip().upper()

    if account_type != "UNIFIED":
        raise BybitTransferableBalanceError(
            "Bybit balance accountType "
            "must be UNIFIED"
        )

    wallet_balance = _decimal(
        balance.get("walletBalance"),
        field_name="walletBalance",
        required=False,
    )
    transfer_balance = _decimal(
        balance.get("transferBalance"),
        field_name="transferBalance",
        required=True,
    )
    transfer_safe_amount = _decimal(
        balance.get("transferSafeAmount"),
        field_name="transferSafeAmount",
        required=False,
    )
    ltv_transfer_safe_amount = _decimal(
        balance.get(
            "ltvTransferSafeAmount"
        ),
        field_name=(
            "ltvTransferSafeAmount"
        ),
        required=False,
    )

    assert transfer_balance is not None

    conservative_candidates = [
        transfer_balance,
    ]

    if transfer_safe_amount is not None:
        conservative_candidates.append(
            transfer_safe_amount
        )

    if (
        ltv_transfer_safe_amount
        is not None
    ):
        conservative_candidates.append(
            ltv_transfer_safe_amount
        )

    confirmed_transferable_amount = min(
        conservative_candidates
    )

    return BybitTransferableBalance(
        account_type=account_type,
        destination_account_type=str(
            destination_account_type
        ).strip().upper(),
        coin=normalized_coin,
        wallet_balance=wallet_balance,
        transfer_balance=transfer_balance,
        transfer_safe_amount=(
            transfer_safe_amount
        ),
        ltv_transfer_safe_amount=(
            ltv_transfer_safe_amount
        ),
        confirmed_transferable_amount=(
            confirmed_transferable_amount
        ),
        source_endpoint=(
            "/v5/asset/transfer/"
            "query-account-coin-balance"
        ),
        raw=dict(payload),
    )


def query_unified_transferable_balance(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
    destination_account_type: str = "FUND",
) -> BybitTransferableBalance:
    normalized_coin = str(
        coin
    ).strip().upper()
    normalized_destination = str(
        destination_account_type
    ).strip().upper()

    if not normalized_coin:
        raise BybitTransferableBalanceError(
            "coin must not be empty"
        )

    if not normalized_destination:
        raise BybitTransferableBalanceError(
            "destination_account_type "
            "must not be empty"
        )

    payload = client.get(
        (
            "/v5/asset/transfer/"
            "query-account-coin-balance"
        ),
        {
            "accountType": "UNIFIED",
            "coin": normalized_coin,
            "toAccountType": (
                normalized_destination
            ),
            "withLtvTransferSafeAmount": 1,
        },
    )

    return parse_unified_transferable_balance(
        payload,
        expected_coin=normalized_coin,
        destination_account_type=(
            normalized_destination
        ),
    )