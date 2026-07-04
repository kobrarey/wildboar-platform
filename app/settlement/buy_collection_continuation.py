from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import Fund, FundOrder, FundSettlementBatch
from app.settlement.statuses import (
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_TERMINAL_STATUSES,
    ORDER_SIDE_BUY,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_BUY_COLLECTING,
    ORDER_STATUS_SETTLING,
)
from app.settlement.transfer_service import BuyCollectionResult, collect_buy_usdt_for_batch


ACTIVE_BUY_COLLECTION_ORDER_STATUSES = {
    ORDER_STATUS_SETTLING,
    ORDER_STATUS_BUY_COLLECTING,
    ORDER_STATUS_BUY_COLLECTED,
}


@dataclass(frozen=True)
class BuyCollectionContinuationPassResult:
    scanned_batch_ids: list[int]
    processed_results: list[BuyCollectionResult]
    dry_run: bool

    @property
    def processed_count(self) -> int:
        return len(self.processed_results)


def _normalize_fund_codes(fund_codes: Iterable[str] | None) -> set[str] | None:
    if fund_codes is None:
        return None

    out = {
        str(code).strip().lower()
        for code in fund_codes
        if str(code).strip()
    }
    return out or None


def scan_active_collecting_buy_usdt_batch_ids(
    db: Session,
    *,
    fund_codes: Iterable[str] | None = None,
    limit: int = 50,
) -> list[int]:
    normalized_fund_codes = _normalize_fund_codes(fund_codes)

    buy_order_exists = (
        db.query(FundOrder.id)
        .filter(
            FundOrder.settlement_batch_id == FundSettlementBatch.id,
            FundOrder.side == ORDER_SIDE_BUY,
            FundOrder.status.in_(sorted(ACTIVE_BUY_COLLECTION_ORDER_STATUSES)),
        )
        .exists()
    )

    q = (
        db.query(FundSettlementBatch.id)
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            Fund.is_active == True,
            FundSettlementBatch.status == BATCH_STATUS_COLLECTING_BUY_USDT,
            FundSettlementBatch.status.notin_(sorted(BATCH_TERMINAL_STATUSES)),
            FundSettlementBatch.pricing_locked_at.isnot(None),
            FundSettlementBatch.pricing_unlocked_at.is_(None),
            buy_order_exists,
        )
    )

    if normalized_fund_codes:
        q = q.filter(Fund.code.in_(sorted(normalized_fund_codes)))

    q = (
        q.order_by(
            FundSettlementBatch.updated_at.asc(),
            FundSettlementBatch.id.asc(),
        )
        .limit(max(int(limit), 1))
    )

    return [int(row[0]) for row in q.all()]


def continue_buy_collection_for_active_batches(
    db: Session,
    *,
    fund_codes: Iterable[str] | None = None,
    limit: int = 50,
    dry_run: bool = False,
) -> BuyCollectionContinuationPassResult:
    batch_ids = scan_active_collecting_buy_usdt_batch_ids(
        db,
        fund_codes=fund_codes,
        limit=limit,
    )

    results: list[BuyCollectionResult] = []

    for batch_id in batch_ids:
        results.append(
            collect_buy_usdt_for_batch(
                db,
                batch_id=batch_id,
                dry_run=dry_run,
            )
        )

    return BuyCollectionContinuationPassResult(
        scanned_batch_ids=batch_ids,
        processed_results=results,
        dry_run=dry_run,
    )