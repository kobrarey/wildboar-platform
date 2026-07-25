from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import workers.fund_negative_bybit_flow_worker as worker
from app.settlement.accounting_service import (
    SettlementShareQuantityError,
)


class FakeDb:
    def __init__(self) -> None:
        self.lock_active = False
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.events: list[str] = []

    def commit(self) -> None:
        self.commit_count += 1
        self.lock_active = False
        self.events.append("commit")

    def rollback(self) -> None:
        self.rollback_count += 1
        self.lock_active = False
        self.events.append("rollback")

    def close(self) -> None:
        self.close_count += 1
        self.events.append("close")


class FakeCandidateQuery:
    def __init__(
        self,
        db: FakeDb,
        batch: Any | None,
    ) -> None:
        self.db = db
        self.batch = batch
        self.first_count = 0

    def first(self):
        self.first_count += 1
        self.db.lock_active = True
        self.db.events.append(
            "candidate_first_for_update"
        )

        return self.batch


def make_batch() -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        fund_id=7,
    )


def make_service_result() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        settlement_batch_id=101,
        flow_id=303,
        status_after=(
            "universal_transfer_intent_prepared"
        ),
        settlement_status_after=(
            "negative_net_master_flow_processing"
        ),
        universal_transfer_id=(
            "11111111-1111-5111-8111-"
            "111111111111"
        ),
        withdrawal_request_id=None,
        settlement_wallet_address=None,
        idempotent=False,
        diagnostics={
            "transition": (
                "prepare_universal_transfer_intent"
            ),
            "did_bybit_post": False,
            "bybit_post_count": 0,
        },
    )


def fail_external_call(
    *args,
    **kwargs,
):
    raise AssertionError(
        "External dependency must not be called"
    )


def test_claim_live_candidate_commits_and_releases_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDb()
    batch = make_batch()

    candidate_query = FakeCandidateQuery(
        db,
        batch,
    )

    def candidate_query_factory(
        db_arg,
        *,
        fund_code=None,
    ):
        assert db_arg is db
        assert fund_code == "wb_test"
        return candidate_query

    def validate_share_state(
        db_arg,
        *,
        batch,
        mark_failed,
    ):
        assert db_arg is db
        assert db.lock_active is True
        assert batch.id == 101
        assert mark_failed is True

        db.events.append(
            "share_state_validated"
        )

    monkeypatch.setattr(
        worker,
        "_candidate_query",
        candidate_query_factory,
    )
    monkeypatch.setattr(
        worker,
        "validate_settlement_share_state_before_external",
        validate_share_state,
    )

    candidate = worker._claim_live_candidate(
        db,
        fund_code="wb_test",
    )

    assert candidate == (101, 7)
    assert candidate_query.first_count == 1

    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.lock_active is False

    assert db.events == [
        "candidate_first_for_update",
        "share_state_validated",
        "commit",
    ]


def test_no_candidate_avoids_credentials_client_and_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDb()

    candidate_query = FakeCandidateQuery(
        db,
        None,
    )

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_candidate_query",
        lambda db_arg, fund_code=None: (
            candidate_query
        ),
    )
    monkeypatch.setattr(
        worker,
        "validate_settlement_share_state_before_external",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "_build_master_bybit_client",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "_get_master_uid",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "_get_fund_sub_uid",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "resume_negative_bybit_flow_once",
        fail_external_call,
    )

    processed = worker.process_one_live_batch()

    assert processed is False
    assert candidate_query.first_count == 1

    assert db.commit_count == 0
    assert db.rollback_count == 1
    assert db.close_count == 1
    assert db.lock_active is False

    assert db.events == [
        "candidate_first_for_update",
        "rollback",
        "close",
    ]


