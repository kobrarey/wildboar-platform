from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.models import (
    FundBscTransactionIntent,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    UserWallet,
)
from app.settlement.bsc_intent_reconciliation import (
    reconcile_bsc_transaction_intent,
)
from app.settlement.erc20_receipt import (
    ERC20_TRANSFER_TOPIC0,
)
import app.settlement.negative_finalization as finalization
import app.settlement.negative_payout_flow as payout_flow
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BYBIT_FLOW_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_COMPLETED,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_PROCESSING,
    ORDER_STATUS_SUCCESS,
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_CONFIRMED,
)


USDT_CONTRACT = (
    "0x55d398326f99059ff775485246999027b"
    "3197955"
)
SETTLEMENT_ADDRESS = f"0x{'11' * 20}"
USER_ONE_ADDRESS = f"0x{'22' * 20}"
USER_TWO_ADDRESS = f"0x{'33' * 20}"
TX_HASH_ONE = f"0x{'aa' * 32}"
TX_HASH_TWO = f"0x{'bb' * 32}"


def _address_topic(address: str) -> str:
    return (
        "0x"
        + ("00" * 12)
        + address.removeprefix("0x")
    )


def _uint256_data(value: int) -> str:
    return f"0x{value:064x}"


def _transfer_log(
    *,
    tx_hash: str,
    destination: str,
    amount_raw: int,
) -> dict[str, Any]:
    return {
        "address": USDT_CONTRACT,
        "transactionHash": tx_hash,
        "logIndex": 1,
        "topics": [
            ERC20_TRANSFER_TOPIC0,
            _address_topic(
                SETTLEMENT_ADDRESS
            ),
            _address_topic(destination),
        ],
        "data": _uint256_data(amount_raw),
    }


class FakeEth:
    def __init__(
        self,
        *,
        transactions: dict[str, dict[str, Any]],
        receipts: dict[str, dict[str, Any]],
    ):
        self.transactions = transactions
        self.receipts = receipts
        self.chain_id = 56
        self.block_number = 111
        self.send_calls = 0

    def get_transaction(
        self,
        tx_hash: str,
    ) -> dict[str, Any]:
        return self.transactions[tx_hash]

    def get_transaction_receipt(
        self,
        tx_hash: str,
    ) -> dict[str, Any]:
        return self.receipts[tx_hash]

    def send_raw_transaction(
        self,
        raw_tx: Any,
    ) -> None:
        self.send_calls += 1
        raise AssertionError(
            "Reconciliation or resume must not "
            "broadcast an existing transaction"
        )


class FakeWeb3:
    def __init__(self, eth: FakeEth):
        self.eth = eth


class FakeQuery:
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

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(
        self,
        *args,
        **kwargs,
    ):
        self.locked = True
        return self

    def first(self):
        return self.session.next_result(
            self.model,
            locked=self.locked,
        )

    def all(self):
        value = self.session.next_result(
            self.model,
            locked=self.locked,
        )
        return list(value)


class FakeSession:
    def __init__(self):
        self.intent_queue = []
        self.wallet_queue = []
        self.leg_queue = []
        self.payout_batch = None
        self.finalization = None

        self.commit_calls = 0
        self.flush_calls = 0
        self.added = []
        self.events = []

    def query(self, model):
        return FakeQuery(
            self,
            model,
        )

    def next_result(
        self,
        model,
        *,
        locked: bool,
    ):
        self.events.append(
            (
                "query",
                model.__name__,
                locked,
            )
        )

        if model is FundBscTransactionIntent:
            return self.intent_queue.pop(0)

        if model is UserWallet:
            return self.wallet_queue.pop(0)

        if model is FundNegativePayoutLeg:
            return self.leg_queue.pop(0)

        if model is FundNegativePayoutBatch:
            return self.payout_batch

        raise AssertionError(
            f"Unexpected query model: {model}"
        )

    def add(self, value):
        self.added.append(value)

        if isinstance(
            value,
            FundNegativeFinalizationBatch,
        ):
            self.finalization = value

    def flush(self):
        self.flush_calls += 1

        if (
            self.finalization is not None
            and self.finalization.id is None
        ):
            self.finalization.id = 701

    def commit(self):
        self.commit_calls += 1
        self.events.append(
            (
                "commit",
                self.commit_calls,
            )
        )


