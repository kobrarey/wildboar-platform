from __future__ import annotations

import argparse
import time

from app.db import SessionLocal
from app.models import Fund, FundNegativePayoutBatch, FundSettlementBatch
from app.settlement.negative_finalization import finalize_negative_net_settlement
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    PAYOUT_BATCH_STATUS_COMPLETED,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 23.6 negative-net final accounting worker"
    )
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fund-code", default=None)
    parser.add_argument("--sleep-seconds", type=int, default=30)
    return parser.parse_args()


def _load_candidate(db, *, fund_code: str | None):
    query = (
        db.query(FundSettlementBatch)
        .join(
            FundNegativePayoutBatch,
            FundNegativePayoutBatch.settlement_batch_id == FundSettlementBatch.id,
        )
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            FundSettlementBatch.status
            == BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
        )
        .filter(FundNegativePayoutBatch.status == PAYOUT_BATCH_STATUS_COMPLETED)
        .order_by(FundSettlementBatch.id.asc())
    )

    if fund_code:
        query = query.filter(Fund.code == str(fund_code))

    return query.first()


def _run_once(*, dry_run: bool, fund_code: str | None) -> int:
    db = SessionLocal()
    try:
        settlement_batch = _load_candidate(db, fund_code=fund_code)
        if settlement_batch is None:
            print(
                {
                    "candidate_found": False,
                    "dry_run": bool(dry_run),
                    "processed": 0,
                }
            )
            db.rollback()
            return 0

        result = finalize_negative_net_settlement(
            db,
            settlement_batch_id=int(settlement_batch.id),
        )

        print(
            {
                "candidate_found": True,
                "settlement_batch_id": result.settlement_batch_id,
                "finalization_batch_id": result.finalization_batch_id,
                "payout_batch_id": result.payout_batch_id,
                "fund_id": result.fund_id,
                "fund_code": result.fund_code,
                "ok": result.ok,
                "status_after": result.status_after,
                "settlement_status_after": result.settlement_status_after,
                "buy_order_count": result.buy_order_count,
                "redeem_order_count": result.redeem_order_count,
                "success_order_count": result.success_order_count,
                "shares_outstanding_before": result.shares_outstanding_before,
                "shares_outstanding_after": result.shares_outstanding_after,
                "accounting_finalized_at": result.accounting_finalized_at,
                "pricing_unlocked_at": result.pricing_unlocked_at,
                "error": result.error,
            }
        )

        if dry_run:
            db.rollback()
            print(
                {
                    "dry_run": True,
                    "rolled_back": True,
                    "processed": 1,
                }
            )
        else:
            db.commit()
            print(
                {
                    "dry_run": False,
                    "committed": True,
                    "processed": 1,
                }
            )

        return 1
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    args = _parse_args()

    while True:
        _run_once(
            dry_run=bool(args.dry_run),
            fund_code=args.fund_code,
        )

        if args.run_once:
            break

        time.sleep(max(int(args.sleep_seconds), 1))


if __name__ == "__main__":
    main()