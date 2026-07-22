from __future__ import annotations

from copy import deepcopy
from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import app.settlement.negative_sale_earn_live_service as service
from app.settlement.negative_sale_earn_runtime import (
    EARN_RUNTIME_STATUS_ACKNOWLEDGED,
    EARN_RUNTIME_STATUS_PENDING,
    EARN_RUNTIME_STATUS_SUCCESS,
    build_negative_sale_earn_intent,
)
from app.settlement.statuses import (
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
    SALE_LEG_STATUS_BUFFER_AVAILABLE,
    SALE_LEG_STATUS_PENDING_CONFIRMATION,
    SALE_LEG_STATUS_USDT_EARN_REDEEMED,
)


NOW = datetime(
    2026,
    7,
    22,
    17,
    0,
    tzinfo=timezone.utc,
)


class FakeClient:
    def __init__(self):
        self.posts: list[
            tuple[str, dict]
        ] = []
        self.gets: list[
            tuple[str, dict]
        ] = []

        self.history_status = None

    def post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        self.posts.append(
            (
                path,
                deepcopy(payload),
            )
        )

        return {
            "retCode": 0,
            "result": {
                "orderId": "EARN-OID-1",
                "orderLinkId": (
                    payload["orderLinkId"]
                ),
            },
        }

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        self.gets.append(
            (
                path,
                deepcopy(params),
            )
        )

        if (
            path
            != "/v5/earn/order"
        ):
            raise AssertionError(
                f"Unexpected GET: {path}"
            )

        if self.history_status is None:
            return {
                "retCode": 0,
                "result": {
                    "list": [],
                },
            }

        return {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "coin": "USDT",
                        "orderValue": "12.34",
                        "orderType": "Redeem",
                        "orderId": (
                            "EARN-OID-1"
                        ),
                        "orderLinkId": (
                            "wbne-test"
                        ),
                        "status": (
                            self.history_status
                        ),
                        "productId": "430",
                    }
                ],
            },
        }


class FakeDB:
    pass


def _objects():
    sale_batch = SimpleNamespace(
        id=10,
        fund_id=1,
        required_master_usdt=(
            Decimal("100")
        ),
        status=(
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        ),
    )
    settlement_batch = (
        SimpleNamespace(
            id=20,
            fund_id=1,
        )
    )
    leg = SimpleNamespace(
        id=30,
        sale_batch_id=10,
        settlement_batch_id=20,
        fund_id=1,
        leg_index=1,
        leg_type="usdt_earn_buffer",
        target_cash_usdt=(
            Decimal("30")
        ),
        status=(
            SALE_LEG_STATUS_BUFFER_AVAILABLE
        ),
        suborders_json=None,
        execution_error=None,
    )

    return (
        sale_batch,
        settlement_batch,
        leg,
    )


def _intent(
    *,
    status: str = "prepared",
):
    intent = (
        build_negative_sale_earn_intent(
            sale_batch_id=10,
            leg_id=30,
            leg_index=1,
            execution_round=0,
            product_id="430",
            product_precision=2,
            target_cash_usdt="30",
            confirmed_available_usdt=(
                "40"
            ),
            available_earn_usdt="20",
            needed_from_earn_usdt=(
                "12.34"
            ),
            amount="12.34",
            amount_str="12.34",
            order_link_id="wbne-test",
            prepared_at=NOW,
        )
    )

    intent["status"] = status

    if status in {
        EARN_RUNTIME_STATUS_ACKNOWLEDGED,
        EARN_RUNTIME_STATUS_PENDING,
        EARN_RUNTIME_STATUS_SUCCESS,
    }:
        intent["submitted_at"] = (
            NOW.isoformat()
        )
        intent["order_id"] = (
            "EARN-OID-1"
        )

    if (
        status
        == EARN_RUNTIME_STATUS_ACKNOWLEDGED
    ):
        intent["acknowledged_at"] = (
            NOW.isoformat()
        )
        intent["submit_ack"] = {
            "order_id": "EARN-OID-1",
            "order_link_id": (
                "wbne-test"
            ),
            "raw": {},
        }

    if status == EARN_RUNTIME_STATUS_SUCCESS:
        intent["acknowledged_at"] = (
            NOW.isoformat()
        )
        intent["confirmed_at"] = (
            NOW.isoformat()
        )
        intent["redeemed_usdt"] = (
            "12.34"
        )
        intent["submit_ack"] = {
            "order_id": "EARN-OID-1",
            "order_link_id": (
                "wbne-test"
            ),
            "raw": {},
        }

    return intent


