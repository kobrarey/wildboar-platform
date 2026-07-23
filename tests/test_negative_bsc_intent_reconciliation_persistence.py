from datetime import (
    datetime,
    timedelta,
    timezone,
)
from decimal import Decimal
from typing import Any

import pytest

import app.settlement.bsc_intent_reconciliation_service as service

from app.models import FundBscTransactionIntent
from app.settlement.bsc_intent_reconciliation import (
    BscIntentReconciliationResult,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_VISIBLE,
)


TX_HASH = f"0x{'ab' * 32}"
OTHER_TX_HASH = f"0x{'cd' * 32}"
SOURCE = f"0x{'11' * 20}"
DESTINATION = f"0x{'22' * 20}"
FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64
NOW = datetime(
    2026,
    7,
    23,
    12,
    0,
    tzinfo=timezone.utc,
)


class FakeSession:
    def __init__(
        self,
        intent: FundBscTransactionIntent,
    ) -> None:
        self.intent = intent
        self.locked = False
        self.commit_count = 0
        self.refresh_count = 0
        self.add_count = 0
        self.events: list[str] = []

    def add(
        self,
        value: Any,
    ) -> None:
        assert value is self.intent
        self.add_count += 1

    def commit(self) -> None:
        self.events.append("commit")
        self.commit_count += 1
        self.locked = False

    def refresh(
        self,
        value: Any,
    ) -> None:
        assert value is self.intent
        self.refresh_count += 1


def _intent(
    *,
    status: str = BSC_INTENT_STATUS_BROADCAST,
) -> FundBscTransactionIntent:
    return FundBscTransactionIntent(
        id=42,
        scope_key="negative-payout:10:20:30",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        asset="USDT",
        amount=Decimal("10"),
        from_address=SOURCE,
        to_address=DESTINATION,
        chain_id=56,
        source_nonce=8,
        prepared_tx_hash=TX_HASH,
        prepared_raw_tx="0x01",
        intent_fingerprint="f" * 64,
        status=status,
        confirmations=0,
        prepared_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _observation(
    *,
    action: str,
    suggested_status: str,
    confirmations: int = 0,
    fingerprint: str | None = None,
    tx_hash: str = TX_HASH,
    receipt_status: int | None = None,
    block_number: int | None = None,
    current_block: int | None = None,
    reason_code: str | None = None,
    error: str | None = None,
) -> BscIntentReconciliationResult:
    return BscIntentReconciliationResult(
        action=action,
        intent_id=42,
        suggested_status=suggested_status,
        tx_hash=tx_hash,
        receipt_status=receipt_status,
        block_number=block_number,
        current_block=current_block,
        confirmations=confirmations,
        required_confirmations=12,
        reason_code=reason_code,
        error=error,
        reconciliation_fingerprint=(
            fingerprint
        ),
        evidence={
            "action": action,
            "confirmations": confirmations,
        },
    )


def _confirmed_observation(
    *,
    fingerprint: str = FINGERPRINT,
    tx_hash: str = TX_HASH,
) -> BscIntentReconciliationResult:
    return _observation(
        action="confirmed",
        suggested_status=(
            BSC_INTENT_STATUS_CONFIRMED
        ),
        confirmations=12,
        fingerprint=fingerprint,
        tx_hash=tx_hash,
        receipt_status=1,
        block_number=100,
        current_block=111,
    )


def _pending_observation(
    *,
    confirmations: int = 5,
    fingerprint: str = FINGERPRINT,
) -> BscIntentReconciliationResult:
    return _observation(
        action="pending_confirmation",
        suggested_status=(
            BSC_INTENT_STATUS_PENDING_CONFIRMATION
        ),
        confirmations=confirmations,
        fingerprint=fingerprint,
        receipt_status=1,
        block_number=100,
        current_block=(
            100 + confirmations - 1
        ),
        reason_code=(
            "insufficient_confirmations"
        ),
    )


@pytest.fixture(autouse=True)
def _patch_db_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_load(
        db: FakeSession,
        *,
        intent_id: int,
    ) -> FundBscTransactionIntent:
        assert intent_id == 42
        assert db.locked is False

        db.locked = True
        db.events.append("lock")
        return db.intent

    def fake_validate(
        db: FakeSession,
        *,
        intent: FundBscTransactionIntent,
    ) -> dict[str, Any]:
        assert db.locked is True
        assert intent is db.intent
        return {}

    def fake_commit(
        db: FakeSession,
        *,
        intent: FundBscTransactionIntent,
    ) -> FundBscTransactionIntent:
        assert db.locked is True
        assert intent is db.intent

        db.add(intent)
        db.commit()
        db.refresh(intent)
        return intent

    monkeypatch.setattr(
        service,
        "_load_bsc_intent_for_update",
        fake_load,
    )
    monkeypatch.setattr(
        service,
        "_validate_persisted_bsc_intent_or_fail",
        fake_validate,
    )
    monkeypatch.setattr(
        service,
        "_commit_and_refresh_bsc_intent",
        fake_commit,
    )


def test_confirmed_observation_advances_one_status_per_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())
    rpc_calls = 0

    def fake_reconcile(
        w3: Any,
        *,
        intent: FundBscTransactionIntent,
        required_confirmations: int,
    ) -> BscIntentReconciliationResult:
        nonlocal rpc_calls

        assert db.locked is False
        assert required_confirmations == 12
        assert intent is not db.intent

        rpc_calls += 1
        return _confirmed_observation()

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        fake_reconcile,
    )

    first = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert first.action == "transition_visible"
    assert first.previous_status == (
        BSC_INTENT_STATUS_BROADCAST
    )
    assert first.status == (
        BSC_INTENT_STATUS_VISIBLE
    )
    assert db.intent.receipt_status == 1
    assert db.intent.block_number == 100
    assert db.intent.confirmations == 12

    second = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW + timedelta(seconds=1),
    )

    assert second.action == (
        "transition_pending_confirmation"
    )
    assert second.status == (
        BSC_INTENT_STATUS_PENDING_CONFIRMATION
    )

    third = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW + timedelta(seconds=2),
    )

    assert third.action == "transition_confirmed"
    assert third.status == (
        BSC_INTENT_STATUS_CONFIRMED
    )
    assert db.intent.confirmed_at == (
        NOW + timedelta(seconds=2)
    )

    confirmed_at = db.intent.confirmed_at
    fourth = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW + timedelta(seconds=3),
    )

    assert fourth.action == "status_confirmed"
    assert fourth.rpc_used is False
    assert db.intent.confirmed_at == confirmed_at
    assert rpc_calls == 3


