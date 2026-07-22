from __future__ import annotations

from decimal import Decimal

import pytest

from app.bybit.earn import (
    BybitEarnError,
    EARN_ORDER_HISTORY_PATH,
    EARN_PLACE_ORDER_PATH,
    build_flexible_saving_redeem_payload,
    query_earn_order_by_link_id,
    submit_flexible_saving_redeem_order,
)


class FakeClient:
    def __init__(self):
        self.get_calls: list[
            tuple[str, dict]
        ] = []
        self.post_calls: list[
            tuple[str, dict]
        ] = []

        self.get_response = {
            "retCode": 0,
            "result": {
                "list": [],
            },
        }
        self.post_response = {
            "retCode": 0,
            "result": {
                "orderId": "EARN-OID-1",
                "orderLinkId": (
                    "wbne-10-20-r0"
                ),
            },
        }

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        self.get_calls.append(
            (
                path,
                dict(params),
            )
        )
        return self.get_response

    def post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        self.post_calls.append(
            (
                path,
                dict(payload),
            )
        )
        return self.post_response


def test_build_redeem_payload_is_exact():
    payload = (
        build_flexible_saving_redeem_payload(
            amount=Decimal("12.34"),
            amount_str="12.34",
            product_id="430",
            order_link_id=(
                "wbne-10-20-r0"
            ),
        )
    )

    assert payload == {
        "category": "FlexibleSaving",
        "orderType": "Redeem",
        "accountType": "FUND",
        "amount": "12.34",
        "coin": "USDT",
        "productId": "430",
        "orderLinkId": (
            "wbne-10-20-r0"
        ),
    }


def test_submit_returns_ack_without_get():
    client = FakeClient()

    ack = (
        submit_flexible_saving_redeem_order(
            client,
            amount=Decimal("12.34"),
            amount_str="12.34",
            product_id="430",
            order_link_id=(
                "wbne-10-20-r0"
            ),
        )
    )

    assert ack.order_id == (
        "EARN-OID-1"
    )
    assert ack.order_link_id == (
        "wbne-10-20-r0"
    )

    assert client.get_calls == []
    assert client.post_calls == [
        (
            EARN_PLACE_ORDER_PATH,
            {
                "category": (
                    "FlexibleSaving"
                ),
                "orderType": "Redeem",
                "accountType": "FUND",
                "amount": "12.34",
                "coin": "USDT",
                "productId": "430",
                "orderLinkId": (
                    "wbne-10-20-r0"
                ),
            },
        )
    ]


def test_submit_rejects_bybit_error():
    client = FakeClient()
    client.post_response = {
        "retCode": 10001,
        "retMsg": "invalid amount",
        "result": {},
    }

    with pytest.raises(
        BybitEarnError,
        match=(
            "request failed"
        ),
    ):
        submit_flexible_saving_redeem_order(
            client,
            amount=Decimal("12.34"),
            amount_str="12.34",
            product_id="430",
            order_link_id=(
                "wbne-10-20-r0"
            ),
        )


def test_query_parses_pending_order():
    client = FakeClient()
    client.get_response = {
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
                        "wbne-10-20-r0"
                    ),
                    "status": "Pending",
                    "productId": "430",
                }
            ],
        },
    }

    order = (
        query_earn_order_by_link_id(
            client,
            order_link_id=(
                "wbne-10-20-r0"
            ),
            category="FlexibleSaving",
            product_id="430",
        )
    )

    assert order is not None
    assert order.order_id == (
        "EARN-OID-1"
    )
    assert order.status == "Pending"
    assert order.amount == (
        Decimal("12.34")
    )

    assert client.get_calls == [
        (
            EARN_ORDER_HISTORY_PATH,
            {
                "category": (
                    "FlexibleSaving"
                ),
                "orderLinkId": (
                    "wbne-10-20-r0"
                ),
                "productId": "430",
            },
        )
    ]
    assert client.post_calls == []


def test_duplicate_history_rows_fail_closed():
    client = FakeClient()

    row = {
        "coin": "USDT",
        "orderValue": "12.34",
        "orderType": "Redeem",
        "orderId": "EARN-OID-1",
        "orderLinkId": (
            "wbne-10-20-r0"
        ),
        "status": "Pending",
        "productId": "430",
    }

    client.get_response = {
        "retCode": 0,
        "result": {
            "list": [
                dict(row),
                {
                    **row,
                    "orderId": (
                        "EARN-OID-2"
                    ),
                },
            ],
        },
    }

    with pytest.raises(
        BybitEarnError,
        match=(
            "multiple matching rows"
        ),
    ):
        query_earn_order_by_link_id(
            client,
            order_link_id=(
                "wbne-10-20-r0"
            ),
            category="FlexibleSaving",
            product_id="430",
        )