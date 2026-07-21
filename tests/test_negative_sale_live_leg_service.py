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
    def fake_prepare(
        db,
        *,
        sale_batch,
        leg,
        execution_round,
        now,
    ):
        intent = _intent()
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