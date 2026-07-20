from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.allocation.bybit_snapshot_completeness import (
    SnapshotEndpointMatrix,
)
from app.bybit.instruments import (
    BybitInstrumentInfo,
    normalize_order_quantity,
)
from app.settlement.negative_sale_plan import (
    NegativeSalePlanError,
    _build_asset_sale_legs,
    _compute_negative_sale_plan,
    _validate_snapshot_for_sale_plan,
)
from app.settlement.negative_sale_planning_policy import (
    compute_proportional_derivative_reduction,
    derivative_close_side,
)
from app.settlement.negative_sale_snapshot import (
    normalize_negative_sale_snapshot,
)
from app.settlement.statuses import (
    SALE_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
)


def _instrument(
    *,
    category: str = "linear",
    symbol: str = "BTCUSDT",
    max_market_order_qty: str = "0.003",
) -> BybitInstrumentInfo:
    return BybitInstrumentInfo(
        category=category,
        symbol=symbol,
        status="Trading",
        base_coin="BTC",
        quote_coin="USDT",
        settle_coin="USDT",
        contract_type="LinearPerpetual",
        lot_size_filter={},
        price_filter={},
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_notional_value=Decimal("5"),
        min_order_amt=None,
        max_market_order_qty=Decimal(
            max_market_order_qty
        ),
        max_order_qty=Decimal("100"),
        base_precision=None,
        quote_precision=None,
        tick_size=Decimal("0.1"),
        captured_at=datetime.now(
            timezone.utc
        ),
        preflight_complete=True,
        completeness_reasons=(),
        raw={},
    )


def _settlement_batch(
    *,
    required_master_usdt: str = "100",
    planned_net_shares_change: str = "-25",
    shares_outstanding_before: str = "100",
) -> SimpleNamespace:
    return SimpleNamespace(
        required_master_usdt=(
            required_master_usdt
        ),
        withdrawal_request_amount_usdt=(
            required_master_usdt
        ),
        total_net_user_payout_usdt="99",
        total_partial_month_fee_usdt="1",
        bybit_withdrawal_fee_usdt="0",
        planned_net_shares_change=(
            planned_net_shares_change
        ),
        shares_outstanding_before=(
            shares_outstanding_before
        ),
    )


def test_required_endpoint_failure_marks_snapshot_incomplete():
    matrix = SnapshotEndpointMatrix()

    matrix.mark_success(
        "wallet:UNIFIED"
    )
    matrix.mark_failure(
        "earn:FlexibleSaving",
        error="denied",
        suppressed=True,
    )

    assert matrix.snapshot_complete is False
    assert matrix.required_endpoints == (
        "wallet:UNIFIED",
        "earn:FlexibleSaving",
    )
    assert matrix.successful_endpoints == (
        "wallet:UNIFIED",
    )
    assert matrix.failed_endpoints == (
        "earn:FlexibleSaving",
    )
    assert matrix.suppressed_errors == [
        {
            "endpoint": (
                "earn:FlexibleSaving"
            ),
            "error": "denied",
            "suppressed": True,
        }
    ]


def test_quantity_normalization_rounds_down_and_splits():
    normalized = normalize_order_quantity(
        instrument=_instrument(),
        requested_qty=Decimal("0.0079"),
        available_qty=Decimal("0.0079"),
        price=Decimal("10000"),
    )

    assert normalized.eligible is True
    assert normalized.normalized_qty == Decimal(
        "0.007"
    )
    assert normalized.slices == (
        Decimal("0.003"),
        Decimal("0.003"),
        Decimal("0.001"),
    )
    assert normalized.normalized_notional == (
        Decimal("70.000")
    )


def test_unknown_earn_redeemable_is_not_cash():
    snapshot = normalize_negative_sale_snapshot(
        {
            "cash": {
                "unified_usdt_available": "5",
                "usdt_earn_available": "10",
                "usdt_earn_redeemable": "10",
                "usdt_earn_redeemable_known": (
                    False
                ),
            },
            "assets": {},
        }
    )

    assert (
        snapshot.usdt_earn_used_as_buffer()
        == Decimal("0")
    )
    assert (
        snapshot.total_cash_like_available_usdt()
        == Decimal("5")
    )


def test_fund_usdt_is_diagnostic_not_task2_cash():
    snapshot = normalize_negative_sale_snapshot(
        {
            "cash": {
                "unified_usdt_available": "20",
                "fund_wallet_usdt_available": (
                    "50"
                ),
                "usdt_earn_available": "0",
                "usdt_earn_redeemable": "0",
            },
            "assets": {},
        }
    )

    computation = _compute_negative_sale_plan(
        settlement_batch=_settlement_batch(),
        snapshot=snapshot,
    )

    assert computation.sale_target_usdt == Decimal(
        "80"
    )
    assert (
        computation.cash_like_available_for_plan
        == Decimal("20")
    )
    assert (
        computation.fund_wallet_usdt_available
        == Decimal("50")
    )

    fund_legs = [
        leg
        for leg in computation.legs
        if leg.leg_type
        == "fund_wallet_usdt_cash"
    ]

    assert len(fund_legs) == 1
    assert (
        fund_legs[0].use_for_deficit_cover
        is False
    )
    assert (
        fund_legs[0].expected_cash_delta_usdt
        == Decimal("0")
    )


def test_proportional_derivative_reduction_policy():
    policy = (
        compute_proportional_derivative_reduction(
            planned_net_shares_change=(
                Decimal("-25")
            ),
            shares_outstanding_before=(
                Decimal("100")
            ),
        )
    )

    assert policy.policy_version == (
        "proportional_net_share_reduction_v1"
    )
    assert policy.net_shares_to_redeem == Decimal(
        "25"
    )
    assert policy.net_redeem_ratio == Decimal(
        "0.25"
    )


