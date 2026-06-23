from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.allocation.live_spot_orders import (
    apply_bybit_order_to_leg,
    fetch_bybit_order_by_link_id,
    submit_bybit_spot_market_order,
)
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_FILLED,
    ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
)
from app.models import FundAllocationLeg


class StubBybitClient:
    def __init__(self):
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []
        self.orders_by_link: dict[str, dict] = {}

    def get(self, path: str, params: dict | None = None) -> dict:
        params = params or {}
        self.get_calls.append((path, dict(params)))

        order_link_id = params.get("orderLinkId")
        order = self.orders_by_link.get(order_link_id)

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [] if order is None else [order],
            },
        }

    def post(self, path: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        self.post_calls.append((path, dict(payload)))

        order_link_id = payload.get("orderLinkId")
        order_id = f"stub-order-{order_link_id}"

        self.orders_by_link[order_link_id] = {
            "orderId": order_id,
            "orderLinkId": order_link_id,
            "orderStatus": "Filled",
            "cumExecQty": "0.1",
            "cumExecValue": "100",
            "avgPrice": "1000",
            "cumExecFee": "0.1",
        }

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "orderId": order_id,
                "orderLinkId": order_link_id,
            },
        }


def assert_ok(name: str, ok: bool) -> None:
    if not ok:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_source_ordering() -> None:
    worker = Path("workers/fund_allocation_execution_worker.py").read_text(encoding="utf-8")

    marker = "def _process_live_spot_leg_in_own_session"
    assert_ok(
        "SOURCE_HAS_LIVE_SPOT_PROCESSOR",
        marker in worker,
    )

    body = worker.split(marker, 1)[1]

    order_link_pos = body.find("orderLinkId persisted before POST")
    guard_pos = body.find("require_trade_guard_for_plan")
    post_pos = body.find("submit_bybit_spot_market_order")
    reconciliation_pos = body.find("idempotency-first reconciliation")
    single_post_pos = body.find("single external POST")

    assert_ok(
        "SOURCE_ORDER_LINK_ID_PERSISTED_BEFORE_POST",
        order_link_pos != -1 and post_pos != -1 and order_link_pos < post_pos,
    )

    assert_ok(
        "SOURCE_GUARD_BEFORE_POST",
        guard_pos != -1 and post_pos != -1 and guard_pos < post_pos,
    )

    assert_ok(
        "SOURCE_IDEMPOTENT_RECONCILIATION_BEFORE_POST",
        reconciliation_pos != -1 and single_post_pos != -1 and reconciliation_pos < single_post_pos,
    )

    assert_ok(
        "SOURCE_NO_NOT_WIRED",
        "not wired" not in worker.lower(),
    )

    assert_ok(
        "SOURCE_LIVE_GATE_DISABLED_RETURNS_ZERO",
        "Allocation execution live gate blocked. No changes." in worker and "return False" in worker,
    )


def test_fetch_existing_order_by_link_id_no_post() -> None:
    client = StubBybitClient()
    client.orders_by_link["alloc:1:leg:2:mkt"] = {
        "orderId": "existing-order-1",
        "orderLinkId": "alloc:1:leg:2:mkt",
        "orderStatus": "Filled",
        "cumExecQty": "0.1",
        "cumExecValue": "100",
        "avgPrice": "1000",
    }

    order = fetch_bybit_order_by_link_id(
        client,
        category="spot",
        symbol="BTCUSDT",
        order_link_id="alloc:1:leg:2:mkt",
    )

    assert_ok("FETCH_EXISTING_ORDER_FOUND", order is not None)
    assert_ok("FETCH_EXISTING_ORDER_NO_POST", len(client.post_calls) == 0)


def test_single_post_and_no_duplicate_on_rerun_lookup() -> None:
    client = StubBybitClient()
    payload = {
        "category": "spot",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Market",
        "qty": "100",
        "marketUnit": "quoteCoin",
        "orderLinkId": "alloc:10:leg:20:mkt",
        "slippageToleranceType": "Percent",
        "slippageTolerance": "1",
    }

    result = submit_bybit_spot_market_order(
        client,
        payload=payload,
    )

    assert_ok("SUBMIT_POST_ONCE", len(client.post_calls) == 1)
    assert_ok("SUBMIT_RETURNED_ORDER_ID", bool(result.get("orderId")))

    existing = fetch_bybit_order_by_link_id(
        client,
        category="spot",
        symbol="BTCUSDT",
        order_link_id="alloc:10:leg:20:mkt",
    )

    assert_ok("RERUN_LOOKUP_FOUND_EXISTING_ORDER", existing is not None)
    assert_ok("RERUN_LOOKUP_DID_NOT_POST_AGAIN", len(client.post_calls) == 1)


def test_apply_filled_order_to_leg() -> None:
    leg = FundAllocationLeg(
        id=20,
        allocation_batch_id=10,
        settlement_batch_id=5,
        fund_id=1,
        leg_index=1,
        leg_key="spot:BTCUSDT",
        leg_group="spot",
        leg_type="spot_buy",
        symbol="BTCUSDT",
        category="spot",
        side="Buy",
        target_usdt=Decimal("100"),
        target_qty=None,
        required_qty=Decimal("0.1"),
        required_usdt=Decimal("100"),
        order_link_id="alloc:10:leg:20:mkt",
        status=ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT,
    )

    result = apply_bybit_order_to_leg(
        leg,
        order={
            "orderId": "stub-order-1",
            "orderLinkId": "alloc:10:leg:20:mkt",
            "orderStatus": "Filled",
            "cumExecQty": "0.1",
            "cumExecValue": "100",
            "avgPrice": "1000",
            "cumExecFee": "0.1",
        },
        min_fill_ratio=Decimal("0.90"),
    )

    assert_ok("APPLY_FILLED_RESULT_OK", result.ok is True)
    assert_ok("APPLY_FILLED_LEG_STATUS_FILLED", leg.status == ALLOCATION_LEG_STATUS_FILLED)
    assert_ok("APPLY_FILLED_BYBIT_ORDER_ID_SET", leg.bybit_order_id == "stub-order-1")
    assert_ok("APPLY_FILLED_RATIO_1", leg.fill_ratio == Decimal("1"))


def main() -> None:
    test_source_ordering()
    test_fetch_existing_order_by_link_id_no_post()
    test_single_post_and_no_duplicate_on_rerun_lookup()
    test_apply_filled_order_to_leg()
    print("STAGE25_2_LIVE_ALLOCATION_STUB_TESTS_OK")


if __name__ == "__main__":
    main()