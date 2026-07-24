from decimal import Decimal

import pytest

import app.settlement.bsc_intent_worker_service as worker_service

from app.models import (
    Fund,
    FundBscTransactionIntent,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundSettlementBatch,
)
from app.settlement.bsc_intent_worker_service import (
    BscIntentWorkerSelectionError,
    select_next_bsc_intent_candidate,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_UNRESOLVED_STATUSES,
    PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
)


class FakeQuery:
    def __init__(self, row):
        self.row = row
        self.filter_calls = []
        self.join_calls = []
        self.outerjoin_calls = []
        self.order_by_calls = []
        self.with_for_update_calls = []
        self.first_calls = 0

    def join(self, *args, **kwargs):
        self.join_calls.append(
            {
                "args": args,
                "kwargs": kwargs,
            }
        )
        return self

    def outerjoin(self, *args, **kwargs):
        self.outerjoin_calls.append(
            {
                "args": args,
                "kwargs": kwargs,
            }
        )
        return self

    def filter(self, *criteria):
        self.filter_calls.append(criteria)
        return self

    def order_by(self, *criteria):
        self.order_by_calls.append(criteria)
        return self

    def with_for_update(self, **kwargs):
        self.with_for_update_calls.append(kwargs)
        return self

    def first(self):
        self.first_calls += 1
        return self.row

    def all(self):
        raise AssertionError(
            "Worker selector must not load multiple candidates"
        )


class FakeSession:
    def __init__(self, row):
        self.query_object = FakeQuery(row)
        self.query_entities = None
        self.commit_calls = 0
        self.rollback_calls = 0

    def query(self, *entities):
        self.query_entities = entities
        return self.query_object

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


def _unresolved_status() -> str:
    return sorted(BSC_INTENT_UNRESOLVED_STATUSES)[0]


def _candidate_row(
    *,
    intent_id: int = 17,
    status: str | None = None,
):
    return (
        intent_id,
        status or _unresolved_status(),
        BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
        f"neg-net-redeem-payout:{intent_id}",
        3,
        5,
        Decimal("10.25"),
    )


def test_selector_returns_exactly_one_candidate():
    status = _unresolved_status()
    db = FakeSession(
        _candidate_row(
            intent_id=17,
            status=status,
        )
    )

    candidate = select_next_bsc_intent_candidate(db)

    assert candidate is not None
    assert candidate.intent_id == 17
    assert candidate.status == status
    assert (
        candidate.action_type
        == BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
    )
    assert candidate.scope_key == "neg-net-redeem-payout:17"
    assert candidate.fund_id == 3
    assert candidate.settlement_batch_id == 5
    assert candidate.amount == Decimal("10.25")

    assert db.query_entities is not None
    assert len(db.query_entities) == 7
    assert (
        db.query_entities[0]
        is FundBscTransactionIntent.id
    )
    assert (
        db.query_entities[1]
        is FundBscTransactionIntent.status
    )

    assert len(db.query_object.join_calls) == 4
    assert (
        db.query_object.join_calls[0]["args"][0]
        is FundSettlementBatch
    )
    assert (
        db.query_object.join_calls[1]["args"][0]
        is FundNegativePayoutBatch
    )
    assert (
        db.query_object.join_calls[2]["args"][0]
        is FundNegativeBybitFlow
    )
    assert (
        db.query_object.join_calls[3]["args"][0]
        is Fund
    )

    assert len(
        db.query_object.outerjoin_calls
    ) == 1
    assert (
        db.query_object
        .outerjoin_calls[0]["args"][0]
        is FundNegativeFinalizationBatch
    )

    assert len(db.query_object.filter_calls) == 10

    assert db.query_object.first_calls == 1
    assert (
        db.query_object.with_for_update_calls
        == [
            {
                "skip_locked": True,
                "of": FundBscTransactionIntent,
            }
        ]
    )

    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_selector_uses_deterministic_ordering():
    db = FakeSession(
        _candidate_row(intent_id=21)
    )

    select_next_bsc_intent_candidate(db)

    assert len(db.query_object.order_by_calls) == 1
    assert len(db.query_object.order_by_calls[0]) == 2


def test_no_candidate_commits_and_returns_none():
    db = FakeSession(None)

    candidate = select_next_bsc_intent_candidate(db)

    assert candidate is None
    assert db.query_object.first_calls == 1
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_invalid_candidate_id_rolls_back():
    db = FakeSession(
        _candidate_row(intent_id=0)
    )

    with pytest.raises(
        BscIntentWorkerSelectionError,
        match="must be positive",
    ):
        select_next_bsc_intent_candidate(db)

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_terminal_candidate_status_rolls_back():
    db = FakeSession(
        _candidate_row(
            intent_id=23,
            status="confirmed",
        )
    )

    with pytest.raises(
        BscIntentWorkerSelectionError,
        match="unsupported status",
    ):
        select_next_bsc_intent_candidate(db)

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_paused_payout_requires_explicit_resume():
    default_statuses = (
        worker_service
        ._eligible_payout_batch_statuses(
            resume_paused=False,
        )
    )
    resumed_statuses = (
        worker_service
        ._eligible_payout_batch_statuses(
            resume_paused=True,
        )
    )

    assert (
        PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
        not in default_statuses
    )
    assert (
        PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
        in resumed_statuses
    )
    assert resumed_statuses == (
        default_statuses
        | {
            PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
        }
    )


def test_fund_code_adds_explicit_filter():
    db = FakeSession(
        _candidate_row(intent_id=31)
    )

    candidate = select_next_bsc_intent_candidate(
        db,
        fund_code="wb10",
    )

    assert candidate is not None
    assert candidate.intent_id == 31
    assert len(db.query_object.filter_calls) == 11
