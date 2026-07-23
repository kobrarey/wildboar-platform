from typing import Any

import pytest

from app.models import FundBscTransactionIntent
from app.settlement.bsc_intent_service import (
    BscIntentError,
    _load_bsc_intent_for_update,
)
from app.settlement.statuses import (
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PREPARED,
)


class FakeQuery:
    def __init__(
        self,
        session: "FakeSession",
    ) -> None:
        self.session = session
        self.for_update = False
        self.populate_existing_requested = False

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
        self.for_update = True
        return self

    def populate_existing(
        self,
    ) -> "FakeQuery":
        self.populate_existing_requested = True
        return self

    def first(self) -> Any:
        if self.for_update:
            assert (
                self.populate_existing_requested
                is True
            )
            self.session.events.append(
                "query_for_update"
            )
            return self.session.locked_intent

        self.session.events.append("query_read")
        return self.session.candidate_intent


class FakeSession:
    def __init__(
        self,
        *,
        candidate_intent: (
            FundBscTransactionIntent | None
        ),
        locked_intent: (
            FundBscTransactionIntent | None
        ),
    ) -> None:
        self.candidate_intent = candidate_intent
        self.locked_intent = locked_intent
        self.events: list[str] = []
        self.execute_calls: list[
            tuple[Any, dict[str, Any]]
        ] = []
        self.added: list[Any] = []
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
        self.events.append("advisory_lock")
        self.execute_calls.append(
            (
                statement,
                dict(params or {}),
            )
        )

    def add(self, value: Any) -> None:
        self.added.append(value)

    def commit(self) -> None:
        self.events.append("commit")
        self.commit_count += 1

    def rollback(self) -> None:
        self.events.append("rollback")
        self.rollback_count += 1

    def refresh(self, value: Any) -> None:
        self.refresh_count += 1


def _intent(
    *,
    from_address: str = "0xabcdef",
) -> FundBscTransactionIntent:
    return FundBscTransactionIntent(
        id=42,
        scope_key="negative-payout:11:22:33",
        from_address=from_address,
        intent_fingerprint="a" * 64,
        status=BSC_INTENT_STATUS_PREPARED,
    )


def test_source_advisory_lock_precedes_row_lock():
    candidate = _intent()
    locked = _intent()

    db = FakeSession(
        candidate_intent=candidate,
        locked_intent=locked,
    )

    result = _load_bsc_intent_for_update(
        db,
        intent_id=42,
    )

    assert result is locked
    assert db.events == [
        "query_read",
        "advisory_lock",
        "query_for_update",
    ]
    assert len(db.execute_calls) == 1
    assert db.commit_count == 0
    assert db.rollback_count == 0


def test_missing_intent_does_not_take_advisory_lock():
    db = FakeSession(
        candidate_intent=None,
        locked_intent=None,
    )

    with pytest.raises(
        BscIntentError,
        match="not found",
    ):
        _load_bsc_intent_for_update(
            db,
            intent_id=42,
        )

    assert db.events == [
        "query_read",
        "rollback",
    ]
    assert db.execute_calls == []


def test_source_change_after_advisory_lock_fails_closed():
    candidate = _intent(
        from_address="0xaaaa",
    )
    locked = _intent(
        from_address="0xbbbb",
    )

    db = FakeSession(
        candidate_intent=candidate,
        locked_intent=locked,
    )

    with pytest.raises(
        BscIntentError,
        match="source changed",
    ):
        _load_bsc_intent_for_update(
            db,
            intent_id=42,
        )

    assert db.events[:3] == [
        "query_read",
        "advisory_lock",
        "query_for_update",
    ]
    assert locked.status == (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert locked.failed_at is not None
    assert (
        locked.reconciliation_json["reason_code"]
        == "source_changed_during_lock_acquisition"
    )
    assert db.commit_count == 1