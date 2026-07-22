from __future__ import annotations

from copy import deepcopy
from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal

import pytest

from app.settlement.negative_sale_earn_runtime import (
    EARN_RUNTIME_STATUS_ACKNOWLEDGED,
    EARN_RUNTIME_STATUS_FAILED,
    EARN_RUNTIME_STATUS_PENDING,
    EARN_RUNTIME_STATUS_SUBMITTED,
    EARN_RUNTIME_STATUS_SUCCESS,
    NegativeSaleEarnRuntimeError,
    build_negative_sale_earn_intent,
    confirm_negative_sale_earn_once,
    submit_negative_sale_earn_once,
    validate_earn_runtime_transition,
)


NOW = datetime(
    2026,
    7,
    22,
    16,
    0,
    tzinfo=timezone.utc,
)


def _intent():
    return build_negative_sale_earn_intent(
        sale_batch_id=10,
        leg_id=20,
        leg_index=1,
        execution_round=0,
        product_id="430",
        product_precision=2,
        target_cash_usdt="20",
        confirmed_available_usdt="80",
        available_earn_usdt="15",
        needed_from_earn_usdt="12.34",
        amount="12.34",
        amount_str="12.34",
        order_link_id="wbne-10-20-r0",
        prepared_at=NOW,
    )


class FakeClient:
    def __init__(self):
        self.posts: list[
            tuple[str, dict]
        ] = []
        self.gets: list[
            tuple[str, dict]
        ] = []

        self.raise_on_post = False
        self.history_status = None
        self.history_amount = "12.34"

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

        if self.raise_on_post:
            raise RuntimeError(
                "unknown POST outcome"
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
                        "orderValue": (
                            self.history_amount
                        ),
                        "orderType": "Redeem",
                        "orderId": (
                            "EARN-OID-1"
                        ),
                        "orderLinkId": (
                            "wbne-10-20-r0"
                        ),
                        "status": (
                            self.history_status
                        ),
                        "productId": "430",
                    }
                ],
            },
        }


def test_submit_persists_before_post_and_ack():
    client = FakeClient()
    persisted: list[dict] = []
    guard_payloads: list[dict] = []

    result, posted = (
        submit_negative_sale_earn_once(
            client,
            raw_intent=_intent(),
            before_submit=lambda payload: (
                guard_payloads.append(
                    deepcopy(payload)
                )
            ),
            persist_state=lambda state: (
                persisted.append(
                    deepcopy(state)
                )
            ),
            now=NOW,
        )
    )

    assert posted is True
    assert len(guard_payloads) == 1
    assert len(persisted) == 2

    assert persisted[0]["status"] == (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    assert persisted[0]["order_id"] is None

    assert persisted[1]["status"] == (
        EARN_RUNTIME_STATUS_ACKNOWLEDGED
    )
    assert persisted[1]["order_id"] == (
        "EARN-OID-1"
    )

    assert result["status"] == (
        EARN_RUNTIME_STATUS_ACKNOWLEDGED
    )
    assert len(client.posts) == 1


def test_unknown_post_outcome_keeps_submitted_claim():
    client = FakeClient()
    client.raise_on_post = True
    persisted: list[dict] = []

    with pytest.raises(
        RuntimeError,
        match="unknown POST outcome",
    ):
        submit_negative_sale_earn_once(
            client,
            raw_intent=_intent(),
            before_submit=lambda payload: None,
            persist_state=lambda state: (
                persisted.append(
                    deepcopy(state)
                )
            ),
            now=NOW,
        )

    assert len(persisted) == 1
    assert persisted[0]["status"] == (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    assert len(client.posts) == 1


def test_submitted_state_is_not_resubmitted():
    client = FakeClient()
    intent = _intent()
    intent["status"] = (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    intent["submitted_at"] = (
        NOW.isoformat()
    )

    result, posted = (
        submit_negative_sale_earn_once(
            client,
            raw_intent=intent,
            before_submit=lambda payload: (
                pytest.fail(
                    "guard must not run"
                )
            ),
            persist_state=lambda state: (
                pytest.fail(
                    "state must not change"
                )
            ),
            now=NOW,
        )
    )

    assert posted is False
    assert result["status"] == (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    assert client.posts == []


def test_confirmation_pending_is_durable():
    client = FakeClient()
    client.history_status = "Pending"
    persisted: list[dict] = []

    intent = _intent()
    intent["status"] = (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    intent["submitted_at"] = (
        NOW.isoformat()
    )

    result = confirm_negative_sale_earn_once(
        client,
        raw_intent=intent,
        persist_state=lambda state: (
            persisted.append(
                deepcopy(state)
            )
        ),
        now=NOW,
    )

    assert result["status"] == (
        EARN_RUNTIME_STATUS_PENDING
    )
    assert result["redeemed_usdt"] == "0"
    assert len(persisted) == 1
    assert client.posts == []


def test_confirmation_success_uses_exact_amount():
    client = FakeClient()
    client.history_status = "Success"
    persisted: list[dict] = []

    intent = _intent()
    intent["status"] = (
        EARN_RUNTIME_STATUS_ACKNOWLEDGED
    )
    intent["submitted_at"] = (
        NOW.isoformat()
    )
    intent["acknowledged_at"] = (
        NOW.isoformat()
    )
    intent["order_id"] = "EARN-OID-1"
    intent["submit_ack"] = {
        "order_id": "EARN-OID-1",
        "order_link_id": (
            "wbne-10-20-r0"
        ),
        "raw": {},
    }

    result = confirm_negative_sale_earn_once(
        client,
        raw_intent=intent,
        persist_state=lambda state: (
            persisted.append(
                deepcopy(state)
            )
        ),
        now=NOW,
    )

    assert result["status"] == (
        EARN_RUNTIME_STATUS_SUCCESS
    )
    assert result["redeemed_usdt"] == (
        "12.34"
    )
    assert result["confirmed_at"] is not None
    assert len(persisted) == 1


def test_success_with_amount_mismatch_fails_closed():
    client = FakeClient()
    client.history_status = "Success"
    client.history_amount = "11.00"
    persisted: list[dict] = []

    intent = _intent()
    intent["status"] = (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    intent["submitted_at"] = (
        NOW.isoformat()
    )

    result = confirm_negative_sale_earn_once(
        client,
        raw_intent=intent,
        persist_state=lambda state: (
            persisted.append(
                deepcopy(state)
            )
        ),
        now=NOW,
    )

    assert result["status"] == (
        EARN_RUNTIME_STATUS_FAILED
    )
    assert result["failure_reason"] == (
        "confirmed_earn_amount_mismatch"
    )
    assert result["redeemed_usdt"] == "0"


def test_terminal_transition_cannot_reopen():
    previous = _intent()
    previous["status"] = (
        EARN_RUNTIME_STATUS_FAILED
    )
    previous["failed_at"] = NOW.isoformat()
    previous["failure_reason"] = (
        "bybit_earn_order_failed"
    )

    updated = deepcopy(previous)
    updated["status"] = (
        EARN_RUNTIME_STATUS_PENDING
    )
    updated["failed_at"] = None
    updated["failure_reason"] = None

    with pytest.raises(
        NegativeSaleEarnRuntimeError,
        match="Illegal Earn runtime transition",
    ):
        validate_earn_runtime_transition(
            previous,
            updated,
        )