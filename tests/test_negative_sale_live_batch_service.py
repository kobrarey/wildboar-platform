from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import app.settlement.negative_sale_live_batch_service as service
from app.settlement.negative_sale_live_leg_service import (
    NegativeSaleLiveLegStepResult,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)


def _plan_leg(
    *,
    symbol: str,
) -> dict:
    return {
        "leg_type": (
            "perp_future_reduce"
        ),
        "symbol": symbol,
        "category": "linear",
        "side": "Sell",
        "close_side": "Sell",
        "position_side": "long",
        "position_idx": 1,
        "target_qty": "1",
        "target_cash_usdt": "0",
        "order_quantity_preflight": {
            "requested_qty": "1",
            "normalized_qty": "1",
            "slices": ["1"],
        },
        "raw": {
            "reduce_only": True,
            "position_idx": 1,
        },
    }


def _objects():
    sale_batch = SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [
                _plan_leg(
                    symbol="BTCUSDT"
                ),
                _plan_leg(
                    symbol="ETHUSDT"
                ),
            ]
        },
    )
    settlement_batch = SimpleNamespace(
        id=30,
    )
    legs = [
        SimpleNamespace(
            id=20,
            leg_index=1,
            leg_type=(
                "perp_future_reduce"
            ),
            symbol="BTCUSDT",
            category="linear",
            side="Sell",
            suborders_json=None,
        ),
        SimpleNamespace(
            id=21,
            leg_index=2,
            leg_type=(
                "perp_future_reduce"
            ),
            symbol="ETHUSDT",
            category="linear",
            side="Sell",
            suborders_json=None,
        ),
    ]

    return (
        sale_batch,
        settlement_batch,
        legs,
    )


def _intent(
    *,
    sale_batch_id: int,
    leg_id: int,
    symbol: str,
    execution_round: int = 0,
    status: str = "filled",
) -> dict:
    intent = (
        build_negative_sale_order_intent(
            sale_batch_id=sale_batch_id,
            leg_id=leg_id,
            execution_round=(
                execution_round
            ),
            category="linear",
            symbol=symbol,
            position_side="long",
            close_side="Sell",
            position_idx=1,
            reduce_only=True,
            market_unit=None,
            requested_qty=Decimal("1"),
            normalized_qty=Decimal("1"),
            target_cash_usdt=Decimal("0"),
            slices=(Decimal("1"),),
        ).to_dict()
    )

    item = intent["suborders"][0]
    item["status"] = status

    if status == "filled":
        item["order_id"] = (
            f"OID-{leg_id}"
        )
        item["reconciliation"] = {
            "aggregate_exec_qty": "1",
            "aggregate_exec_value": (
                "999999"
            ),
            "fees_by_currency": {
                "USDT": "1",
            },
        }

    return intent


def _step(
    *,
    leg_id: int,
    action: str,
    reason: str,
    posted: bool,
    all_terminal: bool,
) -> NegativeSaleLiveLegStepResult:
    intent = _intent(
        sale_batch_id=10,
        leg_id=leg_id,
        symbol=(
            "BTCUSDT"
            if leg_id == 20
            else "ETHUSDT"
        ),
        status=(
            "filled"
            if all_terminal
            else (
                "prepared"
                if action == "prepare"
                else "acknowledged"
            )
        ),
    )

    return NegativeSaleLiveLegStepResult(
        leg_id=leg_id,
        action=action,
        posted=posted,
        confirmed_suborders=0,
        reason=reason,
        intent=intent,
        summary={
            "all_terminal": (
                all_terminal
            ),
            "all_filled": (
                all_terminal
            ),
            "has_failure": False,
        },
    )


def test_only_first_actionable_leg_runs(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        legs,
    ) = _objects()

    called: list[int] = []

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        called.append(int(leg.id))

        return _step(
            leg_id=int(leg.id),
            action="prepare",
            reason="intent_prepared",
            posted=False,
            all_terminal=False,
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
        )
    )

    assert called == [20]
    assert result.action == "order_step"
    assert result.active_leg_id == 20
    assert result.posted is False
    assert (
        result.has_pending_action
        is False
    )


def test_active_first_leg_blocks_second(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        legs,
    ) = _objects()

    legs[0].suborders_json = _intent(
        sale_batch_id=10,
        leg_id=20,
        symbol="BTCUSDT",
        status="acknowledged",
    )

    called: list[int] = []

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        called.append(int(leg.id))

        return _step(
            leg_id=int(leg.id),
            action="confirm",
            reason=(
                "active_suborders_reconciled"
            ),
            posted=False,
            all_terminal=False,
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
        )
    )

    assert called == [20]
    assert result.active_leg_id == 20
    assert (
        result.has_pending_action
        is True
    )


