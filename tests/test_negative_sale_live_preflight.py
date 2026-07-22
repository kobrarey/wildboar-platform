from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal

import pytest

from app.settlement.negative_sale_live_preflight import (
    NegativeSaleLivePreflightError,
    build_live_negative_sale_preflight,
)


NOW = datetime(
    2026,
    7,
    22,
    12,
    0,
    tzinfo=timezone.utc,
)


def _linear_instrument(
    symbol: str,
):
    return {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": symbol,
                    "status": "Trading",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "settleCoin": "USDT",
                    "contractType": (
                        "LinearPerpetual"
                    ),
                    "lotSizeFilter": {
                        "qtyStep": "0.1",
                        "minOrderQty": "0.1",
                        "minNotionalValue": "5",
                        "maxMktOrderQty": "1",
                        "maxOrderQty": "100",
                    },
                    "priceFilter": {
                        "tickSize": "0.1",
                    },
                }
            ]
        },
    }


def _spot_instrument():
    return {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "status": "Trading",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "lotSizeFilter": {
                        "basePrecision": (
                            "0.0001"
                        ),
                        "quotePrecision": (
                            "0.01"
                        ),
                        "qtyStep": "0.0001",
                        "minOrderQty": (
                            "0.0001"
                        ),
                        "minOrderAmt": "1",
                        "maxOrderQty": "100",
                        "maxMarketOrderQty": (
                            "100"
                        ),
                    },
                    "priceFilter": {
                        "tickSize": "0.01",
                    },
                }
            ]
        },
    }


class FakeDerivativeClient:
    def __init__(
        self,
        *,
        side: str,
        position_idx: int,
        size: str,
        position_status: str = "Normal",
    ):
        self.side = side
        self.position_idx = position_idx
        self.size = size
        self.position_status = (
            position_status
        )

    def get(
        self,
        path,
        params,
    ):
        if path == "/v5/position/list":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": (
                                params["symbol"]
                            ),
                            "side": self.side,
                            "size": self.size,
                            "positionIdx": (
                                self.position_idx
                            ),
                            "positionStatus": (
                                self
                                .position_status
                            ),
                            "markPrice": "100.5",
                        }
                    ]
                },
            }

        raise AssertionError(
            f"Unexpected private GET: {path}"
        )

    def public_get(
        self,
        path,
        params,
    ):
        if (
            path
            == "/v5/market/instruments-info"
        ):
            return _linear_instrument(
                params["symbol"]
            )

        if path == "/v5/market/tickers":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": (
                                params["symbol"]
                            ),
                            "bid1Price": "100",
                            "ask1Price": "101",
                        }
                    ]
                },
            }

        raise AssertionError(
            f"Unexpected public GET: {path}"
        )


class FakeSpotClient:
    def get(
        self,
        path,
        params,
    ):
        if (
            path
            == "/v5/asset/transfer/"
            "query-account-coin-balance"
        ):
            assert params == {
                "accountType": "UNIFIED",
                "coin": "BTC",
                "toAccountType": "FUND",
                "withLtvTransferSafeAmount": (
                    1
                ),
            }

            return {
                "retCode": 0,
                "result": {
                    "accountType": "UNIFIED",
                    "balance": {
                        "coin": "BTC",
                        "walletBalance": "10",
                        "transferBalance": "0.8",
                        "transferSafeAmount": (
                            "0.7"
                        ),
                        "ltvTransferSafeAmount": (
                            ""
                        ),
                    },
                },
            }

        raise AssertionError(
            f"Unexpected private GET: {path}"
        )

    def public_get(
        self,
        path,
        params,
    ):
        if (
            path
            == "/v5/market/instruments-info"
        ):
            return _spot_instrument()

        if path == "/v5/market/tickers":
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "bid1Price": "100",
                            "ask1Price": "101",
                        }
                    ]
                },
            }

        raise AssertionError(
            f"Unexpected public GET: {path}"
        )


def test_live_long_position_uses_sell_and_caps_qty():
    result = (
        build_live_negative_sale_preflight(
            FakeDerivativeClient(
                side="Buy",
                position_idx=1,
                size="1.5",
            ),
            category="linear",
            symbol="BTCUSDT",
            requested_qty="2",
            planned_position_side="long",
            planned_close_side="Sell",
            planned_position_idx=1,
            captured_at=NOW,
        )
    )

    assert result.position_side == "Buy"
    assert result.close_side == "Sell"
    assert result.position_idx == 1
    assert result.reduce_only is True
    assert result.available_qty == (
        Decimal("1.5")
    )
    assert result.price == Decimal("100")
    assert result.normalized_qty == (
        Decimal("1.5")
    )
    assert result.slices == (
        Decimal("1.0"),
        Decimal("0.5"),
    )


def test_live_short_position_uses_buy_and_ask():
    result = (
        build_live_negative_sale_preflight(
            FakeDerivativeClient(
                side="Sell",
                position_idx=2,
                size="0.7",
            ),
            category="linear",
            symbol="BTCUSDT",
            requested_qty="0.7",
            planned_position_side="short",
            planned_close_side="Buy",
            planned_position_idx=2,
            captured_at=NOW,
        )
    )

    assert result.position_side == "Sell"
    assert result.close_side == "Buy"
    assert result.position_idx == 2
    assert result.price == Decimal("101")
    assert result.normalized_qty == (
        Decimal("0.7")
    )


def test_spot_uses_conservative_transferable_base_qty():
    result = (
        build_live_negative_sale_preflight(
            FakeSpotClient(),
            category="spot",
            symbol="BTCUSDT",
            requested_qty="1",
            planned_close_side="Sell",
            captured_at=NOW,
        )
    )

    assert result.close_side == "Sell"
    assert result.market_unit == "baseCoin"
    assert result.position_idx is None
    assert result.reduce_only is None

    # walletBalance=10 must not be used.
    # Conservative transferable amount is
    # min(0.8, 0.7) = 0.7.
    assert result.available_qty == (
        Decimal("0.7")
    )
    assert result.normalized_qty == (
        Decimal("0.7")
    )
    assert result.normalized_notional == (
        Decimal("70.0")
    )


def test_position_idx_mismatch_fails_closed():
    with pytest.raises(
        NegativeSaleLivePreflightError,
        match=(
            "Cannot identify exactly one "
            "live derivative position"
        ),
    ):
        build_live_negative_sale_preflight(
            FakeDerivativeClient(
                side="Buy",
                position_idx=1,
                size="1",
            ),
            category="linear",
            symbol="BTCUSDT",
            requested_qty="1",
            planned_position_side="Buy",
            planned_close_side="Sell",
            planned_position_idx=2,
            captured_at=NOW,
        )


def test_non_normal_position_fails_closed():
    with pytest.raises(
        NegativeSaleLivePreflightError,
        match=(
            "Live derivative position "
            "is not Normal"
        ),
    ):
        build_live_negative_sale_preflight(
            FakeDerivativeClient(
                side="Buy",
                position_idx=1,
                size="1",
                position_status="Adl",
            ),
            category="linear",
            symbol="BTCUSDT",
            requested_qty="1",
            planned_position_side="Buy",
            planned_close_side="Sell",
            planned_position_idx=1,
            captured_at=NOW,
        )