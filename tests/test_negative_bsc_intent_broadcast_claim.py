import json
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
from typing import Any

import pytest

from app.models import FundBscTransactionIntent
from app.settlement.bsc_intent_service import (
    BscIntentError,
    PreparedBscTransaction,
    build_bsc_intent_safe_audit,
    claim_bsc_intent_broadcast_attempt,
    mark_bsc_intent_broadcasting,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PREPARED,
)


class FakeQuery:
    def __init__(
        self,
        session: "FakeSession",
    ) -> None:
        self.session = session

    def filter(
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
        return self

    def first(self) -> Any:
        self.session.query_count += 1

        return self.session.intent


class FakeSession:
    def __init__(
        self,
        intent: FundBscTransactionIntent,
    ) -> None:
        self.intent = intent
        self.execute_calls: list[
            tuple[Any, dict[str, Any]]
        ] = []
        self.added: list[Any] = []
        self.query_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0

    def query(
        self,
        model: type[Any],
    ) -> FakeQuery:
        assert model is FundBscTransactionIntent

        return FakeQuery(self)

    def execute(
        self,
        statement: Any,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.execute_calls.append(
            (
                statement,
                dict(params or {}),
            )
        )

    def add(self, value: Any) -> None:
        assert value is self.intent
        self.added.append(value)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, value: Any) -> None:
        assert value is self.intent
        self.refresh_count += 1


def _prepared() -> PreparedBscTransaction:
    return PreparedBscTransaction(
        chain_id=56,
        source_nonce=7,
        tx_hash=f"0x{'ab' * 32}",
        raw_tx_hex="0x0102",
    )


def _intent(
    *,
    status: str = BSC_INTENT_STATUS_PREPARED,
) -> FundBscTransactionIntent:
    prepared = _prepared()

    audit = build_bsc_intent_safe_audit(
        scope_key="negative-payout:11:22:33",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        settlement_batch_id=11,
        payout_batch_id=22,
        payout_leg_id=33,
        fund_id=9,
        asset="USDT",
        amount=Decimal("10.5"),
        from_address="0xabcdef",
        to_address="0x123456",
        prepared=prepared,
    )

    created_at = datetime(
        2026,
        7,
        23,
        10,
        0,
        tzinfo=timezone.utc,
    )

    return FundBscTransactionIntent(
        id=42,
        scope_key="negative-payout:11:22:33",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        settlement_batch_id=11,
        payout_batch_id=22,
        payout_leg_id=33,
        fund_id=9,
        asset="USDT",
        amount=Decimal("10.5"),
        from_address="0xabcdef",
        to_address="0x123456",
        chain_id=56,
        source_nonce=7,
        prepared_tx_hash=prepared.tx_hash,
        prepared_raw_tx=prepared.raw_tx_hex,
        intent_fingerprint=(
            audit["intent_fingerprint"]
        ),
        status=status,
        broadcast_attempts=0,
        prepared_at=created_at,
        prepared_json={
            **audit,
            "durable_boundary": (
                "prepared_before_broadcast"
            ),
        },
        created_at=created_at,
        updated_at=created_at,
    )


def test_prepared_intent_is_only_marked_broadcasting():
    intent = _intent()
    db = FakeSession(intent)

    transition_at = datetime(
        2026,
        7,
        23,
        11,
        0,
        tzinfo=timezone.utc,
    )

    returned = mark_bsc_intent_broadcasting(
        db,
        intent_id=intent.id,
        now=transition_at,
    )

    assert returned is intent
    assert (
        intent.status
        == BSC_INTENT_STATUS_BROADCASTING
    )
    assert intent.broadcast_attempts == 0
    assert intent.broadcast_started_at == transition_at
    assert intent.broadcast_at is None
    assert intent.visible_at is None

    assert intent.broadcast_json["phase"] == (
        "ready_for_broadcast"
    )
    assert (
        intent.broadcast_json[
            "broadcast_attempts"
        ]
        == 0
    )

    assert db.commit_count == 1
    assert db.refresh_count == 1
    assert len(db.execute_calls) == 1

    serialized = json.dumps(
        intent.broadcast_json,
        sort_keys=True,
    )

    assert intent.prepared_raw_tx not in serialized
    assert "prepared_raw_tx" not in serialized
    assert "raw_tx_hex" not in serialized


def test_mark_broadcasting_is_idempotent():
    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCASTING
    )
    intent.broadcast_started_at = datetime(
        2026,
        7,
        23,
        11,
        0,
        tzinfo=timezone.utc,
    )
    intent.broadcast_json = {
        "phase": "ready_for_broadcast",
    }
    db = FakeSession(intent)

    returned = mark_bsc_intent_broadcasting(
        db,
        intent_id=intent.id,
    )

    assert returned is intent
    assert (
        intent.status
        == BSC_INTENT_STATUS_BROADCASTING
    )
    assert intent.broadcast_attempts == 0
    assert db.commit_count == 1


