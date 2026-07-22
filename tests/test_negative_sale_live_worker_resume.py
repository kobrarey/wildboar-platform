from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.dialects import (
    postgresql,
)
from sqlalchemy.orm import Session

import workers.fund_negative_sale_execution_worker as worker


class FakeDB:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakeCandidateQuery:
    def __init__(self, item):
        self.item = item

    def first(self):
        return self.item


def test_live_candidate_query_includes_resume_pairs():
    db = Session()

    try:
        query = (
            worker._live_candidate_query(
                db
            )
        )

        sql = str(
            query.statement.compile(
                dialect=(
                    postgresql.dialect()
                ),
                compile_kwargs={
                    "literal_binds": True,
                },
            )
        ).lower()
    finally:
        db.close()

    assert "sale_plan_created" in sql
    assert (
        "negative_net_sale_planned"
        in sql
    )
    assert (
        "sale_execution_processing"
        in sql
    )
    assert (
        "negative_net_sale_processing"
        in sql
    )
    assert "pending_confirmation" in sql
    assert "for update" in sql
    assert "skip locked" in sql


def test_live_worker_releases_candidate_lock_before_executor(
    monkeypatch,
):
    db = FakeDB()

    sale_batch = SimpleNamespace(
        id=10,
        fund_id=1,
    )

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_candidate_query",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "Mock candidate query "
                    "must not be used"
                )
            )
        ),
    )
    monkeypatch.setattr(
        worker,
        "_live_candidate_query",
        lambda *args, **kwargs: (
            FakeCandidateQuery(
                sale_batch
            )
        ),
    )

    client = object()

    def fake_build_client(
        db_arg,
        *,
        fund_id,
    ):
        assert db_arg is db
        assert fund_id == 1
        assert db.commits == 1
        return client

    monkeypatch.setattr(
        worker,
        "_build_fund_trading_bybit_client",
        fake_build_client,
    )

    def fake_execute(
        db_arg,
        *,
        sale_batch_id,
        client,
    ):
        assert db_arg is db
        assert sale_batch_id == 10
        assert client is not None

        # Candidate FOR UPDATE was released
        # before entering the executor.
        assert db.commits == 1

        return SimpleNamespace(
            ok=False,
            sale_batch_id=10,
            status_after=(
                "sale_execution_processing"
            ),
            settlement_status_after=(
                "pending_confirmation"
            ),
            final_shortage_usdt=None,
            executed_leg_count=0,
        )

    monkeypatch.setattr(
        worker,
        "execute_negative_sale_plan_live",
        fake_execute,
    )

    processed = (
        worker.process_one_live_batch()
    )

    assert processed is True
    assert db.commits == 2
    assert db.rollbacks == 0
    assert db.closed is True