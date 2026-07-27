from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.settlement.negative_finalization as service
from app.settlement.statuses import (
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
)


NOW = datetime(
    2026,
    7,
    26,
    16,
    0,
    tzinfo=timezone.utc,
)

SETTLEMENT_ADDRESS = (
    "0x1111111111111111111111111111111111111111"
)

USER_ADDRESS = (
    "0x2222222222222222222222222222222222222222"
)


def make_leg(
    **overrides: Any,
) -> Any:
    values = {
        "id": 51,
        "user_wallet_id": 501,
        "to_user_wallet_id": 501,
        "to_address": USER_ADDRESS,
        "amount_usdt": Decimal("10"),
        "wallet_balance_before_usdt": Decimal(
            "1"
        ),
        "wallet_balance_after_usdt": Decimal(
            "12"
        ),
        "balance_refresh_json": {
            "live": True,
            "absolute_onchain_sync": True,
            "block_number": 500,
            "user_wallet_id": 501,
            "address": USER_ADDRESS,
            "before_usdt": Decimal("1"),
            "payout_amount_usdt": Decimal(
                "10"
            ),
            "observed_after_usdt": Decimal(
                "12"
            ),
            "arithmetic_credit_applied": False,
        },
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def make_batch(
    **overrides: Any,
) -> Any:
    values = {
        "balance_refresh_status": (
            PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
        ),
        "balance_refresh_completed_at": NOW,
        "expected_total_payout_usdt": Decimal(
            "10"
        ),
        "confirmed_total_payout_usdt": Decimal(
            "10"
        ),
        "settlement_wallet_address": (
            SETTLEMENT_ADDRESS
        ),
        "settlement_wallet_usdt_before": Decimal(
            "100"
        ),
        "settlement_wallet_usdt_after": Decimal(
            "91"
        ),
        "balance_refresh_json": {
            "live": True,
            "absolute_onchain_sync": True,
            "block_number": 500,
            "settlement_wallet": {
                "address": (
                    SETTLEMENT_ADDRESS
                ),
                "before_usdt": Decimal(
                    "100"
                ),
                "confirmed_total_payout_usdt": Decimal(
                    "10"
                ),
                "observed_after_usdt": Decimal(
                    "91"
                ),
                "arithmetic_debit_applied": False,
            },
            "user_wallets": [
                {
                    "user_wallet_id": 501,
                    "address": USER_ADDRESS,
                    "before_usdt": Decimal(
                        "1"
                    ),
                    "payout_amount_usdt": Decimal(
                        "10"
                    ),
                    "observed_after_usdt": Decimal(
                        "12"
                    ),
                    "block_number": 500,
                    "absolute_onchain_sync": True,
                }
            ],
        },
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def validate(
    *,
    batch: Any | None = None,
    legs: list[Any] | None = None,
) -> dict[str, Any]:
    return (
        service
        ._validate_balance_refresh_evidence(
            payout_batch=(
                batch
                if batch is not None
                else make_batch()
            ),
            payout_legs=(
                legs
                if legs is not None
                else [make_leg()]
            ),
        )
    )


def test_valid_absolute_balance_refresh_is_accepted() -> None:
    result = validate()

    assert result[
        "absolute_onchain_sync"
    ] is True

    assert result[
        "arithmetic_balance_updates"
    ] is False

    assert result[
        "confirmed_total_payout_usdt"
    ] == Decimal("10.0000000000")


@pytest.mark.parametrize(
    (
        "overrides",
        "error_match",
    ),
    [
        (
            {
                "balance_refresh_status": (
                    "started"
                ),
            },
            "status must be confirmed",
        ),
        (
            {
                "balance_refresh_completed_at": (
                    None
                ),
            },
            "completed_at",
        ),
        (
            {
                "confirmed_total_payout_usdt": Decimal(
                    "9"
                ),
            },
            "expected and confirmed",
        ),
        (
            {
                "settlement_wallet_usdt_after": Decimal(
                    "90"
                ),
            },
            "balance-after",
        ),
    ],
)
def test_invalid_batch_refresh_evidence_is_rejected(
    overrides: dict[str, Any],
    error_match: str,
) -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=error_match,
    ):
        validate(
            batch=make_batch(
                **overrides
            )
        )


def test_leg_total_must_match_confirmed_total() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match="leg total",
    ):
        validate(
            legs=[
                make_leg(
                    amount_usdt=Decimal(
                        "9"
                    )
                )
            ]
        )


def test_leg_requires_absolute_refresh_evidence() -> None:
    leg = make_leg()

    leg.balance_refresh_json = {
        **deepcopy(
            leg.balance_refresh_json
        ),
        "absolute_onchain_sync": False,
    }

    with pytest.raises(
        service.NegativeFinalizationError,
        match="absolute on-chain",
    ):
        validate(
            legs=[leg]
        )


def test_duplicate_wallet_refresh_rows_are_rejected() -> None:
    batch = make_batch()

    batch.balance_refresh_json[
        "user_wallets"
    ].append(
        deepcopy(
            batch.balance_refresh_json[
                "user_wallets"
            ][0]
        )
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match="count mismatch|duplicate",
    ):
        validate(
            batch=batch
        )


def test_arithmetic_credit_marker_is_rejected() -> None:
    leg = make_leg()

    leg.balance_refresh_json[
        "arithmetic_credit_applied"
    ] = True

    with pytest.raises(
        service.NegativeFinalizationError,
        match="arithmetic credit",
    ):
        validate(
            legs=[leg]
        )