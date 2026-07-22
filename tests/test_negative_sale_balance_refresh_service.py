from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

from app.settlement.negative_sale_balance_reconciliation import (
    append_confirmed_balance_refresh,
)
from app.settlement.negative_sale_balance_refresh_service import (
    next_unreconciled_terminal_action,
    resume_negative_sale_balance_refresh_once,
    terminal_balance_refresh_actions,
)
from app.settlement.negative_sale_earn_runtime import (
    build_negative_sale_earn_intent,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)


NOW = datetime(
    2026,
    7,
    22,
    21,
    0,
    tzinfo=timezone.utc,
)


def _order_intent(
    *,
    status: str,
) -> dict:
    intent = (
        build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=30,
            execution_round=0,
            category="spot",
            symbol="BTCUSDT",
            position_side=None,
            close_side="Sell",
            position_idx=None,
            reduce_only=None,
            market_unit="baseCoin",
            requested_qty=(
                Decimal("0.10")
            ),
            normalized_qty=(
                Decimal("0.10")
            ),
            target_cash_usdt=(
                Decimal("10")
            ),
            slices=(
                Decimal("0.10"),
            ),
            prepared_at=NOW,
        )
        .to_dict()
    )

    item = intent["suborders"][0]
    item["status"] = status

    if status in {
        "filled",
        "terminal_partial",
    }:
        item["order_id"] = "OID-1"
        item["submitted_at"] = (
            NOW.isoformat()
        )
        item["acknowledged_at"] = (
            NOW.isoformat()
        )
        item["terminal_at"] = (
            NOW.isoformat()
        )
        item["reconciliation"] = {
            "aggregate_exec_qty": (
                "0.10"
            ),
            "aggregate_exec_value": (
                "10"
            ),
            "fees_by_currency": {
                "USDT": "0.01",
            },
        }

    return intent


def _earn_success_intent() -> dict:
    intent = (
        build_negative_sale_earn_intent(
            sale_batch_id=10,
            leg_id=20,
            leg_index=1,
            execution_round=0,
            product_id="USDT-FLEX",
            product_precision=2,
            target_cash_usdt="20",
            confirmed_available_usdt=(
                "80"
            ),
            available_earn_usdt="50",
            needed_from_earn_usdt="20",
            amount="20",
            amount_str="20.00",
            order_link_id=(
                "wbne-terminal-test"
            ),
            prepared_at=NOW,
        )
    )

    intent["status"] = "success"
    intent["order_id"] = "EARN-OID-1"
    intent["confirmed_at"] = (
        NOW.isoformat()
    )
    intent["last_checked_at"] = (
        NOW.isoformat()
    )
    intent["redeemed_usdt"] = "20"

    return intent


def _sale_batch():
    return SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        reconciliation_json=None,
    )


def _settlement_batch():
    return SimpleNamespace(
        id=40,
    )


def _order_leg(
    *,
    status: str,
):
    return SimpleNamespace(
        id=30,
        leg_index=2,
        suborders_json=(
            _order_intent(
                status=status
            )
        ),
    )


def _earn_leg():
    return SimpleNamespace(
        id=20,
        leg_index=1,
        suborders_json=(
            _earn_success_intent()
        ),
    )


def _balance_payload(
    amount: str,
) -> dict:
    return {
        "account_type": "UNIFIED",
        "destination_account_type": (
            "FUND"
        ),
        "coin": "USDT",
        "confirmed_transferable_amount": (
            amount
        ),
        "source_endpoint": (
            "/v5/asset/transfer/"
            "query-account-coin-balance"
        ),
        "raw": {
            "retCode": 0,
            "result": {
                "accountType": "UNIFIED",
                "balance": {
                    "coin": "USDT",
                    "transferBalance": amount,
                    "transferSafeAmount": (
                        amount
                    ),
                    "ltvTransferSafeAmount": (
                        amount
                    ),
                },
            },
        },
    }


