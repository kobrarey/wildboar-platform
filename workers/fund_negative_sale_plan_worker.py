from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundSettlementBatch
from app.settlement.negative_sale_plan import create_negative_sale_plan
from app.settlement.negative_sale_snapshot import build_negative_sale_snapshot_mock
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
)


log = logging.getLogger(__name__)

STAGE_NAME = "Stage 23.2"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 23.2 negative sale plan worker. "
            "Safe by default. Creates sell-side snapshot and sale plan. "
            "No real Bybit calls, no trades, no transfers, no withdrawals, "
            "no BSC transfers, no accounting finalization."
        )
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one cycle and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback DB changes after creating sale plans.",
    )
    parser.add_argument(
        "--fund-code",
        type=str,
        default=None,
        help="Optional fund code filter, for example wb_test.",
    )
    parser.add_argument(
        "--mock-snapshot-file",
        type=str,
        default=None,
        help="Required in Stage 23.2. Local JSON snapshot fixture.",
    )
    parser.add_argument(
        "--live-read-only",
        action="store_true",
        help="Live mode is safe-gated by env + CLI flags; no external action is sent when the gate is disabled.",
    )
    parser.add_argument(
        "--live-execution",
        action="store_true",
        help="Live mode is safe-gated by env + CLI flags; no external action is sent when the gate is disabled.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum candidate batches per cycle.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Sleep interval for loop mode.",
    )

    return parser


def _validate_stage23_2_args(args: argparse.Namespace) -> str | None:
    if int(args.limit) <= 0:
        raise RuntimeError("--limit must be positive")

    if int(args.sleep_sec) <= 0:
        raise RuntimeError("--sleep-sec must be positive")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="negative_sale_plan_live_execution",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.NEGATIVE_NET_SALE_EXECUTION_ALLOW_LIVE)
            ),
            cli_enabled=True,
        )
        if not gate.allowed:
            log.info(
                "Negative sale plan live-execution gate blocked. No changes. gate=%s",
                gate.to_dict(),
            )
            return None

        raise RuntimeError(
            "negative_sale_plan_live_execution is not implemented in this worker. "
            "Sale execution must be handled by workers.fund_negative_sale_execution_worker."
        )

    if args.live_read_only:
        gate = evaluate_live_gate(
            feature="negative_sale_plan_live_read_only",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.NEGATIVE_NET_SALE_PLAN_ALLOW_LIVE_READONLY)
            ),
            cli_enabled=True,
        )
        if not gate.allowed:
            log.info(
                "Negative sale plan live-read-only gate blocked. No changes. gate=%s",
                gate.to_dict(),
            )
            return None

        raise RuntimeError(
            "negative_sale_plan_live_read_only is not implemented yet: "
            "no production Bybit negative-sale snapshot reader is wired for this worker."
        )

    if not args.mock_snapshot_file or not str(args.mock_snapshot_file).strip():
        raise RuntimeError(
            "--mock-snapshot-file is required when --live-read-only is not used."
        )

    return str(args.mock_snapshot_file).strip()


def _find_candidate_batch_ids(
    db: Session,
    *,
    fund_code: str | None,
    limit: int,
) -> list[int]:
    query = (
        db.query(FundSettlementBatch.id)
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            FundSettlementBatch.status == BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
        )
    )

    if fund_code:
        query = query.filter(Fund.code == fund_code)

    rows = (
        query.order_by(
            FundSettlementBatch.settlement_date.asc(),
            FundSettlementBatch.id.asc(),
        )
        .limit(int(limit))
        .all()
    )

    return [int(row[0]) for row in rows]
def _run_once(
    args: argparse.Namespace,
    *,
    mock_snapshot_file: str,
) -> int:
    db = SessionLocal()

    ok_count = 0
    failed_count = 0
    total_count = 0

    try:
        candidate_batch_ids = _find_candidate_batch_ids(
            db,
            fund_code=args.fund_code,
            limit=int(args.limit),
        )

        log.info(
            "Negative sale plan worker run_once started fund_code=%s "
            "dry_run=%s mock_snapshot_file=%s candidate_batches=%s",
            args.fund_code,
            args.dry_run,
            mock_snapshot_file,
            candidate_batch_ids,
        )

        if not candidate_batch_ids:
            log.info("No negative sale plan candidate batches found.")

            if args.dry_run:
                db.rollback()
            else:
                db.commit()

            return 0

        snapshot = build_negative_sale_snapshot_mock(
            mock_snapshot_file=mock_snapshot_file,
        )

        for batch_id in candidate_batch_ids:
            total_count += 1

            result = create_negative_sale_plan(
                db,
                settlement_batch_id=batch_id,
                snapshot=snapshot,
            )

            if result.ok:
                ok_count += 1
            else:
                failed_count += 1

            log.info(
                "Negative sale plan result batch_id=%s ok=%s "
                "status_before=%s status_after=%s sale_batch_id=%s "
                "sale_batch_status=%s sale_target_usdt=%s planned_sale_usdt=%s "
                "leg_count=%s error=%s result=%s",
                result.settlement_batch_id,
                result.ok,
                result.status_before,
                result.status_after,
                result.sale_batch_id,
                result.sale_batch_status,
                result.sale_target_usdt,
                result.planned_sale_usdt,
                result.leg_count,
                result.error,
                result.to_dict(),
            )

        if args.dry_run:
            db.rollback()
            log.info(
                "Negative sale plan worker dry-run rollback completed "
                "ok=%s failed=%s total=%s",
                ok_count,
                failed_count,
                total_count,
            )
        else:
            db.commit()
            log.info(
                "Negative sale plan worker mock/local changes committed "
                "ok=%s failed=%s total=%s",
                ok_count,
                failed_count,
                total_count,
            )

        return 0 if failed_count == 0 else 1

    except Exception as exc:
        db.rollback()
        log.exception(
            "Negative sale plan worker cycle failed error=%s",
            exc,
        )
        return 1

    finally:
        db.close()


def main() -> int:
    _setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    mock_snapshot_file = _validate_stage23_2_args(args)
    if mock_snapshot_file is None:
        return 0

    log.info(
        "%s negative sale plan worker started. "
        "Safe by default. No real Bybit calls, trades, transfers, "
        "no withdrawals, no BSC transfers, no accounting finalization, "
        "no server deploy.",
        STAGE_NAME,
    )

    if args.run_once:
        return _run_once(
            args,
            mock_snapshot_file=mock_snapshot_file,
        )

    while True:
        code = _run_once(
            args,
            mock_snapshot_file=mock_snapshot_file,
        )

        if code != 0:
            log.warning(
                "Negative sale plan worker cycle completed with failures code=%s",
                code,
            )

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())