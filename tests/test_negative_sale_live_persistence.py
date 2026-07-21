from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.settlement.negative_sale_live_persistence import (
    NegativeSaleLivePersistenceError,
    apply_runtime_intent_to_leg,
    validate_runtime_intent_transition,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)
from app.settlement.statuses import (
    SALE_LEG_STATUS_FILLED,
)


def _intent(
    *,
    category: str = "linear",
    target_cash_usdt: Decimal = Decimal(
        "0"
    ),
) -> dict:
    return (
        build_negative_sale_order_intent(
            sale_batch_id=1,
            leg_id=2,
            execution_round=0,
            category=category,
            symbol="BTCUSDT",
            position_side=(
                "long"
                if category != "spot"
                else None
            ),
            close_side="Sell",
            position_idx=(
                1
                if category != "spot"
                else None
            ),
            reduce_only=(
                True
                if category != "spot"
                else None
            ),
            market_unit=(
                "baseCoin"
                if category == "spot"
                else None
            ),
            requested_qty=Decimal("1"),
            normalized_qty=Decimal("1"),
            target_cash_usdt=(
                target_cash_usdt
            ),
            slices=(Decimal("1"),),
        ).to_dict()
    )


def _leg() -> SimpleNamespace:
    return SimpleNamespace(
        actual_execution_mode=None,
        execution_round=None,
        deterministic_key=None,
        order_link_id=None,
        bybit_order_id=None,
        bybit_strategy_id=None,
        planned_suborders=None,
        executed_suborders=None,
        suborders_json=None,
        mock_execution_json=None,
        filled_qty=None,
        filled_usdt=None,
        avg_fill_price=None,
        fill_ratio=None,
        unfilled_usdt=None,
        fee_usdt=None,
        cash_delta_usdt=None,
        last_price=None,
        sent_at=None,
        confirmed_at=None,
        failed_at=None,
        execution_error=None,
        status="planned",
        updated_at=None,
    )


def _confirmed(
    intent: dict,
    *,
    status: str = "filled",
    qty: str = "1",
    value: str = "100",
    fee: str = "0.1",
) -> dict:
    result = deepcopy(intent)
    item = result["suborders"][0]

    item["status"] = status
    item["order_id"] = "OID-1"
    item["reconciliation"] = {
        "aggregate_exec_qty": qty,
        "aggregate_exec_value": value,
        "aggregate_avg_price": value,
        "fees_by_currency": {
            "USDT": fee,
        },
    }

    return result


def test_submit_claim_is_single_writer():
    prepared = _intent()
    submitted = deepcopy(prepared)

    submitted[
        "suborders"
    ][0]["status"] = "submitted"

    validate_runtime_intent_transition(
        prepared,
        submitted,
        enforce_submit_claim=True,
    )

    with pytest.raises(
        NegativeSaleLivePersistenceError,
        match="Concurrent submit claim",
    ):
        validate_runtime_intent_transition(
            submitted,
            submitted,
            enforce_submit_claim=True,
        )


def test_confirmed_quantity_cannot_regress():
    base_intent = _intent()

    existing = _confirmed(
        base_intent,
        status=(
            "partially_filled_"
            "pending_confirmation"
        ),
        qty="0.5",
        value="50",
    )

    updated = deepcopy(existing)
    updated_item = updated["suborders"][0]

    updated_item["status"] = (
        "pending_confirmation"
    )
    updated_item["reconciliation"][
        "aggregate_exec_qty"
    ] = "0.4"
    updated_item["reconciliation"][
        "aggregate_exec_value"
    ] = "40"

    with pytest.raises(
        NegativeSaleLivePersistenceError,
        match=(
            "Confirmed execution quantity "
            "regressed"
        ),
    ):
        validate_runtime_intent_transition(
            existing,
            updated,
        )


def test_derivative_execution_value_is_not_cash():
    leg = _leg()
    intent = _confirmed(
        _intent(category="linear"),
        qty="1",
        value="100",
        fee="0.1",
    )

    audit = apply_runtime_intent_to_leg(
        leg=leg,
        raw_intent=intent,
    )

    assert leg.status == (
        SALE_LEG_STATUS_FILLED
    )
    assert leg.filled_qty == Decimal("1")
    assert leg.filled_usdt == Decimal("0")
    assert (
        leg.cash_delta_usdt
        == Decimal("0")
    )
    assert leg.avg_fill_price == (
        Decimal("100")
    )
    assert audit[
        "derivative_execution_value_is_cash"
    ] is False


def test_spot_cash_is_diagnostic_only():
    leg = _leg()
    intent = _confirmed(
        _intent(
            category="spot",
            target_cash_usdt=(
                Decimal("100")
            ),
        ),
        qty="1",
        value="105",
        fee="1",
    )

    audit = apply_runtime_intent_to_leg(
        leg=leg,
        raw_intent=intent,
    )

    assert leg.status == (
        SALE_LEG_STATUS_FILLED
    )
    assert leg.filled_usdt == (
        Decimal("105")
    )
    assert leg.fee_usdt == Decimal("1")
    assert leg.cash_delta_usdt == (
        Decimal("104")
    )
    assert audit[
        "cash_source_of_truth"
    ] == (
        "confirmed_transferable_"
        "usdt_balance"
    )