from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

from cryptography.fernet import Fernet

from app.bybit.client import BybitV5Client
from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.models import Fund, FundBybitAccount, FundNegativeSaleBatch, FundSettlementBatch
from app.settlement.negative_sale_execution import (
    execute_negative_sale_plan_live,
    execute_negative_sale_plan_mock,
    load_negative_sale_execution_mock_file,
)
from app.settlement.accounting_service import (
    SettlementShareQuantityError,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    SALE_BATCH_STATUS_SALE_PLAN_CREATED,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workers.fund_negative_sale_execution_worker",
        description="Stage 25.1 negative-net sale execution worker. Mock mode by default; guarded live execution behind explicit gates.",
    )
    parser.add_argument("--run-once", action="store_true", help="Process at most one sale batch and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Rollback after successful mock execution instead of commit.")
    parser.add_argument("--mock-execution-file", type=Path, default=None, help="Required Stage 23.3.1 mock execution fixture JSON file.")
    parser.add_argument("--fund-code", type=str, default=None, help="Optional fund code filter.")
    parser.add_argument("--sleep-seconds", type=int, default=10, help="Sleep interval for loop mode.")
    parser.add_argument("--live-execution", action="store_true", help="Live execution is safe-gated by env + CLI flags; no external action is sent when the gate is disabled.")
    return parser


def parse_worker_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.sleep_seconds < 1:
        parser.error("--sleep-seconds must be >= 1")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="negative_sale_execution",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.NEGATIVE_NET_SALE_EXECUTION_ALLOW_LIVE)
            ),
            cli_enabled=True,
        )
        args.live_gate_allowed = bool(gate.allowed)
        args.live_gate_reason = str(gate.reason)
        args.live_gate = gate.to_dict()
        return args

    if args.mock_execution_file is None:
        parser.error("--mock-execution-file is required when --live-execution is not used")

    args.live_gate_allowed = False
    args.live_gate_reason = "mock mode"
    args.live_gate = {
        "allowed": False,
        "feature": "negative_sale_execution",
        "reason": "mock mode",
    }
    return args


def _candidate_query(db, *, fund_code: str | None = None):
    query = (
        db.query(FundNegativeSaleBatch)
        .join(FundSettlementBatch, FundSettlementBatch.id == FundNegativeSaleBatch.settlement_batch_id)
        .join(Fund, Fund.id == FundNegativeSaleBatch.fund_id)
        .filter(FundNegativeSaleBatch.status == SALE_BATCH_STATUS_SALE_PLAN_CREATED)
        .filter(FundSettlementBatch.status == BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED)
    )
    if fund_code:
        query = query.filter(Fund.code == str(fund_code))
    return query.order_by(FundNegativeSaleBatch.id.asc()).with_for_update(skip_locked=True)


def _decrypt_bybit_secret(value: str) -> str:
    key = (settings.BYBIT_API_ENC_KEY or "").strip()
    if not key or key in {"CHANGE_ME", "CHANGE_ME_TO_FERNET_KEY"}:
        raise RuntimeError("BYBIT_API_ENC_KEY is not set for fund Bybit API credentials")

    if not value:
        raise RuntimeError("Encrypted Bybit credential is empty")

    return Fernet(key.encode("utf-8")).decrypt(value.encode("utf-8")).decode("utf-8")


def _build_fund_trading_bybit_client(db, *, fund_id: int) -> BybitV5Client:
    account = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == int(fund_id),
            FundBybitAccount.coin == "USDT",
            FundBybitAccount.is_active == True,
            FundBybitAccount.api_key_is_active == True,
        )
        .first()
    )

    if account is None:
        raise RuntimeError(
            "NEGATIVE_SALE_EXECUTION_BYBIT_CLIENT_SCOPE_NOT_READY: "
            f"active fund Bybit API credentials not found for fund_id={fund_id}"
        )

    api_key = _decrypt_bybit_secret(account.api_key_encrypted or "")
    api_secret = _decrypt_bybit_secret(account.api_secret_encrypted or "")

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=settings.BYBIT_MASTER_RECV_WINDOW_MS,
    )


def process_one_batch(*, mock_path: str | Path, fund_code: str | None = None, dry_run: bool = False) -> bool:
    mock_execution = load_negative_sale_execution_mock_file(mock_path)
    db = SessionLocal()
    try:
        sale_batch = _candidate_query(db, fund_code=fund_code).first()
        if sale_batch is None:
            db.rollback()
            return False
        result = execute_negative_sale_plan_mock(db, sale_batch_id=int(sale_batch.id), mock_execution=mock_execution)
        if dry_run:
            db.rollback()
            action = "rollback"
        else:
            db.commit()
            action = "commit"
        print(
            "fund_negative_sale_execution_worker:",
            "action=", action,
            "sale_batch_id=", result.sale_batch_id,
            "status_after=", result.status_after,
            "settlement_status_after=", result.settlement_status_after,
            "final_shortage_usdt=", result.final_shortage_usdt,
            "fund_code_filter=", fund_code or "",
        )
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_one_live_batch(*, fund_code: str | None = None) -> bool:
    db = SessionLocal()
    try:
        sale_batch = _candidate_query(db, fund_code=fund_code).first()
        if sale_batch is None:
            db.rollback()
            return False

        client = _build_fund_trading_bybit_client(
            db,
            fund_id=int(sale_batch.fund_id),
        )

        result = execute_negative_sale_plan_live(
            db,
            sale_batch_id=int(sale_batch.id),
            client=client,
        )

        db.commit()

        print(
            "fund_negative_sale_execution_worker_live:",
            "action= commit",
            "ok=", result.ok,
            "sale_batch_id=", result.sale_batch_id,
            "status_after=", result.status_after,
            "settlement_status_after=", result.settlement_status_after,
            "final_shortage_usdt=", result.final_shortage_usdt,
            "executed_leg_count=", result.executed_leg_count,
            "fund_code_filter=", fund_code or "",
        )
        return True

    except SettlementShareQuantityError as exc:
        db.commit()

        print(
            "fund_negative_sale_execution_worker_live:",
            "action= commit_failed_requires_review",
            "external_action= false",
            "error=", str(exc),
            "fund_code_filter=", fund_code or "",
        )
        return True

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_forever(*, mock_path: str | Path, fund_code: str | None = None, dry_run: bool = False, sleep_seconds: int = 10) -> None:
    while True:
        processed = process_one_batch(mock_path=mock_path, fund_code=fund_code, dry_run=dry_run)
        if not processed:
            time.sleep(sleep_seconds)


def run_live_forever(*, fund_code: str | None = None, sleep_seconds: int = 10) -> None:
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
                    "worker": "fund_negative_sale_execution_worker",
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
                    "worker": "fund_negative_sale_execution_worker",
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
            mock_path=args.mock_execution_file,
            fund_code=args.fund_code,
            dry_run=args.dry_run,
        )
        return 0

    run_forever(
        mock_path=args.mock_execution_file,
        fund_code=args.fund_code,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
