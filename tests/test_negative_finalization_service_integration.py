from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.models import FundNegativeFinalizationBatch
import app.settlement.negative_finalization as finalization
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BYBIT_FLOW_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_COMPLETED,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_PROCESSING,
    ORDER_STATUS_SUCCESS,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
)


class FakeSession:
    def __init__(self):
        self.flush_calls = 0
        self.finalization = None

    def add(self, value):
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


def test_completed_payout_finalizes_accounting_once(
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

    settlement_batch = SimpleNamespace(
        id=11,
        fund_id=3,
        status=(
            BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
        ),
        settlement_price_usdt=Decimal("10"),
        shares_outstanding_before=Decimal("100"),
        planned_shares_to_issue=Decimal("0"),
        planned_shares_to_redeem=Decimal("10"),
        planned_net_shares_change=Decimal("-10"),
        total_net_user_payout_usdt=Decimal(
            "10"
        ),
        withdrawal_request_amount_usdt=Decimal(
            "10"
        ),
        pricing_locked_at=now,
        pricing_unlocked_at=None,
        accounting_finalized_at=None,
        updated_at=now,
    )

    fund = SimpleNamespace(
        id=3,
        code="wb10",
        shares_outstanding_current=Decimal("100"),
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
        settlement_wallet_received_usdt=Decimal(
            "10"
        ),
        settlement_wallet_balance_before_usdt=Decimal(
            "10"
        ),
        settlement_wallet_balance_after_usdt=Decimal(
            "20"
        ),
        settlement_wallet_receipt_json={
            "unrelated_additional_incoming_raw": 0,
        },
    )

    payout_batch = SimpleNamespace(
        id=41,
        status=PAYOUT_BATCH_STATUS_COMPLETED,
        expected_total_payout_usdt=Decimal(
            "10"
        ),
        confirmed_total_payout_usdt=Decimal(
            "10"
        ),
        payout_leg_count=1,
        confirmed_payout_leg_count=1,
        balance_refresh_status=(
            PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
        ),
        balance_refresh_completed_at=now,
        settlement_wallet_usdt_before=Decimal(
            "20"
        ),
        settlement_wallet_usdt_after=Decimal(
            "10"
        ),
        balance_refresh_json={
            "live": True,
            "absolute_onchain_sync": True,
            "block_number": 500,
            "settlement_wallet": {
                "address": (
                    "0x1111111111111111111111111111111111111111"
                ),
                "before_usdt": Decimal(
                    "20"
                ),
                "confirmed_total_payout_usdt": Decimal(
                    "10"
                ),
                "observed_after_usdt": Decimal(
                    "10"
                ),
                "arithmetic_debit_applied": False,
            },
            "user_wallets": [
                {
                    "user_wallet_id": 501,
                    "address": (
                        "0x2222222222222222222222222222222222222222"
                    ),
                    "before_usdt": Decimal(
                        "0"
                    ),
                    "payout_amount_usdt": Decimal(
                        "10"
                    ),
                    "observed_after_usdt": Decimal(
                        "10"
                    ),
                    "block_number": 500,
                    "absolute_onchain_sync": True,
                }
            ],
        },
        settlement_batch_id=11,
        settlement_wallet_address=(
            "0x1111111111111111111111111111111111111111"
        ),
        gas_status="ready",
        gas_topup_tx_hash=None,
        gas_reconciliation_json={
            "live": True,
            "gas_sufficient": True,
            "no_real_gas_topup_needed": True,
            "durable_intent_not_required": True,
        },
    )

    payout_legs = [
        SimpleNamespace(
            id=51,
            user_id=101,
            status=PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
            balance_refresh_json={
                "live": True,
                "absolute_onchain_sync": True,
                "block_number": 500,
                "user_wallet_id": 501,
                "address": (
                    "0x2222222222222222222222222222222222222222"
                ),
                "before_usdt": Decimal(
                    "0"
                ),
                "payout_amount_usdt": Decimal(
                    "10"
                ),
                "observed_after_usdt": Decimal(
                    "10"
                ),
                "arithmetic_credit_applied": False,
            },
            payout_batch_id=41,
            settlement_batch_id=11,
            fund_id=3,
            amount_usdt=Decimal("10"),
            confirmed_at=now,
            user_wallet_id=501,
            to_user_wallet_id=501,
            wallet_balance_before_usdt=Decimal(
                "0"
            ),
            wallet_balance_after_usdt=Decimal(
                "10"
            ),
            from_address=(
                "0x1111111111111111111111111111111111111111"
            ),
            to_address=(
                "0x2222222222222222222222222222222222222222"
            ),
            tx_hash=(
                "0x"
                + ("a" * 64)
            ),
            confirmation_json={
                "durable_intent": True,
                "intent_id": 81,
                "intent_status": "confirmed",
                "tx_hash": (
                    "0x"
                    + ("a" * 64)
                ),
                "confirmed": True,
            },
        )
    ]

    bsc_intents = [
        SimpleNamespace(
            id=81,
            scope_key=(
                "negative-payout:11:41:51"
            ),
            action_type=(
                "negative_redeem_payout"
            ),
            settlement_batch_id=11,
            payout_batch_id=41,
            payout_leg_id=51,
            fund_id=3,
            asset="USDT",
            amount=Decimal("10"),
            from_address=(
                "0x1111111111111111111111111111111111111111"
            ),
            to_address=(
                "0x2222222222222222222222222222222222222222"
            ),
            prepared_tx_hash=(
                "0x"
                + ("a" * 64)
            ),
            intent_fingerprint=(
                "b" * 64
            ),
            status="confirmed",
            receipt_status=1,
            confirmations=max(
                1,
                int(
                    finalization.settings
                    .NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED
                ),
            ),
            confirmed_at=now,
        )
    ]

    order = SimpleNamespace(
        id=61,
        user_id=101,
        side=ORDER_SIDE_REDEEM,
        shares=Decimal("10"),
        amount_usdt=None,
        price_usdt=None,
        gross_redeem_usdt=Decimal("100"),
        success_fee_usdt=Decimal("0"),
        management_fee_usdt=Decimal("0"),
        partial_month_fee_usdt=Decimal("0"),
        net_user_payout_usdt=Decimal("10"),
        net_price_usdt=Decimal("1"),
        status=ORDER_STATUS_PROCESSING,
        executed_at=None,
    )

    position = SimpleNamespace(
        user_id=101,
        fund_id=3,
        shares=Decimal("20"),
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

    payout_wallet = SimpleNamespace(
        id=501,
        user_id=101,
        blockchain="BSC",
        address=(
            "0x2222222222222222222222222222222222222222"
        ),
        usdt_balance=Decimal("10"),
        usdt_balance_updated_at=now,
        usdt_balance_block=500,
        usdt_reserved=Decimal("0"),
        is_active=False,
    )

    wallet_gate_lock_calls: list[
        int
    ] = []

    def lock_payout_user_wallets(
        *args,
        **kwargs,
    ):
        wallet_gate_lock_calls.append(
            501
        )

        return {
            501: payout_wallet,
        }

    context = {
        "orders": [order],
        "buy_orders": [],
        "redeem_orders": [order],
        "redeem_validation": {
            "total_redeem_shares": Decimal("10"),
            "total_net_user_payout_usdt": Decimal(
                "10"
            ),
            "total_partial_month_fee_usdt": Decimal(
                "0"
            ),
        },
        "buy_validation": {
            "total_buy_usdt": Decimal("0"),
            "total_buy_shares": Decimal("0"),
            "computed_shares_by_order_id": {},
        },
        "position_wallet_validation": {
            "redeem_positions": {
                61: position,
            },
            "buy_positions": {},
            "buy_wallets": {},
            "positions_before": {
                finalization._position_key(
                    101,
                    3,
                ): {
                    "user_id": 101,
                    "fund_id": 3,
                    "shares": Decimal("20"),
                    "shares_reserved": Decimal(
                        "10"
                    ),
                }
            },
            "user_wallet_reserves_before": {},
        },
        "share_validation": {
            "shares_outstanding_before": Decimal(
                "100"
            ),
            "shares_outstanding_after": Decimal(
                "90"
            ),
            "actual_net_shares_change": Decimal(
                "-10"
            ),
            "planned_net_shares_change": Decimal(
                "-10"
            ),
        },
    }

    db = FakeSession()

    for name, value in {
        "NEGATIVE_NET_FINALIZATION_ENABLED": True,
        (
            "NEGATIVE_NET_FINALIZATION_"
            "REQUIRE_PAYOUTS_CONFIRMED"
        ): True,
        (
            "NEGATIVE_NET_FINALIZATION_"
            "UNLOCK_PRICING"
        ): True,
    }.items():
        monkeypatch.setattr(
            finalization.settings,
            name,
            value,
        )

    runtime_lock_calls: list[int] = []

    replacements = {
        "_lock_settlement_batch": settlement_batch,
        "_lock_fund": fund,
        "_lock_sale_batch": sale_batch,
        "_lock_bybit_flow": bybit_flow,
        "_lock_payout_batch": payout_batch,
        "_lock_payout_legs": payout_legs,
        "_lock_bsc_intents": bsc_intents,
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
        "_lock_payout_user_wallets",
        lock_payout_user_wallets,
    )

    def lock_runtime_state(
        *args,
        **kwargs,
    ):
        runtime_lock_calls.append(1)
        return runtime_state

    monkeypatch.setattr(
        finalization,
        "_lock_runtime_state",
        lock_runtime_state,
    )

    monkeypatch.setattr(
        finalization,
        (
            "_validate_bybit_cash_"
            "delivery_evidence"
        ),
        lambda *args, **kwargs: {
            "schema": (
                "negative_finalization_"
                "bybit_cash_delivery_gate_v1"
            ),
            "durable_evidence_validated": True,
        },
    )

    monkeypatch.setattr(
        finalization,
        "_lock_existing_finalization",
        lambda *args, **kwargs: db.finalization,
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

    assert wallet_gate_lock_calls == [
        501
    ]

    assert (
        db.finalization
        .validation_json[
            "user_wallet_db_gate"
        ][
            "schema"
        ]
        == (
            finalization
            .USER_WALLET_DB_GATE_SCHEMA
        )
    )

    assert (
        db.finalization
        .validation_json[
            "user_wallet_db_gate"
        ][
            "all_wallets_exact_match"
        ]
        is True
    )

    assert (
        db.finalization
        .validation_json[
            "user_wallet_db_gate"
        ][
            "arithmetic_balance_updates"
        ]
        is False
    )

    assert first.status_after == (
        FINALIZATION_BATCH_STATUS_COMPLETED
    )
    assert first.settlement_status_after == (
        BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
    )

    assert (
        fund.shares_outstanding_current
        == Decimal("90")
    )
    assert position.shares == Decimal("10")
    assert position.shares_reserved == Decimal("0")

    assert order.status == ORDER_STATUS_SUCCESS
    assert order.executed_at == now

    assert runtime_state.pricing_locked is False
    assert runtime_state.pricing_lock_reason is None
    assert runtime_state.pricing_lock_batch_id is None
    assert runtime_state.pricing_unlocked_at == now

    assert len(runtime_lock_calls) == 1

    assert settlement_batch.pricing_unlocked_at == now
    assert settlement_batch.accounting_finalized_at == now

    assert db.finalization is not None
    assert db.finalization.id == 701
    assert db.finalization.status == (
        FINALIZATION_BATCH_STATUS_COMPLETED
    )

    state_after_first = (
        fund.shares_outstanding_current,
        position.shares,
        position.shares_reserved,
        db.flush_calls,
    )

    payout_wallet.usdt_balance = Decimal(
        "25"
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

    assert payout_wallet.usdt_balance == (
        Decimal("25")
    )

    assert wallet_gate_lock_calls == [
        501
    ]

    # Completed rerun returns before trying
    # to acquire or release an active
    # runtime pricing lock.
    assert len(runtime_lock_calls) == 1

    assert (
        fund.shares_outstanding_current,
        position.shares,
        position.shares_reserved,
        db.flush_calls,
    ) == state_after_first