def test_derivative_close_side_matrix():
    assert derivative_close_side("Buy") == "Sell"
    assert derivative_close_side("long") == "Sell"
    assert derivative_close_side("Sell") == "Buy"
    assert derivative_close_side("short") == "Buy"


def test_derivative_notional_is_not_expected_cash():
    snapshot = normalize_negative_sale_snapshot(
        {
            "cash": {
                "unified_usdt_available": "20",
            },
            "assets": {
                "spot": [
                    {
                        "coin": "ETH",
                        "symbol": "ETHUSDT",
                        "qty": "1",
                        "usd_value": "100",
                        "instrument_status": (
                            "Trading"
                        ),
                    }
                ],
                "perp_future_positions": [
                    {
                        "coin": "BTC",
                        "symbol": "BTCUSDT",
                        "category": "linear",
                        "position_side": "Sell",
                        "position_idx": 2,
                        "size": "8",
                        "usd_value": "800",
                        "notional_usd": "800",
                        "instrument_status": (
                            "Trading"
                        ),
                    }
                ],
            },
        }
    )

    computation = _compute_negative_sale_plan(
        settlement_batch=_settlement_batch(),
        snapshot=snapshot,
    )

    derivative_legs = [
        leg
        for leg in computation.legs
        if leg.leg_type
        == "perp_future_reduce"
    ]

    assert computation.planned_sale_usdt == Decimal(
        "80"
    )
    assert computation.expected_shortage_usdt == (
        Decimal("0")
    )
    assert len(derivative_legs) == 1
    assert derivative_legs[0].close_side == "Buy"
    assert derivative_legs[0].position_idx == 2
    assert (
        derivative_legs[0]
        .expected_cash_delta_usdt
        == Decimal("0")
    )
    assert (
        derivative_legs[0]
        .use_for_deficit_cover
        is False
    )
    assert (
        derivative_legs[0]
        .exposure_notional_usdt
        == Decimal("800")
    )


def test_live_incomplete_snapshot_is_fail_closed():
    snapshot = normalize_negative_sale_snapshot(
        {
            "source": "bybit_readonly",
            "snapshot_complete": False,
            "completeness_reasons": [
                (
                    "required_endpoint_failed:"
                    "wallet:UNIFIED:denied"
                )
            ],
            "required_endpoints": [
                "wallet:UNIFIED",
            ],
            "successful_endpoints": [],
            "failed_endpoints": [
                "wallet:UNIFIED",
            ],
            "captured_at": (
                "2026-07-20T10:00:00+00:00"
            ),
            "source_account": (
                "fund:1:UNIFIED"
            ),
            "fund_id": 1,
            "fund_code": "wb",
            "cash": {},
            "assets": {},
        }
    )

    with pytest.raises(
        NegativeSalePlanError,
        match=(
            "live_negative_sale_snapshot_"
            "incomplete"
        ),
    ):
        _validate_snapshot_for_sale_plan(
            settlement_batch=(
                SimpleNamespace(fund_id=1)
            ),
            fund=SimpleNamespace(code="wb"),
            snapshot=snapshot,
        )


def test_live_missing_instrument_status_is_blocked():
    snapshot = normalize_negative_sale_snapshot(
        {
            "source": "bybit_readonly",
            "snapshot_complete": True,
            "required_endpoints": [
                "wallet:UNIFIED",
            ],
            "successful_endpoints": [
                "wallet:UNIFIED",
            ],
            "captured_at": (
                "2026-07-20T10:00:00+00:00"
            ),
            "source_account": (
                "fund:1:UNIFIED"
            ),
            "fund_id": 1,
            "fund_code": "wb",
            "cash": {},
            "assets": {
                "spot": [
                    {
                        "coin": "ETH",
                        "symbol": "ETHUSDT",
                        "qty": "1",
                        "usd_value": "100",
                        "instrument_status": None,
                    }
                ],
            },
        }
    )

    policy = (
        compute_proportional_derivative_reduction(
            planned_net_shares_change="-25",
            shares_outstanding_before="100",
        )
    )

    legs = _build_asset_sale_legs(
        snapshot=snapshot,
        sale_target_usdt=Decimal("80"),
        derivative_reduction=policy,
    )

    assert len(legs) == 1
    assert legs[0].status == (
        SALE_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING
    )
    assert legs[0].eligible is False
    assert legs[0].target_qty is None


def test_position_idx_and_close_side_survive_planning():
    snapshot = normalize_negative_sale_snapshot(
        {
            "cash": {},
            "assets": {
                "perp_future_positions": [
                    {
                        "coin": "BTC",
                        "symbol": "BTCUSDT",
                        "category": "linear",
                        "position_side": "Buy",
                        "position_idx": 1,
                        "size": "8",
                        "usd_value": "800",
                        "notional_usd": "800",
                        "instrument_status": (
                            "Trading"
                        ),
                    }
                ],
            },
        }
    )

    policy = (
        compute_proportional_derivative_reduction(
            planned_net_shares_change="-25",
            shares_outstanding_before="100",
        )
    )

    legs = _build_asset_sale_legs(
        snapshot=snapshot,
        sale_target_usdt=Decimal("0"),
        derivative_reduction=policy,
    )

    assert len(legs) == 1

    leg = legs[0]

    assert leg.position_side == "long"
    assert leg.close_side == "Sell"
    assert leg.side == "Sell"
    assert leg.position_idx == 1
    assert leg.target_qty == Decimal("2.00")
    assert (
        leg.expected_cash_delta_usdt
        == Decimal("0")
    )
    assert (
        leg.raw["position_idx"]
        == 1
    )
    assert (
        leg.raw["reduce_only"]
        is True
    )