def test_rpc_runs_after_database_lock_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    def fake_reconcile(
        w3: Any,
        *,
        intent: FundBscTransactionIntent,
        required_confirmations: int,
    ) -> BscIntentReconciliationResult:
        assert db.locked is False
        assert db.events == [
            "lock",
            "commit",
        ]

        return _observation(
            action="visible",
            suggested_status=(
                BSC_INTENT_STATUS_VISIBLE
            ),
            reason_code="receipt_not_visible",
        )

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        fake_reconcile,
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == "transition_visible"
    assert db.events == [
        "lock",
        "commit",
        "lock",
        "commit",
    ]


def test_pending_confirmation_updates_without_second_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = _intent(
        status=(
            BSC_INTENT_STATUS_PENDING_CONFIRMATION
        )
    )
    intent.receipt_status = 1
    intent.block_number = 100
    intent.confirmations = 3
    intent.reconciliation_json = {
        "reconciliation_fingerprint": (
            FINGERPRINT
        )
    }

    db = FakeSession(intent)

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        lambda *args, **kwargs: (
            _pending_observation(
                confirmations=5,
            )
        ),
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "pending_confirmation"
    )
    assert result.transitioned is False
    assert db.intent.status == (
        BSC_INTENT_STATUS_PENDING_CONFIRMATION
    )
    assert db.intent.confirmations == 5


def test_receipt_fingerprint_change_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = _intent(
        status=BSC_INTENT_STATUS_VISIBLE
    )
    intent.receipt_status = 1
    intent.block_number = 100
    intent.confirmations = 3
    intent.reconciliation_json = {
        "reconciliation_fingerprint": (
            FINGERPRINT
        )
    }

    db = FakeSession(intent)

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        lambda *args, **kwargs: (
            _pending_observation(
                fingerprint=OTHER_FINGERPRINT,
            )
        ),
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert db.intent.status == (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        db.intent.reconciliation_json[
            "reason_code"
        ]
        == "reconciliation_evidence_mismatch"
    )
    assert (
        "reconciliation_fingerprint"
        in db.intent.reconciliation_json[
            "evidence"
        ]["mismatch_fields"]
    )


def test_receipt_disappearance_after_pending_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = _intent(
        status=(
            BSC_INTENT_STATUS_PENDING_CONFIRMATION
        )
    )
    intent.receipt_status = 1
    intent.block_number = 100
    intent.confirmations = 5
    intent.reconciliation_json = {
        "reconciliation_fingerprint": (
            FINGERPRINT
        )
    }

    db = FakeSession(intent)

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        lambda *args, **kwargs: (
            _observation(
                action="visible",
                suggested_status=(
                    BSC_INTENT_STATUS_VISIBLE
                ),
                reason_code="receipt_not_visible",
            )
        ),
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert (
        db.intent.reconciliation_json[
            "reason_code"
        ]
        == (
            "receipt_regressed_after_"
            "pending_confirmation"
        )
    )


