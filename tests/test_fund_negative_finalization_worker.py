from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import FundSettlementBatch
import workers.fund_negative_finalization_worker as worker


class DummyQuery:
    def __init__(self, candidate=None):
        self.candidate = candidate
        self.with_for_update_calls = []
        self.first_calls = 0

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self, **kwargs):
        self.with_for_update_calls.append(
            kwargs
        )
        return self

    def first(self):
        self.first_calls += 1
        return self.candidate


class DummySession:
    def __init__(
        self,
        *,
        query_result=None,
    ):
        self.query_result = query_result
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def query(self, *args, **kwargs):
        assert self.query_result is not None
        return self.query_result

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.close_calls += 1


def finalization_result():
    return SimpleNamespace(
        settlement_batch_id=41,
        finalization_batch_id=52,
        payout_batch_id=63,
        fund_id=7,
        fund_code="wb10",
        ok=True,
        status_after="completed",
        settlement_status_after=(
            "negative_net_completed"
        ),
        buy_order_count=2,
        redeem_order_count=3,
        success_order_count=5,
        shares_outstanding_before="100",
        shares_outstanding_after="95",
        accounting_finalized_at=(
            "2026-07-24T12:00:00+00:00"
        ),
        pricing_unlocked_at=(
            "2026-07-24T12:00:00+00:00"
        ),
        error=None,
    )


def test_candidate_selection_uses_skip_locked():
    candidate = SimpleNamespace(id=41)
    query = DummyQuery(candidate)
    db = DummySession(
        query_result=query
    )

    selected = worker._load_candidate(
        db,
        fund_code="wb10",
    )

    assert selected is candidate
    assert query.first_calls == 1
    assert query.with_for_update_calls == [
        {
            "skip_locked": True,
            "of": FundSettlementBatch,
        }
    ]


def test_no_candidate_returns_zero(
    monkeypatch,
):
    db = DummySession()

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_load_candidate",
        lambda session, **kwargs: None,
    )

    def forbidden_finalization(*args, **kwargs):
        raise AssertionError(
            "Finalization must not run "
            "without a candidate"
        )

    monkeypatch.setattr(
        worker,
        "finalize_negative_net_settlement",
        forbidden_finalization,
    )

    processed = worker._run_once(
        dry_run=False,
        fund_code=None,
    )

    assert processed == 0
    assert db.commit_calls == 0
    assert db.rollback_calls == 1
    assert db.close_calls == 1


def test_candidate_is_finalized_and_committed(
    monkeypatch,
):
    db = DummySession()
    candidate = SimpleNamespace(id=41)
    calls = []

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_load_candidate",
        lambda session, **kwargs: candidate,
    )

    def fake_finalization(
        session,
        *,
        settlement_batch_id,
    ):
        calls.append(
            {
                "session": session,
                "settlement_batch_id": (
                    settlement_batch_id
                ),
            }
        )
        return finalization_result()

    monkeypatch.setattr(
        worker,
        "finalize_negative_net_settlement",
        fake_finalization,
    )

    processed = worker._run_once(
        dry_run=False,
        fund_code="wb10",
    )

    assert processed == 1
    assert calls == [
        {
            "session": db,
            "settlement_batch_id": 41,
        }
    ]
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
    assert db.close_calls == 1


def test_dry_run_rolls_back_finalization(
    monkeypatch,
):
    db = DummySession()
    candidate = SimpleNamespace(id=41)

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_load_candidate",
        lambda session, **kwargs: candidate,
    )
    monkeypatch.setattr(
        worker,
        "finalize_negative_net_settlement",
        lambda *args, **kwargs: (
            finalization_result()
        ),
    )

    processed = worker._run_once(
        dry_run=True,
        fund_code=None,
    )

    assert processed == 1
    assert db.commit_calls == 0
    assert db.rollback_calls == 1
    assert db.close_calls == 1


def test_finalization_error_rolls_back(
    monkeypatch,
):
    db = DummySession()
    candidate = SimpleNamespace(id=41)

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_load_candidate",
        lambda session, **kwargs: candidate,
    )

    def failed_finalization(*args, **kwargs):
        raise RuntimeError(
            "finalization failed"
        )

    monkeypatch.setattr(
        worker,
        "finalize_negative_net_settlement",
        failed_finalization,
    )

    with pytest.raises(
        RuntimeError,
        match="finalization failed",
    ):
        worker._run_once(
            dry_run=False,
            fund_code="wb10",
        )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1
    assert db.close_calls == 1