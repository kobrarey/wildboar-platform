from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import app.settlement.negative_sale_execution as execution
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
    BATCH_STATUS_PENDING_CONFIRMATION,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
    SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW,
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
)


NOW = datetime(
    2026,
    7,
    22,
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


def _objects(
    *,
    execution_round: str | None = None,
):
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
    legs = [
        SimpleNamespace(
            id=30,
            leg_type="spot_sell",
            category="spot",
            location="UNIFIED",
            target_cash_usdt=(
                Decimal("10")
            ),
            status="filled",
            execution_round=(
                execution_round
            ),
            executed_suborders=1,
        )
    ]

    return (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    )


def _step(
    *,
    action: str,
    has_pending_action: bool,
    requires_review: bool,
    available: str | None,
    shortage: str | None,
    reason: str,
) -> NegativeSaleLiveBatchStepResult:
    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=10,
        settlement_batch_id=20,
        action=action,
        reason=reason,
        candidate_leg_count=1,
        active_leg_id=30,
        posted=False,
        all_order_legs_terminal=(
            action == "balance_check"
        ),
        has_pending_action=(
            has_pending_action
        ),
        requires_review=(
            requires_review
        ),
        confirmed_available_usdt=(
            Decimal(available)
            if available is not None
            else None
        ),
        shortage_usdt=(
            Decimal(shortage)
            if shortage is not None
            else None
        ),
        correction_decision=None,
        transferable_balance=(
            {
                "confirmed_transferable_"
                "amount": available,
            }
            if available is not None
            else None
        ),
        leg_step=None,
    )


def test_external_pending_sets_only_pending_confirmation():
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
        step=_step(
            action="order_step",
            has_pending_action=True,
            requires_review=False,
            available=None,
            shortage=None,
            reason=(
                "active_suborders_"
                "reconciled"
            ),
        ),
        now=NOW,
    )

    assert sale_batch.status == (
        SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert settlement_batch.status == (
        BATCH_STATUS_PENDING_CONFIRMATION
    )


def test_confirmed_balance_completes_with_correction_status():
    (
        sale_batch,
        settlement_batch,
        _,
        legs,
    ) = _objects(
        execution_round="1"
    )
    db = FakeDB()

    execution.apply_negative_sale_live_batch_step(
        db,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        legs=legs,
        step=_step(
            action="balance_check",
            has_pending_action=False,
            requires_review=False,
            available="105",
            shortage="0",
            reason=(
                "shortage_resolved"
            ),
        ),
        now=NOW,
    )

    assert sale_batch.status == (
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE
    )
    assert settlement_batch.status == (
        BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED
    )
    assert (
        sale_batch.final_available_usdt
        == Decimal("105")
    )
    assert (
        sale_batch.final_surplus_usdt
        == Decimal("5")
    )


def test_exhausted_shortage_fails_closed():
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
        step=_step(
            action="balance_check",
            has_pending_action=False,
            requires_review=False,
            available="80",
            shortage="20",
            reason=(
                "correction_rounds_"
                "exhausted"
            ),
        ),
        now=NOW,
    )

    assert sale_batch.status == (
        SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW
    )
    assert settlement_batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )


def test_executor_commits_before_state_machine_http(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects()
    db = FakeDB()

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

    def fake_resume(
        db_arg,
        **kwargs,
    ):
        assert db_arg.commits == 1

        return _step(
            action="correction_prepared",
            has_pending_action=False,
            requires_review=False,
            available="80",
            shortage="20",
            reason=(
                "spot_correction_intent_"
                "prepared"
            ),
        )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        fake_resume,
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

    assert db.commits == 2
    assert result.ok is False
    assert result.status_after == (
        SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert (
        result.settlement_status_after
        == BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
    )


def test_pre_submit_revalidation_failure_persists_failed_requires_review(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects()

    db = FakeDB()

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

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_balance_refresh_once",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        lambda *args, **kwargs: None,
    )

    def failed_revalidation_step(
        db_arg,
        **kwargs,
    ):
        assert db_arg.commits == 1

        return _step(
            action="review_required",
            has_pending_action=False,
            requires_review=True,
            available=None,
            shortage=None,
            reason=(
                "pre_submit_revalidation_"
                "failed"
            ),
        )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        failed_revalidation_step,
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

    assert db.commits == 2

    assert sale_batch.status == (
        SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW
    )
    assert settlement_batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert result.ok is False
    assert result.status_after == (
        SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW
    )
    assert (
        result.settlement_status_after
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        "pre_submit_revalidation_failed"
        in sale_batch.error
    )
    assert settlement_batch.error == (
        sale_batch.error
    )
