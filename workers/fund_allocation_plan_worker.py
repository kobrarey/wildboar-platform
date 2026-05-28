from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.allocation.bybit_snapshot_reader import build_allocation_snapshot_from_bybit
from app.allocation.plan_service import build_allocation_plan_for_settlement_batch
from app.allocation.snapshot_service import build_allocation_snapshot_from_fixture_file
from app.db import SessionLocal
from app.models import Fund, FundSettlementBatch
from app.settlement.statuses import BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED


log = logging.getLogger(__name__)


SUPPORTED_FUNDS = {
    "btc_fund",
    "defi_sniper",
    "wb10",
    "wb_test",
    "wb_defi",
    "wb_web3",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build Stage 22.2 allocation snapshot + allocation plan. "
            "No trading, no Bybit orders, no Earn stake, no on-chain transfers."
        )
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one allocation-plan cycle and exit.",
    )

    parser.add_argument(
        "--fund-code",
        default=None,
        help="Optional fund code filter. Example: wb_test.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback DB changes after building the plan.",
    )

    parser.add_argument(
        "--mock-snapshot-file",
        default=None,
        help="Path to mock allocation snapshot JSON fixture.",
    )

    parser.add_argument(
        "--live-read-only",
        action="store_true",
        help=(
            "Prepared code path for future real read-only Bybit snapshot. "
            "Still blocked by default in Stage 22.2.1."
        ),
    )

    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Loop sleep seconds if --run-once is not used.",
    )

    return parser.parse_args()


def _normalize_fund_code(value: str | None) -> str | None:
    if not value:
        return None

    code = value.strip().lower()
    if not code:
        return None

    if code not in SUPPORTED_FUNDS:
        allowed = ", ".join(sorted(SUPPORTED_FUNDS))
        raise ValueError(f"Unsupported fund code: {code}. Allowed: {allowed}")

    return code


def _validate_stage22_2_args(args: argparse.Namespace) -> None:
    if args.live_read_only:
        raise RuntimeError(
            "--live-read-only code path exists via build_allocation_snapshot_from_bybit(...), "
            "but real Bybit reads are still blocked in Stage 22.2.1. "
            "Use --mock-snapshot-file for mocked/non-live checks."
        )

    if not args.mock_snapshot_file:
        raise RuntimeError(
            "--mock-snapshot-file is required while --live-read-only is blocked."
        )

    snapshot_path = Path(args.mock_snapshot_file)
    if not snapshot_path.exists():
        raise RuntimeError(f"Mock snapshot file not found: {snapshot_path}")


def _find_candidate_batches(
    db: Session,
    *,
    fund_code: str | None,
) -> list[FundSettlementBatch]:
    q = (
        db.query(FundSettlementBatch)
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            FundSettlementBatch.status == BATCH_STATUS_POSITIVE_CASH_SETTLEMENT_COMPLETED,
            FundSettlementBatch.net_cash_usdt > 0,
        )
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code)

    return (
        q.order_by(
            FundSettlementBatch.settlement_date.asc(),
            FundSettlementBatch.id.asc(),
        )
        .all()
    )


def _get_fund(db: Session, *, fund_id: int) -> Fund:
    fund = db.query(Fund).filter(Fund.id == fund_id).first()
    if fund is None:
        raise RuntimeError(f"Fund not found: fund_id={fund_id}")
    return fund


def _build_snapshot(
    db: Session,
    *,
    fund: Fund,
    mock_snapshot_file: str | None,
    live_read_only: bool,
):
    if live_read_only:
        # Prepared for a later approved stage.
        # Stage 22.2.1 validation blocks this path before execution.
        return build_allocation_snapshot_from_bybit(
            db,
            fund_id=fund.id,
        )

    if not mock_snapshot_file:
        raise RuntimeError("mock_snapshot_file is required when live_read_only=False")

    return build_allocation_snapshot_from_fixture_file(
        fund_id=fund.id,
        fund_code=fund.code,
        path=mock_snapshot_file,
    )


