from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.settlement.negative_sale_live_batch_service as service
from app.settlement.negative_sale_correction_policy import (
    CorrectionRoundDecision,
)
from app.settlement.negative_sale_live_preflight import (
    NegativeSaleLivePreflightError,
)


NOW = datetime(
    2026,
    7,
    22,
    19,
    0,
    tzinfo=timezone.utc,
)


def _sale_batch():
    return SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={"legs": []},
    )


def _leg():
    return SimpleNamespace(
        id=20,
        sale_batch_id=10,
        leg_index=1,
        leg_type="spot_sell",
        symbol="BTCUSDT",
        category="spot",
        side="Sell",
        location="UNIFIED",
        suborders_json=None,
    )


def _source_row():
    return {
        "source_key": "sale-leg:20",
        "leg_id": 20,
        "symbol": "BTCUSDT",
        "category": "spot",
        "asset_type": "spot",
        "location": "UNIFIED",
        "eligible": True,
        "use_for_deficit_cover": True,
        "requires_fund_to_unified_transfer": (
            False
        ),
        "remaining_qty": "0.50",
        "snapshot_remaining_qty": "2",
        "confirmed_live_qty": "0.50",
        "live_available_qty": "0.50",
        "live_sellable_qty": "0.50",
        "base_coin": "BTC",
        "best_bid": "100",
        "remaining_sellable_usdt": "50",
        "instrument_snapshot": {
            "source": "live_scan",
        },
        "transferable_balance": {
            "confirmed_transferable_amount": (
                "0.50"
            ),
        },
        "capacity_preflight": {
            "normalized_qty": "0.50",
        },
    }


def _decision():
    return CorrectionRoundDecision(
        allowed=True,
        next_round=1,
        shortage_usdt=Decimal("20"),
        reason=(
            "correction_round_available"
        ),
    )


def test_source_scan_caps_snapshot_by_live_balance(
    monkeypatch,
):
    leg = _leg()

    monkeypatch.setattr(
        service,
        "_plan_snapshot",
        lambda **kwargs: {
            "category": "spot",
            "eligible": True,
            "use_for_deficit_cover": True,
            "location": "UNIFIED",
            "symbol": "BTCUSDT",
            "raw": {
                "asset_type": "spot",
            },
        },
    )
    monkeypatch.setattr(
        service,
        "_remaining_spot_qty",
        lambda **kwargs: Decimal("2"),
    )

    instrument = SimpleNamespace(
        trading=True,
        preflight_complete=True,
        completeness_reasons=(),
        base_coin="BTC",
        to_dict=lambda: {
            "symbol": "BTCUSDT",
        },
    )

    monkeypatch.setattr(
        service,
        "query_instrument_info",
        lambda *args, **kwargs: (
            instrument
        ),
    )

    balance = SimpleNamespace(
        confirmed_transferable_amount=(
            Decimal("0.30")
        ),
        to_dict=lambda: {
            "confirmed_transferable_amount": (
                "0.30"
            ),
        },
    )

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: (
            balance
        ),
    )
    monkeypatch.setattr(
        service,
        "_query_spot_best_bid",
        lambda *args, **kwargs: (
            Decimal("100")
        ),
    )

    captured: dict = {}

    def fake_normalize(
        *,
        instrument,
        requested_qty,
        available_qty,
        price,
    ):
        captured["requested_qty"] = (
            requested_qty
        )
        captured["available_qty"] = (
            available_qty
        )
        captured["price"] = price

        return SimpleNamespace(
            eligible=True,
            normalized_qty=(
                Decimal("0.25")
            ),
            normalized_notional=(
                Decimal("25")
            ),
            slices=(Decimal("0.25"),),
            reasons=(),
            to_dict=lambda: {
                "normalized_qty": "0.25",
            },
        )

    monkeypatch.setattr(
        service,
        "normalize_order_quantity",
        fake_normalize,
    )

    rows = service._spot_correction_sources(
        client=object(),
        sale_batch=_sale_batch(),
        legs=[leg],
        now=NOW,
    )

    assert len(rows) == 1
    assert captured["requested_qty"] == (
        Decimal("0.30")
    )
    assert captured["available_qty"] == (
        Decimal("0.30")
    )
    assert rows[0][
        "snapshot_remaining_qty"
    ] == "2"
    assert rows[0][
        "confirmed_live_qty"
    ] == "0.30"
    assert rows[0][
        "live_sellable_qty"
    ] == "0.25"
    assert rows[0][
        "remaining_sellable_usdt"
    ] == "25"


