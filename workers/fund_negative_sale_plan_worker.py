from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundSettlementBatch
from app.settlement.negative_sale_bybit_snapshot import build_negative_sale_snapshot_from_bybit
from app.settlement.negative_sale_plan import create_negative_sale_plan
from app.settlement.negative_sale_snapshot import (
    NegativeSaleSnapshot,
    build_negative_sale_snapshot_mock,
)
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


def _validate_stage23_2_args(args: argparse.Namespace) -> bool:
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
            return False

        raise RuntimeError(
            "negative_sale_plan does not support live execution. "
            "Use --live-read-only for live Bybit snapshot planning; "
            "sale execution is handled by workers.fund_negative_sale_execution_worker."
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
            return False

        return True

    if not args.mock_snapshot_file or not str(args.mock_snapshot_file).strip():
        raise RuntimeError(
            "--mock-snapshot-file is required when --live-read-only is not used."
        )

    return True


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


def _get_fund(db: Session, *, fund_id: int) -> Fund:
    fund = db.query(Fund).filter(Fund.id == int(fund_id)).first()
    if fund is None:
        raise RuntimeError(f"Fund not found: fund_id={fund_id}")
    return fund


def _build_snapshot(
    db: Session,
    *,
    fund: Fund,
    mock_snapshot_file: str | None,
    live_read_only: bool,
) -> NegativeSaleSnapshot:
    if live_read_only:
        return build_negative_sale_snapshot_from_bybit(
            db,
            fund_id=int(fund.id),
        )

    if not mock_snapshot_file:
        raise RuntimeError("mock_snapshot_file is required when live_read_only=False")

    return build_negative_sale_snapshot_mock(
        mock_snapshot_file=mock_snapshot_file,
    )


def _run_once(
    args: argparse.Namespace,
    *,
    mock_snapshot_file: str | None,
    live_read_only: bool,
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
            "dry_run=%s live_read_only=%s mock_snapshot_file=%s candidate_batches=%s",
            args.fund_code,
            args.dry_run,
            live_read_only,
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

        mock_snapshot: NegativeSaleSnapshot | None = None
        if not live_read_only:
            mock_snapshot = build_negative_sale_snapshot_mock(
                mock_snapshot_file=mock_snapshot_file,
            )

        for batch_id in candidate_batch_ids:
            total_count += 1

            if live_read_only:
                settlement_batch = (
                    db.query(FundSettlementBatch)
                    .filter(FundSettlementBatch.id == int(batch_id))
                    .first()
                )
                if settlement_batch is None:
                    raise RuntimeError(f"Settlement batch not found: {batch_id}")

                fund = _get_fund(db, fund_id=int(settlement_batch.fund_id))
                snapshot = _build_snapshot(
                    db,
                    fund=fund,
                    mock_snapshot_file=None,
                    live_read_only=True,
                )
            else:
                snapshot = mock_snapshot

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
                "Negative sale plan worker changes committed "
                "live_read_only=%s ok=%s failed=%s total=%s",
                live_read_only,
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

    if not _validate_stage23_2_args(args):
        return 0

    live_read_only = bool(args.live_read_only)
    mock_snapshot_file = (
        str(args.mock_snapshot_file).strip()
        if args.mock_snapshot_file and str(args.mock_snapshot_file).strip()
        else None
    )
    snapshot_mode = "bybit_readonly" if live_read_only else "mock_fixture"

    log.info(
        "%s negative sale plan worker started snapshot_mode=%s. "
        "Safe by default. No trades, transfers, withdrawals, "
        "BSC transfers, or accounting finalization.",
        STAGE_NAME,
        snapshot_mode,
    )

    if args.run_once:
        return _run_once(
            args,
            mock_snapshot_file=mock_snapshot_file,
            live_read_only=live_read_only,
        )

    while True:
        code = _run_once(
            args,
            mock_snapshot_file=mock_snapshot_file,
            live_read_only=live_read_only,
        )

        if code != 0:
            log.warning(
                "Negative sale plan worker cycle completed with failures code=%s",
                code,
            )

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())