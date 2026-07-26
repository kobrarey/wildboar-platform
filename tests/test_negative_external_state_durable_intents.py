from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    FundBscTransactionIntent,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
)
from app.settlement.negative_external_state import (
    BSC_DURABLE_EXTERNAL_STATUSES,
    inspect_negative_external_state,
)


NOW = datetime(
    2026,
    7,
    26,
    12,
    0,
    tzinfo=timezone.utc,
)


class FakeQuery:
    def __init__(
        self,
        *,
        session: "FakeSession",
        model: type[Any],
        rows: list[Any],
    ) -> None:
        self.session = session
        self.model = model
        self.rows = list(rows)

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeQuery":
        return self

    def order_by(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeQuery":
        self.session.locked_models.append(
            self.model
        )

        return self

    def first(self) -> Any:
        return (
            self.rows[0]
            if self.rows
            else None
        )

    def all(self) -> list[Any]:
        return list(self.rows)


class FakeSession:
    def __init__(
        self,
        *,
        batch: Any,
        bybit_flow: Any | None = None,
        bsc_intents: list[Any] | None = None,
    ) -> None:
        self.rows = {
            FundSettlementBatch: [batch],
            FundOrder: [],
            FundNegativeSaleBatch: [],
            FundNegativeSaleLeg: [],
            FundNegativeBybitFlow: (
                [bybit_flow]
                if bybit_flow is not None
                else []
            ),
            FundNegativePayoutBatch: [],
            FundNegativePayoutLeg: [],
            FundBscTransactionIntent: list(
                bsc_intents or []
            ),
            FundNegativeFinalizationBatch: [],
            FundSettlementTransfer: [],
        }

        self.locked_models: list[
            type[Any]
        ] = []

    def query(
        self,
        model: type[Any],
    ) -> FakeQuery:
        return FakeQuery(
            session=self,
            model=model,
            rows=self.rows.get(
                model,
                [],
            ),
        )


def make_batch() -> Any:
    return SimpleNamespace(
        id=101,
        accounting_finalized_at=None,
    )


def make_bybit_flow() -> Any:
    return SimpleNamespace(
        id=201,
        status="created",
        universal_transfer_intent_json=None,
        withdrawal_intent_json=None,
        universal_transfer_submitted_at=None,
        withdrawal_submitted_at=None,
        universal_transfer_id=None,
        universal_transfer_status=None,
        universal_transfer_created_at=None,
        universal_transfer_confirmed_at=None,
        universal_transfer_reconciliation_json=None,
        withdrawal_request_id=None,
        withdrawal_id=None,
        withdrawal_status=None,
        withdrawal_tx_hash=None,
        withdrawal_created_at=None,
        withdrawal_confirmed_at=None,
        withdrawal_record_json=None,
        withdrawal_reconciliation_json=None,
        settlement_wallet_receipt_tx_hash=None,
        settlement_wallet_receipt_confirmed_at=None,
    )


def make_bsc_intent(
    *,
    status: str,
    intent_id: int = 301,
) -> Any:
    return SimpleNamespace(
        id=intent_id,
        settlement_batch_id=101,
        scope_key=(
            f"negative-payout:101:{intent_id}"
        ),
        action_type="bsc_redeem_payout",
        payout_batch_id=401,
        payout_leg_id=501,
        asset="USDT",
        amount=Decimal("12.5"),
        from_address=(
            "0x1111111111111111111111111111111111111111"
        ),
        to_address=(
            "0x2222222222222222222222222222222222222222"
        ),
        source_nonce=77,
        prepared_tx_hash=(
            "0x"
            + ("a" * 64)
        ),
        prepared_raw_tx=(
            "0xsecret_signed_raw_transaction"
        ),
        intent_fingerprint=(
            "b" * 64
        ),
        status=status,
        prepared_at=NOW,
        broadcast_started_at=None,
        broadcast_at=None,
        confirmed_at=(
            NOW
            if status == "confirmed"
            else None
        ),
    )


def find_evidence(
    state: Any,
    *,
    model: str,
    field: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in state.evidence
        if item["model"] == model
        and item["field"] == field
    ]

    assert len(matches) == 1

    return matches[0]


def test_empty_external_state_is_safe_and_locks_bsc_query() -> None:
    db = FakeSession(
        batch=make_batch(),
    )

    state = inspect_negative_external_state(
        db,
        settlement_batch_id=101,
    )

    assert state.safe_to_release_reserves is True
    assert state.safe_to_unlock_pricing is True
    assert state.bsc_intent_action_detected is False
    assert state.evidence == ()

    assert (
        FundBscTransactionIntent
        in db.locked_models
    )


@pytest.mark.parametrize(
    "status",
    sorted(
        BSC_DURABLE_EXTERNAL_STATUSES
    ),
)
def test_each_durable_bsc_status_blocks_release_and_unlock(
    status: str,
) -> None:
    intent = make_bsc_intent(
        status=status,
    )

    db = FakeSession(
        batch=make_batch(),
        bsc_intents=[intent],
    )

    state = inspect_negative_external_state(
        db,
        settlement_batch_id=101,
    )

    assert state.safe_to_release_reserves is False
    assert state.safe_to_unlock_pricing is False
    assert state.bsc_intent_action_detected is True
    assert state.payout_action_detected is True

    item = find_evidence(
        state,
        model="FundBscTransactionIntent",
        field="durable_intent",
    )

    value = item["value"]

    assert value["id"] == intent.id
    assert value["status"] == status
    assert value["source_nonce"] == 77

    assert value[
        "prepared_tx_hash"
    ] == intent.prepared_tx_hash

    assert value[
        "intent_fingerprint"
    ] == intent.intent_fingerprint

    assert value[
        "prepared_raw_tx"
    ] == "redacted_present"

    assert (
        "secret_signed_raw_transaction"
        not in repr(state.evidence)
    )


@pytest.mark.parametrize(
    (
        "intent_field",
        "submitted_field",
        "identifier_field",
        "identifier_value",
        "action_field",
    ),
    [
        (
            "universal_transfer_intent_json",
            "universal_transfer_submitted_at",
            "transfer_id",
            (
                "11111111-1111-5111-"
                "8111-111111111111"
            ),
            "universal_transfer_action_detected",
        ),
        (
            "withdrawal_intent_json",
            "withdrawal_submitted_at",
            "request_id",
            "A" * 32,
            "withdrawal_action_detected",
        ),
    ],
)
@pytest.mark.parametrize(
    "intent_state",
    [
        "prepared",
        "submitting",
        "reconciling",
        "confirmed",
        "failed_requires_review",
    ],
)
def test_durable_bybit_intent_blocks_release_without_exposing_payload(
    intent_field: str,
    submitted_field: str,
    identifier_field: str,
    identifier_value: str,
    action_field: str,
    intent_state: str,
) -> None:
    flow = make_bybit_flow()

    secret_payload = {
        "api_key": "must_not_be_exposed",
        "address": "sensitive_payload_value",
    }

    intent = {
        "schema": "durable_intent_v2",
        "state": intent_state,
        identifier_field: identifier_value,
        "payload": secret_payload,
        "payload_fingerprint": "c" * 64,
        "prepared_at": NOW.isoformat(),
        "submit_claim": {
            "claim_token": "secret_claim",
        },
        "acknowledgement": {
            "outcome": "accepted",
        },
        "reconciliation": {
            "state": (
                "confirmed"
                if intent_state == "confirmed"
                else "pending"
            ),
        },
    }

    setattr(
        flow,
        intent_field,
        intent,
    )

    setattr(
        flow,
        submitted_field,
        None,
    )

    db = FakeSession(
        batch=make_batch(),
        bybit_flow=flow,
    )

    state = inspect_negative_external_state(
        db,
        settlement_batch_id=101,
    )

    assert state.safe_to_release_reserves is False
    assert state.safe_to_unlock_pricing is False
    assert getattr(
        state,
        action_field,
    ) is True

    item = find_evidence(
        state,
        model="FundNegativeBybitFlow",
        field=intent_field,
    )

    value = item["value"]

    assert value["state"] == intent_state

    assert value[
        identifier_field
    ] == identifier_value

    assert value[
        "payload_fingerprint"
    ] == "c" * 64

    assert value[
        "claim_token_present"
    ] is True

    assert value[
        "acknowledgement_present"
    ] is True

    assert value[
        "payload"
    ] == "redacted_present"

    evidence_text = repr(
        state.evidence
    )

    assert "must_not_be_exposed" not in (
        evidence_text
    )

    assert "sensitive_payload_value" not in (
        evidence_text
    )

    assert "secret_claim" not in (
        evidence_text
    )


@pytest.mark.parametrize(
    (
        "submitted_field",
        "action_field",
    ),
    [
        (
            "universal_transfer_submitted_at",
            "universal_transfer_action_detected",
        ),
        (
            "withdrawal_submitted_at",
            "withdrawal_action_detected",
        ),
    ],
)
def test_submitted_at_without_intent_json_remains_external_evidence(
    submitted_field: str,
    action_field: str,
) -> None:
    flow = make_bybit_flow()

    setattr(
        flow,
        submitted_field,
        NOW,
    )

    db = FakeSession(
        batch=make_batch(),
        bybit_flow=flow,
    )

    state = inspect_negative_external_state(
        db,
        settlement_batch_id=101,
    )

    assert state.safe_to_release_reserves is False
    assert state.safe_to_unlock_pricing is False

    assert getattr(
        state,
        action_field,
    ) is True


def test_unknown_bsc_status_still_blocks_fail_closed() -> None:
    intent = make_bsc_intent(
        status="unexpected_new_status",
    )

    db = FakeSession(
        batch=make_batch(),
        bsc_intents=[intent],
    )

    state = inspect_negative_external_state(
        db,
        settlement_batch_id=101,
    )

    assert state.safe_to_release_reserves is False
    assert state.safe_to_unlock_pricing is False
    assert state.bsc_intent_action_detected is True

    assert any(
        "unknown_or_invalid_status"
        in reason
        for reason in state.reasons
    )