from types import SimpleNamespace

import pytest

import app.settlement.bsc_intent_worker_service as worker_service
from app.settlement.statuses import (
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_PREPARED,
    BSC_INTENT_STATUS_VISIBLE,
)


class DummySession:
    pass


def _forbidden(name):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            f"{name} must not be called"
        )

    return forbidden


def test_no_candidate_does_not_create_web3_or_call_services(
    monkeypatch,
):
    db = DummySession()
    web3_calls = 0

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        lambda session, **kwargs: None,
    )
    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        _forbidden(
            "mark_bsc_intent_broadcasting"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        _forbidden(
            "claim_bsc_intent_broadcast_attempt"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        _forbidden(
            "execute_claimed_bsc_intent_broadcast"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        _forbidden(
            "reconcile_bsc_intent_once"
        ),
    )

    def w3_factory():
        nonlocal web3_calls
        web3_calls += 1
        raise AssertionError(
            "Web3 must not be created without candidate"
        )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=w3_factory,
    )

    assert result.action == "no_candidate"
    assert result.intent_id is None
    assert result.status is None
    assert result.web3_created is False
    assert result.broadcast_execution_invoked is False
    assert web3_calls == 0


def test_prepared_cycle_only_marks_broadcasting(
    monkeypatch,
):
    db = DummySession()
    candidate = worker_service.BscIntentWorkerCandidate(
        intent_id=11,
        status=BSC_INTENT_STATUS_PREPARED,
    )
    calls = []

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        lambda session, **kwargs: candidate,
    )

    def mark_broadcasting(session, *, intent_id):
        calls.append((session, intent_id))
        return SimpleNamespace(
            id=intent_id,
            status=BSC_INTENT_STATUS_BROADCASTING,
        )

    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        mark_broadcasting,
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        _forbidden(
            "claim_bsc_intent_broadcast_attempt"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        _forbidden(
            "execute_claimed_bsc_intent_broadcast"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        _forbidden(
            "reconcile_bsc_intent_once"
        ),
    )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=_forbidden("w3_factory"),
    )

    assert calls == [(db, 11)]
    assert result.action == "marked_broadcasting"
    assert result.intent_id == 11
    assert result.status == BSC_INTENT_STATUS_BROADCASTING
    assert result.web3_created is False
    assert result.broadcast_execution_invoked is False


def test_active_broadcast_claim_does_not_create_web3(
    monkeypatch,
):
    db = DummySession()
    candidate = worker_service.BscIntentWorkerCandidate(
        intent_id=12,
        status=BSC_INTENT_STATUS_BROADCASTING,
    )

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        lambda session, **kwargs: candidate,
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        lambda session, *, intent_id: SimpleNamespace(
            action="claim_already_active",
            intent_id=intent_id,
            status=BSC_INTENT_STATUS_BROADCASTING,
            claim_token=None,
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        _forbidden(
            "mark_bsc_intent_broadcasting"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        _forbidden(
            "execute_claimed_bsc_intent_broadcast"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        _forbidden(
            "reconcile_bsc_intent_once"
        ),
    )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=_forbidden("w3_factory"),
    )

    assert result.action == "claim_already_active"
    assert result.intent_id == 12
    assert result.status == BSC_INTENT_STATUS_BROADCASTING
    assert result.web3_created is False
    assert result.broadcast_execution_invoked is False


def test_claimed_broadcast_cycle_executes_once(
    monkeypatch,
):
    db = DummySession()
    web3 = object()
    calls = {
        "factory": 0,
        "execute": 0,
    }

    candidate = worker_service.BscIntentWorkerCandidate(
        intent_id=13,
        status=BSC_INTENT_STATUS_BROADCASTING,
    )

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        lambda session, **kwargs: candidate,
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        lambda session, *, intent_id: SimpleNamespace(
            action="claim_created",
            intent_id=intent_id,
            status=BSC_INTENT_STATUS_BROADCASTING,
            claim_token="a" * 32,
        ),
    )

    def w3_factory():
        calls["factory"] += 1
        return web3

    def execute(
        session,
        received_web3,
        *,
        intent_id,
        claim_token,
    ):
        calls["execute"] += 1
        assert session is db
        assert received_web3 is web3
        assert intent_id == 13
        assert claim_token == "a" * 32

        return SimpleNamespace(
            action="broadcast",
            intent_id=13,
            status=BSC_INTENT_STATUS_BROADCAST,
        )

    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        execute,
    )
    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        _forbidden(
            "mark_bsc_intent_broadcasting"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        _forbidden(
            "reconcile_bsc_intent_once"
        ),
    )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=w3_factory,
    )

    assert calls == {
        "factory": 1,
        "execute": 1,
    }
    assert result.action == "broadcast"
    assert result.intent_id == 13
    assert result.status == BSC_INTENT_STATUS_BROADCAST
    assert result.web3_created is True
    assert result.broadcast_execution_invoked is True


