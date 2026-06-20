from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.orchestrator import process_positive_net_allocation_batch_mock
from app.allocation.statuses import RETRYABLE_ALLOCATION_BATCH_STATUSES
from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundAllocationBatch
from workers.fund_allocation_execution_worker import MockAllocationExecutionClient


log = logging.getLogger(__name__)


STAGE_NAME = "Stage 22.6"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _positive_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 25 positive-net allocation integration worker. "
            "Mock mode by default; guarded live mode only delegates "
            "candidate batches to the dedicated allocation execution path."
        )
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one polling cycle and exit.",
    )
    parser.add_argument(
        "--fund-code",
        type=str,
        default=None,
        help="Optional fund code filter, for example wb_test.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback each processed allocation batch after mock processing.",
    )
    parser.add_argument(
        "--mock-allocation",
        action="store_true",
        help="Required in Stage 22.6. Enables mock allocation mode.",
    )
    parser.add_argument(
        "--live-execution",
        action="store_true",
        help="Live execution is safe-gated by env + CLI flags; no external action is sent when the gate is disabled.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum allocation batches per cycle.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Sleep interval for loop mode.",
    )

    return parser


def _validate_stage22_6_args(args: argparse.Namespace) -> bool:
    if int(args.limit) <= 0:
        raise RuntimeError("--limit must be positive")

    if int(args.sleep_sec) <= 0:
        raise RuntimeError("--sleep-sec must be positive")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="positive_net_allocation",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.POSITIVE_NET_ALLOCATION_ENABLED)
                and not bool(settings.POSITIVE_NET_ALLOCATION_MOCK_ONLY)
            ),
            cli_enabled=True,
        )
        if not gate.allowed:
            log.info(
                "Positive-net allocation live-execution gate blocked. No changes. gate=%s",
                gate.to_dict(),
            )
            return False

        return True

    if not args.mock_allocation:
        raise RuntimeError(
            "--mock-allocation is required when --live-execution is not used."
        )

    if not settings.POSITIVE_NET_ALLOCATION_MOCK_ONLY:
        raise RuntimeError(
            "Mock allocation mode requires POSITIVE_NET_ALLOCATION_MOCK_ONLY=true."
        )

    return True


def _build_client(args: argparse.Namespace) -> MockAllocationExecutionClient:
    _ = args
    return MockAllocationExecutionClient()


def _find_candidate_allocation_batch_ids(
    db: Session,
    *,
    fund_code: str | None,
    limit: int,
) -> list[int]:
    q = (
        db.query(FundAllocationBatch.id)
        .join(Fund, Fund.id == FundAllocationBatch.fund_id)
        .filter(
            FundAllocationBatch.status.in_(list(RETRYABLE_ALLOCATION_BATCH_STATUSES))
        )
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code)

    rows = (
        q.order_by(
            FundAllocationBatch.id.asc(),
        )
        .limit(int(limit))
        .all()
    )

    return [int(row[0]) for row in rows]


def _process_batch_in_own_session(
    *,
    allocation_batch_id: int,
    dry_run: bool,
    args: argparse.Namespace,
) -> bool:
    if args.live_execution:
        log.error(
            "Positive-net allocation live processing reached candidate batch "
            "but live allocation execution is delegated to the dedicated allocation "
            "execution worker. batch_id=%s",
            allocation_batch_id,
        )
        return False

    db = SessionLocal()
    client = _build_client(args)

    try:
        result = process_positive_net_allocation_batch_mock(
            db,
            allocation_batch_id=allocation_batch_id,
            client=client,
        )

        if client.post_calls:
            raise RuntimeError(f"Unexpected POST calls recorded: {client.post_calls}")

        if dry_run:
            db.rollback()
            log.info(
                "Positive-net allocation dry-run rollback completed "
                "batch_id=%s status_before=%s status_after=%s ok=%s reason=%s "
                "planned_legs=%s alerts=%s",
                result.allocation_batch_id,
                result.status_before,
                result.status_after,
                result.ok,
                result.reason,
                len(result.planned_leg_results),
                result.alert_result.to_dict(),
            )
        else:
            db.commit()
            log.info(
                "Positive-net allocation mock decision committed "
                "batch_id=%s status_before=%s status_after=%s ok=%s reason=%s "
                "planned_legs=%s alerts=%s",
                result.allocation_batch_id,
                result.status_before,
                result.status_after,
                result.ok,
                result.reason,
                len(result.planned_leg_results),
                result.alert_result.to_dict(),
            )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Positive-net allocation batch failed batch_id=%s error=%s",
            allocation_batch_id,
            exc,
        )
        return False

    finally:
        db.close()


def _run_once(args: argparse.Namespace) -> int:
    db = SessionLocal()

    try:
        candidate_batch_ids = _find_candidate_allocation_batch_ids(
            db,
            fund_code=args.fund_code,
            limit=args.limit,
        )

        log.info(
            "Positive-net allocation worker run_once started "
            "fund_code=%s dry_run=%s mock_allocation=%s candidate_batches=%s",
            args.fund_code,
            args.dry_run,
            args.mock_allocation,
            candidate_batch_ids,
        )

    finally:
        db.close()

    if not candidate_batch_ids:
        log.info("No positive-net allocation candidate batches found.")
        return 0

    ok_count = 0
    failed_count = 0

    for allocation_batch_id in candidate_batch_ids:
        ok = _process_batch_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=args.dry_run,
            args=args,
        )

        if ok:
            ok_count += 1
        else:
            failed_count += 1

    log.info(
        "Positive-net allocation worker run_once completed "
        "ok=%s failed=%s total=%s",
        ok_count,
        failed_count,
        ok_count + failed_count,
    )

    return 0 if failed_count == 0 else 1


def main() -> int:
    _setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    if not _validate_stage22_6_args(args):
        return 0

    log.info(
        "%s positive-net allocation worker started. "
        "Mock mode by default; guarded live mode does not run mock handlers "
        "against real candidate batches.",
        STAGE_NAME,
    )

    if args.run_once:
        return _run_once(args)

    while True:
        code = _run_once(args)
        if code != 0:
            log.warning(
                "Positive-net allocation worker cycle completed with failures code=%s",
                code,
            )

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())