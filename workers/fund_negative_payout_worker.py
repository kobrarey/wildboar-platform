from __future__ import annotations

import argparse
import time

from app.db import SessionLocal
from app.models import Fund, FundNegativeBybitFlow, FundSettlementBatch
from app.settlement.negative_payout_flow import execute_negative_payout_flow_mock
from app.settlement.negative_payout_flow_mock import load_negative_payout_mock_file
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BYBIT_FLOW_STATUS_COMPLETED,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 23.5 negative-net payout mock worker"
    )
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock-payout-file", default=None)
    parser.add_argument("--fund-code", default=None)
    parser.add_argument("--sleep-seconds", type=int, default=30)
    parser.add_argument("--live-execution", action="store_true")
    return parser.parse_args()


def _load_candidates(db, *, fund_code: str | None):
    query = (
        db.query(FundSettlementBatch)
        .join(
            FundNegativeBybitFlow,
            FundNegativeBybitFlow.settlement_batch_id == FundSettlementBatch.id,
        )
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            FundSettlementBatch.status
            == BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
        )
        .filter(FundNegativeBybitFlow.status == BYBIT_FLOW_STATUS_COMPLETED)
        .order_by(FundSettlementBatch.id.asc())
    )

    if fund_code:
        query = query.filter(Fund.code == str(fund_code))

    return query.all()


def _run_once(*, mock_payout_file: str, dry_run: bool, fund_code: str | None) -> int:
    mock_payout = load_negative_payout_mock_file(mock_payout_file)

    db = SessionLocal()
    try:
        candidates = _load_candidates(db, fund_code=fund_code)
        processed = 0

        for settlement_batch in candidates:
            result = execute_negative_payout_flow_mock(
                db,
                settlement_batch_id=int(settlement_batch.id),
                mock_payout=mock_payout,
            )
            processed += 1

            print(
                {
                    "settlement_batch_id": result.settlement_batch_id,
                    "payout_batch_id": result.payout_batch_id,
                    "ok": result.ok,
                    "status_after": result.status_after,
                    "settlement_status_after": result.settlement_status_after,
                    "paused_operator_action_required": (
                        result.paused_operator_action_required
                    ),
                    "operator_action_id": result.operator_action_id,
                    "error": result.error,
                }
            )

        if dry_run:
            db.rollback()
            print(
                {
                    "dry_run": True,
                    "rolled_back": True,
                    "processed": processed,
                }
            )
        else:
            db.commit()
            print(
                {
                    "dry_run": False,
                    "committed": True,
                    "processed": processed,
                }
            )

        return processed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    args = _parse_args()

    if args.live_execution:
        raise SystemExit("--live-execution is forbidden in Stage 23.5")

    if not args.mock_payout_file:
        raise SystemExit("--mock-payout-file is required in Stage 23.5")

    while True:
        _run_once(
            mock_payout_file=args.mock_payout_file,
            dry_run=bool(args.dry_run),
            fund_code=args.fund_code,
        )

        if args.run_once:
            break

        time.sleep(max(int(args.sleep_seconds), 1))


if __name__ == "__main__":
    main()