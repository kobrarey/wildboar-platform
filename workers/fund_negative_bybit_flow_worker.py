from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Sequence

from app.bybit.client import BybitV5Client
from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundBybitAccount, FundNegativeSaleBatch, FundSettlementBatch
from app.settlement.negative_bybit_flow import (
    execute_negative_bybit_flow_live,
    execute_negative_bybit_flow_mock,
)
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
            "Negative-net Bybit master flow worker. "
            "Mock mode uses fixture files; live mode executes guarded Universal Transfer "
            "and guarded master withdrawal."
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
        help="Required only in mock mode. Not used with --live-execution.",
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
        help="Live execution is safe-gated by env + CLI flags; no external Bybit call is sent when the gate is disabled.",
    )
    return parser


def parse_worker_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.sleep_seconds < 1:
        parser.error("--sleep-seconds must be >= 1")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="negative_bybit_flow",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE_EXECUTION)
            ),
            cli_enabled=True,
        )
        args.live_gate_allowed = bool(gate.allowed)
        args.live_gate_reason = str(gate.reason)
        args.live_gate = gate.to_dict()
        return args

    if args.mock_flow_file is None:
        parser.error("--mock-flow-file is required when --live-execution is not used")

    args.live_gate_allowed = False
    args.live_gate_reason = "mock mode"
    args.live_gate = {
        "allowed": False,
        "feature": "negative_bybit_flow",
        "reason": "mock mode",
    }
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


def _build_master_bybit_client() -> BybitV5Client:
    api_key = (os.getenv("BYBIT_MASTER_API_KEY") or "").strip()
    api_secret = (os.getenv("BYBIT_MASTER_API_SECRET") or "").strip()

    if not api_key or not api_secret:
        raise RuntimeError(
            "BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET are required "
            "for live negative-net Bybit flow. Use a restricted master API key "
            "with server IP whitelist."
        )

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=settings.BYBIT_MASTER_RECV_WINDOW_MS,
    )


def _get_master_uid() -> str:
    master_uid = (os.getenv("BYBIT_MASTER_UID") or "").strip()
    if not master_uid:
        raise RuntimeError("BYBIT_MASTER_UID is required for live negative-net Bybit flow")
    return master_uid


def _get_fund_sub_uid(db, *, fund_id: int) -> str:
    account = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == int(fund_id),
            FundBybitAccount.coin == settings.NEGATIVE_NET_BYBIT_FLOW_COIN,
            FundBybitAccount.chain_type == settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN,
            FundBybitAccount.is_active == True,
        )
        .first()
    )

    if account is None:
        raise RuntimeError(
            "Active fund_bybit_accounts row is required for live negative-net "
            f"Bybit flow: fund_id={fund_id}, "
            f"coin={settings.NEGATIVE_NET_BYBIT_FLOW_COIN}, "
            f"chain_type={settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN}"
        )

    bybit_sub_uid = str(account.bybit_sub_uid or "").strip()
    if not bybit_sub_uid:
        raise RuntimeError(f"bybit_sub_uid is empty for fund_id={fund_id}")

    return bybit_sub_uid


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


def process_one_live_batch(
    *,
    fund_code: str | None = None,
) -> bool:
    db = SessionLocal()
    try:
        settlement_batch = _candidate_query(db, fund_code=fund_code).first()
        if settlement_batch is None:
            db.rollback()
            return False

        master_client = _build_master_bybit_client()
        master_uid = _get_master_uid()
        fund_sub_uid = _get_fund_sub_uid(
            db,
            fund_id=int(settlement_batch.fund_id),
        )

        result = execute_negative_bybit_flow_live(
            db,
            settlement_batch_id=int(settlement_batch.id),
            bybit_client=master_client,
            fund_sub_uid=fund_sub_uid,
            master_uid=master_uid,
        )

        db.commit()

        print(
            "fund_negative_bybit_flow_worker_live:",
            "action= commit",
            "ok=", result.ok,
            "settlement_batch_id=", result.settlement_batch_id,
            "flow_id=", result.flow_id,
            "status_after=", result.status_after,
            "settlement_status_after=", result.settlement_status_after,
            "transfer_id=", result.universal_transfer_id,
            "request_id=", result.withdrawal_request_id,
            "settlement_wallet_address=", result.settlement_wallet_address,
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


def run_live_forever(
    *,
    fund_code: str | None = None,
    sleep_seconds: int = 10,
) -> None:
    while True:
        processed = process_one_live_batch(
            fund_code=fund_code,
        )
        if not processed:
            time.sleep(sleep_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_worker_args(argv)

    if args.live_execution:
        if not bool(getattr(args, "live_gate_allowed", False)):
            print(
                {
                    "worker": "fund_negative_bybit_flow_worker",
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
                    "worker": "fund_negative_bybit_flow_worker",
                    "live_execution": True,
                    "ok": False,
                    "external_action": False,
                    "reason": "--dry-run is not allowed with --live-execution; use mock mode for dry-run checks",
                }
            )
            return 2

        if args.run_once:
            process_one_live_batch(
                fund_code=args.fund_code,
            )
            return 0

        run_live_forever(
            fund_code=args.fund_code,
            sleep_seconds=args.sleep_seconds,
        )
        return 0

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