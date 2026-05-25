from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.config import settings
from app.db import SessionLocal
from app.models import Fund, FundSettlementBatch
from app.settlement.positive_net_service import process_positive_net_batch
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_PENDING_CONFIRMATION,
    BATCH_STATUS_POSITIVE_NET_PROCESSING,
)

if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("workers.fund_positive_net_worker")


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
        description="Run Wild Boar positive-net settlement worker."
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one positive-net settlement pass and exit.",
    )

    parser.add_argument(
        "--fund-code",
        default=None,
        help="Optional fund code. Example: wb_test.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run inside rollback transaction and do not send real on-chain/Bybit actions.",
    )

    parser.add_argument(
        "--mock-chain",
        action="store_true",
        help="Mock-confirm on-chain seller payouts and positive net transfer. Local checks only.",
    )

    parser.add_argument(
        "--mock-bybit",
        action="store_true",
        help="Mock-confirm Bybit deposit/internal transfer. Local checks only.",
    )

    parser.add_argument(
        "--no-finalize-accounting",
        action="store_true",
        help="Stop after prerequisites; do not run accounting finalization.",
    )

    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Loop sleep seconds when running without --run-once.",
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


def _build_bybit_client_if_needed(
    *,
    dry_run: bool,
    mock_chain: bool,
    mock_bybit: bool,
) -> BybitV5Client | None:
    """
    Bybit client is needed only for real deposit confirmation.

    Stage 22.1 local mocked checks should use --dry-run and/or --mock-bybit.
    """
    if dry_run or mock_chain or mock_bybit:
        return None

    api_key = (os.getenv("BYBIT_MASTER_API_KEY") or "").strip()
    api_secret = (os.getenv("BYBIT_MASTER_API_SECRET") or "").strip()

    if not api_key or not api_secret:
        raise RuntimeError(
            "BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET are required for real "
            "Bybit deposit confirmation. For local checks use --dry-run --mock-chain --mock-bybit."
        )

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=settings.BYBIT_MASTER_RECV_WINDOW_MS,
    )


