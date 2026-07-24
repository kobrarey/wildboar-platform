from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session
from web3 import Web3

from app.models import (
    Fund,
    FundBscTransactionIntent,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundSettlementBatch,
)
from app.settlement.bsc_intent_reconciliation_service import (
    reconcile_bsc_intent_once,
)
from app.settlement.bsc_intent_service import (
    claim_bsc_intent_broadcast_attempt,
    execute_claimed_bsc_intent_broadcast,
    mark_bsc_intent_broadcasting,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
    BSC_INTENT_ACTION_TYPES,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_PREPARED,
    BSC_INTENT_STATUS_VISIBLE,
    BSC_INTENT_UNRESOLVED_STATUSES,
    BYBIT_FLOW_STATUS_COMPLETED,
    PAYOUT_BATCH_STATUS_CREATED,
    PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED,
    PAYOUT_BATCH_STATUS_GAS_READY,
    PAYOUT_BATCH_STATUS_GAS_TOPUP_MOCKED,
    PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
    PAYOUT_BATCH_STATUS_PAYOUTS_PLANNED,
)


class BscIntentWorkerSelectionError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class BscIntentWorkerCandidate:
    intent_id: int
    status: str


def _candidate_from_row(
    row: Any,
) -> BscIntentWorkerCandidate:
    try:
        intent_id = int(row[0])
        status = str(row[1] or "").strip()
    except (
        TypeError,
        ValueError,
        KeyError,
        IndexError,
    ) as exc:
        raise BscIntentWorkerSelectionError(
            "Invalid BSC intent worker candidate row"
        ) from exc

    if intent_id <= 0:
        raise BscIntentWorkerSelectionError(
            "BSC intent worker candidate id "
            "must be positive"
        )

    if status not in BSC_INTENT_UNRESOLVED_STATUSES:
        raise BscIntentWorkerSelectionError(
            "BSC intent worker candidate has "
            f"unsupported status: {status or 'empty'}"
        )

    return BscIntentWorkerCandidate(
        intent_id=intent_id,
        status=status,
    )


_ELIGIBLE_SETTLEMENT_STATUSES = frozenset(
    {
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
        BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
    }
)


_ACTIVE_PAYOUT_BATCH_STATUSES = frozenset(
    {
        PAYOUT_BATCH_STATUS_CREATED,
        PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED,
        PAYOUT_BATCH_STATUS_GAS_READY,
        PAYOUT_BATCH_STATUS_GAS_TOPUP_MOCKED,
        PAYOUT_BATCH_STATUS_PAYOUTS_PLANNED,
    }
)


def _eligible_payout_batch_statuses(
    *,
    resume_paused: bool,
) -> frozenset[str]:
    statuses = set(_ACTIVE_PAYOUT_BATCH_STATUSES)

    if resume_paused:
        statuses.add(
            PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
        )

    return frozenset(statuses)


def select_next_bsc_intent_candidate(
    db: Session,
    *,
    fund_code: str | None = None,
    resume_paused: bool = False,
) -> BscIntentWorkerCandidate | None:
    """
    Select at most one eligible unresolved BSC intent.

    The row lock exists only inside this short
    transaction. No RPC, Web3 construction or
    broadcast occurs while the selector owns it.
    """
    eligible_payout_statuses = (
        _eligible_payout_batch_statuses(
            resume_paused=bool(resume_paused),
        )
    )
    normalized_fund_code = str(
        fund_code or ""
    ).strip()

    try:
        query = (
            db.query(
                FundBscTransactionIntent.id,
                FundBscTransactionIntent.status,
            )
            .join(
                FundSettlementBatch,
                FundSettlementBatch.id
                == FundBscTransactionIntent
                .settlement_batch_id,
            )
            .join(
                FundNegativePayoutBatch,
                FundNegativePayoutBatch.id
                == FundBscTransactionIntent
                .payout_batch_id,
            )
            .join(
                FundNegativeBybitFlow,
                FundNegativeBybitFlow.id
                == FundNegativePayoutBatch
                .bybit_flow_id,
            )
            .join(
                Fund,
                Fund.id
                == FundBscTransactionIntent.fund_id,
            )
            .outerjoin(
                FundNegativeFinalizationBatch,
                FundNegativeFinalizationBatch
                .payout_batch_id
                == FundNegativePayoutBatch.id,
            )
            .filter(
                FundBscTransactionIntent.status.in_(
                    sorted(
                        BSC_INTENT_UNRESOLVED_STATUSES
                    )
                )
            )
            .filter(
                FundBscTransactionIntent
                .action_type.in_(
                    sorted(BSC_INTENT_ACTION_TYPES)
                )
            )
            .filter(
                FundSettlementBatch.status.in_(
                    sorted(
                        _ELIGIBLE_SETTLEMENT_STATUSES
                    )
                )
            )
            .filter(
                FundNegativePayoutBatch.status.in_(
                    sorted(eligible_payout_statuses)
                )
            )
            .filter(
                FundNegativeBybitFlow.status
                == BYBIT_FLOW_STATUS_COMPLETED
            )
            .filter(
                FundNegativeFinalizationBatch.id.is_(
                    None
                )
            )
            .filter(
                FundBscTransactionIntent.fund_id
                == FundSettlementBatch.fund_id
            )
            .filter(
                FundBscTransactionIntent.fund_id
                == FundNegativePayoutBatch.fund_id
            )
            .filter(
                FundBscTransactionIntent
                .settlement_batch_id
                == FundNegativePayoutBatch
                .settlement_batch_id
            )
            .filter(
                FundSettlementBatch.id
                == FundNegativeBybitFlow
                .settlement_batch_id
            )
            .order_by(
                FundBscTransactionIntent
                .updated_at.asc(),
                FundBscTransactionIntent.id.asc(),
            )
        )

        if normalized_fund_code:
            query = query.filter(
                Fund.code == normalized_fund_code
            )

        row = (
            query.with_for_update(
                skip_locked=True,
                of=FundBscTransactionIntent,
            )
            .first()
        )

        if row is None:
            db.commit()
            return None

        candidate = _candidate_from_row(row)

        # Release FOR UPDATE before any RPC,
        # Web3 construction or broadcast.
        db.commit()

        return candidate

    except Exception:
        db.rollback()
        raise


