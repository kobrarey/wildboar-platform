from types import SimpleNamespace

import pytest

import workers.fund_negative_payout_worker as worker


class DummySession:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def rollback(self):
        self.rollback_calls += 1

    def commit(self):
        self.commit_calls += 1

    def close(self):
        self.close_calls += 1


def test_live_worker_runs_single_intent_cycle(
    monkeypatch,
):
    db = DummySession()
    calls = []

    def fake_get_web3():
        raise AssertionError(
            "Worker must pass factory without "
            "calling it directly"
        )

    def fake_cycle(
        session,
        *,
        w3_factory,
        fund_code,
        resume_paused,
    ):
        calls.append(
            {
                "session": session,
                "w3_factory": w3_factory,
                "fund_code": fund_code,
                "resume_paused": resume_paused,
            }
        )

        return SimpleNamespace(
            action="marked_broadcasting",
            intent_id=17,
            status="broadcasting",
            web3_created=False,
            broadcast_execution_invoked=False,
        )

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )
    monkeypatch.setattr(
        worker,
        "get_web3",
        fake_get_web3,
    )
    monkeypatch.setattr(
        worker,
        "run_bsc_intent_worker_cycle",
        fake_cycle,
    )

    processed = worker._run_live_once(
        fund_code="wb10",
        resume_paused=True,
    )

    assert processed == 1
    assert len(calls) == 1
    assert calls[0]["session"] is db
    assert calls[0]["w3_factory"] is fake_get_web3
    assert calls[0]["fund_code"] == "wb10"
    assert calls[0]["resume_paused"] is True
    assert db.rollback_calls == 0
    assert db.close_calls == 1


def test_live_worker_no_candidate_returns_zero(
    monkeypatch,
):
    db = DummySession()

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )

    def fake_cycle(
        session,
        *,
        w3_factory,
        fund_code,
        resume_paused,
    ):
        assert session is db
        assert fund_code is None
        assert resume_paused is False

        return SimpleNamespace(
            action="no_candidate",
            intent_id=None,
            status=None,
            web3_created=False,
            broadcast_execution_invoked=False,
        )

    monkeypatch.setattr(
        worker,
        "run_bsc_intent_worker_cycle",
        fake_cycle,
    )

    monkeypatch.setattr(
        worker,
        "_select_live_settlement_batch_id",
        lambda session, **kwargs: None,
    )

    processed = worker._run_live_once(
        fund_code=None,
    )

    assert processed == 0
    assert db.rollback_calls == 0
    assert db.close_calls == 1


def test_live_worker_rolls_back_on_error(
    monkeypatch,
):
    db = DummySession()

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )

    def fake_cycle(*args, **kwargs):
        raise RuntimeError("cycle failed")

    monkeypatch.setattr(
        worker,
        "run_bsc_intent_worker_cycle",
        fake_cycle,
    )

    with pytest.raises(
        RuntimeError,
        match="cycle failed",
    ):
        worker._run_live_once(
            fund_code="wb10",
        )

    assert db.rollback_calls == 1
    assert db.close_calls == 1


def test_live_worker_advances_one_batch_when_no_intent(
    monkeypatch,
):
    db = DummySession()
    selector_calls = []
    flow_calls = []

    monkeypatch.setattr(
        worker,
        "SessionLocal",
        lambda: db,
    )

    monkeypatch.setattr(
        worker,
        "run_bsc_intent_worker_cycle",
        lambda *args, **kwargs: SimpleNamespace(
            action="no_candidate",
            intent_id=None,
            status=None,
            web3_created=False,
            broadcast_execution_invoked=False,
        ),
    )

    def fake_selector(
        session,
        *,
        fund_code,
        resume_paused,
    ):
        selector_calls.append(
            {
                "session": session,
                "fund_code": fund_code,
                "resume_paused": resume_paused,
            }
        )
        return 41

    monkeypatch.setattr(
        worker,
        "_select_live_settlement_batch_id",
        fake_selector,
    )

    def fake_flow(
        session,
        *,
        settlement_batch_id,
    ):
        flow_calls.append(
            {
                "session": session,
                "settlement_batch_id": (
                    settlement_batch_id
                ),
            }
        )

        return SimpleNamespace(
            ok=False,
            settlement_batch_id=41,
            payout_batch_id=52,
            status_after="payouts_planned",
            settlement_status_after=(
                "negative_net_payout_processing"
            ),
            payout_leg_count=3,
            confirmed_payout_leg_count=1,
            error=None,
        )

    monkeypatch.setattr(
        worker,
        "execute_negative_payout_flow_live",
        fake_flow,
    )

    processed = worker._run_live_once(
        fund_code="wb10",
        resume_paused=True,
    )

    assert processed == 1
    assert selector_calls == [
        {
            "session": db,
            "fund_code": "wb10",
            "resume_paused": True,
        }
    ]
    assert flow_calls == [
        {
            "session": db,
            "settlement_batch_id": 41,
        }
    ]
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
    assert db.close_calls == 1