def _process_batch_in_own_session(
    *,
    batch_id: int,
    mock_snapshot_file: str | None,
    live_read_only: bool,
    dry_run: bool,
) -> bool:
    db = SessionLocal()

    try:
        batch = (
            db.query(FundSettlementBatch)
            .filter(FundSettlementBatch.id == batch_id)
            .first()
        )
        if batch is None:
            raise RuntimeError(f"Settlement batch not found: {batch_id}")

        fund = _get_fund(db, fund_id=batch.fund_id)

        snapshot = _build_snapshot(
            db,
            fund=fund,
            mock_snapshot_file=mock_snapshot_file,
            live_read_only=live_read_only,
        )

        summary = build_allocation_plan_for_settlement_batch(
            db,
            settlement_batch_id=batch.id,
            snapshot=snapshot,
        )

        if dry_run:
            db.rollback()
            log.info(
                "Allocation plan dry-run rollback completed "
                "allocation_batch_id=%s settlement_batch_id=%s fund_code=%s "
                "positive_net_usdt=%s base_nav_for_scale_usdt=%s scale=%s "
                "legs_count=%s legs_by_group=%s status=%s warnings=%s",
                summary.allocation_batch_id,
                summary.settlement_batch_id,
                summary.fund_code,
                summary.positive_net_usdt,
                summary.base_nav_for_scale_usdt,
                summary.scale,
                summary.legs_count,
                summary.legs_by_group,
                summary.status,
                summary.warnings,
            )
        else:
            db.commit()
            log.info(
                "Allocation plan created "
                "allocation_batch_id=%s settlement_batch_id=%s fund_code=%s "
                "positive_net_usdt=%s base_nav_for_scale_usdt=%s scale=%s "
                "legs_count=%s legs_by_group=%s status=%s warnings=%s",
                summary.allocation_batch_id,
                summary.settlement_batch_id,
                summary.fund_code,
                summary.positive_net_usdt,
                summary.base_nav_for_scale_usdt,
                summary.scale,
                summary.legs_count,
                summary.legs_by_group,
                summary.status,
                summary.warnings,
            )

        return True

    except Exception as exc:
        db.rollback()
        log.exception("Allocation plan batch failed batch_id=%s error=%s", batch_id, exc)
        return False

    finally:
        db.close()


def _run_once(args: argparse.Namespace) -> int:
    fund_code = _normalize_fund_code(args.fund_code)

    with SessionLocal() as db:
        batches = _find_candidate_batches(db, fund_code=fund_code)
        batch_ids = [batch.id for batch in batches]

    snapshot_mode = "bybit_readonly" if args.live_read_only else "mock_fixture"

    log.info(
        "Allocation plan worker run_once started fund_code=%s dry_run=%s "
        "snapshot_mode=%s mock_snapshot_file=%s candidate_batches=%s",
        fund_code or "all",
        bool(args.dry_run),
        snapshot_mode,
        args.mock_snapshot_file,
        batch_ids,
    )

    if not batch_ids:
        log.info("No allocation-plan candidate batches found.")
        return 0

    ok_count = 0
    failed_count = 0

    for batch_id in batch_ids:
        ok = _process_batch_in_own_session(
            batch_id=batch_id,
            mock_snapshot_file=args.mock_snapshot_file,
            live_read_only=bool(args.live_read_only),
            dry_run=bool(args.dry_run),
        )

        if ok:
            ok_count += 1
        else:
            failed_count += 1

    log.info(
        "Allocation plan worker run_once completed ok=%s failed=%s total=%s",
        ok_count,
        failed_count,
        len(batch_ids),
    )

    return 0 if failed_count == 0 else 1


def _run_loop(args: argparse.Namespace) -> int:
    sleep_sec = max(int(args.sleep_sec), 10)

    snapshot_mode = "bybit_readonly" if args.live_read_only else "mock_fixture"

    log.info(
        "Allocation plan worker loop started sleep_sec=%s dry_run=%s "
        "snapshot_mode=%s mock_snapshot_file=%s",
        sleep_sec,
        bool(args.dry_run),
        snapshot_mode,
        args.mock_snapshot_file,
    )

    while True:
        code = _run_once(args)
        if code != 0:
            log.warning("Allocation plan worker loop iteration completed with code=%s", code)
        time.sleep(sleep_sec)


def main() -> int:
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = parse_args()
    _validate_stage22_2_args(args)

    log.info(
        "Stage 22.2.1 allocation plan worker started. "
        "Real Bybit read-only path is implemented but blocked by default. "
        "No trades, no transfers, no Earn stake."
    )

    if args.run_once:
        return _run_once(args)

    return _run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())