@dataclass(frozen=True)
class BscIntentWorkerCycleResult:
    action: str
    intent_id: int | None
    status: str | None
    web3_created: bool
    broadcast_execution_invoked: bool


_RECONCILIATION_STATUSES = frozenset(
    {
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    }
)


def run_bsc_intent_worker_cycle(
    db: Session,
    *,
    w3_factory: Callable[[], Web3],
    fund_code: str | None = None,
    resume_paused: bool = False,
) -> BscIntentWorkerCycleResult:
    """
    Execute one worker cycle for at most one intent.

    prepared:
        persist broadcasting state only.

    broadcasting:
        claim one broadcast attempt; perform at most
        one send_raw_transaction when claim ownership
        is obtained.

    broadcast / visible / pending_confirmation:
        perform one read-only reconciliation cycle.

    No candidate:
        do not construct Web3 and do not access keys.
    """
    candidate = select_next_bsc_intent_candidate(
        db,
        fund_code=fund_code,
        resume_paused=resume_paused,
    )

    if candidate is None:
        return BscIntentWorkerCycleResult(
            action="no_candidate",
            intent_id=None,
            status=None,
            web3_created=False,
            broadcast_execution_invoked=False,
        )

    if candidate.status == BSC_INTENT_STATUS_PREPARED:
        intent = mark_bsc_intent_broadcasting(
            db,
            intent_id=candidate.intent_id,
        )

        return BscIntentWorkerCycleResult(
            action="marked_broadcasting",
            intent_id=int(intent.id),
            status=str(intent.status),
            web3_created=False,
            broadcast_execution_invoked=False,
        )

    if candidate.status == BSC_INTENT_STATUS_BROADCASTING:
        claim = claim_bsc_intent_broadcast_attempt(
            db,
            intent_id=candidate.intent_id,
        )

        if (
            claim.action != "claim_created"
            or not claim.claim_token
        ):
            return BscIntentWorkerCycleResult(
                action=str(claim.action),
                intent_id=int(claim.intent_id),
                status=str(claim.status),
                web3_created=False,
                broadcast_execution_invoked=False,
            )

        w3 = w3_factory()

        result = execute_claimed_bsc_intent_broadcast(
            db,
            w3,
            intent_id=claim.intent_id,
            claim_token=claim.claim_token,
        )

        return BscIntentWorkerCycleResult(
            action=str(result.action),
            intent_id=int(result.intent_id),
            status=str(result.status),
            web3_created=True,
            broadcast_execution_invoked=True,
        )

    if candidate.status in _RECONCILIATION_STATUSES:
        w3 = w3_factory()

        result = reconcile_bsc_intent_once(
            db,
            w3,
            intent_id=candidate.intent_id,
        )

        return BscIntentWorkerCycleResult(
            action=str(result.action),
            intent_id=int(result.intent_id),
            status=str(result.status),
            web3_created=True,
            broadcast_execution_invoked=False,
        )

    raise BscIntentWorkerSelectionError(
        "Selected BSC intent has no supported "
        f"worker state step: {candidate.status}"
    )
