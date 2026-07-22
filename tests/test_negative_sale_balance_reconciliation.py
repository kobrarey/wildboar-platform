from __future__ import annotations

from copy import deepcopy
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal

import pytest

from app.settlement.negative_sale_balance_reconciliation import (
    NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA,
    NegativeSaleBalanceReconciliationError,
    append_confirmed_balance_refresh,
    has_balance_refresh_for_action,
    latest_confirmed_available_usdt,
    validate_balance_reconciliation_json,
)


NOW = datetime(
    2026,
    7,
    22,
    20,
    0,
    tzinfo=timezone.utc,
)


def _balance(
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


def _append(
    *,
    existing=None,
    after: str = "80",
    captured_at: datetime = NOW,
):
    return append_confirmed_balance_refresh(
        existing_reconciliation_json=(
            existing
        ),
        required_master_usdt="100",
        balance_before_usdt=None,
        balance_after_usdt=after,
        transferable_balance=(
            _balance(after)
        ),
        action_type=(
            "order_terminal_confirmed"
        ),
        active_leg_id=30,
        order_link_id=(
            "wbns-10-30-r0-s0"
        ),
        captured_at=captured_at,
    )


def test_first_refresh_builds_auditable_snapshot():
    result = _append()

    assert result["schema"] == (
        NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    )
    assert result[
        "confirmed_available_usdt"
    ] == "80"
    assert result[
        "confirmed_shortage_usdt"
    ] == "20"
    assert result[
        "confirmed_surplus_usdt"
    ] == "0"

    assert len(
        result["refresh_history"]
    ) == 1

    record = result[
        "refresh_history"
    ][0]

    assert record[
        "balance_before_usdt"
    ] is None
    assert record[
        "balance_after_usdt"
    ] == "80"
    assert record[
        "active_leg_id"
    ] == 30
    assert record[
        "order_link_id"
    ] == "wbns-10-30-r0-s0"
    assert len(
        record["raw_fingerprint"]
    ) == 64
    assert len(record["event_id"]) == 64

    validate_balance_reconciliation_json(
        result
    )


def test_identical_external_state_is_idempotent():
    first = _append()

    second = _append(
        existing=first,
        captured_at=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    assert second == first
    assert len(
        second["refresh_history"]
    ) == 1


def test_changed_external_state_appends_record():
    first = _append()

    second = _append(
        existing=first,
        after="95",
        captured_at=(
            NOW
            + timedelta(minutes=5)
        ),
    )

    assert len(
        second["refresh_history"]
    ) == 2

    latest = second[
        "latest_refresh"
    ]

    assert latest[
        "balance_before_usdt"
    ] == "80"
    assert latest[
        "balance_after_usdt"
    ] == "95"
    assert second[
        "confirmed_shortage_usdt"
    ] == "5"


def test_legacy_reconciliation_is_preserved():
    legacy = {
        "schema": (
            "negative_sale_confirmed_"
            "transferable_balance_v1"
        ),
        "confirmed_available_usdt": (
            "70"
        ),
    }

    result = _append(
        existing=legacy
    )

    assert result[
        "previous_reconciliation_json"
    ] == legacy
    assert len(
        result["refresh_history"]
    ) == 1


def test_action_lookup_and_latest_balance():
    result = _append(
        after="105"
    )

    assert (
        latest_confirmed_available_usdt(
            result
        )
        == Decimal("105")
    )

    assert has_balance_refresh_for_action(
        result,
        action_type=(
            "order_terminal_confirmed"
        ),
        active_leg_id=30,
        order_link_id=(
            "wbns-10-30-r0-s0"
        ),
    ) is True

    assert has_balance_refresh_for_action(
        result,
        action_type=(
            "earn_terminal_confirmed"
        ),
        active_leg_id=30,
        order_link_id=(
            "wbns-10-30-r0-s0"
        ),
    ) is False


def test_tampered_raw_payload_fails_closed():
    result = _append()
    tampered = deepcopy(result)

    tampered[
        "refresh_history"
    ][0][
        "transferable_balance"
    ]["raw"]["result"]["balance"][
        "transferBalance"
    ] = "999"

    tampered["latest_refresh"] = (
        deepcopy(
            tampered[
                "refresh_history"
            ][0]
        )
    )

    with pytest.raises(
        NegativeSaleBalanceReconciliationError,
        match=(
            "Balance raw fingerprint "
            "mismatch"
        ),
    ):
        validate_balance_reconciliation_json(
            tampered
        )