def test_retryable_error_keeps_current_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        lambda *args, **kwargs: (
            _observation(
                action="retryable_error",
                suggested_status=(
                    BSC_INTENT_STATUS_BROADCAST
                ),
                reason_code=(
                    "transaction_lookup_unavailable"
                ),
                error="temporary rpc error",
            )
        ),
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == "retryable_error"
    assert result.transitioned is False
    assert db.intent.status == (
        BSC_INTENT_STATUS_BROADCAST
    )
    assert db.intent.error == (
        "temporary rpc error"
    )


def test_failed_receipt_is_persisted_as_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        lambda *args, **kwargs: (
            _observation(
                action="failed_requires_review",
                suggested_status=(
                    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
                ),
                confirmations=0,
                fingerprint=FINGERPRINT,
                receipt_status=0,
                block_number=100,
                reason_code=(
                    "receipt_execution_failed"
                ),
                error=(
                    "BSC transaction receipt "
                    "status is 0"
                ),
            )
        ),
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert db.intent.receipt_status == 0
    assert db.intent.block_number == 100
    assert db.intent.failed_at == NOW


def test_stale_worker_does_not_overwrite_newer_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    def fake_reconcile(
        *args: Any,
        **kwargs: Any,
    ) -> BscIntentReconciliationResult:
        assert db.locked is False

        # Simulate another worker committing a
        # newer transition during RPC.
        db.intent.status = (
            BSC_INTENT_STATUS_VISIBLE
        )

        return _confirmed_observation()

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        fake_reconcile,
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "stale_snapshot_status_visible"
    )
    assert result.transitioned is False
    assert db.intent.status == (
        BSC_INTENT_STATUS_VISIBLE
    )
    assert db.intent.confirmed_at is None


def test_result_transaction_hash_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        lambda *args, **kwargs: (
            _confirmed_observation(
                tx_hash=OTHER_TX_HASH,
            )
        ),
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert (
        db.intent.reconciliation_json[
            "reason_code"
        ]
        == (
            "reconciliation_result_"
            "contract_mismatch"
        )
    )


def test_unexpected_reconciliation_exception_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    def raise_error(
        *args: Any,
        **kwargs: Any,
    ) -> BscIntentReconciliationResult:
        raise RuntimeError(
            "unexpected decoder failure"
        )

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        raise_error,
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert (
        db.intent.reconciliation_json[
            "reason_code"
        ]
        == "unexpected_reconciliation_exception"
    )


def test_terminal_status_does_not_use_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = _intent(
        status=BSC_INTENT_STATUS_CONFIRMED
    )
    intent.confirmed_at = NOW

    db = FakeSession(intent)

    def forbidden_rpc(
        *args: Any,
        **kwargs: Any,
    ) -> BscIntentReconciliationResult:
        raise AssertionError(
            "Terminal intent must not use RPC"
        )

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        forbidden_rpc,
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW + timedelta(seconds=1),
    )

    assert result.action == "status_confirmed"
    assert result.rpc_used is False
    assert result.transitioned is False
    assert db.intent.confirmed_at == NOW


def test_stale_worker_detects_immutable_contract_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSession(_intent())

    def fake_reconcile(
        *args: Any,
        **kwargs: Any,
    ) -> BscIntentReconciliationResult:
        assert db.locked is False

        db.intent.intent_fingerprint = (
            "e" * 64
        )
        db.intent.prepared_tx_hash = (
            OTHER_TX_HASH
        )

        return _confirmed_observation()

    monkeypatch.setattr(
        service,
        "reconcile_bsc_transaction_intent",
        fake_reconcile,
    )

    result = service.reconcile_bsc_intent_once(
        db,
        object(),
        intent_id=42,
        required_confirmations=12,
        now=NOW,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert db.intent.status == (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        db.intent.reconciliation_json[
            "reason_code"
        ]
        == (
            "stale_reconciliation_snapshot_"
            "contract_mismatch"
        )
    )

    mismatch_fields = (
        db.intent.reconciliation_json[
            "evidence"
        ]["mismatch_fields"]
    )

    assert (
        "intent.intent_fingerprint"
        in mismatch_fields
    )
    assert (
        "intent.prepared_tx_hash"
        in mismatch_fields
    )
