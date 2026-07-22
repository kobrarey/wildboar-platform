from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import app.settlement.negative_sale_execution as execution
from app.settlement.negative_sale_balance_reconciliation import (
    NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA,
)
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
)


NOW = datetime(
    2026,
    7,
    22,
    22,
    0,
    tzinfo=timezone.utc,
)


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.flushes = 0

    def add(self, item):
        return None

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1


def _objects():
    sale_batch = SimpleNamespace(
        id=10,
        settlement_batch_id=20,
        fund_id=1,
        required_master_usdt=(
            Decimal("100")
        ),
        status=(
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        ),
        execution_json=None,
        reconciliation_json=None,
        final_available_usdt=None,
        final_shortage_usdt=None,
        final_surplus_usdt=None,
        execution_completed_at=None,
        error=None,
        updated_at=None,
    )

    settlement_batch = SimpleNamespace(
        id=20,
        fund_id=1,
        status=(
            BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
        ),
        error=None,
        updated_at=None,
    )

    fund = SimpleNamespace(
        id=1,
        code="TEST",
    )

    legs = [
        SimpleNamespace(
            id=30,
            leg_index=1,
            leg_type="spot_sell",
            category="spot",
            location="UNIFIED",
            status="filled",
            execution_round="0",
            executed_suborders=1,
            suborders_json=None,
        )
    ]

    return (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    )


def _transferable(
    amount: str,
) -> dict:
    return {
        "account_type": "UNIFIED",
        "destination_account_type": (
            "FUND"
        ),
        "coin": "USDT",
        "confirmed_transferable_amount": (
            amount
        ),
        "source_endpoint": (
            "/v5/asset/transfer/"
            "query-account-coin-balance"
        ),
        "raw": {
            "retCode": 0,
            "result": {
                "accountType": "UNIFIED",
                "balance": {
                    "coin": "USDT",
                    "transferBalance": amount,
                    "transferSafeAmount": (
                        amount
                    ),
                    "ltvTransferSafeAmount": (
                        amount
                    ),
                },
            },
        },
    }


def _balance_refresh_step(
    *,
    amount: str = "80",
) -> NegativeSaleLiveBatchStepResult:
    available = Decimal(amount)
    shortage = max(
        Decimal("100") - available,
        Decimal("0"),
    )

    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=10,
        settlement_batch_id=20,
        action="balance_refresh",
        reason=(
            "terminal_external_action_"
            "balance_refreshed"
        ),
        candidate_leg_count=1,
        active_leg_id=30,
        posted=False,
        all_order_legs_terminal=False,
        has_pending_action=False,
        requires_review=False,
        confirmed_available_usdt=(
            available
        ),
        shortage_usdt=shortage,
        correction_decision=None,
        transferable_balance=(
            _transferable(amount)
        ),
        leg_step={
            "balance_refresh_action": {
                "action_type": (
                    "order_terminal_confirmed"
                ),
                "active_leg_id": 30,
                "leg_index": 1,
                "order_link_id": (
                    "wbns-10-30-r0-s0"
                ),
                "external_status": "filled",
                "execution_round": 0,
                "suborder_index": 0,
                "source_schema": (
                    "negative_sale_order_"
                    "intent_v1"
                ),
            },
            "read_only": True,
            "no_order_post": True,
            "no_earn_post": True,
        },
    )


def _state_balance_step(
    *,
    amount: str,
) -> NegativeSaleLiveBatchStepResult:
    available = Decimal(amount)
    shortage = max(
        Decimal("100") - available,
        Decimal("0"),
    )

    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=10,
        settlement_batch_id=20,
        action="balance_check",
        reason=(
            "confirmed_balance_"
            "covers_requirement"
        ),
        candidate_leg_count=1,
        active_leg_id=None,
        posted=False,
        all_order_legs_terminal=True,
        has_pending_action=False,
        requires_review=False,
        confirmed_available_usdt=(
            available
        ),
        shortage_usdt=shortage,
        correction_decision=None,
        transferable_balance=(
            _transferable(amount)
        ),
        leg_step=None,
    )


