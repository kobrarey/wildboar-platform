from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from web3 import Web3

from app.config import settings
from app.models import (
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    UserWallet,
)
import app.settlement.negative_payout_flow as payout_flow
from app.settlement.statuses import (
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED,
)
from app.settlement.bsc_balance_service import (
    BscBalanceReadError,
    read_bsc_block_number,
    read_bsc_usdt_balance,
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


class BalanceRefreshQuery:
    def __init__(
        self,
        session,
        model,
    ):
        self.session = session
        self.model = model
        self.locked = False

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(
        self,
        *args,
        **kwargs,
    ):
        self.locked = True
        self.session.events.append(
            (
                "lock",
                self.model.__name__,
                self.session.commit_calls,
            )
        )
        return self

    def first(self):
        self.session.events.append(
            (
                "first",
                self.model.__name__,
                self.locked,
                self.session.commit_calls,
            )
        )

        if self.model is UserWallet:
            return self.session.wallet

        if self.model is FundNegativePayoutBatch:
            return self.session.batch

        if self.model is FundNegativePayoutLeg:
            return self.session.leg

        raise AssertionError(
            f"Unexpected query model: {self.model}"
        )


class BalanceRefreshSession:
    def __init__(
        self,
        *,
        batch,
        leg,
        wallet,
    ):
        self.batch = batch
        self.leg = leg
        self.wallet = wallet

        self.commit_calls = 0
        self.flush_calls = 0
        self.added = []
        self.events = []

    def query(self, model):
        return BalanceRefreshQuery(
            self,
            model,
        )

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commit_calls += 1
        self.events.append(
            (
                "commit",
                self.commit_calls,
            )
        )

    def flush(self):
        self.flush_calls += 1
        self.events.append(
            (
                "flush",
                self.flush_calls,
            )
        )


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
        payout_flow
        ._refresh_live_balances_after_confirmed_payouts
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


def test_live_balance_refresh_uses_absolute_chain_values(
    monkeypatch,
):
    settlement_address = (
        "0x3333333333333333333333333333333333333333"
    )
    user_address = WALLET_ADDRESS

    now = datetime(
        2026,
        7,
        24,
        12,
        0,
        tzinfo=timezone.utc,
    )

    batch = SimpleNamespace(
        id=52,
        settlement_wallet_address=(
            settlement_address
        ),
        settlement_wallet_usdt_before=(
            Decimal("500")
        ),
        balance_refresh_started_at=None,
    )

    leg = SimpleNamespace(
        id=63,
        user_wallet_id=41,
        to_user_wallet_id=41,
        to_address=user_address,
        amount_usdt=Decimal("10"),
        status=(
            PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
        ),
    )

    wallet = SimpleNamespace(
        id=41,
        address=user_address,
        usdt_balance=Decimal("100"),
        usdt_balance_updated_at=None,
        usdt_balance_block=None,
    )

    db = BalanceRefreshSession(
        batch=batch,
        leg=leg,
        wallet=wallet,
    )

    def fake_read_block_number(w3):
        db.events.append(
            (
                "rpc_block",
                db.commit_calls,
            )
        )

        assert db.commit_calls == 1

        return 987654

    observed_balances = {
        settlement_address.lower(): Decimal(
            "321.5"
        ),
        user_address.lower(): Decimal(
            "7.25"
        ),
    }

    def fake_read_balance(
        w3,
        address,
        *,
        block_identifier,
    ):
        db.events.append(
            (
                "rpc_balance",
                address.lower(),
                block_identifier,
                db.commit_calls,
            )
        )

        assert db.commit_calls == 1
        assert block_identifier == 987654

        return observed_balances[
            address.lower()
        ]

    monkeypatch.setattr(
        payout_flow,
        "read_bsc_block_number",
        fake_read_block_number,
    )
    monkeypatch.setattr(
        payout_flow,
        "read_bsc_usdt_balance",
        fake_read_balance,
    )

    payout_flow._refresh_live_balances_after_confirmed_payouts(
        db,
        w3=object(),
        batch=batch,
        legs=[leg],
        expected_total_payout_usdt=(
            Decimal("10")
        ),
        now=now,
    )

    assert db.commit_calls == 1
    assert db.flush_calls == 1

    # Absolute on-chain value, not 100 + 10.
    assert wallet.usdt_balance == Decimal(
        "7.25"
    )
    assert wallet.usdt_balance != Decimal(
        "110"
    )
    assert wallet.usdt_balance_block == 987654
    assert (
        wallet.usdt_balance_updated_at
        == now
    )

    assert leg.status == (
        PAYOUT_LEG_STATUS_BALANCE_REFRESHED
    )
    assert (
        leg.wallet_balance_before_usdt
        == Decimal("100")
    )
    assert (
        leg.wallet_balance_after_usdt
        == Decimal("7.25")
    )
    assert (
        leg.balance_refresh_json[
            "absolute_onchain_sync"
        ]
        is True
    )
    assert (
        leg.balance_refresh_json[
            "arithmetic_credit_applied"
        ]
        is False
    )

    # Absolute settlement-wallet value,
    # not 500 - 10.
    assert (
        batch.settlement_wallet_usdt_after
        == Decimal("321.5")
    )
    assert (
        batch.settlement_wallet_usdt_after
        != Decimal("490")
    )
    assert (
        batch.balance_refresh_status
        == PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
    )
    assert (
        batch.confirmed_total_payout_usdt
        == Decimal("10")
    )
    assert (
        batch.confirmed_payout_leg_count
        == 1
    )
    assert (
        batch.balance_refresh_completed_at
        == now
    )
    assert (
        batch.balance_refresh_json[
            "absolute_onchain_sync"
        ]
        is True
    )
    assert (
        batch.balance_refresh_json[
            "settlement_wallet"
        ][
            "arithmetic_debit_applied"
        ]
        is False
    )

    commit_index = next(
        index
        for index, event in enumerate(
            db.events
        )
        if event[0] == "commit"
    )

    rpc_indices = [
        index
        for index, event in enumerate(
            db.events
        )
        if event[0] in {
            "rpc_block",
            "rpc_balance",
        }
    ]

    lock_indices = [
        index
        for index, event in enumerate(
            db.events
        )
        if event[0] == "lock"
    ]

    assert rpc_indices
    assert lock_indices

    # Durable checkpoint precedes every RPC.
    assert commit_index < min(
        rpc_indices
    )

    # Row locks are reacquired only after
    # all read-only RPC calls finish.
    assert max(rpc_indices) < min(
        lock_indices
    )

    balance_rpc_events = [
        event
        for event in db.events
        if event[0] == "rpc_balance"
    ]

    assert len(balance_rpc_events) == 2
    assert {
        event[2]
        for event in balance_rpc_events
    } == {
        987654
    }
