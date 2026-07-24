from __future__ import annotations

import inspect
from decimal import Decimal

import pytest
from web3 import Web3

from app.config import settings
from app.settlement.bsc_balance_service import (
    BscBalanceReadError,
    read_bsc_block_number,
    read_bsc_usdt_balance,
)
from app.settlement.negative_payout_flow import (
    _refresh_live_balances_after_confirmed_payouts,
)


CONTRACT_ADDRESS = (
    "0x1111111111111111111111111111111111111111"
)
WALLET_ADDRESS = (
    "0x2222222222222222222222222222222222222222"
)


class FakeEth:
    def __init__(
        self,
        *,
        block_number: int = 123456,
        call_result: bytes | str | None = None,
        call_error: Exception | None = None,
    ):
        self.block_number = block_number
        self.call_result = (
            call_result
            if call_result is not None
            else (1_234_567).to_bytes(
                32,
                byteorder="big",
            )
        )
        self.call_error = call_error
        self.calls = []

    def call(
        self,
        transaction,
        block_identifier,
    ):
        self.calls.append(
            {
                "transaction": transaction,
                "block_identifier": (
                    block_identifier
                ),
            }
        )

        if self.call_error is not None:
            raise self.call_error

        return self.call_result


class FakeWeb3:
    def __init__(self, eth: FakeEth):
        self.eth = eth


def configure_usdt(
    monkeypatch,
    *,
    decimals: int = 6,
):
    monkeypatch.setattr(
        settings,
        "USDT_BSC_ADDRESS",
        CONTRACT_ADDRESS,
    )
    monkeypatch.setattr(
        settings,
        "BSC_USDT_CONTRACT",
        "",
    )
    monkeypatch.setattr(
        settings,
        "BSC_USDT_DECIMALS",
        decimals,
    )


def test_read_bsc_block_number():
    w3 = FakeWeb3(
        FakeEth(block_number=987654)
    )

    assert (
        read_bsc_block_number(w3)
        == 987654
    )


def test_read_bsc_usdt_balance_at_pinned_block(
    monkeypatch,
):
    configure_usdt(monkeypatch)

    eth = FakeEth(
        call_result=(1_234_567).to_bytes(
            32,
            byteorder="big",
        )
    )
    w3 = FakeWeb3(eth)

    balance = read_bsc_usdt_balance(
        w3,
        WALLET_ADDRESS,
        block_identifier=456789,
    )

    assert balance == Decimal("1.234567")
    assert len(eth.calls) == 1

    rpc_call = eth.calls[0]

    assert (
        rpc_call["block_identifier"]
        == 456789
    )
    assert (
        rpc_call["transaction"]["to"]
        == Web3.to_checksum_address(
            CONTRACT_ADDRESS
        )
    )
    assert (
        rpc_call["transaction"]["data"]
        == (
            "0x70a08231"
            + ("0" * 24)
            + WALLET_ADDRESS[2:].lower()
        )
    )


def test_invalid_wallet_address_is_rejected(
    monkeypatch,
):
    configure_usdt(monkeypatch)

    eth = FakeEth()
    w3 = FakeWeb3(eth)

    with pytest.raises(
        BscBalanceReadError,
        match="wallet address must contain 20 bytes",
    ):
        read_bsc_usdt_balance(
            w3,
            "0x1234",
            block_identifier=1,
        )

    assert eth.calls == []


@pytest.mark.parametrize(
    "malformed_result",
    [
        b"",
        b"\x00",
        b"\x00" * 31,
        b"\x00" * 33,
        "0x",
    ],
)
def test_malformed_balance_response_is_rejected(
    monkeypatch,
    malformed_result,
):
    configure_usdt(monkeypatch)

    w3 = FakeWeb3(
        FakeEth(
            call_result=malformed_result,
        )
    )

    with pytest.raises(
        BscBalanceReadError,
        match="exactly 32 bytes",
    ):
        read_bsc_usdt_balance(
            w3,
            WALLET_ADDRESS,
            block_identifier=1,
        )


def test_rpc_failure_is_fail_closed(
    monkeypatch,
):
    configure_usdt(monkeypatch)

    w3 = FakeWeb3(
        FakeEth(
            call_error=RuntimeError(
                "RPC unavailable"
            )
        )
    )

    with pytest.raises(
        BscBalanceReadError,
        match="Unable to read BSC USDT balance",
    ):
        read_bsc_usdt_balance(
            w3,
            WALLET_ADDRESS,
            block_identifier=1,
        )


def test_live_balance_refresh_has_no_arithmetic_credit():
    source = inspect.getsource(
        _refresh_live_balances_after_confirmed_payouts
    )

    assert "read_bsc_block_number" in source
    assert "read_bsc_usdt_balance" in source
    assert "usdt_balance_block" in source
    assert (
        "before + dec(leg.amount_usdt)"
        not in source
    )
    assert (
        "settlement_before "
        "- expected_total_payout_usdt"
        not in source
    )