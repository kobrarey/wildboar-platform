from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.settlement.negative_sale_execution as execution
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
    BATCH_STATUS_PENDING_CONFIRMATION,
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
)


NOW = datetime(
    2026,
    7,
    22,
    18,
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

    settlement_batch = (
        SimpleNamespace(
            id=20,
            fund_id=1,
            status=(
                BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
            ),
            error=None,
            updated_at=None,
        )
    )

    fund = SimpleNamespace(
        id=1,
        code="TEST",
    )

    earn_leg = SimpleNamespace(
        id=30,
        sale_batch_id=10,
        leg_index=1,
        leg_type="usdt_earn_buffer",
        category="earn",
        location="EARN",
        target_cash_usdt=(
            Decimal("20")
        ),
        status="buffer_available",
        execution_round=None,
        executed_suborders=0,
    )

    order_leg = SimpleNamespace(
        id=31,
        sale_batch_id=10,
        leg_index=2,
        leg_type="spot_sell",
        category="spot",
        location="UNIFIED",
        target_cash_usdt=(
            Decimal("30")
        ),
        status="planned",
        execution_round=None,
        executed_suborders=0,
    )

    return (
        sale_batch,
        settlement_batch,
        fund,
        [
            earn_leg,
            order_leg,
        ],
    )


def _step(
    *,
    action: str,
    reason: str,
    active_leg_id: int,
    posted: bool,
    has_pending_action: bool,
) -> NegativeSaleLiveBatchStepResult:
    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=10,
        settlement_batch_id=20,
        action=action,
        reason=reason,
        candidate_leg_count=1,
        active_leg_id=active_leg_id,
        posted=posted,
        all_order_legs_terminal=False,
        has_pending_action=(
            has_pending_action
        ),
        requires_review=False,
        confirmed_available_usdt=None,
        shortage_usdt=None,
        correction_decision=None,
        transferable_balance=None,
        leg_step={
            "source": action,
        },
    )


def _install_prepare(
    monkeypatch,
    *,
    sale_batch,
    settlement_batch,
    fund,
    legs,
):
    monkeypatch.setattr(
        execution,
        "prepare_negative_sale_live_execution",
        lambda *args, **kwargs: (
            sale_batch,
            settlement_batch,
            fund,
            legs,
            "sale_plan_created",
            "negative_net_sale_planned",
        ),
    )


def test_earn_step_has_priority_over_order_step(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects()
    db = FakeDB()
    trace: list[str] = []

    _install_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    def fake_earn_resume(
        db_arg,
        **kwargs,
    ):
        assert db_arg.commits == 1
        trace.append("earn")

        return _step(
            action="earn_prepare",
            reason="earn_intent_prepared",
            active_leg_id=30,
            posted=False,
            has_pending_action=False,
        )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        fake_earn_resume,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        lambda *args, **kwargs: (
            pytest.fail(
                "Order state machine must "
                "not run in the same cycle"
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

    assert trace == ["earn"]
    assert db.commits == 2
    assert result.ok is False
    assert result.status_after == (
        SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert (
        result.settlement_status_after
        == BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
    )
    assert result.diagnostics[
        "state_machine_step"
    ]["action"] == "earn_prepare"


def test_pending_earn_blocks_order_machine(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects()
    db = FakeDB()

    _install_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        lambda *args, **kwargs: (
            _step(
                action="earn_submit",
                reason=(
                    "earn_post_acknowledged"
                ),
                active_leg_id=30,
                posted=True,
                has_pending_action=True,
            )
        ),
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        lambda *args, **kwargs: (
            pytest.fail(
                "Pending Earn must block "
                "trading order execution"
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

    assert result.status_after == (
        SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert (
        result.settlement_status_after
        == BATCH_STATUS_PENDING_CONFIRMATION
    )
    assert result.diagnostics[
        "state_machine_step"
    ]["action"] == "earn_submit"
    assert result.diagnostics[
        "bybit_order_posted"
    ] is True


def test_order_machine_runs_after_earn_is_inactive(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects()
    db = FakeDB()
    trace: list[str] = []

    _install_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    def fake_earn_resume(
        *args,
        **kwargs,
    ):
        trace.append("earn")
        return None

    def fake_order_resume(
        *args,
        **kwargs,
    ):
        trace.append("order")

        return _step(
            action="correction_prepared",
            reason=(
                "spot_correction_intent_"
                "prepared"
            ),
            active_leg_id=31,
            posted=False,
            has_pending_action=False,
        )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        fake_earn_resume,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        fake_order_resume,
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

    assert trace == [
        "earn",
        "order",
    ]
    assert db.commits == 2
    assert result.diagnostics[
        "state_machine_step"
    ]["action"] == (
        "correction_prepared"
    )