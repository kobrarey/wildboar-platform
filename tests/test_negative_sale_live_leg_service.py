from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import app.settlement.negative_sale_live_leg_service as service
from app.settlement.negative_sale_live_persistence import (
    validate_runtime_intent_transition,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)

import pytest

from app.settlement.negative_sale_live_preflight import (
    NegativeSaleLivePreflight,
)


class FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.mode = "empty"

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        return {
            "retCode": 0,
            "result": {
                "list": [],
            },
        }

    def paginate_get(
        self,
        path: str,
        params: dict,
    ) -> list[dict]:
        if (
            self.mode == "filled"
            and path
            == "/v5/order/history"
        ):
            return [
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "orderId": "OID-1",
                    "orderLinkId": (
                        "wbns-10-20-r0-s0"
                    ),
                    "orderStatus": "Filled",
                    "side": "Sell",
                    "qty": "0.6",
                }
            ]

        if (
            self.mode == "filled"
            and path
            == "/v5/execution/list"
        ):
            return [
                {
                    "execId": "EXEC-1",
                    "orderId": "OID-1",
                    "orderLinkId": (
                        "wbns-10-20-r0-s0"
                    ),
                    "symbol": "BTCUSDT",
                    "side": "Sell",
                    "execQty": "0.6",
                    "execPrice": "100",
                    "execValue": "60",
                    "execFee": "0.06",
                    "feeCurrency": "USDT",
                    "execTime": "1",
                }
            ]

        return []

    def post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        self.posts.append(
            dict(payload)
        )

        return {
            "retCode": 0,
            "result": {
                "orderId": "OID-1",
                "orderLinkId": (
                    payload["orderLinkId"]
                ),
            },
        }


