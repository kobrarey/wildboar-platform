from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.settlement import (
    negative_sale_live_batch_service
    as batch_service,
)
from app.settlement.negative_sale_live_persistence import (
    NegativeSaleLivePersistenceError,
    archive_terminal_intent_and_activate_next_round,
    validated_terminal_intent_history,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)


def _intent(
    execution_round: int,
    *,
    terminal: bool,
) -> dict:
    result = (
        build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=20,
            execution_round=(
                execution_round
            ),
            category="spot",
            symbol="BTCUSDT",
            position_side=None,
            close_side="Sell",
            position_idx=None,
            reduce_only=None,
            market_unit="baseCoin",
            requested_qty=Decimal("1"),
            normalized_qty=Decimal("1"),
            target_cash_usdt=(
                Decimal("100")
            ),
            slices=(Decimal("1"),),
        ).to_dict()
    )

    if terminal:
        item = result["suborders"][0]
        item["status"] = "filled"
        item["order_id"] = (
            f"OID-{execution_round}"
        )
        item["reconciliation"] = {
            "aggregate_exec_qty": "1",
            "aggregate_exec_value": (
                "100"
            ),
            "fees_by_currency": {
                "USDT": "0.1",
            },
        }

    return result


class FakeDB:
    def add(self, item):
        return None

    def flush(self):
        return None

    def commit(self):
        return None

    def refresh(self, item):
        return None


def _leg(
    intent: dict,
):
    return SimpleNamespace(
        id=20,
        suborders_json=deepcopy(
            intent
        ),
        mock_execution_json={
            "existing_audit_key": "kept",
        },
        actual_execution_mode=None,
        execution_round=None,
        deterministic_key=None,
        order_link_id=None,
        strategy_id=None,
        bybit_order_id="OLD-OID",
        bybit_strategy_id=None,
        planned_suborders=1,
        executed_suborders=1,
        filled_qty=Decimal("1"),
        filled_usdt=Decimal("100"),
        avg_fill_price=Decimal("100"),
        fill_ratio=Decimal("1"),
        unfilled_usdt=Decimal("0"),
        fee_usdt=Decimal("0.1"),
        cash_delta_usdt=Decimal(
            "99.9"
        ),
        last_price=Decimal("100"),
        sent_at=datetime(
            2026,
            7,
            20,
            tzinfo=timezone.utc,
        ),
        confirmed_at=datetime(
            2026,
            7,
            20,
            tzinfo=timezone.utc,
        ),
        failed_at=None,
        execution_error=None,
        error=None,
        updated_at=None,
    )


def test_terminal_intent_is_archived_before_next_round():
    previous = _intent(
        0,
        terminal=True,
    )
    next_intent = _intent(
        1,
        terminal=False,
    )
    leg = _leg(previous)

    activated = (
        archive_terminal_intent_and_activate_next_round(
            FakeDB(),
            leg=leg,
            new_intent=next_intent,
            now=datetime(
                2026,
                7,
                21,
                tzinfo=timezone.utc,
            ),
        )
    )

    history = (
        validated_terminal_intent_history(
            leg.mock_execution_json
        )
    )

    assert len(history) == 1
    assert (
        history[0]["execution_round"]
        == 0
    )
    assert (
        history[0]["intent"]
        == previous
    )
    assert (
        activated["execution_round"]
        == 1
    )
    assert (
        leg.suborders_json
        == next_intent
    )
    assert (
        leg.mock_execution_json[
            "existing_audit_key"
        ]
        == "kept"
    )
    assert leg.bybit_order_id is None
    assert leg.executed_suborders == 0
    assert leg.sent_at is None
    assert leg.confirmed_at is None


def test_nonterminal_intent_cannot_roll_over():
    leg = _leg(
        _intent(
            0,
            terminal=False,
        )
    )

    with pytest.raises(
        NegativeSaleLivePersistenceError,
        match=(
            "Previous intent must be "
            "terminal"
        ),
    ):
        archive_terminal_intent_and_activate_next_round(
            FakeDB(),
            leg=leg,
            new_intent=_intent(
                1,
                terminal=False,
            ),
        )


def test_completed_rounds_include_history_and_active_intent():
    leg = _leg(
        _intent(
            0,
            terminal=True,
        )
    )

    archive_terminal_intent_and_activate_next_round(
        FakeDB(),
        leg=leg,
        new_intent=_intent(
            1,
            terminal=True,
        ),
    )

    archive_terminal_intent_and_activate_next_round(
        FakeDB(),
        leg=leg,
        new_intent=_intent(
            2,
            terminal=True,
        ),
    )

    assert (
        batch_service
        ._completed_correction_rounds(
            [leg]
        )
        == 2
    )