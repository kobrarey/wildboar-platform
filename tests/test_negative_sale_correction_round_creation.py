from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.settlement import (
    negative_sale_live_batch_service
    as service,
)
from app.settlement.negative_sale_live_persistence import (
    archive_terminal_intent_and_activate_next_round,
    validated_terminal_intent_history,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)


NOW = datetime(
    2026,
    7,
    21,
    tzinfo=timezone.utc,
)


def _terminal_intent(
    *,
    leg_id: int,
    symbol: str,
    execution_round: int = 0,
) -> dict:
    intent = (
        build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=leg_id,
            execution_round=(
                execution_round
            ),
            category="spot",
            symbol=symbol,
            position_side=None,
            close_side="Sell",
            position_idx=None,
            reduce_only=None,
            market_unit="baseCoin",
            requested_qty=Decimal("1"),
            normalized_qty=Decimal("1"),
            target_cash_usdt=(
                Decimal("10")
            ),
            slices=(Decimal("1"),),
            prepared_at=NOW,
        ).to_dict()
    )

    item = intent["suborders"][0]
    item["status"] = "filled"
    item["order_id"] = (
        f"OID-{leg_id}-"
        f"{execution_round}"
    )
    item["reconciliation"] = {
        "aggregate_exec_qty": "1",
        "aggregate_exec_value": "10",
        "fees_by_currency": {
            "USDT": "0.01",
        },
    }

    return intent


def _plan_leg(
    *,
    symbol: str,
    current_qty: str,
    current_value: str,
) -> dict:
    return {
        "leg_group": "spot",
        "leg_type": "spot_sell",
        "coin": symbol.replace(
            "USDT",
            "",
        ),
        "symbol": symbol,
        "category": "spot",
        "side": "Sell",
        "close_side": "Sell",
        "location": "UNIFIED",
        "current_qty": current_qty,
        "current_usd_value": (
            current_value
        ),
        "target_cash_usdt": "10",
        "target_qty": "1",
        "eligible": True,
        "use_for_deficit_cover": True,
        "raw": {
            "asset_type": "spot",
            "market_unit": "baseCoin",
            "requires_fund_to_unified_transfer": (
                False
            ),
        },
        "order_quantity_preflight": {
            "requested_qty": "1",
            "normalized_qty": "1",
            "slices": ["1"],
        },
    }


def _leg(
    *,
    leg_id: int,
    leg_index: int,
    symbol: str,
) -> SimpleNamespace:
    intent = _terminal_intent(
        leg_id=leg_id,
        symbol=symbol,
    )

    return SimpleNamespace(
        id=leg_id,
        leg_index=leg_index,
        leg_group="spot",
        leg_type="spot_sell",
        coin=symbol.replace(
            "USDT",
            "",
        ),
        symbol=symbol,
        category="spot",
        side="Sell",
        location="UNIFIED",
        current_qty=Decimal("1"),
        current_size=None,
        current_usd_value=None,
        current_notional_usd=None,
        target_cash_usdt=Decimal("10"),
        target_qty=Decimal("1"),
        expected_cash_delta_usdt=(
            Decimal("10")
        ),
        eligible=True,
        use_for_deficit_cover=True,
        instrument_status="Trading",
        min_order_passed=True,
        planned_execution_mode=(
            "live_market_order"
        ),
        actual_execution_mode=(
            "live_market_order"
        ),
        execution_round="0",
        deterministic_key=(
            intent["deterministic_key"]
        ),
        order_link_id=(
            intent["suborders"][0][
                "order_link_id"
            ]
        ),
        strategy_id=None,
        bybit_order_id="OLD",
        bybit_strategy_id=None,
        planned_suborders=1,
        executed_suborders=1,
        suborders_json=deepcopy(
            intent
        ),
        mock_execution_json={},
        filled_qty=Decimal("1"),
        filled_usdt=Decimal("10"),
        avg_fill_price=Decimal("10"),
        fill_ratio=Decimal("1"),
        unfilled_usdt=Decimal("0"),
        fee_usdt=Decimal("0.01"),
        cash_delta_usdt=Decimal(
            "9.99"
        ),
        last_price=Decimal("10"),
        sent_at=NOW,
        confirmed_at=NOW,
        failed_at=None,
        execution_error=None,
        status="filled",
        error=None,
        updated_at=NOW,
    )


