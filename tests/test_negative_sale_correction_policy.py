from __future__ import annotations

from decimal import Decimal

import pytest

from app.settlement.negative_sale_correction_policy import (
    NegativeSaleCorrectionPolicyError,
    compute_spot_correction_target_usdt,
    evaluate_next_correction_round,
    select_largest_eligible_spot_source,
)


def test_pending_action_blocks_correction():
    decision = (
        evaluate_next_correction_round(
            required_master_usdt=(
                Decimal("100")
            ),
            confirmed_available_usdt=(
                Decimal("60")
            ),
            completed_rounds=0,
            max_rounds=2,
            has_pending_action=True,
        )
    )

    assert decision.allowed is False
    assert decision.next_round is None
    assert decision.reason == (
        "pending_action_blocks_correction"
    )
    assert decision.shortage_usdt == (
        Decimal("40")
    )


def test_correction_rounds_are_bounded():
    decision = (
        evaluate_next_correction_round(
            required_master_usdt=(
                Decimal("100")
            ),
            confirmed_available_usdt=(
                Decimal("90")
            ),
            completed_rounds=2,
            max_rounds=2,
            has_pending_action=False,
        )
    )

    assert decision.allowed is False
    assert decision.next_round is None
    assert decision.reason == (
        "correction_rounds_exhausted"
    )


def test_next_round_is_deterministic():
    decision = (
        evaluate_next_correction_round(
            required_master_usdt=(
                Decimal("100")
            ),
            confirmed_available_usdt=(
                Decimal("90")
            ),
            completed_rounds=1,
            max_rounds=2,
            has_pending_action=False,
        )
    )

    assert decision.allowed is True
    assert decision.next_round == 2
    assert decision.shortage_usdt == (
        Decimal("10")
    )


def test_buffer_is_applied_once():
    target = (
        compute_spot_correction_target_usdt(
            shortage_usdt=Decimal("100"),
            remaining_sellable_usdt=(
                Decimal("1000")
            ),
            oversell_cap_usdt=(
                Decimal("1000")
            ),
            buffer_pct=Decimal("0.10"),
        )
    )

    assert target == Decimal("110.00")


def test_remaining_value_and_oversell_cap_limit_target():
    by_remaining = (
        compute_spot_correction_target_usdt(
            shortage_usdt=Decimal("100"),
            remaining_sellable_usdt=(
                Decimal("70")
            ),
            oversell_cap_usdt=(
                Decimal("1000")
            ),
            buffer_pct=Decimal("0.10"),
        )
    )
    by_cap = (
        compute_spot_correction_target_usdt(
            shortage_usdt=Decimal("100"),
            remaining_sellable_usdt=(
                Decimal("1000")
            ),
            oversell_cap_usdt=(
                Decimal("80")
            ),
            buffer_pct=Decimal("0.10"),
        )
    )

    assert by_remaining == Decimal("70")
    assert by_cap == Decimal("80")


def test_only_largest_eligible_unified_spot_is_selected():
    selected = (
        select_largest_eligible_spot_source(
            [
                {
                    "source_key": "derivative",
                    "symbol": "BTCUSDT",
                    "category": "linear",
                    "asset_type": "perpetual",
                    "eligible": True,
                    "use_for_deficit_cover": True,
                    "remaining_sellable_usdt": (
                        "999999"
                    ),
                    "notional_usd": "999999",
                },
                {
                    "source_key": "fund-spot",
                    "symbol": "ETHUSDT",
                    "category": "spot",
                    "asset_type": "spot",
                    "location": "FUND_WALLET",
                    "eligible": True,
                    "use_for_deficit_cover": True,
                    "requires_fund_to_unified_transfer": (
                        True
                    ),
                    "remaining_sellable_usdt": (
                        "5000"
                    ),
                },
                {
                    "source_key": "small",
                    "symbol": "SOLUSDT",
                    "category": "spot",
                    "asset_type": "spot",
                    "location": "UNIFIED",
                    "eligible": True,
                    "use_for_deficit_cover": True,
                    "remaining_sellable_usdt": (
                        "200"
                    ),
                },
                {
                    "source_key": "large",
                    "symbol": "ETHUSDT",
                    "category": "spot",
                    "asset_type": "spot",
                    "location": "UNIFIED",
                    "eligible": True,
                    "use_for_deficit_cover": True,
                    "remaining_sellable_usdt": (
                        "300"
                    ),
                },
            ]
        )
    )

    assert selected is not None
    assert selected.source_key == "large"
    assert selected.symbol == "ETHUSDT"
    assert (
        selected.remaining_sellable_usdt
        == Decimal("300")
    )


def test_float_is_rejected():
    with pytest.raises(
        NegativeSaleCorrectionPolicyError,
        match="must not be float",
    ):
        compute_spot_correction_target_usdt(
            shortage_usdt=100.0,
            remaining_sellable_usdt=(
                Decimal("1000")
            ),
            oversell_cap_usdt=(
                Decimal("1000")
            ),
            buffer_pct=Decimal("0.10"),
        )