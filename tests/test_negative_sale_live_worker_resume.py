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


def _compiled_live_query(
    *,
    fund_code: str | None = None,
) -> str:
    db = Session()

    try:
        query = (
            worker._live_candidate_query(
                db,
                fund_code=fund_code,
            )
        )

        return str(
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


def test_live_candidate_query_excludes_terminal_statuses():
    sql = _compiled_live_query(
        fund_code="WB10"
    )

    # Explicitly resumable pairs.
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

    # Completed and review-required
    # batches must never be selected.
    assert (
        "sale_execution_completed"
        not in sql
    )
    assert (
        "sale_execution_completed_"
        "with_extra_sale"
        not in sql
    )
    assert (
        "sale_execution_failed_"
        "requires_review"
        not in sql
    )
    assert (
        "negative_net_sale_executed"
        not in sql
    )
    assert (
        "failed_requires_review"
        not in sql
    )

    # Optional fund filter must remain
    # part of the locked candidate query.
    assert "funds.code = 'wb10'" in sql
    assert "order by" in sql
    assert (
        "fund_negative_sale_batches.id asc"
        in sql
    )
    assert "for update" in sql
    assert "skip locked" in sql


def test_live_worker_no_candidate_has_no_external_path(
    monkeypatch,
):
    db = FakeDB()

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_live_candidate_query",
        lambda *args, **kwargs: (
            FakeCandidateQuery(None)
        ),
    )
    monkeypatch.setattr(
        worker,
        "_build_fund_trading_bybit_client",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "Client must not be built "
                    "without a candidate"
                )
            )
        ),
    )
    monkeypatch.setattr(
        worker,
        "execute_negative_sale_plan_live",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "Executor must not run "
                    "without a candidate"
                )
            )
        ),
    )

    processed = (
        worker.process_one_live_batch(
            fund_code="WB10"
        )
    )

    assert processed is False
    assert db.commits == 0
    assert db.rollbacks == 1
    assert db.closed is True


def test_live_worker_passes_fund_filter_to_candidate_query(
    monkeypatch,
):
    db = FakeDB()
    calls: list[
        tuple[object, str | None]
    ] = []

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )

    def fake_query(
        db_arg,
        *,
        fund_code=None,
    ):
        calls.append(
            (
                db_arg,
                fund_code,
            )
        )

        return FakeCandidateQuery(
            None
        )

    monkeypatch.setattr(
        worker,
        "_live_candidate_query",
        fake_query,
    )

    processed = (
        worker.process_one_live_batch(
            fund_code="WB10"
        )
    )

    assert processed is False
    assert calls == [
        (
            db,
            "WB10",
        )
    ]
    assert db.rollbacks == 1
    assert db.closed is True


def test_live_worker_invokes_executor_once_per_cycle(
    monkeypatch,
):
    db = FakeDB()
    sale_batch = SimpleNamespace(
        id=10,
        fund_id=1,
    )
    client = object()

    calls = {
        "client": 0,
        "executor": 0,
    }

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
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

    def fake_build_client(
        db_arg,
        *,
        fund_id,
    ):
        calls["client"] += 1

        assert db_arg is db
        assert fund_id == 1
        assert db.commits == 1

        return client

    def fake_execute(
        db_arg,
        *,
        sale_batch_id,
        client: object,
    ):
        calls["executor"] += 1

        assert db_arg is db
        assert sale_batch_id == 10
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
            final_shortage_usdt=(
                None
            ),
            executed_leg_count=0,
        )

    monkeypatch.setattr(
        worker,
        "_build_fund_trading_bybit_client",
        fake_build_client,
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
    assert calls == {
        "client": 1,
        "executor": 1,
    }
    assert db.commits == 2
    assert db.rollbacks == 0
    assert db.closed is True


def test_single_worker_cycle_allows_at_most_one_fake_post(
    monkeypatch,
):
    class FakePostClient:
        def __init__(self):
            self.posts: list[
                tuple[str, dict]
            ] = []

        def post(
            self,
            path: str,
            payload: dict,
        ) -> dict:
            self.posts.append(
                (
                    path,
                    dict(payload),
                )
            )

            return {
                "retCode": 0,
                "result": {
                    "orderId": "OID-1",
                },
            }

    db = FakeDB()
    sale_batch = SimpleNamespace(
        id=10,
        fund_id=1,
    )
    client = FakePostClient()
    executor_calls = 0

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
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
    monkeypatch.setattr(
        worker,
        "_build_fund_trading_bybit_client",
        lambda *args, **kwargs: (
            client
        ),
    )

    def fake_execute(
        db_arg,
        *,
        sale_batch_id,
        client,
    ):
        nonlocal executor_calls
        executor_calls += 1

        assert db_arg is db
        assert sale_batch_id == 10

        client.post(
            "/fake/external-action",
            {
                "orderLinkId": (
                    "worker-cycle-test"
                ),
            },
        )

        return SimpleNamespace(
            ok=False,
            sale_batch_id=10,
            status_after=(
                "sale_execution_processing"
            ),
            settlement_status_after=(
                "pending_confirmation"
            ),
            final_shortage_usdt=(
                None
            ),
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
    assert executor_calls == 1
    assert len(client.posts) == 1
    assert client.posts[0][0] == (
        "/fake/external-action"
    )
    assert db.commits == 2
    assert db.rollbacks == 0
    assert db.closed is True
