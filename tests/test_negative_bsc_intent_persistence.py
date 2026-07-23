import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import FundBscTransactionIntent
from app.settlement.bsc_intent_service import (
    BscIntentError,
    PreparedBscTransaction,
    _source_advisory_lock_key,
    build_bsc_intent_safe_audit,
    persist_prepared_bsc_intent,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PREPARED,
)


class FakeIntentQuery:
    def __init__(
        self,
        session: "FakeIntentSession",
    ) -> None:
        self.session = session

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeIntentQuery":
        return self

    def order_by(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeIntentQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeIntentQuery":
        return self

    def first(self) -> Any:
        if not self.session.query_results:
            raise AssertionError(
                "Unexpected intent query"
            )

        self.session.first_count += 1

        return self.session.query_results.pop(0)


class FakeIntentSession:
    def __init__(
        self,
        query_results: list[Any],
    ) -> None:
        self.query_results = list(query_results)
        self.execute_calls: list[
            tuple[Any, dict[str, Any]]
        ] = []
        self.added: list[Any] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0
        self.refresh_count = 0
        self.first_count = 0

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

    def query(
        self,
        model: type[Any],
    ) -> FakeIntentQuery:
        assert model is FundBscTransactionIntent

        return FakeIntentQuery(self)

    def add(self, value: Any) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1

        for value in self.added:
            if (
                isinstance(
                    value,
                    FundBscTransactionIntent,
                )
                and value.id is None
            ):
                value.id = 9001

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, value: Any) -> None:
        assert isinstance(
            value,
            FundBscTransactionIntent,
        )

        self.refresh_count += 1


def _prepared(
    *,
    nonce: int = 7,
    raw_tx_hex: str = "0x0102",
) -> PreparedBscTransaction:
    return PreparedBscTransaction(
        chain_id=56,
        source_nonce=nonce,
        tx_hash=f"0x{'ab' * 32}",
        raw_tx_hex=raw_tx_hex,
    )


