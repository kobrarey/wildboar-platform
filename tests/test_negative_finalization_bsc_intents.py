from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.settlement.negative_finalization as service
from app.settlement.statuses import (
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_UNRESOLVED_STATUSES,
)


NOW = datetime(
    2026,
    7,
    26,
    14,
    0,
    tzinfo=timezone.utc,
)

SETTLEMENT_ADDRESS = (
    "0x1111111111111111111111111111111111111111"
)

USER_ADDRESS = (
    "0x2222222222222222222222222222222222222222"
)

PAYOUT_HASH = (
    "0x"
    + ("a" * 64)
)

GAS_HASH = (
    "0x"
    + ("c" * 64)
)


def make_batch(
    *,
    gas_topup: bool = False,
) -> Any:
    return SimpleNamespace(
        id=41,
        settlement_batch_id=11,
        settlement_wallet_address=(
            SETTLEMENT_ADDRESS
        ),
        gas_status="ready",
        gas_topup_tx_hash=(
            GAS_HASH
            if gas_topup
            else None
        ),
        gas_reconciliation_json=(
            {
                "live": True,
                "durable_intent": True,
                "intent_id": 82,
                "intent_status": "confirmed",
                "prepared_tx_hash": GAS_HASH,
                "confirmed": True,
            }
            if gas_topup
            else {
                "live": True,
                "gas_sufficient": True,
                "no_real_gas_topup_needed": True,
                "durable_intent_not_required": True,
            }
        ),
    )


def make_leg() -> Any:
    return SimpleNamespace(
        id=51,
        payout_batch_id=41,
        settlement_batch_id=11,
        fund_id=3,
        amount_usdt=Decimal("10"),
        from_address=SETTLEMENT_ADDRESS,
        to_address=USER_ADDRESS,
        tx_hash=PAYOUT_HASH,
        confirmation_json={
            "durable_intent": True,
            "intent_id": 81,
            "intent_status": "confirmed",
            "tx_hash": PAYOUT_HASH,
            "confirmed": True,
        },
    )


def make_payout_intent(
    *,
    status: str = "confirmed",
    **overrides: Any,
) -> Any:
    values = {
        "id": 81,
        "scope_key": (
            "negative-payout:11:41:51"
        ),
        "action_type": (
            "negative_redeem_payout"
        ),
        "settlement_batch_id": 11,
        "payout_batch_id": 41,
        "payout_leg_id": 51,
        "fund_id": 3,
        "asset": "USDT",
        "amount": Decimal("10"),
        "from_address": SETTLEMENT_ADDRESS,
        "to_address": USER_ADDRESS,
        "prepared_tx_hash": PAYOUT_HASH,
        "intent_fingerprint": "b" * 64,
        "status": status,
        "receipt_status": 1,
        "confirmations": max(
            1,
            int(
                service.settings
                .NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED
            ),
        ),
        "confirmed_at": (
            NOW
            if status == "confirmed"
            else None
        ),
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def make_gas_intent(
    *,
    status: str = "confirmed",
    **overrides: Any,
) -> Any:
    values = {
        "id": 82,
        "scope_key": (
            "negative-gas:11:41"
        ),
        "action_type": (
            "negative_settlement_gas_topup"
        ),
        "settlement_batch_id": 11,
        "payout_batch_id": 41,
        "payout_leg_id": None,
        "fund_id": 3,
        "asset": "BNB",
        "amount": Decimal(
            "0.001"
        ),
        "from_address": (
            "0x3333333333333333333333333333333333333333"
        ),
        "to_address": SETTLEMENT_ADDRESS,
        "prepared_tx_hash": GAS_HASH,
        "intent_fingerprint": "d" * 64,
        "status": status,
        "receipt_status": 1,
        "confirmations": max(
            1,
            int(
                service.settings
                .NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED
            ),
        ),
        "confirmed_at": (
            NOW
            if status == "confirmed"
            else None
        ),
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def validate(
    *,
    batch: Any | None = None,
    legs: list[Any] | None = None,
    intents: list[Any] | None = None,
) -> dict[str, Any]:
    return (
        service
        ._validate_bsc_delivery_intents(
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
            bsc_intents=(
                intents
                if intents is not None
                else [
                    make_payout_intent()
                ]
            ),
        )
    )


def test_confirmed_payout_intent_and_no_gas_topup_are_accepted() -> None:
    result = validate()

    assert result[
        "all_intents_terminal_confirmed"
    ] is True

    assert result[
        "prepared_raw_tx_omitted"
    ] is True

    assert result["gas"]["mode"] == (
        "not_needed"
    )

    assert result[
        "payout_intents"
    ][0]["intent_id"] == 81


@pytest.mark.parametrize(
    "status",
    sorted(
        BSC_INTENT_UNRESOLVED_STATUSES
    ),
)
def test_each_unresolved_intent_status_blocks_finalization(
    status: str,
) -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match="Unresolved BSC intents",
    ):
        validate(
            intents=[
                make_payout_intent(
                    status=status
                )
            ]
        )


def test_failed_requires_review_intent_blocks_finalization() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match="requiring review",
    ):
        validate(
            intents=[
                make_payout_intent(
                    status=(
                        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
                    )
                )
            ]
        )