def test_live_worker_releases_lock_before_all_external_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDb()
    batch = make_batch()

    candidate_query = FakeCandidateQuery(
        db,
        batch,
    )

    service_calls: list[
        dict[str, Any]
    ] = []

    master_client = SimpleNamespace(
        retries=0,
    )

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_candidate_query",
        lambda db_arg, fund_code=None: (
            candidate_query
        ),
    )

    def validate_share_state(
        db_arg,
        *,
        batch,
        mark_failed,
    ):
        assert db_arg is db
        assert db.lock_active is True
        assert batch.id == 101
        assert mark_failed is True

        db.events.append(
            "share_state_validated"
        )

    def build_client():
        assert db.lock_active is False
        assert db.events[-1] == "commit"

        db.events.append(
            "build_master_client"
        )

        return master_client

    def get_master_uid():
        assert db.lock_active is False

        db.events.append(
            "get_master_uid"
        )

        return "90001"

    def get_fund_sub_uid(
        db_arg,
        *,
        fund_id,
    ):
        assert db_arg is db
        assert db.lock_active is False
        assert fund_id == 7

        db.events.append(
            "get_fund_sub_uid"
        )

        return "70001"

    def resume_once(
        db_arg,
        *,
        settlement_batch_id,
        bybit_client,
        fund_sub_uid,
        master_uid,
    ):
        assert db_arg is db
        assert db.lock_active is False

        assert settlement_batch_id == 101
        assert bybit_client is master_client
        assert bybit_client.retries == 0
        assert fund_sub_uid == "70001"
        assert master_uid == "90001"

        db.events.append(
            "resume_service_once"
        )

        service_calls.append(
            {
                "settlement_batch_id": (
                    settlement_batch_id
                ),
                "fund_sub_uid": fund_sub_uid,
                "master_uid": master_uid,
            }
        )

        return make_service_result()

    monkeypatch.setattr(
        worker,
        "validate_settlement_share_state_before_external",
        validate_share_state,
    )
    monkeypatch.setattr(
        worker,
        "_build_master_bybit_client",
        build_client,
    )
    monkeypatch.setattr(
        worker,
        "_get_master_uid",
        get_master_uid,
    )
    monkeypatch.setattr(
        worker,
        "_get_fund_sub_uid",
        get_fund_sub_uid,
    )
    monkeypatch.setattr(
        worker,
        "resume_negative_bybit_flow_once",
        resume_once,
    )

    processed = (
        worker.process_one_live_batch(
            fund_code="wb_test",
        )
    )

    assert processed is True
    assert candidate_query.first_count == 1
    assert len(service_calls) == 1

    # Only the claim boundary is committed
    # by the worker. The resumable service
    # owns all later transaction boundaries.
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.close_count == 1
    assert db.lock_active is False

    assert db.events == [
        "candidate_first_for_update",
        "share_state_validated",
        "commit",
        "build_master_client",
        "get_master_uid",
        "get_fund_sub_uid",
        "resume_service_once",
        "close",
    ]


def test_share_validation_failure_prevents_external_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDb()
    batch = make_batch()

    candidate_query = FakeCandidateQuery(
        db,
        batch,
    )

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "_candidate_query",
        lambda db_arg, fund_code=None: (
            candidate_query
        ),
    )

    def invalid_share_state(
        db_arg,
        *,
        batch,
        mark_failed,
    ):
        assert db_arg is db
        assert db.lock_active is True
        assert batch.id == 101
        assert mark_failed is True

        db.events.append(
            "share_state_failed"
        )

        raise SettlementShareQuantityError(
            "share state mismatch"
        )

    monkeypatch.setattr(
        worker,
        "validate_settlement_share_state_before_external",
        invalid_share_state,
    )
    monkeypatch.setattr(
        worker,
        "_build_master_bybit_client",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "_get_master_uid",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "_get_fund_sub_uid",
        fail_external_call,
    )
    monkeypatch.setattr(
        worker,
        "resume_negative_bybit_flow_once",
        fail_external_call,
    )

    processed = worker.process_one_live_batch()

    assert processed is True
    assert candidate_query.first_count == 1

    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.close_count == 1
    assert db.lock_active is False

    assert db.events == [
        "candidate_first_for_update",
        "share_state_failed",
        "commit",
        "close",
    ]


def test_master_client_is_constructed_with_zero_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    expected_client = object()

    monkeypatch.setenv(
        "BYBIT_MASTER_API_KEY",
        "test-key",
    )
    monkeypatch.setenv(
        "BYBIT_MASTER_API_SECRET",
        "test-secret",
    )

    def fake_client_constructor(
        **kwargs,
    ):
        captured.update(kwargs)
        return expected_client

    monkeypatch.setattr(
        worker,
        "BybitV5Client",
        fake_client_constructor,
    )

    result = (
        worker._build_master_bybit_client()
    )

    assert result is expected_client
    assert captured["api_key"] == "test-key"
    assert captured["api_secret"] == (
        "test-secret"
    )
    assert captured["retries"] == 0


def test_worker_does_not_import_monolithic_live_executor() -> None:
    assert not hasattr(
        worker,
        "execute_negative_bybit_flow_live",
    )