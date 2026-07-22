from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.settlement.negative_sale_live_leg_service as service
from app.settlement.negative_sale_live_preflight import (
    NegativeSaleLivePreflight,
)


NOW = datetime(
    2026,
    7,
    22,
    15,
    0,
    tzinfo=timezone.utc,
)


def _objects():
    sale_batch = SimpleNamespace(
        id=10,
        plan_json={
            "legs": [
                {
                    "leg_type": (
                        "perp_future_reduce"
                    ),
                    "symbol": "BTCUSDT",
                    "category": "linear",
                    "side": "Sell",
                    "close_side": "Sell",
                    "position_side": "long",
                    "position_idx": 1,
                    "target_qty": "2",
                    "target_cash_usdt": (
                        "0"
                    ),
                    "order_quantity_preflight": {
                        "requested_qty": "2",
                        "normalized_qty": "2",
                        "slices": ["2"],
                    },
                    "raw": {
                        "position_idx": 1,
                        "reduce_only": True,
                    },
                }
            ]
        },
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
        target_qty=Decimal("2"),
        target_cash_usdt=Decimal("0"),
        suborders_json=None,
        bybit_order_id=None,
        sent_at=None,
        executed_suborders=0,
    )

    return sale_batch, leg


def _live_preflight():
    return NegativeSaleLivePreflight(
        category="linear",
        symbol="BTCUSDT",
        position_side="Buy",
        close_side="Sell",
        position_idx=1,
        reduce_only=True,
        market_unit=None,
        requested_qty=Decimal("2"),
        available_qty=Decimal("1.5"),
        price=Decimal("100"),
        normalized_qty=(
            Decimal("1.5")
        ),
        normalized_notional=(
            Decimal("150.0")
        ),
        slices=(
            Decimal("1.0"),
            Decimal("0.5"),
        ),
        instrument_snapshot={
            "category": "linear",
            "symbol": "BTCUSDT",
            "captured_at": (
                NOW.isoformat()
            ),
            "trading": True,
        },
        position_snapshot={
            "position_side": "Buy",
            "close_side": "Sell",
            "position_idx": 1,
            "available_qty": "1.5",
        },
        quantity_preflight={
            "requested_qty": "2",
            "available_qty": "1.5",
            "normalized_qty": "1.5",
            "slices": [
                "1.0",
                "0.5",
            ],
            "eligible": True,
        },
        captured_at=NOW,
    )


def test_live_preflight_drives_immutable_payload(
    monkeypatch,
):
    sale_batch, leg = _objects()
    captured: dict = {}

    def fake_preflight(
        client,
        **kwargs,
    ):
        captured.update(kwargs)
        return _live_preflight()

    monkeypatch.setattr(
        service,
        "build_live_negative_sale_preflight",
        fake_preflight,
    )

    intent = (
        service
        .build_live_prepared_intent_for_leg(
            object(),
            sale_batch=sale_batch,
            leg=leg,
            execution_round=0,
            now=NOW,
        )
    )

    assert captured["requested_qty"] == (
        "2"
    )
    assert (
        captured[
            "planned_position_side"
        ]
        == "long"
    )
    assert (
        intent["position_side"]
        == "Buy"
    )
    assert intent["close_side"] == (
        "Sell"
    )
    assert intent["position_idx"] == 1
    assert intent["reduce_only"] is True
    assert intent["normalized_qty"] == (
        "1.5"
    )
    assert intent["planned_suborders"] == 2

    assert intent["suborders"][0][
        "payload"
    ]["qty"] == "1.0"
    assert intent["suborders"][1][
        "payload"
    ]["qty"] == "0.5"

    assert intent["suborders"][0][
        "payload"
    ]["side"] == "Sell"
    assert intent["suborders"][0][
        "payload"
    ]["positionIdx"] == 1
    assert intent["suborders"][0][
        "payload"
    ]["reduceOnly"] is True

    assert intent["position_snapshot"][
        "live_quantity_preflight"
    ]["available_qty"] == "1.5"


def test_prepare_cycle_persists_live_intent_without_post(
    monkeypatch,
):
    sale_batch, leg = _objects()
    prepared = (
        service
        .build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=20,
            execution_round=0,
            category="linear",
            symbol="BTCUSDT",
            position_side="Buy",
            close_side="Sell",
            position_idx=1,
            reduce_only=True,
            market_unit=None,
            requested_qty="2",
            normalized_qty="1.5",
            target_cash_usdt="0",
            slices=("1.0", "0.5"),
            prepared_at=NOW,
        )
        .to_dict()
    )

    persisted: list[dict] = []

    monkeypatch.setattr(
        service,
        "build_live_prepared_intent_for_leg",
        lambda *args, **kwargs: (
            prepared
        ),
    )

    def fake_persist(
        db,
        *,
        sale_batch,
        leg,
        execution_round,
        prepared_intent,
        now,
    ):
        persisted.append(
            prepared_intent
        )
        leg.suborders_json = (
            prepared_intent
        )
        return prepared_intent

    monkeypatch.setattr(
        service,
        "persist_prepared_intent_before_submit",
        fake_persist,
    )

    result = (
        service.prepare_live_leg_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            leg=leg,
            execution_round=0,
            now=NOW,
        )
    )

    assert result.action == "prepare"
    assert result.posted is False
    assert result.reason == (
        "intent_prepared"
    )
    assert len(persisted) == 1
    assert result.intent[
        "normalized_qty"
    ] == "1.5"


def test_preflight_failure_blocks_persistence(
    monkeypatch,
):
    sale_batch, leg = _objects()
    persistence_called = False

    def fail_builder(
        *args,
        **kwargs,
    ):
        raise (
            service
            .NegativeSaleLiveLegServiceError(
                "Live pre-submit preflight "
                "failed"
            )
        )

    def forbidden_persist(
        *args,
        **kwargs,
    ):
        nonlocal persistence_called
        persistence_called = True
        raise AssertionError(
            "Invalid intent must not persist"
        )

    monkeypatch.setattr(
        service,
        "build_live_prepared_intent_for_leg",
        fail_builder,
    )
    monkeypatch.setattr(
        service,
        "persist_prepared_intent_before_submit",
        forbidden_persist,
    )

    with pytest.raises(
        service
        .NegativeSaleLiveLegServiceError,
        match=(
            "Live pre-submit preflight"
        ),
    ):
        service.prepare_live_leg_once(
            object(),
            client=object(),
            sale_batch=sale_batch,
            leg=leg,
            execution_round=0,
            now=NOW,
        )

    assert persistence_called is False
    assert leg.suborders_json is None