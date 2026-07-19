from __future__ import annotations

import argparse
import logging
import os
import time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
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
            "Negative-net targets worker. "
            "Mock mode uses a configured withdrawal fee. "
            "Live read-only mode performs only authenticated "
            "GET /v5/asset/coin/query-info using the master "
            "Bybit API key. No Bybit POST, transfer, withdrawal, "
            "trade, BSC transaction or accounting finalization."
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
        "--live-read-only",
        action="store_true",
        help=(
            "Read BSC USDT withdrawal constraints from "
            "GET /v5/asset/coin/query-info. Requires both "
            "production live env gates. Performs no POST."
        ),
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


def _resolve_mock_bybit_withdrawal_fee(
    args: argparse.Namespace,
) -> Decimal:
    raw = args.mock_bybit_withdrawal_fee_usdt

    if raw is None or str(raw).strip() == "":
        raw = str(
            settings
            .NEGATIVE_NET_MOCK_BYBIT_WITHDRAWAL_FEE_USDT
        )

    return _parse_decimal(
        raw,
        name=(
            "--mock-bybit-withdrawal-fee-usdt"
        ),
    )


def _build_master_bybit_client() -> BybitV5Client:
    api_key = (
        os.getenv("BYBIT_MASTER_API_KEY")
        or ""
    ).strip()
    api_secret = (
        os.getenv("BYBIT_MASTER_API_SECRET")
        or ""
    ).strip()

    if not api_key or not api_secret:
        raise RuntimeError(
            "BYBIT_MASTER_API_KEY and "
            "BYBIT_MASTER_API_SECRET are required "
            "for --live-read-only"
        )

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=(
            settings.BYBIT_MASTER_RECV_WINDOW_MS
        ),
    )


def _validate_stage23_1_args(
    args: argparse.Namespace,
) -> tuple[
    Decimal | None,
    BybitV5Client | None,
    bool,
] | None:
    if int(args.limit) <= 0:
        raise RuntimeError(
            "--limit must be positive"
        )

    if int(args.sleep_sec) <= 0:
        raise RuntimeError(
            "--sleep-sec must be positive"
        )

    if args.live_read_only:
        if (
            args.mock_bybit_withdrawal_fee_usdt
            is not None
            and str(
                args.mock_bybit_withdrawal_fee_usdt
            ).strip()
        ):
            raise RuntimeError(
                "--mock-bybit-withdrawal-fee-usdt "
                "cannot be used with "
                "--live-read-only"
            )

        gate = evaluate_live_gate(
            feature=(
                "negative_net_targets_read_only"
            ),
            env_enabled=(
                bool(
                    settings
                    .LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED
                )
                and bool(
                    settings
                    .NEGATIVE_NET_TARGETS_ALLOW_LIVE_FEE
                )
            ),
            cli_enabled=True,
        )

        if not gate.allowed:
            log.info(
                "Negative-net targets live "
                "read-only gate blocked. "
                "No Bybit call and no DB changes. "
                "gate=%s",
                gate.to_dict(),
            )
            return None

        return (
            None,
            _build_master_bybit_client(),
            True,
        )

    return (
        _resolve_mock_bybit_withdrawal_fee(
            args
        ),
        None,
        False,
    )


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
    bybit_withdrawal_fee_usdt: (
        Decimal | None
    ),
    bybit_client: BybitV5Client | None,
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
            "Negative-net targets worker "
            "run_once started fund_code=%s "
            "dry_run=%s mode=%s "
            "mock_withdrawal_fee_usdt=%s "
            "candidate_batches=%s",
            args.fund_code,
            args.dry_run,
            (
                "bybit_live_read_only"
                if live_read_only
                else "mock"
            ),
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

            result = (
                calculate_and_store_negative_net_targets(
                    db,
                    settlement_batch_id=batch_id,
                    bybit_withdrawal_fee_usdt=(
                        bybit_withdrawal_fee_usdt
                    ),
                    bybit_client=bybit_client,
                    use_live_bybit_withdrawal_fee=(
                        live_read_only
                    ),
                )
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
                "Negative-net targets worker "
                "changes committed mode=%s "
                "ok=%s failed=%s total=%s",
                (
                    "bybit_live_read_only"
                    if live_read_only
                    else "mock"
                ),
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

    validated = _validate_stage23_1_args(
        args
    )

    if validated is None:
        return 0

    (
        bybit_withdrawal_fee_usdt,
        bybit_client,
        live_read_only,
    ) = validated

    log.info(
        "%s negative-net targets worker "
        "started mode=%s. "
        "Live read-only mode permits only "
        "GET /v5/asset/coin/query-info. "
        "No Bybit POST, transfer, withdrawal, "
        "trade, BSC transaction, accounting "
        "finalization or server deploy.",
        STAGE_NAME,
        (
            "bybit_live_read_only"
            if live_read_only
            else "mock"
        ),
    )

    if args.run_once:
        return _run_once(
            args,
            bybit_withdrawal_fee_usdt=(
                bybit_withdrawal_fee_usdt
            ),
            bybit_client=bybit_client,
            live_read_only=live_read_only,
        )

    while True:
        code = _run_once(
            args,
            bybit_withdrawal_fee_usdt=(
                bybit_withdrawal_fee_usdt
            ),
            bybit_client=bybit_client,
            live_read_only=live_read_only,
        )

        if code != 0:
            log.warning(
                "Negative-net targets worker cycle completed with failures code=%s",
                code,
            )

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())