def _install_fake_persistence(
    monkeypatch,
):
    persisted: list[dict] = []

    def fake_persist(
        db,
        *,
        leg,
        raw_intent,
        now,
    ):
        state = deepcopy(
            raw_intent
        )
        persisted.append(state)
        leg.suborders_json = state

        if (
            state["status"]
            in {
                "submitted",
                "acknowledged",
                "pending_confirmation",
            }
        ):
            leg.status = (
                SALE_LEG_STATUS_PENDING_CONFIRMATION
            )

        if (
            state["status"]
            == EARN_RUNTIME_STATUS_SUCCESS
        ):
            leg.status = (
                SALE_LEG_STATUS_USDT_EARN_REDEEMED
            )

        return deepcopy(state)

    monkeypatch.setattr(
        service,
        "persist_negative_sale_earn_state",
        fake_persist,
    )

    return persisted


def test_first_cycle_prepares_without_post(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()
    persisted = (
        _install_fake_persistence(
            monkeypatch
        )
    )

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: (
            SimpleNamespace(
                confirmed_transferable_amount=(
                    Decimal("40")
                ),
                to_dict=lambda: {
                    "confirmed_transferable_"
                    "amount": "40",
                },
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "resolve_flexible_saving_product",
        lambda *args, **kwargs: (
            SimpleNamespace(
                product_id="430",
                precision=2,
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "total_flexible_saving_available_amount",
        lambda *args, **kwargs: (
            Decimal("20")
        ),
    )

    client = FakeClient()

    result = (
        service
        .resume_negative_sale_earn_once(
            FakeDB(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result is not None
    assert result.action == (
        "earn_prepare"
    )
    assert result.posted is False
    assert (
        result.has_pending_action
        is False
    )
    assert len(persisted) == 1
    assert persisted[0]["status"] == (
        "prepared"
    )
    assert persisted[0]["amount"] == "20"
    assert client.posts == []


def test_second_cycle_submits_once_and_persists_ack(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()
    leg.suborders_json = _intent()

    persisted = (
        _install_fake_persistence(
            monkeypatch
        )
    )
    guarded: list[dict] = []

    monkeypatch.setattr(
        service,
        "require_bybit_earn_redeem_guard",
        lambda *args, **kwargs: (
            guarded.append(
                deepcopy(
                    kwargs["metadata"][
                        "exact_payload"
                    ]
                )
            )
        ),
    )

    client = FakeClient()

    result = (
        service
        .resume_negative_sale_earn_once(
            FakeDB(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result is not None
    assert result.action == (
        "earn_submit"
    )
    assert result.posted is True
    assert result.has_pending_action is True
    assert len(client.posts) == 1
    assert len(guarded) == 1

    assert [
        state["status"]
        for state in persisted
    ] == [
        "submitted",
        "acknowledged",
    ]


def test_pending_confirmation_never_posts(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()
    leg.status = (
        SALE_LEG_STATUS_PENDING_CONFIRMATION
    )
    leg.suborders_json = _intent(
        status=(
            EARN_RUNTIME_STATUS_ACKNOWLEDGED
        )
    )

    persisted = (
        _install_fake_persistence(
            monkeypatch
        )
    )
    client = FakeClient()
    client.history_status = "Pending"

    result = (
        service
        .resume_negative_sale_earn_once(
            FakeDB(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result is not None
    assert result.action == (
        "earn_confirm"
    )
    assert result.posted is False
    assert result.has_pending_action is True
    assert persisted[-1]["status"] == (
        EARN_RUNTIME_STATUS_PENDING
    )
    assert client.posts == []


def test_success_confirmation_finishes_earn_step(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()
    leg.status = (
        SALE_LEG_STATUS_PENDING_CONFIRMATION
    )
    leg.suborders_json = _intent(
        status=(
            EARN_RUNTIME_STATUS_ACKNOWLEDGED
        )
    )

    persisted = (
        _install_fake_persistence(
            monkeypatch
        )
    )
    client = FakeClient()
    client.history_status = "Success"

    result = (
        service
        .resume_negative_sale_earn_once(
            FakeDB(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result is not None
    assert result.action == (
        "earn_confirm"
    )
    assert result.reason == (
        "earn_redeem_confirmed"
    )
    assert result.posted is False
    assert (
        result.has_pending_action
        is False
    )
    assert persisted[-1]["status"] == (
        EARN_RUNTIME_STATUS_SUCCESS
    )
    assert client.posts == []


def test_terminal_success_allows_order_machine_to_continue():
    (
        sale_batch,
        settlement_batch,
        leg,
    ) = _objects()
    leg.status = (
        SALE_LEG_STATUS_USDT_EARN_REDEEMED
    )
    leg.suborders_json = _intent(
        status=(
            EARN_RUNTIME_STATUS_SUCCESS
        )
    )

    result = (
        service
        .resume_negative_sale_earn_once(
            FakeDB(),
            client=FakeClient(),
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result is None