def test_transferable_balance_is_only_cash_source(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        legs,
    ) = _objects()

    legs[0].suborders_json = _intent(
        sale_batch_id=10,
        leg_id=20,
        symbol="BTCUSDT",
        status="filled",
    )
    legs[1].suborders_json = _intent(
        sale_batch_id=10,
        leg_id=21,
        symbol="ETHUSDT",
        status="filled",
    )

    class Balance:
        confirmed_transferable_amount = (
            Decimal("80")
        )

        def to_dict(self):
            return {
                "confirmed_transferable_amount": (
                    "80"
                ),
            }

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: Balance(),
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
        )
    )

    assert result.action == "balance_check"
    assert (
        result.confirmed_available_usdt
        == Decimal("80")
    )
    assert result.shortage_usdt == (
        Decimal("20")
    )
    assert result.correction_decision[
        "allowed"
    ] is True
    assert result.correction_decision[
        "next_round"
    ] == 1


def test_completed_rounds_are_bounded(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        legs,
    ) = _objects()

    legs[0].suborders_json = _intent(
        sale_batch_id=10,
        leg_id=20,
        symbol="BTCUSDT",
        execution_round=1,
        status="filled",
    )
    legs[1].suborders_json = _intent(
        sale_batch_id=10,
        leg_id=21,
        symbol="ETHUSDT",
        execution_round=2,
        status="filled",
    )

    class Balance:
        confirmed_transferable_amount = (
            Decimal("90")
        )

        def to_dict(self):
            return {
                "confirmed_transferable_amount": (
                    "90"
                ),
            }

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: Balance(),
    )
    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_LIVE_CORRECTION_MAX_ROUNDS",
        2,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
        )
    )

    assert result.shortage_usdt == (
        Decimal("10")
    )
    assert result.correction_decision[
        "allowed"
    ] is False
    assert result.correction_decision[
        "reason"
    ] == (
        "correction_rounds_exhausted"
    )


def test_derivative_leg_runs_before_spot_even_with_later_index(
    monkeypatch,
):
    sale_batch = SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [
                {
                    "leg_type": "spot_sell",
                    "symbol": "ETHUSDT",
                    "category": "spot",
                    "side": "Sell",
                    "close_side": "Sell",
                    "target_qty": "1",
                    "target_cash_usdt": "100",
                    "order_quantity_preflight": {
                        "requested_qty": "1",
                        "normalized_qty": "1",
                        "slices": ["1"],
                    },
                    "raw": {
                        "market_unit": (
                            "baseCoin"
                        ),
                    },
                },
                _plan_leg(
                    symbol="BTCUSDT"
                ),
            ]
        },
    )
    settlement_batch = (
        SimpleNamespace(id=30)
    )
    legs = [
        SimpleNamespace(
            id=20,
            leg_index=1,
            leg_type="spot_sell",
            symbol="ETHUSDT",
            category="spot",
            side="Sell",
            suborders_json=None,
        ),
        SimpleNamespace(
            id=21,
            leg_index=2,
            leg_type=(
                "perp_future_reduce"
            ),
            symbol="BTCUSDT",
            category="linear",
            side="Sell",
            suborders_json=None,
        ),
    ]

    called: list[int] = []

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        called.append(int(leg.id))

        return _step(
            leg_id=int(leg.id),
            action="prepare",
            reason="intent_prepared",
            posted=False,
            all_terminal=False,
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
        )
    )

    assert called == [21]
    assert result.active_leg_id == 21


def test_existing_correction_intent_resumes_its_round(
    monkeypatch,
):
    sale_batch = SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [
                _plan_leg(
                    symbol="BTCUSDT"
                ),
            ]
        },
    )
    settlement_batch = (
        SimpleNamespace(id=30)
    )
    leg = SimpleNamespace(
        id=20,
        leg_index=1,
        leg_type=(
            "perp_future_reduce"
        ),
        symbol="BTCUSDT",
        category="linear",
        side="Sell",
        suborders_json=_intent(
            sale_batch_id=10,
            leg_id=20,
            symbol="BTCUSDT",
            execution_round=2,
            status="acknowledged",
        ),
    )

    received_rounds: list[int] = []

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        received_rounds.append(
            execution_round
        )

        return _step(
            leg_id=int(leg.id),
            action="confirm",
            reason=(
                "active_suborders_reconciled"
            ),
            posted=False,
            all_terminal=False,
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[leg],
        )
    )

    assert received_rounds == [2]
    assert result.active_leg_id == 20


