from __future__ import annotations

from decimal import Decimal

import pytest

from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)
from app.settlement.negative_sale_order_runtime import (
    confirm_prepared_suborder,
    prepared_intent_runtime_summary,
    submit_prepared_suborder,
)


class FakeBybitClient:
    def __init__(self) -> None:
        self.posts: list[
            tuple[str, dict]
        ] = []
        self.mode = "empty"

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        if path != "/v5/order/realtime":
            raise AssertionError(path)

        if self.mode == "realtime_new":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "category": "linear",
                            "symbol": "BTCUSDT",
                            "orderId": "OID-1",
                            "orderLinkId": (
                                "wbns-1-2-r0-s0"
                            ),
                            "orderStatus": "New",
                            "side": "Sell",
                            "qty": "1",
                        }
                    ]
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
        if path == "/v5/order/history":
            if self.mode == "filled":
                return [
                    {
                        "category": "linear",
                        "symbol": "BTCUSDT",
                        "orderId": "OID-1",
                        "orderLinkId": (
                            "wbns-1-2-r0-s0"
                        ),
                        "orderStatus": "Filled",
                        "side": "Sell",
                        "qty": "1",
                    }
                ]

            return []

        if path == "/v5/execution/list":
            if self.mode == "filled":
                return [
                    {
                        "execId": "EXEC-1",
                        "orderId": "OID-1",
                        "orderLinkId": (
                            "wbns-1-2-r0-s0"
                        ),
                        "symbol": "BTCUSDT",
                        "side": "Sell",
                        "execQty": "0.4",
                        "execPrice": "100",
                        "execValue": "40",
                        "execFee": "0.04",
                        "feeCurrency": "USDT",
                        "execTime": "1",
                    },
                    {
                        "execId": "EXEC-2",
                        "orderId": "OID-1",
                        "orderLinkId": (
                            "wbns-1-2-r0-s0"
                        ),
                        "symbol": "BTCUSDT",
                        "side": "Sell",
                        "execQty": "0.6",
                        "execPrice": "110",
                        "execValue": "66",
                        "execFee": "0.06",
                        "feeCurrency": "USDT",
                        "execTime": "2",
                    },
                ]

            return []

        raise AssertionError(path)

    def post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        self.posts.append(
            (
                path,
                dict(payload),
            )
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


class ErrorBybitClient(
    FakeBybitClient
):
    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        raise RuntimeError(
            "realtime unavailable"
        )

    def paginate_get(
        self,
        path: str,
        params: dict,
    ) -> list[dict]:
        raise RuntimeError(
            f"{path} unavailable"
        )


class FailingPostBybitClient(
    FakeBybitClient
):
    def post(
        self,
        path: str,
        payload: dict,
    ) -> dict:
        self.posts.append(
            (
                path,
                dict(payload),
            )
        )

        raise RuntimeError(
            "transport failure after submit"
        )


def _intent() -> dict:
    return (
        build_negative_sale_order_intent(
            sale_batch_id=1,
            leg_id=2,
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


def test_submit_and_confirmation_are_separate():
    client = FakeBybitClient()
    guard_calls: list[dict] = []
    persisted_states: list[dict] = []

    submitted_intent, submitted = (
        submit_prepared_suborder(
            client,
            raw_intent=_intent(),
            suborder_index=0,
            before_submit=(
                lambda payload: (
                    guard_calls.append(payload)
                )
            ),
            persist_state=(
                lambda state: (
                    persisted_states.append(
                        state
                    )
                )
            ),
        )
    )

    assert submitted is True
    assert len(client.posts) == 1
    assert len(guard_calls) == 1
    assert [
        state["suborders"][0]["status"]
        for state in persisted_states
    ] == [
        "submitted",
        "acknowledged",
    ]

    item = submitted_intent[
        "suborders"
    ][0]

    assert item["status"] == (
        "acknowledged"
    )
    assert item["order_id"] == "OID-1"
    assert item["reconciliation"] is not None
    assert item["reconciliation"][
        "classification"
    ]["reason"] == "order_not_found"
    assert item["reconciliation"][
        "aggregate_exec_qty"
    ] == "0"
    assert item["submit_ack"][
        "source"
    ] == "create_ack"

    client.mode = "filled"

    confirmed_intent, reconciliation = (
        confirm_prepared_suborder(
            client,
            raw_intent=submitted_intent,
            suborder_index=0,
        )
    )

    assert (
        reconciliation
        .classification
        .state
        == "terminal_success"
    )

    confirmed_item = confirmed_intent[
        "suborders"
    ][0]

    assert confirmed_item["status"] == (
        "filled"
    )

    summary = (
        prepared_intent_runtime_summary(
            confirmed_intent
        )
    )

    assert summary["all_terminal"] is True
    assert summary["all_filled"] is True
    assert summary[
        "aggregate_exec_qty"
    ] == "1.0"
    assert summary[
        "aggregate_exec_value"
    ] == "106"


def test_resume_does_not_duplicate_post():
    client = FakeBybitClient()

    first, submitted = (
        submit_prepared_suborder(
            client,
            raw_intent=_intent(),
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=lambda state: None,
        )
    )

    assert submitted is True
    assert len(client.posts) == 1

    client.mode = "realtime_new"

    second, submitted_again = (
        submit_prepared_suborder(
            client,
            raw_intent=first,
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=lambda state: None,
        )
    )

    assert submitted_again is False
    assert len(client.posts) == 1

    item = second["suborders"][0]

    assert item["status"] == (
        "pending_confirmation"
    )


def test_provider_errors_block_submit():
    client = ErrorBybitClient()

    result, submitted = (
        submit_prepared_suborder(
            client,
            raw_intent=_intent(),
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=lambda state: None,
        )
    )

    assert submitted is False
    assert client.posts == []

    item = result["suborders"][0]

    assert item["status"] == (
        "pending_confirmation"
    )

    errors = item[
        "reconciliation"
    ]["source_errors"]

    assert {
        row["source"]
        for row in errors
    } == {
        "realtime",
        "history",
        "executions",
    }


def test_acknowledged_order_not_found_is_not_resubmitted():
    client = FakeBybitClient()

    first, submitted = (
        submit_prepared_suborder(
            client,
            raw_intent=_intent(),
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=lambda state: None,
        )
    )

    assert submitted is True
    assert len(client.posts) == 1

    client.mode = "empty"

    second, submitted_again = (
        submit_prepared_suborder(
            client,
            raw_intent=first,
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=lambda state: None,
        )
    )

    assert submitted_again is False
    assert len(client.posts) == 1
    assert second["suborders"][0][
        "status"
    ] == "pending_confirmation"


def test_crash_after_durable_submit_does_not_resubmit():
    client = FailingPostBybitClient()
    persisted_states: list[dict] = []

    with pytest.raises(
        RuntimeError,
        match="transport failure after submit",
    ):
        submit_prepared_suborder(
            client,
            raw_intent=_intent(),
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=(
                lambda state: (
                    persisted_states.append(
                        state
                    )
                )
            ),
        )

    assert len(client.posts) == 1
    assert len(persisted_states) == 1

    durable_intent = persisted_states[-1]

    assert durable_intent[
        "suborders"
    ][0]["status"] == "submitted"

    resumed, submitted_again = (
        submit_prepared_suborder(
            client,
            raw_intent=durable_intent,
            suborder_index=0,
            before_submit=lambda payload: None,
            persist_state=lambda state: None,
        )
    )

    assert submitted_again is False
    assert len(client.posts) == 1
    assert resumed[
        "suborders"
    ][0]["status"] == (
        "pending_confirmation"
    )


def test_cancelled_order_with_execution_is_terminal_partial():
    from app.bybit.order_execution import (
        BybitOrderResult,
    )
    from app.bybit.order_reconciliation import (
        classify_reconciled_order,
    )

    order = BybitOrderResult(
        category="linear",
        symbol="BTCUSDT",
        order_id="OID-1",
        order_link_id=(
            "wbns-1-2-r0-s0"
        ),
        status="Cancelled",
        side="Sell",
        order_type="Market",
        qty=Decimal("1"),
        cum_exec_qty=None,
        cum_exec_value=None,
        avg_price=None,
        raw={},
    )

    classification = (
        classify_reconciled_order(
            order=order,
            aggregate_exec_qty=(
                Decimal("0.4")
            ),
        )
    )

    assert classification.state == (
        "terminal_partial"
    )
    assert classification.terminal is True
    assert classification.partial is True
    assert classification.failed is False
    assert (
        classification.execution_confirmed
        is True
    )


def test_cancelled_order_without_execution_is_terminal_failed():
    from app.bybit.order_execution import (
        BybitOrderResult,
    )
    from app.bybit.order_reconciliation import (
        classify_reconciled_order,
    )

    order = BybitOrderResult(
        category="linear",
        symbol="BTCUSDT",
        order_id="OID-1",
        order_link_id=(
            "wbns-1-2-r0-s0"
        ),
        status="Cancelled",
        side="Sell",
        order_type="Market",
        qty=Decimal("1"),
        cum_exec_qty=None,
        cum_exec_value=None,
        avg_price=None,
        raw={},
    )

    classification = (
        classify_reconciled_order(
            order=order,
            aggregate_exec_qty=Decimal("0"),
        )
    )

    assert classification.state == (
        "terminal_failed"
    )
    assert classification.terminal is True
    assert classification.failed is True
    assert classification.partial is False