from __future__ import annotations

from decimal import Decimal

import pytest

from app.bybit.transferable_balance import (
    BybitTransferableBalanceError,
    parse_unified_transferable_balance,
    query_unified_transferable_balance,
)


def _payload(
    *,
    transfer_balance: str | None = "100",
    transfer_safe_amount: str = "",
    ltv_transfer_safe_amount: str = "",
    extra_balance_fields: (
        dict | None
    ) = None,
) -> dict:
    balance = {
        "coin": "USDT",
        "walletBalance": "150",
        "transferBalance": (
            transfer_balance
        ),
        "transferSafeAmount": (
            transfer_safe_amount
        ),
        "ltvTransferSafeAmount": (
            ltv_transfer_safe_amount
        ),
    }

    balance.update(
        extra_balance_fields or {}
    )

    return {
        "retCode": 0,
        "retMsg": "success",
        "result": {
            "accountType": "UNIFIED",
            "balance": balance,
        },
    }


def test_transfer_balance_is_source_of_truth():
    result = (
        parse_unified_transferable_balance(
            _payload(
                transfer_balance="100",
            )
        )
    )

    assert (
        result.wallet_balance
        == Decimal("150")
    )
    assert (
        result.transfer_balance
        == Decimal("100")
    )
    assert (
        result.confirmed_transferable_amount
        == Decimal("100")
    )


def test_ltv_safe_amount_caps_transferable():
    result = (
        parse_unified_transferable_balance(
            _payload(
                transfer_balance="100",
                ltv_transfer_safe_amount="60",
            )
        )
    )

    assert (
        result.confirmed_transferable_amount
        == Decimal("60")
    )


def test_transfer_safe_amount_caps_transferable():
    result = (
        parse_unified_transferable_balance(
            _payload(
                transfer_balance="100",
                transfer_safe_amount="75",
            )
        )
    )

    assert (
        result.confirmed_transferable_amount
        == Decimal("75")
    )


def test_deprecated_available_to_withdraw_is_not_fallback():
    payload = _payload(
        transfer_balance=None,
        extra_balance_fields={
            "availableToWithdraw": "999",
        },
    )

    with pytest.raises(
        BybitTransferableBalanceError,
        match="transferBalance is required",
    ):
        parse_unified_transferable_balance(
            payload
        )


def test_query_uses_exact_unified_transfer_request():
    calls: list[
        tuple[str, dict]
    ] = []

    class Client:
        def get(
            self,
            path: str,
            params: dict,
        ) -> dict:
            calls.append(
                (
                    path,
                    dict(params),
                )
            )

            return _payload(
                transfer_balance="88"
            )

    result = (
        query_unified_transferable_balance(
            Client()
        )
    )

    assert (
        result.confirmed_transferable_amount
        == Decimal("88")
    )
    assert calls == [
        (
            (
                "/v5/asset/transfer/"
                "query-account-coin-balance"
            ),
            {
                "accountType": "UNIFIED",
                "coin": "USDT",
                "toAccountType": "FUND",
                "withLtvTransferSafeAmount": 1,
            },
        )
    ]