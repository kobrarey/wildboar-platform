from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from app.bybit.order_execution import (
    BybitOrderExecutionError,
    build_market_order_payload,
    create_market_order_from_payload,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)
from app.settlement.negative_sale_order_runtime import (
    confirm_prepared_suborder,
    submit_prepared_suborder,
)


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
            slices=(Decimal("1"),),
        ).to_dict()
    )


class EmptyOrderClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []

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
        return []

    def post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        self.posts.append(
            deepcopy(payload)
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


class FilledOrderClient:
    def __init__(
        self,
        *,
        execution_qty: str,
    ) -> None:
        self.execution_qty = (
            execution_qty
        )

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        if path == "/v5/order/realtime":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "category": (
                                "linear"
                            ),
                            "symbol": (
                                "BTCUSDT"
                            ),
                            "orderId": (
                                "OID-1"
                            ),
                            "orderLinkId": (
                                "wbns-10-20-r0-s0"
                            ),
                            "orderStatus": (
                                "Filled"
                            ),
                            "side": "Sell",
                            "orderType": (
                                "Market"
                            ),
                            "qty": "1",
                            "cumExecQty": "1",
                            "cumExecValue": (
                                "100"
                            ),
                            "avgPrice": "100",
                        }
                    ],
                },
            }

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
        if path == "/v5/execution/list":
            qty = Decimal(
                self.execution_qty
            )

            return [
                {
                    "execId": "EXEC-1",
                    "orderId": "OID-1",
                    "orderLinkId": (
                        "wbns-10-20-r0-s0"
                    ),
                    "symbol": "BTCUSDT",
                    "side": "Sell",
                    "execQty": str(qty),
                    "execPrice": "100",
                    "execValue": str(
                        qty * Decimal("100")
                    ),
                    "execFee": "0.01",
                    "feeCurrency": "USDT",
                    "execTime": "1",
                }
            ]

        return []


def test_submit_posts_exact_persisted_payload():
    client = EmptyOrderClient()
    intent = _intent()

    persisted_payload = deepcopy(
        intent["suborders"][0][
            "payload"
        ]
    )

    updated, posted = (
        submit_prepared_suborder(
            client,
            raw_intent=intent,
            suborder_index=0,
            before_submit=(
                lambda payload: None
            ),
            persist_state=(
                lambda state: None
            ),
        )
    )

    assert posted is True
    assert len(client.posts) == 1
    assert (
        client.posts[0]
        == persisted_payload
    )
    assert updated[
        "suborders"
    ][0]["status"] == "acknowledged"


def test_tampered_payload_is_rejected_before_post():
    client = EmptyOrderClient()

    payload = (
        build_market_order_payload(
            category="linear",
            symbol="BTCUSDT",
            side="Sell",
            qty=Decimal("1"),
            order_link_id=(
                "wbns-10-20-r0-s0"
            ),
            reduce_only=True,
            position_idx=1,
        )
    )
    payload["unexpected"] = "value"

    with pytest.raises(
        BybitOrderExecutionError,
        match="unexpected keys",
    ):
        create_market_order_from_payload(
            client,
            payload=payload,
        )

    assert client.posts == []


def test_filled_status_with_undercovered_executions_stays_pending():
    intent = _intent()
    item = intent["suborders"][0]

    item["status"] = "acknowledged"
    item["order_id"] = "OID-1"

    updated, reconciliation = (
        confirm_prepared_suborder(
            FilledOrderClient(
                execution_qty="0.4"
            ),
            raw_intent=intent,
            suborder_index=0,
        )
    )

    updated_item = (
        updated["suborders"][0]
    )

    assert (
        reconciliation
        .aggregate_exec_qty
        == Decimal("0.4")
    )
    assert updated_item["status"] == (
        "partially_filled_"
        "pending_confirmation"
    )
    assert (
        "terminal_at"
        not in updated_item
    )


def test_filled_status_with_full_execution_coverage_is_terminal():
    intent = _intent()
    item = intent["suborders"][0]

    item["status"] = "acknowledged"
    item["order_id"] = "OID-1"

    updated, reconciliation = (
        confirm_prepared_suborder(
            FilledOrderClient(
                execution_qty="1"
            ),
            raw_intent=intent,
            suborder_index=0,
        )
    )

    updated_item = (
        updated["suborders"][0]
    )

    assert (
        reconciliation
        .aggregate_exec_qty
        == Decimal("1")
    )
    assert (
        updated_item["status"]
        == "filled"
    )
    assert (
        updated_item.get(
            "terminal_at"
        )
        is not None
    )