def test_broadcast_attempt_claim_is_durable():
    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCASTING
    )
    intent.broadcast_json = {
        "phase": "ready_for_broadcast",
    }
    db = FakeSession(intent)

    claim_at = datetime(
        2026,
        7,
        23,
        11,
        5,
        tzinfo=timezone.utc,
    )

    claim = claim_bsc_intent_broadcast_attempt(
        db,
        intent_id=intent.id,
        now=claim_at,
    )

    assert claim.action == "claim_created"
    assert claim.claim_token
    assert claim.broadcast_attempts == 1

    assert intent.broadcast_attempts == 1
    assert intent.status == (
        BSC_INTENT_STATUS_BROADCASTING
    )
    assert intent.broadcast_json["phase"] == (
        "attempt_claimed"
    )
    assert (
        intent.broadcast_json["claim_token"]
        == claim.claim_token
    )
    assert (
        intent.broadcast_json["claimed_at"]
        == claim_at.isoformat()
    )

    assert db.commit_count == 1
    assert db.refresh_count == 1


def test_active_claim_blocks_second_worker():
    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCASTING
    )
    claimed_at = datetime(
        2026,
        7,
        23,
        11,
        5,
        tzinfo=timezone.utc,
    )
    intent.broadcast_attempts = 1
    intent.broadcast_json = {
        "phase": "attempt_claimed",
        "claim_token": "worker-one-token",
        "claimed_at": claimed_at.isoformat(),
    }
    db = FakeSession(intent)

    second = claim_bsc_intent_broadcast_attempt(
        db,
        intent_id=intent.id,
        now=claimed_at + timedelta(seconds=30),
    )

    assert second.action == "claim_already_active"
    assert second.claim_token is None
    assert second.broadcast_attempts == 1
    assert intent.broadcast_attempts == 1
    assert (
        intent.broadcast_json["claim_token"]
        == "worker-one-token"
    )


def test_stale_claim_can_be_reclaimed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.settlement.bsc_intent_service."
        "settings."
        "NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC",
        60,
    )

    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCASTING
    )
    first_claimed_at = datetime(
        2026,
        7,
        23,
        11,
        0,
        tzinfo=timezone.utc,
    )
    intent.broadcast_attempts = 1
    intent.broadcast_json = {
        "phase": "attempt_claimed",
        "claim_token": "stale-token",
        "claimed_at": first_claimed_at.isoformat(),
    }
    db = FakeSession(intent)

    replacement = (
        claim_bsc_intent_broadcast_attempt(
            db,
            intent_id=intent.id,
            now=(
                first_claimed_at
                + timedelta(seconds=61)
            ),
        )
    )

    assert replacement.action == "claim_created"
    assert replacement.claim_token
    assert replacement.claim_token != "stale-token"
    assert replacement.broadcast_attempts == 2
    assert intent.broadcast_attempts == 2
    assert (
        intent.broadcast_json[
            "previous_claim_was_stale"
        ]
        is True
    )


def test_claim_requires_broadcasting_transition():
    intent = _intent()
    db = FakeSession(intent)

    with pytest.raises(
        BscIntentError,
        match="must first be marked broadcasting",
    ):
        claim_bsc_intent_broadcast_attempt(
            db,
            intent_id=intent.id,
        )

    assert intent.status == (
        BSC_INTENT_STATUS_PREPARED
    )
    assert intent.broadcast_attempts == 0
    assert db.commit_count == 1


def test_later_status_never_receives_new_claim():
    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCAST
    )
    intent.broadcast_attempts = 1
    db = FakeSession(intent)

    claim = claim_bsc_intent_broadcast_attempt(
        db,
        intent_id=intent.id,
    )

    assert claim.action == (
        "status_broadcast"
    )
    assert claim.claim_token is None
    assert claim.broadcast_attempts == 1


def test_corrupted_fingerprint_fails_closed():
    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCASTING
    )
    intent.intent_fingerprint = "0" * 64
    db = FakeSession(intent)

    with pytest.raises(
        BscIntentError,
        match="contract mismatch",
    ):
        claim_bsc_intent_broadcast_attempt(
            db,
            intent_id=intent.id,
        )

    assert intent.status == (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert intent.failed_at is not None
    assert db.commit_count == 1