def test_final_live_preflight_builds_intent(
    monkeypatch,
):
    leg = _leg()
    persisted: list[dict] = []
    received: dict = {}

    monkeypatch.setattr(
        service,
        "_spot_correction_sources",
        lambda **kwargs: [
            _source_row()
        ],
    )
    monkeypatch.setattr(
        service,
        "_plan_snapshot",
        lambda **kwargs: {
            "close_side": "Sell",
            "side": "Sell",
        },
    )
    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_LIVE_CORRECTION_BUFFER_PCT",
        Decimal("0.10"),
    )

    preflight = SimpleNamespace(
        category="spot",
        symbol="BTCUSDT",
        position_side=None,
        close_side="Sell",
        position_idx=None,
        reduce_only=None,
        market_unit="baseCoin",
        requested_qty=Decimal("0.22"),
        available_qty=Decimal("0.25"),
        price=Decimal("100"),
        normalized_qty=Decimal("0.20"),
        normalized_notional=(
            Decimal("20")
        ),
        slices=(
            Decimal("0.10"),
            Decimal("0.10"),
        ),
        instrument_snapshot={
            "filters": "current",
        },
        position_snapshot={
            "base_coin": "BTC",
            "transferable_balance": {
                "confirmed_transferable_amount": (
                    "0.25"
                ),
            },
        },
        to_dict=lambda: {
            "normalized_qty": "0.20",
            "available_qty": "0.25",
            "price": "100",
        },
    )

    def fake_preflight(
        client,
        *,
        category,
        symbol,
        requested_qty,
        planned_close_side,
        captured_at,
    ):
        received["category"] = category
        received["symbol"] = symbol
        received["requested_qty"] = (
            requested_qty
        )
        received[
            "planned_close_side"
        ] = planned_close_side

        return preflight

    monkeypatch.setattr(
        service,
        "build_live_negative_sale_preflight",
        fake_preflight,
    )
    monkeypatch.setattr(
        service,
        "persist_new_correction_intent_without_previous",
        lambda db, *, leg, new_intent, now: (
            persisted.append(new_intent)
        ),
    )
    monkeypatch.setattr(
        service,
        "archive_terminal_intent_and_activate_next_round",
        lambda *args, **kwargs: (
            pytest.fail(
                "No previous intent exists"
            )
        ),
    )

    result = (
        service
        ._prepare_spot_correction_round(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            legs=[leg],
            decision=_decision(),
            confirmed_available_usdt=(
                Decimal("80")
            ),
            now=NOW,
        )
    )

    assert result is not None
    assert received["category"] == "spot"
    assert received["symbol"] == (
        "BTCUSDT"
    )
    assert received["requested_qty"] == (
        Decimal("0.22")
    )
    assert received[
        "planned_close_side"
    ] == "Sell"

    assert len(persisted) == 1
    intent = persisted[0]

    assert intent["requested_qty"] == (
        "0.22"
    )
    assert intent["normalized_qty"] == (
        "0.20"
    )
    assert intent["target_cash_usdt"] == (
        "20"
    )
    assert [
        item["qty"]
        for item in intent["suborders"]
    ] == [
        "0.10",
        "0.10",
    ]
    assert intent[
        "instrument_snapshot"
    ] == {
        "filters": "current",
    }
    assert intent[
        "position_snapshot"
    ]["source_live_sellable_qty"] == (
        "0.50"
    )

    assert result["posted"] is False
    assert result["normalized_qty"] == (
        "0.20"
    )
    assert result[
        "final_live_available_qty"
    ] == "0.25"


def test_final_preflight_failure_does_not_persist(
    monkeypatch,
):
    leg = _leg()

    monkeypatch.setattr(
        service,
        "_spot_correction_sources",
        lambda **kwargs: [
            _source_row()
        ],
    )
    monkeypatch.setattr(
        service,
        "_plan_snapshot",
        lambda **kwargs: {
            "close_side": "Sell",
            "side": "Sell",
        },
    )
    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_LIVE_CORRECTION_BUFFER_PCT",
        Decimal("0.10"),
    )
    monkeypatch.setattr(
        service,
        "build_live_negative_sale_preflight",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                NegativeSaleLivePreflightError(
                    "live balance is zero"
                )
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "persist_new_correction_intent_without_previous",
        lambda *args, **kwargs: (
            pytest.fail(
                "Invalid correction intent "
                "must not be persisted"
            )
        ),
    )

    with pytest.raises(
        service
        .NegativeSaleLiveBatchServiceError,
        match=(
            "Correction final live "
            "preflight failed"
        ),
    ):
        service._prepare_spot_correction_round(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            legs=[leg],
            decision=_decision(),
            confirmed_available_usdt=(
                Decimal("80")
            ),
            now=NOW,
        )