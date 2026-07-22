from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import app.settlement.negative_sale_execution as execution
from app.settlement.negative_sale_balance_reconciliation import (
    NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA,
    append_confirmed_balance_refresh,
)
from app.settlement.negative_sale_balance_refresh_service import (
    resume_negative_sale_balance_refresh_once,
)
from app.settlement.negative_sale_earn_runtime import (
    build_negative_sale_earn_intent,
)
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
)


NOW = datetime(
    2026,
    7,
    22,
    23,
    0,
    tzinfo=timezone.utc,
)


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.flushes = 0

    def add(self, item):
        return None

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1


class FakeClient:
    def __init__(
        self,
        amounts: list[str],
    ):
        self.amounts = list(amounts)
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
        assert params["accountType"] == (
            "UNIFIED"
        )
        assert params["coin"] == "USDT"
        assert params["toAccountType"] == (
            "FUND"
        )

        if not self.amounts:
            raise AssertionError(
                "Unexpected repeated "
                "balance GET"
            )

        amount = self.amounts.pop(0)

        return {
            "retCode": 0,
            "result": {
                "accountType": "UNIFIED",
                "balance": {
                    "coin": "USDT",
                    "walletBalance": amount,
                    "transferBalance": (
                        amount
                    ),
                    "transferSafeAmount": (
                        amount
                    ),
                    "ltvTransferSafeAmount": (
                        amount
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
            "Crash/restart balance-refresh "
            "cycle must not POST"
        )


def _transferable_payload(
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
                    "walletBalance": amount,
                    "transferBalance": (
                        amount
                    ),
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


def _terminal_order_intent() -> dict:
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

    suborder = intent[
        "suborders"
    ][0]

    suborder["status"] = "filled"
    suborder["order_id"] = "OID-1"
    suborder["submitted_at"] = (
        NOW.isoformat()
    )
    suborder["acknowledged_at"] = (
        NOW.isoformat()
    )
    suborder["terminal_at"] = (
        NOW.isoformat()
    )
    suborder["reconciliation"] = {
        "aggregate_exec_qty": "0.10",
        "aggregate_exec_value": "10",
        "fees_by_currency": {
            "USDT": "0.01",
        },
    }

    return intent


def _terminal_earn_intent() -> dict:
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
                "60"
            ),
            available_earn_usdt="50",
            needed_from_earn_usdt="20",
            amount="20",
            amount_str="20.00",
            order_link_id=(
                "wbne-crash-restart"
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


def _objects(
    *,
    include_order: bool,
    include_earn: bool,
):
    sale_batch = SimpleNamespace(
        id=10,
        settlement_batch_id=20,
        fund_id=1,
        required_master_usdt=(
            Decimal("100")
        ),
        status=(
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        ),
        execution_json=None,
        reconciliation_json=None,
        final_available_usdt=None,
        final_shortage_usdt=None,
        final_surplus_usdt=None,
        execution_completed_at=None,
        error=None,
        updated_at=None,
    )

    settlement_batch = SimpleNamespace(
        id=20,
        fund_id=1,
        status=(
            BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
        ),
        error=None,
        updated_at=None,
    )

    fund = SimpleNamespace(
        id=1,
        code="TEST",
    )

    legs = []

    if include_earn:
        legs.append(
            SimpleNamespace(
                id=20,
                sale_batch_id=10,
                leg_index=1,
                leg_type=(
                    "usdt_earn_buffer"
                ),
                category=None,
                location="EARN",
                status=(
                    "usdt_earn_redeemed"
                ),
                execution_round="0",
                executed_suborders=0,
                suborders_json=(
                    _terminal_earn_intent()
                ),
            )
        )

    if include_order:
        legs.append(
            SimpleNamespace(
                id=30,
                sale_batch_id=10,
                leg_index=2,
                leg_type="spot_sell",
                category="spot",
                location="UNIFIED",
                status="filled",
                execution_round="0",
                executed_suborders=1,
                suborders_json=(
                    _terminal_order_intent()
                ),
            )
        )

    return (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    )


def _patch_prepare(
    monkeypatch,
    *,
    sale_batch,
    settlement_batch,
    fund,
    legs,
) -> None:
    monkeypatch.setattr(
        execution,
        "prepare_negative_sale_live_execution",
        lambda *args, **kwargs: (
            sale_batch,
            settlement_batch,
            fund,
            legs,
            (
                SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
            ),
            (
                BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
            ),
        ),
    )


def _forbidden_routing(
    *args,
    **kwargs,
):
    raise AssertionError(
        "Earn/order routing must not run "
        "while terminal balance refresh "
        "is pending"
    )


def _routing_step() -> (
    NegativeSaleLiveBatchStepResult
):
    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=10,
        settlement_batch_id=20,
        action="correction_prepared",
        reason=(
            "routing_resumed_after_"
            "balance_refresh"
        ),
        candidate_leg_count=1,
        active_leg_id=30,
        posted=False,
        all_order_legs_terminal=False,
        has_pending_action=False,
        requires_review=False,
        confirmed_available_usdt=None,
        shortage_usdt=None,
        correction_decision=None,
        transferable_balance=None,
        leg_step=None,
    )


def test_restart_after_terminal_order_runs_only_balance_get(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects(
        include_order=True,
        include_earn=False,
    )
    db = FakeDB()
    client = FakeClient(
        ["80"]
    )

    _patch_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        _forbidden_routing,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        _forbidden_routing,
    )

    result = (
        execution
        .execute_negative_sale_plan_live(
            db,
            sale_batch_id=10,
            client=client,
            now=NOW,
        )
    )

    assert result.ok is False
    assert db.commits == 2
    assert len(client.gets) == 1
    assert client.posts == []

    reconciliation = (
        sale_batch.reconciliation_json
    )

    assert reconciliation["schema"] == (
        NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    )
    assert len(
        reconciliation[
            "refresh_history"
        ]
    ) == 1

    latest = reconciliation[
        "latest_refresh"
    ]

    assert latest["action_type"] == (
        "order_terminal_confirmed"
    )
    assert latest["active_leg_id"] == 30
    assert latest["order_link_id"] == (
        "wbns-10-30-r0-s0"
    )


def test_restart_after_terminal_earn_runs_only_balance_get(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects(
        include_order=False,
        include_earn=True,
    )
    db = FakeDB()
    client = FakeClient(
        ["80"]
    )

    _patch_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        _forbidden_routing,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        _forbidden_routing,
    )

    execution.execute_negative_sale_plan_live(
        db,
        sale_batch_id=10,
        client=client,
        now=NOW,
    )

    assert len(client.gets) == 1
    assert client.posts == []

    latest = (
        sale_batch
        .reconciliation_json[
            "latest_refresh"
        ]
    )

    assert latest["action_type"] == (
        "earn_terminal_confirmed"
    )
    assert latest["active_leg_id"] == 20
    assert latest["order_link_id"] == (
        "wbne-crash-restart"
    )


def test_crash_after_get_before_persist_repeats_only_get():
    (
        sale_batch,
        settlement_batch,
        _,
        legs,
    ) = _objects(
        include_order=True,
        include_earn=False,
    )
    client = FakeClient(
        [
            "80",
            "80",
        ]
    )

    first = (
        resume_negative_sale_balance_refresh_once(
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
            now=NOW,
        )
    )

    # Simulate a process crash before
    # apply_negative_sale_live_batch_step()
    # persists the returned refresh.
    assert (
        sale_batch.reconciliation_json
        is None
    )

    second = (
        resume_negative_sale_balance_refresh_once(
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
            now=(
                NOW
                + timedelta(minutes=1)
            ),
        )
    )

    assert first is not None
    assert second is not None
    assert first.action == (
        "balance_refresh"
    )
    assert second.action == (
        "balance_refresh"
    )
    assert len(client.gets) == 2
    assert client.posts == []


def test_multiple_terminal_actions_refresh_one_per_cycle(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects(
        include_order=True,
        include_earn=True,
    )
    db = FakeDB()
    client = FakeClient(
        [
            "80",
            "90",
        ]
    )

    _patch_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        _forbidden_routing,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        _forbidden_routing,
    )

    execution.execute_negative_sale_plan_live(
        db,
        sale_batch_id=10,
        client=client,
        now=NOW,
    )

    first_history = (
        sale_batch
        .reconciliation_json[
            "refresh_history"
        ]
    )

    assert len(first_history) == 1
    assert first_history[0][
        "action_type"
    ] == "earn_terminal_confirmed"

    execution.execute_negative_sale_plan_live(
        db,
        sale_batch_id=10,
        client=client,
        now=(
            NOW
            + timedelta(minutes=1)
        ),
    )

    history = (
        sale_batch
        .reconciliation_json[
            "refresh_history"
        ]
    )

    assert len(history) == 2
    assert history[0][
        "action_type"
    ] == "earn_terminal_confirmed"
    assert history[1][
        "action_type"
    ] == "order_terminal_confirmed"
    assert history[1][
        "balance_before_usdt"
    ] == "80"
    assert history[1][
        "balance_after_usdt"
    ] == "90"

    assert len(client.gets) == 2
    assert client.posts == []
    assert db.commits == 4


def test_persisted_refresh_allows_routing_without_repeat_get(
    monkeypatch,
):
    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
    ) = _objects(
        include_order=True,
        include_earn=False,
    )
    db = FakeDB()
    client = FakeClient(
        []
    )

    sale_batch.reconciliation_json = (
        append_confirmed_balance_refresh(
            existing_reconciliation_json=None,
            required_master_usdt="100",
            balance_before_usdt=None,
            balance_after_usdt="80",
            transferable_balance=(
                _transferable_payload(
                    "80"
                )
            ),
            action_type=(
                "order_terminal_confirmed"
            ),
            active_leg_id=30,
            order_link_id=(
                "wbns-10-30-r0-s0"
            ),
            captured_at=NOW,
        )
    )
    sale_batch.final_available_usdt = (
        Decimal("80")
    )
    sale_batch.final_shortage_usdt = (
        Decimal("20")
    )
    sale_batch.final_surplus_usdt = (
        Decimal("0")
    )

    initial_history = (
        sale_batch
        .reconciliation_json[
            "refresh_history"
        ]
    )

    _patch_prepare(
        monkeypatch,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        fund=fund,
        legs=legs,
    )

    calls = {
        "earn": 0,
        "order": 0,
    }

    def fake_earn(
        *args,
        **kwargs,
    ):
        calls["earn"] += 1
        return None

    def fake_order(
        *args,
        **kwargs,
    ):
        calls["order"] += 1
        return _routing_step()

    monkeypatch.setattr(
        execution,
        "resume_negative_sale_earn_once",
        fake_earn,
    )
    monkeypatch.setattr(
        execution,
        "resume_negative_sale_order_batch_once",
        fake_order,
    )

    execution.execute_negative_sale_plan_live(
        db,
        sale_batch_id=10,
        client=client,
        now=(
            NOW
            + timedelta(minutes=1)
        ),
    )

    assert calls == {
        "earn": 1,
        "order": 1,
    }
    assert client.gets == []
    assert client.posts == []

    final_history = (
        sale_batch
        .reconciliation_json[
            "refresh_history"
        ]
    )

    assert final_history == (
        initial_history
    )
    assert len(final_history) == 1