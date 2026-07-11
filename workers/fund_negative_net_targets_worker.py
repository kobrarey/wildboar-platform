from __future__ import annotations

import argparse
import logging
import time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundSettlementBatch
from app.settlement.negative_net_targets import (
    calculate_and_store_negative_net_targets,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
)


log = logging.getLogger(__name__)

STAGE_NAME = "Stage 23.1"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 23.1 negative-net targets worker. "
            "Local/mock only. Calculates redeem fees, net payouts, "
            "batch negative-net targets and mocked Bybit withdrawal fee. "
            "No real Bybit, no BSC, no accounting finalization."
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
        help="Rollback DB changes after calculation.",
    )
    parser.add_argument(
        "--fund-code",
        type=str,
        default=None,
        help="Optional fund code filter, for example wb_test.",
    )
    parser.add_argument(
        "--mock-bybit-withdrawal-fee-usdt",
        type=str,
        default=None,
        help=(
            "Mock Bybit withdrawal fee in USDT. "
            "Stage 23.1 must not call real Bybit fee endpoint."
        ),
    )
    parser.add_argument(
        "--static-bybit-withdrawal-fee-usdt",
        type=str,
        default=None,
        help=(
            "Approved static Bybit withdrawal fee in USDT for production-safe "
            "negative-net target calculation. This does not call Bybit."
        ),
    )
    parser.add_argument(
        "--live-execution",
        action="store_true",
        help="Live fee mode is safe-gated by env + CLI flags; no Bybit call is made when the gate is disabled.",
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


def _parse_decimal(value: str | None, *, name: str) -> Decimal:
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"{name} is required")

    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise RuntimeError(f"{name} must be a valid Decimal value: {value}") from exc

    if result < Decimal("0"):
        raise RuntimeError(f"{name} must be non-negative")

    return result


def _resolve_bybit_withdrawal_fee(args: argparse.Namespace) -> Decimal:
    raw = args.static_bybit_withdrawal_fee_usdt

    if raw is None or str(raw).strip() == "":
        raw = args.mock_bybit_withdrawal_fee_usdt

    if raw is None or str(raw).strip() == "":
        raw = str(settings.NEGATIVE_NET_MOCK_BYBIT_WITHDRAWAL_FEE_USDT)

    return _parse_decimal(
        raw,
        name="--static-bybit-withdrawal-fee-usdt / --mock-bybit-withdrawal-fee-usdt",
    )


def _validate_stage23_1_args(args: argparse.Namespace) -> Decimal | None:
    if int(args.limit) <= 0:
        raise RuntimeError("--limit must be positive")

    if int(args.sleep_sec) <= 0:
        raise RuntimeError("--sleep-sec must be positive")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="negative_net_targets_fee",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.NEGATIVE_NET_TARGETS_ALLOW_LIVE_FEE)
            ),
            cli_enabled=True,
        )
        if not gate.allowed:
            log.info(
                "Negative-net targets live fee gate blocked. No changes. gate=%s",
                gate.to_dict(),
            )
            return None

        return _resolve_bybit_withdrawal_fee(args)

    return _resolve_bybit_withdrawal_fee(args)


def _find_candidate_batch_ids(
    db: Session,
    *,
    fund_code: str | None,
    limit: int,
) -> list[int]:
    q = (
        db.query(FundSettlementBatch.id)
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            FundSettlementBatch.status == BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
            FundSettlementBatch.net_cash_usdt < 0,
        )
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code)

    rows = (
        q.order_by(
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
    bybit_withdrawal_fee_usdt: Decimal,
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
            "Negative-net targets worker run_once started fund_code=%s "
            "dry_run=%s mock_bybit_withdrawal_fee_usdt=%s candidate_batches=%s",
            args.fund_code,
            args.dry_run,
            bybit_withdrawal_fee_usdt,
            candidate_batch_ids,
        )

        if not candidate_batch_ids:
            log.info("No negative-net target candidate batches found.")

            if args.dry_run:
                db.rollback()
            else:
                db.commit()

            return 0

        for batch_id in candidate_batch_ids:
            total_count += 1

            result = calculate_and_store_negative_net_targets(
                db,
                settlement_batch_id=batch_id,
                bybit_withdrawal_fee_usdt=bybit_withdrawal_fee_usdt,
                use_live_bybit_withdrawal_fee=False,
            )

            if result.ok:
                ok_count += 1
            else:
                failed_count += 1

            log.info(
                "Negative-net target calculation result batch_id=%s ok=%s "
                "status_before=%s status_after=%s order_count=%s error=%s result=%s",
                result.settlement_batch_id,
                result.ok,
                result.status_before,
                result.status_after,
                result.order_count,
                result.error,
                result.to_dict(),
            )

        if args.dry_run:
            db.rollback()
            log.info(
                "Negative-net targets worker dry-run rollback completed "
                "ok=%s failed=%s total=%s",
                ok_count,
                failed_count,
                total_count,
            )
        else:
            db.commit()
            log.info(
                "Negative-net targets worker mock/local changes committed "
                "ok=%s failed=%s total=%s",
                ok_count,
                failed_count,
                total_count,
            )

        return 0 if failed_count == 0 else 1

    except Exception as exc:
        db.rollback()
        log.exception(
            "Negative-net targets worker cycle failed error=%s",
            exc,
        )
        return 1

    finally:
        db.close()


def main() -> int:
    _setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    bybit_withdrawal_fee_usdt = _validate_stage23_1_args(args)
    if bybit_withdrawal_fee_usdt is None:
        return 0

    log.info(
        "%s negative-net targets worker started. "
        "Safe by default. No real Bybit calls, Bybit transfers, "
        "no BSC transfers, no accounting finalization, no server deploy.",
        STAGE_NAME,
    )

    if args.run_once:
        return _run_once(
            args,
            bybit_withdrawal_fee_usdt=bybit_withdrawal_fee_usdt,
        )

    while True:
        code = _run_once(
            args,
            bybit_withdrawal_fee_usdt=bybit_withdrawal_fee_usdt,
        )

        if code != 0:
            log.warning(
                "Negative-net targets worker cycle completed with failures code=%s",
                code,
            )

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())