@pytest.mark.parametrize(
    "status",
    [
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    ],
)
def test_reconciliation_cycle_uses_one_read_only_rpc(
    monkeypatch,
    status,
):
    db = DummySession()
    web3 = object()
    calls = {
        "factory": 0,
        "reconcile": 0,
    }

    candidate = worker_service.BscIntentWorkerCandidate(
        intent_id=14,
        status=status,
    )

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        lambda session, **kwargs: candidate,
    )

    def w3_factory():
        calls["factory"] += 1
        return web3

    def reconcile(
        session,
        received_web3,
        *,
        intent_id,
    ):
        calls["reconcile"] += 1
        assert session is db
        assert received_web3 is web3
        assert intent_id == 14

        return SimpleNamespace(
            action="checked",
            intent_id=14,
            status=status,
            rpc_used=True,
        )

    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        reconcile,
    )
    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        _forbidden(
            "mark_bsc_intent_broadcasting"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        _forbidden(
            "claim_bsc_intent_broadcast_attempt"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        _forbidden(
            "execute_claimed_bsc_intent_broadcast"
        ),
    )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=w3_factory,
    )

    assert calls == {
        "factory": 1,
        "reconcile": 1,
    }
    assert result.action == "checked"
    assert result.intent_id == 14
    assert result.status == status
    assert result.web3_created is True
    assert result.broadcast_execution_invoked is False


def test_broadcast_race_status_result_is_not_counted_as_attempt(
    monkeypatch,
):
    db = DummySession()
    web3 = object()
    calls = {
        "factory": 0,
        "execute": 0,
    }

    candidate = worker_service.BscIntentWorkerCandidate(
        intent_id=15,
        status=BSC_INTENT_STATUS_BROADCASTING,
    )

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        lambda session, **kwargs: candidate,
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        lambda session, *, intent_id: SimpleNamespace(
            action="claim_created",
            intent_id=intent_id,
            status=BSC_INTENT_STATUS_BROADCASTING,
            claim_token="b" * 32,
        ),
    )

    def w3_factory():
        calls["factory"] += 1
        return web3

    def execute(
        session,
        received_web3,
        *,
        intent_id,
        claim_token,
    ):
        calls["execute"] += 1
        assert session is db
        assert received_web3 is web3
        assert intent_id == 15
        assert claim_token == "b" * 32

        # Simulates another cycle advancing the
        # intent after claim creation but before
        # this cycle reaches external broadcast.
        return SimpleNamespace(
            action="status_broadcast",
            intent_id=15,
            status=BSC_INTENT_STATUS_BROADCAST,
        )

    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        execute,
    )
    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        _forbidden(
            "mark_bsc_intent_broadcasting"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        _forbidden(
            "reconcile_bsc_intent_once"
        ),
    )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=w3_factory,
    )

    assert calls == {
        "factory": 1,
        "execute": 1,
    }
    assert result.action == "status_broadcast"
    assert result.intent_id == 15
    assert result.status == BSC_INTENT_STATUS_BROADCAST
    assert result.web3_created is True
    assert result.broadcast_execution_invoked is True


def test_cycle_forwards_selector_scope(
    monkeypatch,
):
    db = DummySession()
    received = {}

    def select_candidate(
        session,
        *,
        fund_code,
        resume_paused,
    ):
        assert session is db
        received["fund_code"] = fund_code
        received["resume_paused"] = resume_paused
        return None

    monkeypatch.setattr(
        worker_service,
        "select_next_bsc_intent_candidate",
        select_candidate,
    )
    monkeypatch.setattr(
        worker_service,
        "mark_bsc_intent_broadcasting",
        _forbidden(
            "mark_bsc_intent_broadcasting"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "claim_bsc_intent_broadcast_attempt",
        _forbidden(
            "claim_bsc_intent_broadcast_attempt"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "execute_claimed_bsc_intent_broadcast",
        _forbidden(
            "execute_claimed_bsc_intent_broadcast"
        ),
    )
    monkeypatch.setattr(
        worker_service,
        "reconcile_bsc_intent_once",
        _forbidden(
            "reconcile_bsc_intent_once"
        ),
    )

    result = worker_service.run_bsc_intent_worker_cycle(
        db,
        w3_factory=_forbidden("w3_factory"),
        fund_code="wb10",
        resume_paused=True,
    )

    assert received == {
        "fund_code": "wb10",
        "resume_paused": True,
    }
    assert result.action == "no_candidate"
    assert result.web3_created is False
    assert (
        result.broadcast_execution_invoked
        is False
    )