def _persist_kwargs(
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scope_key": "negative-payout:11:22:33",
        "action_type": (
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        "settlement_batch_id": 11,
        "payout_batch_id": 22,
        "payout_leg_id": 33,
        "fund_id": 9,
        "asset": "USDT",
        "amount": Decimal("10.5"),
        "from_address": "0xABCDEF",
        "to_address": "0x123456",
        "prepared": _prepared(),
    }
    values.update(overrides)

    return values


def _existing_intent(
    **overrides: Any,
) -> FundBscTransactionIntent:
    kwargs = _persist_kwargs()

    for key, value in overrides.items():
        if key in kwargs:
            kwargs[key] = value

    prepared = kwargs["prepared"]

    prepared_audit = build_bsc_intent_safe_audit(
        scope_key=kwargs["scope_key"],
        action_type=kwargs["action_type"],
        settlement_batch_id=(
            kwargs["settlement_batch_id"]
        ),
        payout_batch_id=kwargs["payout_batch_id"],
        payout_leg_id=kwargs["payout_leg_id"],
        fund_id=kwargs["fund_id"],
        asset=kwargs["asset"],
        amount=kwargs["amount"],
        from_address=kwargs["from_address"],
        to_address=kwargs["to_address"],
        prepared=prepared,
    )

    now = datetime.now(timezone.utc)

    intent = FundBscTransactionIntent(
        id=42,
        scope_key=kwargs["scope_key"],
        action_type=kwargs["action_type"],
        settlement_batch_id=(
            kwargs["settlement_batch_id"]
        ),
        payout_batch_id=kwargs["payout_batch_id"],
        payout_leg_id=kwargs["payout_leg_id"],
        fund_id=kwargs["fund_id"],
        asset=kwargs["asset"],
        amount=kwargs["amount"],
        from_address=str(
            kwargs["from_address"]
        ).lower(),
        to_address=str(
            kwargs["to_address"]
        ).lower(),
        chain_id=prepared.chain_id,
        source_nonce=prepared.source_nonce,
        prepared_tx_hash=prepared.tx_hash.lower(),
        prepared_raw_tx=prepared.raw_tx_hex.lower(),
        intent_fingerprint=(
            prepared_audit["intent_fingerprint"]
        ),
        status=BSC_INTENT_STATUS_PREPARED,
        broadcast_attempts=0,
        prepared_at=now,
        prepared_json={
            **prepared_audit,
            "durable_boundary": (
                "prepared_before_broadcast"
            ),
        },
        created_at=now,
        updated_at=now,
    )

    for key, value in overrides.items():
        if hasattr(intent, key):
            setattr(intent, key, value)

    return intent


def test_new_prepared_intent_is_committed_before_broadcast():
    db = FakeIntentSession(
        [
            None,
            None,
            None,
            None,
        ]
    )
    prepared = _prepared()

    intent = persist_prepared_bsc_intent(
        db,
        **_persist_kwargs(
            prepared=prepared,
        ),
    )

    assert intent.id == 9001
    assert intent.status == BSC_INTENT_STATUS_PREPARED
    assert intent.broadcast_attempts == 0
    assert intent.broadcast_started_at is None
    assert intent.broadcast_at is None
    assert intent.visible_at is None
    assert intent.confirmed_at is None

    assert intent.from_address == "0xabcdef"
    assert intent.to_address == "0x123456"
    assert intent.prepared_tx_hash == (
        f"0x{'ab' * 32}"
    )
    assert intent.prepared_raw_tx == "0x0102"

    assert db.flush_count == 1
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.refresh_count == 1
    assert db.first_count == 4

    assert len(db.execute_calls) == 1
    statement, params = db.execute_calls[0]

    assert (
        "pg_advisory_xact_lock"
        in str(statement)
    )
    assert isinstance(params["lock_key"], int)

    serialized_audit = json.dumps(
        intent.prepared_json,
        sort_keys=True,
    )

    assert prepared.raw_tx_hex not in serialized_audit
    assert "prepared_raw_tx" not in serialized_audit
    assert "raw_tx_hex" not in serialized_audit


def test_exact_scope_key_retry_is_idempotent():
    existing = _existing_intent()
    db = FakeIntentSession([existing])

    returned = persist_prepared_bsc_intent(
        db,
        **_persist_kwargs(),
    )

    assert returned is existing
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.refresh_count == 1
    assert db.added == []
    assert db.first_count == 1


def test_immutable_mismatch_fails_existing_intent():
    existing = _existing_intent()
    db = FakeIntentSession([existing])

    with pytest.raises(
        BscIntentError,
        match="Immutable BSC transaction intent",
    ):
        persist_prepared_bsc_intent(
            db,
            **_persist_kwargs(
                amount=Decimal("11"),
            ),
        )

    assert (
        existing.status
        == BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert existing.failed_at is not None
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert "amount" in (
        existing.reconciliation_json[
            "mismatch_fields"
        ]
    )

    serialized = json.dumps(
        existing.reconciliation_json,
        sort_keys=True,
    )

    assert existing.prepared_raw_tx not in serialized


def test_unresolved_source_intent_blocks_second_prepare():
    conflict = SimpleNamespace(
        id=88,
        scope_key="another-intent",
        status=BSC_INTENT_STATUS_PREPARED,
    )
    db = FakeIntentSession(
        [
            None,
            conflict,
        ]
    )

    with pytest.raises(
        BscIntentError,
        match="Another unresolved BSC transaction",
    ):
        persist_prepared_bsc_intent(
            db,
            **_persist_kwargs(),
        )

    assert db.commit_count == 0
    assert db.rollback_count == 1
    assert db.added == []


def test_nonce_and_payout_leg_are_query_before_create_gates():
    nonce_owner = SimpleNamespace(id=71)
    nonce_db = FakeIntentSession(
        [
            None,
            None,
            nonce_owner,
        ]
    )

    with pytest.raises(
        BscIntentError,
        match="source nonce is already owned",
    ):
        persist_prepared_bsc_intent(
            nonce_db,
            **_persist_kwargs(),
        )

    assert nonce_db.rollback_count == 1
    assert nonce_db.added == []

    leg_owner = SimpleNamespace(id=72)
    leg_db = FakeIntentSession(
        [
            None,
            None,
            None,
            leg_owner,
        ]
    )

    with pytest.raises(
        BscIntentError,
        match="Payout leg already has",
    ):
        persist_prepared_bsc_intent(
            leg_db,
            **_persist_kwargs(),
        )

    assert leg_db.rollback_count == 1
    assert leg_db.added == []


def test_action_asset_and_leg_contracts_fail_closed():
    db = FakeIntentSession([])

    with pytest.raises(
        BscIntentError,
        match="must use USDT",
    ):
        persist_prepared_bsc_intent(
            db,
            **_persist_kwargs(asset="BNB"),
        )

    with pytest.raises(
        BscIntentError,
        match="requires payout_leg_id",
    ):
        persist_prepared_bsc_intent(
            db,
            **_persist_kwargs(
                payout_leg_id=None,
            ),
        )

    with pytest.raises(
        BscIntentError,
        match="must use BNB",
    ):
        persist_prepared_bsc_intent(
            db,
            **_persist_kwargs(
                scope_key="negative-gas:11:22",
                action_type=(
                    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP
                ),
                payout_leg_id=None,
                asset="USDT",
            ),
        )

    assert db.execute_calls == []
    assert db.commit_count == 0
    assert db.rollback_count == 0


def test_source_lock_key_is_case_normalized_and_signed():
    first = _source_advisory_lock_key(
        "0xAbCdEf"
    )
    second = _source_advisory_lock_key(
        "0xabcdef"
    )
    third = _source_advisory_lock_key(
        "0x123456"
    )

    assert first == second
    assert first != third
    assert -(1 << 63) <= first < (1 << 63)