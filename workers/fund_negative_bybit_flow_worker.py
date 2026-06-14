from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

from app.db import SessionLocal
from app.models import Fund, FundNegativeSaleBatch, FundSettlementBatch
from app.settlement.negative_bybit_flow import execute_negative_bybit_flow_mock
from app.settlement.negative_bybit_flow_mock import load_negative_bybit_flow_mock_file
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workers.fund_negative_bybit_flow_worker",
        description=(
            "Stage 23.4 negative-net Bybit master flow worker. "
            "Mock/preflight/reconciliation only."
        ),
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Process at most one settlement batch and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback after successful mock flow instead of commit.",
    )
    parser.add_argument(
        "--mock-flow-file",
        type=Path,
        default=None,
        help="Required Stage 23.4 mock flow fixture JSON file.",
    )
    parser.add_argument(
        "--fund-code",
        type=str,
        default=None,
        help="Optional fund code filter.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=int,
        default=10,
        help="Sleep interval for loop mode.",
    )
    parser.add_argument(
        "--live-execution",
        action="store_true",
        help="Forbidden in Stage 23.4. Always hard-fails.",
    )
    return parser


def parse_worker_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.live_execution:
        parser.error("--live-execution is forbidden in Stage 23.4")

    if args.mock_flow_file is None:
        parser.error("--mock-flow-file is required in Stage 23.4")

    if args.sleep_seconds < 1:
        parser.error("--sleep-seconds must be >= 1")

    return args


def _candidate_query(db, *, fund_code: str | None = None):
    query = (
        db.query(FundSettlementBatch)
        .join(
            FundNegativeSaleBatch,
            FundNegativeSaleBatch.settlement_batch_id == FundSettlementBatch.id,
        )
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(FundSettlementBatch.status == BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED)
        .filter(
            FundNegativeSaleBatch.status.in_(
                [
                    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
                    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
                ]
            )
        )
    )

    if fund_code:
        query = query.filter(Fund.code == str(fund_code))

    return query.order_by(FundSettlementBatch.id.asc()).with_for_update(skip_locked=True)


def process_one_batch(
    *,
    mock_path: str | Path,
    fund_code: str | None = None,
    dry_run: bool = False,
) -> bool:
    mock_flow = load_negative_bybit_flow_mock_file(mock_path)

    db = SessionLocal()
    try:
        settlement_batch = _candidate_query(db, fund_code=fund_code).first()
        if settlement_batch is None:
            db.rollback()
            return False

        result = execute_negative_bybit_flow_mock(
            db,
            settlement_batch_id=int(settlement_batch.id),
            mock_flow=mock_flow,
        )

        if dry_run:
            db.rollback()
            action = "rollback"
        else:
            db.commit()
            action = "commit"

        print(
            "fund_negative_bybit_flow_worker:",
            "action=", action,
            "settlement_batch_id=", result.settlement_batch_id,
            "flow_id=", result.flow_id,
            "status_after=", result.status_after,
            "settlement_status_after=", result.settlement_status_after,
            "transfer_id=", result.universal_transfer_id,
            "request_id=", result.withdrawal_request_id,
            "idempotent=", result.idempotent,
            "fund_code_filter=", fund_code or "",
        )
        return True

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_forever(
    *,
    mock_path: str | Path,
    fund_code: str | None = None,
    dry_run: bool = False,
    sleep_seconds: int = 10,
) -> None:
    while True:
        processed = process_one_batch(
            mock_path=mock_path,
            fund_code=fund_code,
            dry_run=dry_run,
        )
        if not processed:
            time.sleep(sleep_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_worker_args(argv)

    if args.run_once:
        process_one_batch(
            mock_path=args.mock_flow_file,
            fund_code=args.fund_code,
            dry_run=args.dry_run,
        )
        return 0

    run_forever(
        mock_path=args.mock_flow_file,
        fund_code=args.fund_code,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())