class FakeDB:
    def add(self, item):
        return None

    def flush(self):
        return None

    def commit(self):
        return None

    def refresh(self, item):
        return None


class FakeClient:
    def __init__(self):
        self.posts: list[
            tuple[str, dict]
        ] = []

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        symbol = params["symbol"]

        if path == "/v5/market/tickers":
            price = {
                "BTCUSDT": "10",
                "ETHUSDT": "5",
            }[symbol]

            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": symbol,
                            "bid1Price": price,
                        }
                    ],
                },
            }

        if (
            path
            == "/v5/market/instruments-info"
        ):
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": symbol,
                            "status": "Trading",
                            "baseCoin": (
                                symbol.replace(
                                    "USDT",
                                    "",
                                )
                            ),
                            "quoteCoin": "USDT",
                            "lotSizeFilter": {
                                "qtyStep": "0.1",
                                "minOrderQty": "0.1",
                                "maxOrderQty": "1000",
                                "maxMarketOrderQty": (
                                    "1000"
                                ),
                                "basePrecision": "0.1",
                                "quotePrecision": (
                                    "0.01"
                                ),
                                "minOrderAmt": "1",
                            },
                            "priceFilter": {
                                "tickSize": "0.01",
                            },
                        }
                    ],
                },
            }

        raise AssertionError(
            f"Unexpected GET: {path}"
        )

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

        raise AssertionError(
            "Correction preparation must "
            "not POST"
        )


class Balance:
    confirmed_transferable_amount = (
        Decimal("80")
    )

    def to_dict(self):
        return {
            "confirmed_transferable_amount": (
                "80"
            ),
        }


def test_largest_spot_source_prepares_next_round(
    monkeypatch,
):
    sale_batch = SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [
                _plan_leg(
                    symbol="BTCUSDT",
                    current_qty="10",
                    current_value="100",
                ),
                _plan_leg(
                    symbol="ETHUSDT",
                    current_qty="50",
                    current_value="250",
                ),
            ]
        },
    )
    settlement_batch = (
        SimpleNamespace(id=30)
    )

    btc_leg = _leg(
        leg_id=20,
        leg_index=1,
        symbol="BTCUSDT",
    )
    eth_leg = _leg(
        leg_id=21,
        leg_index=2,
        symbol="ETHUSDT",
    )

    monkeypatch.setattr(
        service,
        "query_unified_transferable_balance",
        lambda *args, **kwargs: (
            Balance()
        ),
    )

    client = FakeClient()

    result = (
        service
        .resume_negative_sale_order_batch_once(
            FakeDB(),
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=[
                btc_leg,
                eth_leg,
            ],
            now=NOW,
        )
    )

    assert (
        result.action
        == "correction_prepared"
    )
    assert result.active_leg_id == 21
    assert result.posted is False
    assert (
        result.has_pending_action
        is False
    )
    assert client.posts == []

    active = eth_leg.suborders_json

    assert active[
        "execution_round"
    ] == 1
    assert active["symbol"] == "ETHUSDT"
    assert active[
        "normalized_qty"
    ] == "4.4"
    assert active[
        "target_cash_usdt"
    ] == "22.0"

    history = (
        validated_terminal_intent_history(
            eth_leg.mock_execution_json
        )
    )

    assert len(history) == 1
    assert (
        history[0]["execution_round"]
        == 0
    )
    assert (
        btc_leg.suborders_json[
            "execution_round"
        ]
        == 0
    )


def test_rollover_allows_global_round_gap():
    leg = _leg(
        leg_id=20,
        leg_index=1,
        symbol="BTCUSDT",
    )

    next_intent = (
        build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=20,
            execution_round=2,
            category="spot",
            symbol="BTCUSDT",
            position_side=None,
            close_side="Sell",
            position_idx=None,
            reduce_only=None,
            market_unit="baseCoin",
            requested_qty=Decimal("1"),
            normalized_qty=Decimal("1"),
            target_cash_usdt=(
                Decimal("10")
            ),
            slices=(Decimal("1"),),
            prepared_at=NOW,
        ).to_dict()
    )

    archive_terminal_intent_and_activate_next_round(
        FakeDB(),
        leg=leg,
        new_intent=next_intent,
        now=NOW,
    )

    assert (
        leg.suborders_json[
            "execution_round"
        ]
        == 2
    )