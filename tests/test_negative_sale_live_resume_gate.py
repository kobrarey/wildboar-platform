from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.settlement.negative_sale_execution as execution
from app.settlement.negative_sale_execution_types import (
    NegativeSaleExecutionError,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
    BATCH_STATUS_PENDING_CONFIRMATION,
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
    SALE_BATCH_STATUS_SALE_PLAN_CREATED,
    SALE_LEG_STATUS_PLANNED,
)


def _sale_batch(
    *,
    status: str,
):
    return SimpleNamespace(
        id=10,
        settlement_batch_id=20,
        fund_id=1,
        required_master_usdt=(
            Decimal("100")
        ),
        status=status,
        execution_started_at=None,
        updated_at=None,
        error=None,
    )


def _settlement_batch(
    *,
    status: str,
):
    return SimpleNamespace(
        id=20,
        fund_id=1,
        status=status,
        updated_at=None,
        error=None,
    )


def _leg(
    *,
    category: str,
    target_qty: str,
    target_cash_usdt: str,
):
    return SimpleNamespace(
        id=30,
        status=SALE_LEG_STATUS_PLANNED,
        category=category,
        target_qty=Decimal(
            target_qty
        ),
        target_cash_usdt=Decimal(
            target_cash_usdt
        ),
        use_for_deficit_cover=True,
        actual_execution_mode=None,
        execution_round=None,
        deterministic_key=None,
        order_link_id=None,
        bybit_order_id=None,
        bybit_strategy_id=None,
        filled_qty=None,
        filled_usdt=None,
        cash_delta_usdt=None,
        suborders_json=None,
        mock_execution_json=None,
    )


def test_derivative_leg_is_executable_without_expected_cash():
    leg = _leg(
        category="linear",
        target_qty="1",
        target_cash_usdt="0",
    )

    assert (
        execution
        .planned_executable_leg(leg)
        is True
    )


@pytest.mark.parametrize(
    (
        "sale_status",
        "settlement_status",
    ),
    [
        (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
            BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
        ),
        (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
            BATCH_STATUS_PENDING_CONFIRMATION,
        ),
    ],
)
def test_resume_status_pairs_are_allowed(
    sale_status,
    settlement_status,
):
    execution._validate_sale_execution_input(
        sale_batch=_sale_batch(
            status=sale_status
        ),
        settlement_batch=(
            _settlement_batch(
                status=settlement_status
            )
        ),
        legs=[
            _leg(
                category="linear",
                target_qty="1",
                target_cash_usdt="0",
            )
        ],
    )


def test_inconsistent_resume_pair_is_rejected():
    with pytest.raises(
        NegativeSaleExecutionError,
        match=(
            "Unsupported negative-sale "
            "resume status pair"
        ),
    ):
        execution._validate_sale_execution_input(
            sale_batch=_sale_batch(
                status=(
                    SALE_BATCH_STATUS_SALE_PLAN_CREATED
                )
            ),
            settlement_batch=(
                _settlement_batch(
                    status=(
                        BATCH_STATUS_PENDING_CONFIRMATION
                    )
                )
            ),
            legs=[
                _leg(
                    category="linear",
                    target_qty="1",
                    target_cash_usdt="0",
                )
            ],
        )


def test_prepare_preserves_pending_confirmation(
    monkeypatch,
):
    now = datetime(
        2026,
        7,
        20,
        tzinfo=timezone.utc,
    )

    sale_batch = _sale_batch(
        status=(
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        )
    )
    settlement_batch = (
        _settlement_batch(
            status=(
                BATCH_STATUS_PENDING_CONFIRMATION
            )
        )
    )
    fund = SimpleNamespace(
        id=1,
        code="TEST",
    )
    legs = [
        _leg(
            category="linear",
            target_qty="1",
            target_cash_usdt="0",
        )
    ]

    class FakeDB:
        def add(self, item):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(
        execution,
        "_lock_sale_batch",
        lambda db, sale_batch_id: (
            sale_batch
        ),
    )
    monkeypatch.setattr(
        execution,
        "_lock_settlement_batch",
        lambda db, settlement_batch_id: (
            settlement_batch
        ),
    )
    monkeypatch.setattr(
        execution,
        "_get_fund",
        lambda db, fund_id: fund,
    )
    monkeypatch.setattr(
        execution,
        "_load_sale_legs_for_update",
        lambda db, sale_batch_id: legs,
    )
    monkeypatch.setattr(
        execution,
        (
            "validate_settlement_"
            "share_state_before_external"
        ),
        lambda *args, **kwargs: None,
    )

    result = (
        execution
        .prepare_negative_sale_live_execution(
            FakeDB(),
            sale_batch_id=10,
            now=now,
        )
    )

    assert (
        result[0].status
        == SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    )
    assert (
        result[1].status
        == BATCH_STATUS_PENDING_CONFIRMATION
    )
    assert (
        result[0].execution_started_at
        == now
    )