def _intent() -> dict:
    return (
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


def _instrument_snapshot(
    *,
    qty_step: str,
) -> dict:
    return {
        "category": "linear",
        "symbol": "BTCUSDT",
        "status": "Trading",
        "trading": True,
        "baseCoin": "BTC",
        "quoteCoin": "USDT",
        "settleCoin": "USDT",
        "contractType": (
            "LinearPerpetual"
        ),
        "qtyStep": qty_step,
        "minOrderQty": "0.001",
        "minNotionalValue": "5",
        "minOrderAmt": None,
        "maxMarketOrderQty": "100",
        "maxOrderQty": "100",
        "basePrecision": None,
        "quotePrecision": None,
        "tickSize": "0.1",
        "preflight_complete": True,
        "completeness_reasons": [],
    }


def _objects():
    sale_batch = SimpleNamespace(
        id=10,
        fund_id=1,
    )
    settlement_batch = SimpleNamespace(
        id=30,
    )
    leg = SimpleNamespace(
        id=20,
        leg_index=1,
        suborders_json=None,
    )

    return (
        sale_batch,
        settlement_batch,
        leg,
    )


def _install_persistence(
    monkeypatch,
    *,
    leg,
    guard_calls,
):
    def fake_live_intent_builder(
        client,
        *,
        sale_batch,
        leg,
        execution_round,
        now,
    ):
        return deepcopy(
            _intent()
        )

    def fake_prepare(
        db,
        *,
        sale_batch,
        leg,
        execution_round,
        prepared_intent,
        now,
    ):
        intent = deepcopy(
            prepared_intent
        )
        leg.suborders_json = deepcopy(
            intent
        )
        return deepcopy(intent)

    def fake_persist(
        db,
        *,
        leg_id,
        raw_intent,
        now,
    ):
        existing = leg.suborders_json

        assert isinstance(
            existing,
            dict,
        )

        validate_runtime_intent_transition(
            existing,
            raw_intent,
            enforce_submit_claim=True,
        )

        leg.suborders_json = deepcopy(
            raw_intent
        )
        return leg

    def fake_guard(
        db,
        **kwargs,
    ):
        guard_calls.append(kwargs)
        return SimpleNamespace(
            allowed=True
        )

    def fake_submit_revalidation(
        client,
        *,
        intent,
        payload,
        now,
    ):
        return SimpleNamespace(
            to_dict=lambda: {
                "validated": True,
                "read_only": True,
                "category": payload[
                    "category"
                ],
                "symbol": payload[
                    "symbol"
                ],
                "qty": payload["qty"],
            }
        )

    monkeypatch.setattr(
        service,
        "build_live_prepared_intent_for_leg",
        fake_live_intent_builder,
    )
    monkeypatch.setattr(
        service,
        "persist_prepared_intent_before_submit",
        fake_prepare,
    )
    monkeypatch.setattr(
        service,
        "persist_runtime_intent_state",
        fake_persist,
    )
    monkeypatch.setattr(
        service,
        "require_bybit_negative_sale_order_guard",
        fake_guard,
    )
    monkeypatch.setattr(
        service,
        "validate_prepared_suborder_before_submit",
        fake_submit_revalidation,
    )


def test_prepare_and_submit_are_separate_cycles(
    monkeypatch,
):
    client = FakeClient()
    guard_calls: list[dict] = []

    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()

    _install_persistence(
        monkeypatch,
        leg=leg,
        guard_calls=guard_calls,
    )

    prepared = (
        service.resume_live_leg_once(
            object(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            execution_round=0,
        )
    )

    assert prepared.action == "prepare"
    assert prepared.posted is False
    assert client.posts == []
    assert prepared.intent[
        "suborders"
    ][0]["status"] == "prepared"

    submitted = (
        service.resume_live_leg_once(
            object(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            execution_round=0,
        )
    )

    assert submitted.action == "submit"
    assert submitted.posted is True
    assert len(client.posts) == 1
    assert len(guard_calls) == 1
    assert submitted.intent[
        "suborders"
    ][0]["status"] == (
        "acknowledged"
    )
    assert submitted.intent[
        "suborders"
    ][1]["status"] == "prepared"
    assert submitted.summary[
        "all_terminal"
    ] is False


def test_confirmation_does_not_submit_next_slice(
    monkeypatch,
):
    client = FakeClient()
    guard_calls: list[dict] = []

    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()

    _install_persistence(
        monkeypatch,
        leg=leg,
        guard_calls=guard_calls,
    )

    prepared = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert prepared.action == "prepare"
    assert len(client.posts) == 0

    submitted = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert submitted.posted is True
    assert len(client.posts) == 1

    client.mode = "filled"

    confirmed = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert confirmed.action == "confirm"
    assert confirmed.posted is False
    assert len(client.posts) == 1
    assert confirmed.intent[
        "suborders"
    ][0]["status"] == "filled"
    assert confirmed.intent[
        "suborders"
    ][1]["status"] == "prepared"


def test_next_slice_requires_fourth_cycle(
    monkeypatch,
):
    client = FakeClient()
    guard_calls: list[dict] = []

    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()

    _install_persistence(
        monkeypatch,
        leg=leg,
        guard_calls=guard_calls,
    )

    first = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert first.action == "prepare"
    assert client.posts == []

    second = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert second.action == "submit"
    assert second.posted is True
    assert len(client.posts) == 1

    client.mode = "filled"

    third = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert third.action == "confirm"
    assert third.posted is False
    assert len(client.posts) == 1

    client.mode = "empty"

    fourth = service.resume_live_leg_once(
        object(),
        client=client,
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        leg=leg,
        execution_round=0,
    )

    assert fourth.action == "submit"
    assert fourth.posted is True
    assert len(client.posts) == 2
    assert client.posts[1][
        "orderLinkId"
    ] == "wbns-10-20-r0-s1"


def test_changed_instrument_filter_blocks_before_guard_and_post(
    monkeypatch,
):
    client = FakeClient()

    (
        sale_batch,
        settlement_batch,
        leg,
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
            slices=(Decimal("1"),),
            instrument_snapshot=(
                _instrument_snapshot(
                    qty_step="0.1",
                )
            ),
            position_snapshot={
                "position_side": "Buy",
                "close_side": "Sell",
                "position_idx": 1,
                "available_qty": "1",
            },
        ).to_dict()
    )

    leg.suborders_json = deepcopy(intent)

    guard_called = False
    persistence_called = False

    def forbidden_guard(
        *args,
        **kwargs,
    ):
        nonlocal guard_called
        guard_called = True

        raise AssertionError(
            "Operation Guard must not run "
            "after failed revalidation"
        )

    def forbidden_persistence(
        *args,
        **kwargs,
    ):
        nonlocal persistence_called
        persistence_called = True

        raise AssertionError(
            "Submitted state must not persist "
            "after failed revalidation"
        )

    def changed_live_preflight(
        client_arg,
        *,
        category,
        symbol,
        requested_qty,
        planned_position_side,
        planned_close_side,
        planned_position_idx,
        captured_at,
    ):
        return NegativeSaleLivePreflight(
            category="linear",
            symbol="BTCUSDT",
            position_side="Buy",
            close_side="Sell",
            position_idx=1,
            reduce_only=True,
            market_unit=None,
            requested_qty=Decimal("1"),
            available_qty=Decimal("1"),
            price=Decimal("100"),
            normalized_qty=Decimal("1"),
            normalized_notional=(
                Decimal("100")
            ),
            slices=(Decimal("1"),),
            instrument_snapshot=(
                _instrument_snapshot(
                    qty_step="0.2",
                )
            ),
            position_snapshot={
                "position_side": "Buy",
                "close_side": "Sell",
                "position_idx": 1,
                "available_qty": "1",
            },
            quantity_preflight={
                "requested_qty": "1",
                "available_qty": "1",
                "normalized_qty": "1",
                "slices": ["1"],
                "eligible": True,
            },
            captured_at=captured_at,
        )

    monkeypatch.setattr(
        service,
        "build_live_negative_sale_preflight",
        changed_live_preflight,
    )
    monkeypatch.setattr(
        service,
        "require_bybit_negative_sale_order_guard",
        forbidden_guard,
    )
    monkeypatch.setattr(
        service,
        "persist_runtime_intent_state",
        forbidden_persistence,
    )

    with pytest.raises(
        service
        .NegativeSalePreSubmitRevalidationError,
        match=(
            "instrument contract changed"
        ),
    ):
        service.resume_live_leg_once(
            object(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            execution_round=0,
        )

    assert guard_called is False
    assert persistence_called is False
    assert client.posts == []

    assert leg.suborders_json[
        "suborders"
    ][0]["status"] == "prepared"


def test_unchanged_instrument_contract_passes_revalidation(
    monkeypatch,
):
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
            slices=(Decimal("1"),),
            instrument_snapshot=(
                _instrument_snapshot(
                    qty_step="0.1",
                )
            ),
            position_snapshot={
                "position_side": "Buy",
                "close_side": "Sell",
                "position_idx": 1,
                "available_qty": "1",
            },
        ).to_dict()
    )

    payload = deepcopy(
        intent["suborders"][0]["payload"]
    )

    def unchanged_live_preflight(
        client_arg,
        *,
        category,
        symbol,
        requested_qty,
        planned_position_side,
        planned_close_side,
        planned_position_idx,
        captured_at,
    ):
        assert category == "linear"
        assert symbol == "BTCUSDT"
        assert requested_qty == Decimal("1")
        assert planned_position_side == (
            "long"
        )
        assert planned_close_side == (
            "Sell"
        )
        assert planned_position_idx == 1

        return NegativeSaleLivePreflight(
            category="linear",
            symbol="BTCUSDT",
            position_side="Buy",
            close_side="Sell",
            position_idx=1,
            reduce_only=True,
            market_unit=None,
            requested_qty=Decimal("1"),
            available_qty=Decimal("1"),
            price=Decimal("100"),
            normalized_qty=Decimal("1"),
            normalized_notional=(
                Decimal("100")
            ),
            slices=(Decimal("1"),),
            instrument_snapshot=(
                _instrument_snapshot(
                    qty_step="0.1",
                )
            ),
            position_snapshot={
                "position_side": "Buy",
                "close_side": "Sell",
                "position_idx": 1,
                "available_qty": "1",
            },
            quantity_preflight={
                "requested_qty": "1",
                "available_qty": "1",
                "normalized_qty": "1",
                "slices": ["1"],
                "eligible": True,
            },
            captured_at=captured_at,
        )

    monkeypatch.setattr(
        service,
        "build_live_negative_sale_preflight",
        unchanged_live_preflight,
    )

    result = (
        service
        .validate_prepared_suborder_before_submit(
            object(),
            intent=intent,
            payload=payload,
        )
    )

    assert result.category == "linear"
    assert result.symbol == "BTCUSDT"
    assert result.close_side == "Sell"
    assert result.position_idx == 1
    assert result.reduce_only is True
    assert result.normalized_qty == (
        Decimal("1")
    )
    assert result.slices == (
        Decimal("1"),
    )