def test_confirmed_balance_can_skip_spot_phase(
    monkeypatch,
):
    sale_batch = SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [
                _plan_leg(
                    symbol="BTCUSDT"
                ),
                {
                    "leg_type": "spot_sell",
                    "symbol": "ETHUSDT",
                    "category": "spot",
                    "side": "Sell",
                    "close_side": "Sell",
                    "target_qty": "1",
                    "target_cash_usdt": "100",
                    "order_quantity_preflight": {
                        "requested_qty": "1",
                        "normalized_qty": "1",
                        "slices": ["1"],
                    },
                    "raw": {
                        "market_unit": (
                            "baseCoin"
                        ),
                    },
                },
            ]
        },
    )
    settlement_batch = (
        SimpleNamespace(id=30)
    )
    derivative_leg = SimpleNamespace(
        id=20,
        leg_index=1,
        leg_type=(
            "perp_future_reduce"
        ),
        symbol="BTCUSDT",
        category="linear",
        side="Sell",
        suborders_json=_intent(
            sale_batch_id=10,
            leg_id=20,
            symbol="BTCUSDT",
            status="filled",
        ),
    )
    spot_leg = SimpleNamespace(
        id=21,
        leg_index=2,
        leg_type="spot_sell",
        symbol="ETHUSDT",
        category="spot",
        side="Sell",
        suborders_json=None,
    )

    called: list[int] = []

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        lambda *args, **kwargs: (
            called.append(
                int(kwargs["leg"].id)
            )
        ),
    )

    class Balance:
        confirmed_transferable_amount = (
            Decimal("120")
        )

        def to_dict(self):
            return {
                "confirmed_transferable_amount": (
                    "120"
                ),
            }

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: Balance(),
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[
                derivative_leg,
                spot_leg,
            ],
        )
    )

    assert called == []
    assert result.action == (
        "balance_check"
    )
    assert result.shortage_usdt == (
        Decimal("0")
    )
    assert (
        result.confirmed_available_usdt
        == Decimal("120")
    )


def test_spot_phase_runs_only_after_balance_shortage(
    monkeypatch,
):
    sale_batch = SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [
                _plan_leg(
                    symbol="BTCUSDT"
                ),
                {
                    "leg_type": "spot_sell",
                    "symbol": "ETHUSDT",
                    "category": "spot",
                    "side": "Sell",
                    "close_side": "Sell",
                    "target_qty": "1",
                    "target_cash_usdt": "100",
                    "order_quantity_preflight": {
                        "requested_qty": "1",
                        "normalized_qty": "1",
                        "slices": ["1"],
                    },
                    "raw": {
                        "market_unit": (
                            "baseCoin"
                        ),
                    },
                },
            ]
        },
    )
    settlement_batch = (
        SimpleNamespace(id=30)
    )
    derivative_leg = SimpleNamespace(
        id=20,
        leg_index=1,
        leg_type=(
            "perp_future_reduce"
        ),
        symbol="BTCUSDT",
        category="linear",
        side="Sell",
        suborders_json=_intent(
            sale_batch_id=10,
            leg_id=20,
            symbol="BTCUSDT",
            status="filled",
        ),
    )
    spot_leg = SimpleNamespace(
        id=21,
        leg_index=2,
        leg_type="spot_sell",
        symbol="ETHUSDT",
        category="spot",
        side="Sell",
        suborders_json=None,
    )

    called: list[int] = []

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        called.append(int(leg.id))

        return _step(
            leg_id=int(leg.id),
            action="prepare",
            reason="intent_prepared",
            posted=False,
            all_terminal=False,
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    class Balance:
        confirmed_transferable_amount = (
            Decimal("80")
        )

        def to_dict(self):
            return {
                "confirmed_transferable_amount": (
                    "80"
                ),
            }

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: Balance(),
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[
                derivative_leg,
                spot_leg,
            ],
        )
    )

    assert called == [21]
    assert result.action == "order_step"
    assert result.active_leg_id == 21
    assert (
        result.confirmed_available_usdt
        == Decimal("80")
    )
    assert result.shortage_usdt == (
        Decimal("20")
    )


def test_filled_slice_with_next_prepared_has_no_external_pending(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        legs,
    ) = _objects()

    intent = (
        build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=20,
            execution_round=0,
            category="linear",
            symbol="BTCUSDT",
            position_side="long",
            close_side="Sell",
            position_idx=1,
            reduce_only=True,
            market_unit=None,
            requested_qty=Decimal("1"),
            normalized_qty=Decimal("1"),
            target_cash_usdt=Decimal("0"),
            slices=(
                Decimal("0.6"),
                Decimal("0.4"),
            ),
        ).to_dict()
    )

    first = intent["suborders"][0]
    first["status"] = "filled"
    first["order_id"] = "OID-1"
    first["reconciliation"] = {
        "aggregate_exec_qty": "0.6",
        "aggregate_exec_value": "60",
        "fees_by_currency": {
            "USDT": "0.01",
        },
    }

    legs[0].suborders_json = intent

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        return (
            NegativeSaleLiveLegStepResult(
                leg_id=int(leg.id),
                action="confirm",
                posted=False,
                confirmed_suborders=1,
                reason=(
                    "active_suborders_"
                    "reconciled"
                ),
                intent=deepcopy(intent),
                summary={
                    "all_terminal": False,
                    "all_filled": False,
                    "has_failure": False,
                },
            )
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
        )
    )

    assert result.action == "order_step"
    assert (
        result.has_pending_action
        is False
    )