def test_missing_payout_intent_blocks_finalization() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match="exactly one confirmed",
    ):
        validate(
            intents=[]
        )


def test_duplicate_payout_intents_block_finalization() -> None:
    duplicate = make_payout_intent(
        id=83,
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match="exactly one confirmed",
    ):
        validate(
            intents=[
                make_payout_intent(),
                duplicate,
            ]
        )


@pytest.mark.parametrize(
    (
        "field_name",
        "bad_value",
        "error_match",
    ),
    [
        (
            "amount",
            Decimal("9"),
            "amount mismatch",
        ),
        (
            "from_address",
            (
                "0x4444444444444444444444444444444444444444"
            ),
            "source address",
        ),
        (
            "to_address",
            (
                "0x5555555555555555555555555555555555555555"
            ),
            "destination",
        ),
        (
            "prepared_tx_hash",
            (
                "0x"
                + ("f" * 64)
            ),
            "transaction hash",
        ),
        (
            "payout_batch_id",
            999,
            "payout batch",
        ),
    ],
)
def test_payout_intent_contract_mismatch_blocks_finalization(
    field_name: str,
    bad_value: Any,
    error_match: str,
) -> None:
    intent = make_payout_intent(
        **{
            field_name: bad_value,
        }
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match=error_match,
    ):
        validate(
            intents=[intent]
        )


@pytest.mark.parametrize(
    (
        "overrides",
        "error_match",
    ),
    [
        (
            {
                "confirmed_at": None,
            },
            "confirmed_at",
        ),
        (
            {
                "receipt_status": 0,
            },
            "receipt_status",
        ),
        (
            {
                "confirmations": 0,
            },
            "insufficient confirmations",
        ),
    ],
)
def test_incomplete_confirmation_evidence_blocks_finalization(
    overrides: dict[str, Any],
    error_match: str,
) -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=error_match,
    ):
        validate(
            intents=[
                make_payout_intent(
                    **overrides
                )
            ]
        )


def test_confirmed_gas_intent_is_accepted() -> None:
    result = validate(
        batch=make_batch(
            gas_topup=True
        ),
        intents=[
            make_payout_intent(),
            make_gas_intent(),
        ],
    )

    assert result["gas"]["mode"] == (
        "confirmed_intent"
    )

    assert result["gas"][
        "intent_id"
    ] == 82


def test_missing_gas_intent_blocks_when_topup_was_recorded() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match="Exactly one confirmed gas intent",
    ):
        validate(
            batch=make_batch(
                gas_topup=True
            ),
            intents=[
                make_payout_intent()
            ],
        )


def test_gas_intent_blocks_when_no_topup_was_needed() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match="despite no-topup-needed",
    ):
        validate(
            intents=[
                make_payout_intent(),
                make_gas_intent(),
            ]
        )


def test_unknown_intent_action_blocks_finalization() -> None:
    unknown = make_payout_intent(
        id=90,
        action_type="unexpected_action",
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match="Unknown BSC intent actions",
    ):
        validate(
            intents=[
                make_payout_intent(),
                unknown,
            ]
        )