from __future__ import annotations

from copy import deepcopy
from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace

from app.settlement.negative_sale_earn_persistence import (
    persist_negative_sale_earn_state,
)
from app.settlement.negative_sale_earn_runtime import (
    EARN_RUNTIME_STATUS_SUBMITTED,
    EARN_RUNTIME_STATUS_SUCCESS,
    build_negative_sale_earn_intent,
)
from app.settlement.statuses import (
    SALE_LEG_STATUS_BUFFER_AVAILABLE,
    SALE_LEG_STATUS_PENDING_CONFIRMATION,
    SALE_LEG_STATUS_USDT_EARN_REDEEMED,
)


NOW = datetime(
    2026,
    7,
    22,
    16,
    0,
    tzinfo=timezone.utc,
)


class FakeDB:
    def __init__(self):
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.refreshes = 0

    def add(self, item):
        self.added.append(item)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def refresh(self, item):
        self.refreshes += 1


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


def _leg():
    return SimpleNamespace(
        id=20,
        sale_batch_id=10,
        leg_index=1,
        status=(
            SALE_LEG_STATUS_BUFFER_AVAILABLE
        ),
        suborders_json=None,
        actual_execution_mode=None,
        execution_round=None,
        deterministic_key=None,
        order_link_id=None,
        bybit_order_id=None,
        bybit_strategy_id=None,
        planned_suborders=None,
        executed_suborders=None,
        filled_qty=None,
        filled_usdt=None,
        avg_fill_price=None,
        fill_ratio=None,
        unfilled_usdt=None,
        fee_usdt=None,
        cash_delta_usdt=None,
        sent_at=None,
        confirmed_at=None,
        failed_at=None,
        execution_error=None,
        updated_at=None,
    )


def test_prepared_intent_uses_existing_jsonb():
    db = FakeDB()
    leg = _leg()

    persisted = (
        persist_negative_sale_earn_state(
            db,
            leg=leg,
            raw_intent=_intent(),
            now=NOW,
        )
    )

    assert persisted["status"] == (
        "prepared"
    )
    assert leg.status == (
        SALE_LEG_STATUS_BUFFER_AVAILABLE
    )
    assert leg.suborders_json[
        "schema"
    ] == (
        "negative_sale_earn_redeem_"
        "intent_v1"
    )
    assert db.commits == 1
    assert db.refreshes == 1


def test_submitted_maps_to_pending_confirmation():
    db = FakeDB()
    leg = _leg()

    prepared = _intent()

    persist_negative_sale_earn_state(
        db,
        leg=leg,
        raw_intent=prepared,
        now=NOW,
    )

    submitted = deepcopy(prepared)
    submitted["status"] = (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    submitted["submitted_at"] = (
        NOW.isoformat()
    )

    persist_negative_sale_earn_state(
        db,
        leg=leg,
        raw_intent=submitted,
        now=NOW,
    )

    assert leg.status == (
        SALE_LEG_STATUS_PENDING_CONFIRMATION
    )
    assert leg.sent_at == NOW
    assert leg.cash_delta_usdt is None
    assert db.commits == 2


def test_success_sets_confirmed_cash_delta():
    db = FakeDB()
    leg = _leg()

    prepared = _intent()

    persist_negative_sale_earn_state(
        db,
        leg=leg,
        raw_intent=prepared,
        now=NOW,
    )

    success = deepcopy(prepared)
    success["status"] = (
        EARN_RUNTIME_STATUS_SUCCESS
    )
    success["order_id"] = "EARN-OID-1"
    success["submitted_at"] = (
        NOW.isoformat()
    )
    success["acknowledged_at"] = (
        NOW.isoformat()
    )
    success["confirmed_at"] = (
        NOW.isoformat()
    )
    success["redeemed_usdt"] = (
        "12.34"
    )

    persist_negative_sale_earn_state(
        db,
        leg=leg,
        raw_intent=success,
        now=NOW,
    )

    assert leg.status == (
        SALE_LEG_STATUS_USDT_EARN_REDEEMED
    )
    assert str(
        leg.cash_delta_usdt
    ) == "12.34"
    assert str(leg.filled_usdt) == (
        "12.34"
    )
    assert str(leg.avg_fill_price) == "1"
    assert leg.executed_suborders == 1
    assert leg.confirmed_at == NOW
    assert db.commits == 2