def test_terminal_order_requires_balance_refresh():
    sale_batch = _sale_batch()
    leg = _order_leg(
        status="filled"
    )

    actions = (
        terminal_balance_refresh_actions(
            legs=[leg]
        )
    )

    assert len(actions) == 1
    assert actions[0].action_type == (
        "order_terminal_confirmed"
    )
    assert actions[0].active_leg_id == 30
    assert actions[0].external_status == (
        "filled"
    )
    assert actions[0].order_link_id == (
        "wbns-10-30-r0-s0"
    )

    pending = (
        next_unreconciled_terminal_action(
            sale_batch=sale_batch,
            legs=[leg],
        )
    )

    assert pending == actions[0]


def test_non_terminal_order_has_no_barrier():
    actions = (
        terminal_balance_refresh_actions(
            legs=[
                _order_leg(
                    status="acknowledged"
                )
            ]
        )
    )

    assert actions == []


def test_earn_terminal_action_has_priority():
    actions = (
        terminal_balance_refresh_actions(
            legs=[
                _order_leg(
                    status="filled"
                ),
                _earn_leg(),
            ]
        )
    )

    assert len(actions) == 2
    assert actions[0].action_type == (
        "earn_terminal_confirmed"
    )
    assert actions[0].active_leg_id == 20
    assert actions[1].action_type == (
        "order_terminal_confirmed"
    )


def test_existing_refresh_skips_terminal_action():
    sale_batch = _sale_batch()
    leg = _order_leg(
        status="filled"
    )
    action = (
        terminal_balance_refresh_actions(
            legs=[leg]
        )[0]
    )

    sale_batch.reconciliation_json = (
        append_confirmed_balance_refresh(
            existing_reconciliation_json=None,
            required_master_usdt="100",
            balance_before_usdt=None,
            balance_after_usdt="80",
            transferable_balance=(
                _balance_payload("80")
            ),
            action_type=(
                action.action_type
            ),
            active_leg_id=(
                action.active_leg_id
            ),
            order_link_id=(
                action.order_link_id
            ),
            captured_at=NOW,
        )
    )

    pending = (
        next_unreconciled_terminal_action(
            sale_batch=sale_batch,
            legs=[leg],
        )
    )

    assert pending is None


def test_resume_cycle_performs_only_balance_get():
    class FakeClient:
        def __init__(self):
            self.gets: list[
                tuple[str, dict]
            ] = []
            self.posts: list[
                tuple[str, dict]
            ] = []

        def get(
            self,
            path: str,
            params: dict,
        ) -> dict:
            self.gets.append(
                (
                    path,
                    dict(params),
                )
            )

            assert path == (
                "/v5/asset/transfer/"
                "query-account-coin-balance"
            )

            return {
                "retCode": 0,
                "result": {
                    "accountType": (
                        "UNIFIED"
                    ),
                    "balance": {
                        "coin": "USDT",
                        "walletBalance": "80",
                        "transferBalance": (
                            "80"
                        ),
                        "transferSafeAmount": (
                            "80"
                        ),
                        "ltvTransferSafeAmount": (
                            "80"
                        ),
                    },
                },
            }

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

            raise AssertionError(
                "Balance refresh cycle "
                "must not POST"
            )

    client = FakeClient()

    step = (
        resume_negative_sale_balance_refresh_once(
            client=client,
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[
                _order_leg(
                    status="filled"
                )
            ],
            now=NOW,
        )
    )

    assert step is not None
    assert step.action == (
        "balance_refresh"
    )
    assert step.posted is False
    assert step.has_pending_action is False
    assert (
        step.confirmed_available_usdt
        == Decimal("80")
    )
    assert step.shortage_usdt == (
        Decimal("20")
    )
    assert len(client.gets) == 1
    assert client.posts == []

    metadata = step.leg_step[
        "balance_refresh_action"
    ]

    assert metadata["action_type"] == (
        "order_terminal_confirmed"
    )
    assert metadata["order_link_id"] == (
        "wbns-10-30-r0-s0"
    )