def _intent(
    *,
    intent_id: int,
    payout_leg_id: int,
    tx_hash: str,
    nonce: int,
    destination: str,
    amount: Decimal,
) -> FundBscTransactionIntent:
    return FundBscTransactionIntent(
        id=intent_id,
        scope_key=(
            payout_flow
            .deterministic_redeem_payout_request_id(
                settlement_batch_id=11,
                payout_batch_id=41,
                payout_leg_id=payout_leg_id,
                user_wallet_id=(
                    101
                    if payout_leg_id == 51
                    else 102
                ),
                amount_usdt=amount,
                to_address=destination,
            )
        ),
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        settlement_batch_id=11,
        payout_batch_id=41,
        payout_leg_id=payout_leg_id,
        fund_id=3,
        asset="USDT",
        amount=amount,
        from_address=SETTLEMENT_ADDRESS,
        to_address=destination,
        chain_id=56,
        source_nonce=nonce,
        prepared_tx_hash=tx_hash,
        prepared_raw_tx="0xdeadbeef",
        intent_fingerprint=(
            "a" * 64
            if payout_leg_id == 51
            else "b" * 64
        ),
        status=BSC_INTENT_STATUS_BROADCAST,
    )


def test_negative_payout_full_fake_e2e(
    monkeypatch,
):
    now = datetime(
        2026,
        7,
        25,
        12,
        0,
        tzinfo=timezone.utc,
    )

    settings_values = {
        "BSC_USDT_CONTRACT": USDT_CONTRACT,
        "BSC_USDT_DECIMALS": 18,
        "NEGATIVE_NET_PAYOUT_COIN": "USDT",
        "NEGATIVE_NET_PAYOUT_CHAIN": "BSC",
        (
            "NEGATIVE_NET_PAYOUT_"
            "CONFIRMATIONS_REQUIRED"
        ): 12,
        "NEGATIVE_NET_FINALIZATION_ENABLED": True,
        (
            "NEGATIVE_NET_FINALIZATION_"
            "REQUIRE_PAYOUTS_CONFIRMED"
        ): True,
        (
            "NEGATIVE_NET_FINALIZATION_"
            "UNLOCK_PRICING"
        ): True,
    }

    for name, value in settings_values.items():
        monkeypatch.setattr(
            payout_flow.settings,
            name,
            value,
        )

    intent_one = _intent(
        intent_id=201,
        payout_leg_id=51,
        tx_hash=TX_HASH_ONE,
        nonce=7,
        destination=USER_ONE_ADDRESS,
        amount=Decimal("10"),
    )
    intent_two = _intent(
        intent_id=202,
        payout_leg_id=52,
        tx_hash=TX_HASH_TWO,
        nonce=8,
        destination=USER_TWO_ADDRESS,
        amount=Decimal("20"),
    )

    transactions = {
        TX_HASH_ONE: {
            "hash": TX_HASH_ONE,
            "from": SETTLEMENT_ADDRESS,
            "to": USDT_CONTRACT,
            "nonce": 7,
            "value": 0,
        },
        TX_HASH_TWO: {
            "hash": TX_HASH_TWO,
            "from": SETTLEMENT_ADDRESS,
            "to": USDT_CONTRACT,
            "nonce": 8,
            "value": 0,
        },
    }

    receipts = {
        TX_HASH_ONE: {
            "transactionHash": TX_HASH_ONE,
            "status": 1,
            "blockNumber": 100,
            "logs": [
                _transfer_log(
                    tx_hash=TX_HASH_ONE,
                    destination=USER_ONE_ADDRESS,
                    amount_raw=10 * 10**18,
                )
            ],
        },
        TX_HASH_TWO: {
            "transactionHash": TX_HASH_TWO,
            "status": 1,
            "blockNumber": 100,
            "logs": [
                _transfer_log(
                    tx_hash=TX_HASH_TWO,
                    destination=USER_TWO_ADDRESS,
                    amount_raw=20 * 10**18,
                )
            ],
        },
    }

    eth = FakeEth(
        transactions=transactions,
        receipts=receipts,
    )
    w3 = FakeWeb3(eth)

    reconciliation_results = []

    for intent in (
        intent_one,
        intent_two,
    ):
        result = (
            reconcile_bsc_transaction_intent(
                w3,
                intent=intent,
                required_confirmations=12,
            )
        )

        assert result.action == "confirmed"
        assert result.suggested_status == (
            BSC_INTENT_STATUS_CONFIRMED
        )
        assert result.confirmations == 12

        intent.status = (
            BSC_INTENT_STATUS_CONFIRMED
        )
        intent.receipt_status = 1
        intent.block_number = 100
        intent.confirmations = 12
        intent.confirmed_at = now

        reconciliation_results.append(
            result
        )

    assert len(reconciliation_results) == 2
    assert eth.send_calls == 0

    settlement_batch = SimpleNamespace(
        id=11,
        fund_id=3,
        status="negative_net_payout_processing",
        settlement_price_usdt=Decimal("10"),
        shares_outstanding_before=Decimal("100"),
        planned_shares_to_issue=Decimal("0"),
        planned_shares_to_redeem=Decimal("15"),
        planned_net_shares_change=Decimal("-15"),
        pricing_locked_at=now,
        pricing_unlocked_at=None,
        accounting_finalized_at=None,
        updated_at=now,
        error=None,
    )

    fund = SimpleNamespace(
        id=3,
        code="wb10",
        shares_outstanding_current=Decimal("100"),
    )

    settlement_wallet = SimpleNamespace(
        id=31,
        address=SETTLEMENT_ADDRESS,
        encrypted_private_key="unused",
    )

    payout_batch = SimpleNamespace(
        id=41,
        settlement_batch_id=11,
        bybit_flow_id=31,
        fund_id=3,
        status="gas_ready",
        coin="USDT",
        chain="BSC",
        settlement_wallet_id=31,
        settlement_wallet_address=(
            SETTLEMENT_ADDRESS
        ),
        expected_total_payout_usdt=Decimal("30"),
        planned_total_payout_usdt=Decimal("30"),
        confirmed_total_payout_usdt=None,
        payout_leg_count=2,
        confirmed_payout_leg_count=0,
        gas_status="ready",
        gas_topup_tx_hash=None,
        gas_reconciliation_json={
            "live": True,
            "gas_sufficient": True,
            "no_real_gas_topup_needed": True,
            "durable_intent_not_required": True,
        },
        balance_refresh_status="not_started",
        balance_refresh_started_at=None,
        balance_refresh_completed_at=None,
        balance_refresh_json=None,
        settlement_wallet_usdt_before=Decimal("100"),
        settlement_wallet_usdt_after=None,
        updated_at=now,
    )

    leg_one = SimpleNamespace(
        id=51,
        payout_batch_id=41,
        settlement_batch_id=11,
        bybit_flow_id=31,
        fund_id=3,
        user_id=1001,
        user_wallet_id=101,
        to_user_wallet_id=101,
        status="planned",
        coin="USDT",
        chain="BSC",
        from_address=SETTLEMENT_ADDRESS,
        to_address=USER_ONE_ADDRESS,
        amount_usdt=Decimal("10"),
        tx_hash=None,
        confirmations=None,
        confirmed_at=None,
        failed_at=None,
        error=None,
        payout_mock_json=None,
        confirmation_json=None,
        balance_refresh_json=None,
        updated_at=now,
    )

    leg_two = SimpleNamespace(
        id=52,
        payout_batch_id=41,
        settlement_batch_id=11,
        bybit_flow_id=31,
        fund_id=3,
        user_id=1002,
        user_wallet_id=102,
        to_user_wallet_id=102,
        status="planned",
        coin="USDT",
        chain="BSC",
        from_address=SETTLEMENT_ADDRESS,
        to_address=USER_TWO_ADDRESS,
        amount_usdt=Decimal("20"),
        tx_hash=None,
        confirmations=None,
        confirmed_at=None,
        failed_at=None,
        error=None,
        payout_mock_json=None,
        confirmation_json=None,
        balance_refresh_json=None,
        updated_at=now,
    )

    db = FakeSession()
    db.payout_batch = payout_batch
    db.intent_queue = [
        intent_one,
        intent_two,
    ]

    confirmed_order = []

    for leg in (
        leg_one,
        leg_two,
    ):
        confirmed = (
            payout_flow
            ._send_or_confirm_live_payout_leg(
                db,
                w3=w3,
                batch=payout_batch,
                settlement_batch=(
                    settlement_batch
                ),
                fund=fund,
                settlement_wallet=(
                    settlement_wallet
                ),
                leg=leg,
                now=now,
            )
        )

        assert confirmed is True
        assert leg.status == (
            PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
        )
        assert leg.confirmations == 12

        confirmed_order.append(leg.id)

    assert confirmed_order == [51, 52]
    assert eth.send_calls == 0

    wallet_one = SimpleNamespace(
        id=101,
        user_id=1001,
        address=USER_ONE_ADDRESS,
        usdt_balance=Decimal("1"),
        usdt_balance_updated_at=None,
        usdt_balance_block=None,
    )
    wallet_two = SimpleNamespace(
        id=102,
        user_id=1002,
        address=USER_TWO_ADDRESS,
        usdt_balance=Decimal("2"),
        usdt_balance_updated_at=None,
        usdt_balance_block=None,
    )

    db.wallet_queue = [
        wallet_one,
        wallet_two,
        wallet_one,
        wallet_two,
    ]
    db.leg_queue = [
        leg_one,
        leg_two,
    ]

    observed_balances = {
        SETTLEMENT_ADDRESS.lower(): Decimal(
            "77.5"
        ),
        USER_ONE_ADDRESS.lower(): Decimal(
            "13.25"
        ),
        USER_TWO_ADDRESS.lower(): Decimal(
            "21.75"
        ),
    }

    monkeypatch.setattr(
        payout_flow,
        "read_bsc_block_number",
        lambda value: 987654,
    )
    monkeypatch.setattr(
        payout_flow,
        "read_bsc_usdt_balance",
        lambda value, address, *,
        block_identifier: (
            observed_balances[
                address.lower()
            ]
        ),
    )

    payout_flow._refresh_live_balances_after_confirmed_payouts(
        db,
        w3=w3,
        batch=payout_batch,
        legs=[
            leg_one,
            leg_two,
        ],
        expected_total_payout_usdt=Decimal(
            "30"
        ),
        now=now,
    )

    assert db.commit_calls == 1

    assert wallet_one.usdt_balance == Decimal(
        "13.25"
    )
    assert wallet_one.usdt_balance != Decimal(
        "11"
    )

    assert wallet_two.usdt_balance == Decimal(
        "21.75"
    )
    assert wallet_two.usdt_balance != Decimal(
        "22"
    )

    assert (
        payout_batch.settlement_wallet_usdt_after
        == Decimal("77.5")
    )
    assert (
        payout_batch.settlement_wallet_usdt_after
        != Decimal("70")
    )

    assert leg_one.status == (
        PAYOUT_LEG_STATUS_BALANCE_REFRESHED
    )
    assert leg_two.status == (
        PAYOUT_LEG_STATUS_BALANCE_REFRESHED
    )

    assert payout_batch.balance_refresh_status == (
        PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
    )
    assert (
        payout_batch.confirmed_total_payout_usdt
        == Decimal("30")
    )
    assert (
        payout_batch.confirmed_payout_leg_count
        == 2
    )

    payout_batch.status = (
        PAYOUT_BATCH_STATUS_COMPLETED
    )
    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
    )

    sale_batch = SimpleNamespace(
        id=21,
        status=(
            SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED
        ),
    )
    bybit_flow = SimpleNamespace(
        id=31,
        status=BYBIT_FLOW_STATUS_COMPLETED,
    )

    order_one = SimpleNamespace(
        id=61,
        user_id=1001,
        side=ORDER_SIDE_REDEEM,
        shares=Decimal("5"),
        amount_usdt=None,
        price_usdt=None,
        gross_redeem_usdt=Decimal("10"),
        success_fee_usdt=Decimal("0"),
        management_fee_usdt=Decimal("0"),
        partial_month_fee_usdt=Decimal("0"),
        net_user_payout_usdt=Decimal("10"),
        net_price_usdt=Decimal("2"),
        status=ORDER_STATUS_PROCESSING,
        executed_at=None,
    )
    order_two = SimpleNamespace(
        id=62,
        user_id=1002,
        side=ORDER_SIDE_REDEEM,
        shares=Decimal("10"),
        amount_usdt=None,
        price_usdt=None,
        gross_redeem_usdt=Decimal("20"),
        success_fee_usdt=Decimal("0"),
        management_fee_usdt=Decimal("0"),
        partial_month_fee_usdt=Decimal("0"),
        net_user_payout_usdt=Decimal("20"),
        net_price_usdt=Decimal("2"),
        status=ORDER_STATUS_PROCESSING,
        executed_at=None,
    )

    position_one = SimpleNamespace(
        user_id=1001,
        fund_id=3,
        shares=Decimal("20"),
        shares_reserved=Decimal("5"),
    )
    position_two = SimpleNamespace(
        user_id=1002,
        fund_id=3,
        shares=Decimal("30"),
        shares_reserved=Decimal("10"),
    )

    runtime_state = SimpleNamespace(
        fund_id=3,
        pricing_locked=True,
        pricing_lock_reason="settlement",
        pricing_lock_batch_id=11,
        pricing_locked_at=now,
        pricing_unlocked_at=None,
        updated_at=now,
    )

    context = {
        "orders": [
            order_one,
            order_two,
        ],
        "buy_orders": [],
        "redeem_orders": [
            order_one,
            order_two,
        ],
        "redeem_validation": {
            "total_redeem_shares": Decimal(
                "15"
            ),
            "total_net_user_payout_usdt": (
                Decimal("30")
            ),
            "total_partial_month_fee_usdt": (
                Decimal("0")
            ),
        },
        "buy_validation": {
            "total_buy_usdt": Decimal("0"),
            "total_buy_shares": Decimal("0"),
            "computed_shares_by_order_id": {},
        },
        "position_wallet_validation": {
            "redeem_positions": {
                61: position_one,
                62: position_two,
            },
            "buy_positions": {},
            "buy_wallets": {},
            "positions_before": {
                finalization._position_key(
                    1001,
                    3,
                ): {
                    "user_id": 1001,
                    "fund_id": 3,
                    "shares": Decimal("20"),
                    "shares_reserved": Decimal("5"),
                },
                finalization._position_key(
                    1002,
                    3,
                ): {
                    "user_id": 1002,
                    "fund_id": 3,
                    "shares": Decimal("30"),
                    "shares_reserved": Decimal("10"),
                },
            },
            "user_wallet_reserves_before": {},
        },
        "share_validation": {
            "shares_outstanding_before": (
                Decimal("100")
            ),
            "shares_outstanding_after": (
                Decimal("85")
            ),
            "actual_net_shares_change": (
                Decimal("-15")
            ),
            "planned_net_shares_change": (
                Decimal("-15")
            ),
        },
    }

    replacements = {
        "_lock_settlement_batch": (
            settlement_batch
        ),
        "_lock_fund": fund,
        "_lock_sale_batch": sale_batch,
        "_lock_bybit_flow": bybit_flow,
        "_lock_payout_batch": payout_batch,
        "_lock_payout_legs": [
            leg_one,
            leg_two,
        ],
        "_lock_bsc_intents": [
            intent_one,
            intent_two,
        ],
        "_lock_runtime_state": runtime_state,
    }

    for name, value in replacements.items():
        monkeypatch.setattr(
            finalization,
            name,
            lambda *args,
            _value=value,
            **kwargs: _value,
        )

    monkeypatch.setattr(
        finalization,
        "_lock_existing_finalization",
        lambda *args, **kwargs: (
            db.finalization
        ),
    )
    monkeypatch.setattr(
        finalization,
        "_prepare_accounting_context",
        lambda *args, **kwargs: context,
    )
    monkeypatch.setattr(
        finalization,
        "apply_redeem_cost_basis",
        lambda *args, **kwargs: None,
    )

    bybit_cash_delivery_gate_calls = []

    def validate_bybit_cash_delivery(
        *,
        settlement_batch,
        bybit_flow,
    ):
        bybit_cash_delivery_gate_calls.append(
            (
                int(settlement_batch.id),
                int(bybit_flow.id),
            )
        )

        return {
            "schema": (
                "negative_finalization_"
                "bybit_cash_delivery_gate_v1"
            ),
            "durable_evidence_validated": True,
        }

    monkeypatch.setattr(
        finalization,
        (
            "_validate_bybit_cash_"
            "delivery_evidence"
        ),
        validate_bybit_cash_delivery,
    )

    first = (
        finalization
        .finalize_negative_net_settlement(
            db,
            settlement_batch_id=11,
            now=now,
        )
    )

    assert first.ok is True
    assert first.idempotent is False
    assert first.status_after == (
        FINALIZATION_BATCH_STATUS_COMPLETED
    )
    assert first.settlement_status_after == (
        BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
    )

    assert (
        fund.shares_outstanding_current
        == Decimal("85")
    )
    assert position_one.shares == Decimal("15")
    assert position_one.shares_reserved == Decimal(
        "0"
    )
    assert position_two.shares == Decimal("20")
    assert position_two.shares_reserved == Decimal(
        "0"
    )

    assert order_one.status == ORDER_STATUS_SUCCESS
    assert order_two.status == ORDER_STATUS_SUCCESS

    assert runtime_state.pricing_locked is False
    assert runtime_state.pricing_unlocked_at == now

    assert (
        bybit_cash_delivery_gate_calls
        == [(11, 31)]
    )

    state_after_first = (
        fund.shares_outstanding_current,
        position_one.shares,
        position_one.shares_reserved,
        position_two.shares,
        position_two.shares_reserved,
        db.flush_calls,
    )

    second = (
        finalization
        .finalize_negative_net_settlement(
            db,
            settlement_batch_id=11,
            now=now,
        )
    )

    assert second.ok is True
    assert second.idempotent is True

    assert (
        bybit_cash_delivery_gate_calls
        == [
            (11, 31),
            (11, 31),
        ]
    )

    assert (
        fund.shares_outstanding_current,
        position_one.shares,
        position_one.shares_reserved,
        position_two.shares,
        position_two.shares_reserved,
        db.flush_calls,
    ) == state_after_first

    assert eth.send_calls == 0
