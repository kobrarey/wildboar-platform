from __future__ import annotations

import argparse
import time
from typing import Sequence

from sqlalchemy import and_, or_

from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundNegativeBybitFlow, FundNegativePayoutBatch, FundSettlementBatch
from app.settlement.negative_payout_flow import (
    LIVE_RESUMABLE_PAYOUT_BATCH_STATUSES,
    execute_negative_payout_flow_live,
    execute_negative_payout_flow_mock,
)
from app.settlement.negative_payout_flow_mock import load_negative_payout_mock_file
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
    BYBIT_FLOW_STATUS_COMPLETED,
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Negative-net payout worker. Mock mode uses fixture files; "
            "live mode executes guarded BSC gas top-up and guarded USDT payouts."
        )
    )
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock-payout-file", default=None)
    parser.add_argument("--fund-code", default=None)
    parser.add_argument("--sleep-seconds", type=int, default=30)
    parser.add_argument("--live-execution", action="store_true")

    args = parser.parse_args(argv)

    if int(args.sleep_seconds) < 1:
        parser.error("--sleep-seconds must be >= 1")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="negative_payout",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION)
            ),
            cli_enabled=True,
        )
        args.live_gate_allowed = bool(gate.allowed)
        args.live_gate_reason = str(gate.reason)
        args.live_gate = gate.to_dict()
        return args

    if not args.mock_payout_file:
        parser.error("--mock-payout-file is required when --live-execution is not used")

    args.live_gate_allowed = False
    args.live_gate_reason = "mock mode"
    args.live_gate = {
        "allowed": False,
        "feature": "negative_payout",
        "reason": "mock mode",
    }
    return args


def _load_candidates(db, *, fund_code: str | None):
    query = (
        db.query(FundSettlementBatch)
        .join(
            FundNegativeBybitFlow,
            FundNegativeBybitFlow.settlement_batch_id == FundSettlementBatch.id,
        )
        .outerjoin(
            FundNegativePayoutBatch,
            FundNegativePayoutBatch.settlement_batch_id == FundSettlementBatch.id,
        )
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(FundNegativeBybitFlow.status == BYBIT_FLOW_STATUS_COMPLETED)
        .filter(
            or_(
                FundSettlementBatch.status
                == BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
                and_(
                    FundSettlementBatch.status
                    == BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
                    FundNegativePayoutBatch.id.isnot(None),
                    FundNegativePayoutBatch.status.in_(
                        sorted(LIVE_RESUMABLE_PAYOUT_BATCH_STATUSES)
                    ),
                ),
            )
        )
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


def _run_live_once(*, fund_code: str | None) -> int:
    db = SessionLocal()
    try:
        candidates = _load_candidates(db, fund_code=fund_code)
        processed = 0

        for settlement_batch in candidates:
            result = execute_negative_payout_flow_live(
                db,
                settlement_batch_id=int(settlement_batch.id),
            )
            processed += 1

            print(
                {
                    "worker": "fund_negative_payout_worker",
                    "live_execution": True,
                    "settlement_batch_id": result.settlement_batch_id,
                    "payout_batch_id": result.payout_batch_id,
                    "ok": result.ok,
                    "status_after": result.status_after,
                    "settlement_status_after": result.settlement_status_after,
                    "payout_leg_count": result.payout_leg_count,
                    "confirmed_payout_leg_count": result.confirmed_payout_leg_count,
                    "expected_total_payout_usdt": result.expected_total_payout_usdt,
                    "confirmed_total_payout_usdt": result.confirmed_total_payout_usdt,
                    "error": result.error,
                    "diagnostics": result.diagnostics,
                }
            )

        db.commit()
        print(
            {
                "worker": "fund_negative_payout_worker",
                "live_execution": True,
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.live_execution:
        if not bool(getattr(args, "live_gate_allowed", False)):
            print(
                {
                    "worker": "fund_negative_payout_worker",
                    "live_execution": True,
                    "skipped": True,
                    "external_action": False,
                    "reason": getattr(args, "live_gate_reason", "live gate blocked"),
                }
            )
            return 0

        if args.dry_run:
            print(
                {
                    "worker": "fund_negative_payout_worker",
                    "live_execution": True,
                    "ok": False,
                    "external_action": False,
                    "reason": "--dry-run is not allowed with --live-execution; use mock mode for dry-run checks",
                }
            )
            return 2

        while True:
            _run_live_once(
                fund_code=args.fund_code,
            )

            if args.run_once:
                break

            time.sleep(max(int(args.sleep_seconds), 1))

        return 0

    while True:
        _run_once(
            mock_payout_file=args.mock_payout_file,
            dry_run=bool(args.dry_run),
            fund_code=args.fund_code,
        )

        if args.run_once:
            break

        time.sleep(max(int(args.sleep_seconds), 1))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())