def _find_candidate_batches(
    db: Session,
    *,
    fund_code: str | None,
) -> list[FundSettlementBatch]:
    q = (
        db.query(FundSettlementBatch)
        .join(Fund, Fund.id == FundSettlementBatch.fund_id)
        .filter(
            FundSettlementBatch.status.in_(
                [
                    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
                    BATCH_STATUS_POSITIVE_NET_PROCESSING,
                    BATCH_STATUS_PENDING_CONFIRMATION,
                ]
            ),
            FundSettlementBatch.net_cash_usdt >= 0,
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


def _process_batch_in_own_session(
    *,
    batch_id: int,
    bybit_client: BybitV5Client | None,
    dry_run: bool,
    mock_chain: bool,
    mock_bybit: bool,
    finalize_accounting: bool,
) -> bool:
    """
    Per-batch isolation:
    - one batch failure does not stop other funds/batches;
    - each batch uses its own DB session/transaction.
    """
    db = SessionLocal()

    try:
        result = process_positive_net_batch(
            db,
            batch_id=batch_id,
            bybit_client=bybit_client,
            dry_run=dry_run,
            mock_chain=mock_chain,
            mock_bybit=mock_bybit,
            finalize_accounting=finalize_accounting,
        )

        if dry_run:
            db.rollback()
            log.info(
                "Positive net dry-run rollback completed batch_id=%s status=%s "
                "seller_payouts=%s positive_transfer=%s bybit_deposit=%s "
                "internal_ready=%s accounting=%s pricing_unlocked=%s message=%s",
                result.batch_id,
                result.status,
                result.seller_payouts_completed,
                result.positive_net_transfer_confirmed,
                result.bybit_deposit_confirmed,
                result.internal_transfer_ready,
                result.accounting_finalized,
                result.pricing_unlocked,
                result.message,
            )
        else:
            db.commit()
            log.info(
                "Positive net batch processed batch_id=%s status=%s "
                "seller_payouts=%s positive_transfer=%s bybit_deposit=%s "
                "internal_ready=%s accounting=%s pricing_unlocked=%s message=%s",
                result.batch_id,
                result.status,
                result.seller_payouts_completed,
                result.positive_net_transfer_confirmed,
                result.bybit_deposit_confirmed,
                result.internal_transfer_ready,
                result.accounting_finalized,
                result.pricing_unlocked,
                result.message,
            )

        if result.accounting_result is not None:
            ar = result.accounting_result
            log.info(
                "Accounting result batch_id=%s buys=%s redeems=%s buyer_shares=%s "
                "redeem_shares=%s redeem_usdt=%s fund_shares_before=%s "
                "fund_shares_after=%s pricing_unlocked=%s",
                ar.batch_id,
                ar.buy_orders_count,
                ar.redeem_orders_count,
                ar.buyer_shares_issued,
                ar.redeem_shares_burned,
                ar.redeem_usdt_total,
                ar.fund_shares_before,
                ar.fund_shares_after,
                ar.pricing_unlocked,
            )

        return True

    except Exception as exc:
        db.rollback()
        log.exception("Positive net batch failed batch_id=%s error=%s", batch_id, exc)
        return False

    finally:
        db.close()


def _run_once(args: argparse.Namespace) -> int:
    fund_code = _normalize_fund_code(args.fund_code)
    finalize_accounting = not bool(args.no_finalize_accounting)

    bybit_client = _build_bybit_client_if_needed(
        dry_run=bool(args.dry_run),
        mock_chain=bool(args.mock_chain),
        mock_bybit=bool(args.mock_bybit),
    )

    with SessionLocal() as db:
        batches = _find_candidate_batches(db, fund_code=fund_code)
        batch_ids = [batch.id for batch in batches]

    log.info(
        "Positive net worker run_once started fund_code=%s dry_run=%s mock_chain=%s "
        "mock_bybit=%s finalize_accounting=%s candidate_batches=%s",
        fund_code or "all",
        bool(args.dry_run),
        bool(args.mock_chain),
        bool(args.mock_bybit),
        finalize_accounting,
        batch_ids,
    )

    if not batch_ids:
        log.info("No positive-net candidate batches found.")
        return 0

    ok_count = 0
    failed_count = 0

    for batch_id in batch_ids:
        ok = _process_batch_in_own_session(
            batch_id=batch_id,
            bybit_client=bybit_client,
            dry_run=bool(args.dry_run),
            mock_chain=bool(args.mock_chain),
            mock_bybit=bool(args.mock_bybit),
            finalize_accounting=finalize_accounting,
        )

        if ok:
            ok_count += 1
        else:
            failed_count += 1

    log.info(
        "Positive net worker run_once completed ok=%s failed=%s total=%s",
        ok_count,
        failed_count,
        len(batch_ids),
    )

    return 0 if failed_count == 0 else 1


def _run_loop(args: argparse.Namespace) -> int:
    sleep_sec = max(int(args.sleep_sec), 10)

    log.info(
        "Positive net worker loop started sleep_sec=%s",
        sleep_sec,
    )

    while True:
        rc = _run_once(args)
        if rc != 0:
            log.warning("Positive net scheduled pass had failures rc=%s", rc)

        time.sleep(sleep_sec)


def main() -> int:
    load_dotenv()

    args = parse_args()

    if not settings.POSITIVE_NET_SETTLEMENT_ENABLED and not args.dry_run:
        log.info(
            "Positive net settlement worker disabled: POSITIVE_NET_SETTLEMENT_ENABLED=false. "
            "Exit without changes. Use --dry-run for local checks."
        )
        return 0

    if not args.run_once:
        log.warning(
            "Positive net worker started without --run-once. "
            "Loop mode is for future production scheduling, not Stage 22.1 local checks."
        )
        return _run_loop(args)

    return _run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())