def test_executor_balance_refresh_precedes_earn_and_order(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects()
    db = FakeDB()
    calls: list[str] = []

    monkeypatch.setattr(
        execution,
        "prepare_negative_sale_live_execution",
        lambda *args, **kwargs: (
            sale_batch,
            settlement_batch,
            fund,
            legs,
            "sale_execution_processing",
            "negative_net_sale_processing",
        ),
    )

    def fake_balance_refresh(
        **kwargs,
    ):
        calls.append(
            "balance_refresh"
        )
        assert db.commits == 1

        return _balance_refresh_step()

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_balance_refresh_once",
        fake_balance_refresh,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "Earn must not run after "
                    "a balance-refresh step"
                )
            )
        ),
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "Order must not run after "
                    "a balance-refresh step"
                )
            )
        ),
    )

    result = (
        execution
        .execute_negative_sale_plan_live(
            db,
            sale_batch_id=10,
            client=object(),
            now=NOW,
        )
    )

    assert calls == [
        "balance_refresh"
    ]
    assert db.commits == 2
    assert result.ok is False
    assert result.status_after == (
        SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert (
        sale_batch.final_available_usdt
        == Decimal("80")
    )

    reconciliation = (
        sale_batch.reconciliation_json
    )

    assert reconciliation["schema"] == (
        NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    )
    assert len(
        reconciliation[
            "refresh_history"
        ]
    ) == 1

    latest = reconciliation[
        "latest_refresh"
    ]

    assert latest["action_type"] == (
        "order_terminal_confirmed"
    )
    assert latest["active_leg_id"] == 30
    assert latest["order_link_id"] == (
        "wbns-10-30-r0-s0"
    )


def test_repeated_balance_refresh_is_idempotent():
    (
        sale_batch,
        settlement_batch,
        _,
        legs,
    ) = _objects()
    db = FakeDB()
    step = _balance_refresh_step()

    execution.apply_negative_sale_live_batch_step(
        db,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        legs=legs,
        step=step,
        now=NOW,
    )

    first = (
        sale_batch.reconciliation_json
    )

    execution.apply_negative_sale_live_batch_step(
        db,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        legs=legs,
        step=step,
        now=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    second = (
        sale_batch.reconciliation_json
    )

    assert second == first
    assert len(
        second["refresh_history"]
    ) == 1


def test_balance_refresh_does_not_finalize_sale():
    (
        sale_batch,
        settlement_batch,
        _,
        legs,
    ) = _objects()
    db = FakeDB()

    execution.apply_negative_sale_live_batch_step(
        db,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        legs=legs,
        step=_balance_refresh_step(
            amount="105"
        ),
        now=NOW,
    )

    assert sale_batch.status == (
        SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert settlement_batch.status == (
        BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
    )
    assert (
        sale_batch.execution_completed_at
        is None
    )


def test_final_balance_check_uses_v2_and_completes():
    (
        sale_batch,
        settlement_batch,
        _,
        legs,
    ) = _objects()
    db = FakeDB()

    execution.apply_negative_sale_live_batch_step(
        db,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        legs=legs,
        step=_state_balance_step(
            amount="105"
        ),
        now=NOW,
    )

    assert sale_batch.status == (
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED
    )
    assert settlement_batch.status == (
        BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED
    )

    reconciliation = (
        sale_batch.reconciliation_json
    )

    assert reconciliation["schema"] == (
        NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    )
    assert reconciliation[
        "confirmed_available_usdt"
    ] == "105"
    assert reconciliation[
        "confirmed_shortage_usdt"
    ] == "0"
    assert reconciliation[
        "latest_refresh"
    ]["action_type"] == (
        "state_machine_balance_check"
    )